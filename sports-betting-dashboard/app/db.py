"""SQLite persistence: settings, odds cache, API usage, manual prices, bets."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS odds_cache (
    sport_key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, payload TEXT NOT NULL, markets TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS props_cache (
    event_id TEXT PRIMARY KEY, sport_key TEXT NOT NULL, fetched_at REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, endpoint TEXT NOT NULL,
    sport_key TEXT, cost INTEGER NOT NULL, remaining INTEGER, used INTEGER
);
CREATE TABLE IF NOT EXISTS manual_prices (sel_id TEXT PRIMARY KEY, price REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fair_history (
    sel_id TEXT PRIMARY KEY, fair_prob REAL NOT NULL, ts REAL NOT NULL, commence_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    kind TEXT NOT NULL,                -- single | acca
    description TEXT NOT NULL,
    sport_group TEXT,
    commence_time TEXT,
    odds REAL NOT NULL,
    stake REAL NOT NULL,
    fair_prob REAL,                    -- our probability when placed
    legs TEXT NOT NULL,                -- JSON list of {sel_id, label, odds, fair_prob}
    status TEXT NOT NULL DEFAULT 'open',  -- open|won|lost|void|half_won|half_lost
    profit REAL,
    closing_prob REAL,                 -- fair prob at last snapshot before kickoff
    settled_at REAL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def reset_connection() -> None:
    """Used by tests to point at a fresh DB."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


@contextmanager
def tx():
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


# ---------- settings ----------

def get_settings() -> dict:
    with tx() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: json.loads(r["value"]) for r in rows}
    merged = dict(config.DEFAULT_SETTINGS)
    merged.update({k: v for k, v in stored.items() if k in config.DEFAULT_SETTINGS})
    return merged


def update_settings(values: dict) -> dict:
    with tx() as c:
        for k, v in values.items():
            if k in config.DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
    return get_settings()


# ---------- kv cache ----------

def kv_get(key: str, max_age: float | None = None):
    with tx() as c:
        row = c.execute("SELECT value, updated_at FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None or (max_age is not None and time.time() - row["updated_at"] > max_age):
        return None
    return json.loads(row["value"])


def kv_set(key: str, value) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
                  (key, json.dumps(value), time.time()))


# ---------- odds cache ----------

def save_odds(sport_key: str, events: list, markets: list[str]) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO odds_cache (sport_key, fetched_at, payload, markets) VALUES (?, ?, ?, ?)",
                  (sport_key, time.time(), json.dumps(events), json.dumps(markets)))


def load_all_odds() -> dict[str, dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM odds_cache").fetchall()
    return {r["sport_key"]: {"fetched_at": r["fetched_at"], "events": json.loads(r["payload"]),
                             "markets": json.loads(r["markets"])} for r in rows}


def clear_odds() -> None:
    with tx() as c:
        c.execute("DELETE FROM odds_cache")
        c.execute("DELETE FROM props_cache")


def save_props(event_id: str, sport_key: str, event: dict) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO props_cache (event_id, sport_key, fetched_at, payload) VALUES (?, ?, ?, ?)",
                  (event_id, sport_key, time.time(), json.dumps(event)))


def load_props() -> dict[str, dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM props_cache").fetchall()
    return {r["event_id"]: json.loads(r["payload"]) for r in rows}


# ---------- api usage ----------

def log_usage(endpoint: str, sport_key: str | None, cost: int, remaining: int | None, used: int | None) -> None:
    with tx() as c:
        c.execute("INSERT INTO api_usage (ts, endpoint, sport_key, cost, remaining, used) VALUES (?, ?, ?, ?, ?, ?)",
                  (time.time(), endpoint, sport_key, cost, remaining, used))


def usage_summary() -> dict:
    with tx() as c:
        last = c.execute("SELECT remaining, used, ts FROM api_usage WHERE remaining IS NOT NULL "
                         "ORDER BY id DESC LIMIT 1").fetchone()
        day_ago = time.time() - 86400
        today = c.execute("SELECT COALESCE(SUM(cost), 0) AS s FROM api_usage WHERE ts > ?", (day_ago,)).fetchone()
    return {
        "remaining": last["remaining"] if last else None,
        "used": last["used"] if last else None,
        "last_call": last["ts"] if last else None,
        "spent_24h": today["s"],
    }


# ---------- manual prices & fair history ----------

def set_manual_price(sel_id: str, price: float | None) -> None:
    with tx() as c:
        if price is None:
            c.execute("DELETE FROM manual_prices WHERE sel_id = ?", (sel_id,))
        else:
            c.execute("INSERT OR REPLACE INTO manual_prices (sel_id, price, updated_at) VALUES (?, ?, ?)",
                      (sel_id, float(price), time.time()))


def manual_prices() -> dict[str, float]:
    with tx() as c:
        rows = c.execute("SELECT sel_id, price FROM manual_prices").fetchall()
    return {r["sel_id"]: r["price"] for r in rows}


def record_fair_probs(items: list[tuple[str, float, str]]) -> None:
    """Store the latest fair probability for selections that haven't started (closing-line proxy)."""
    with tx() as c:
        c.executemany("INSERT OR REPLACE INTO fair_history (sel_id, fair_prob, ts, commence_time) VALUES (?, ?, ?, ?)",
                      [(s, p, time.time(), ct) for s, p, ct in items])


def fair_probs(sel_ids: list[str]) -> dict[str, float]:
    if not sel_ids:
        return {}
    with tx() as c:
        q = "SELECT sel_id, fair_prob FROM fair_history WHERE sel_id IN (%s)" % ",".join("?" * len(sel_ids))
        rows = c.execute(q, sel_ids).fetchall()
    return {r["sel_id"]: r["fair_prob"] for r in rows}


# ---------- bets ----------

def add_bet(bet: dict) -> int:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO bets (created_at, kind, description, sport_group, commence_time, odds, stake, fair_prob, legs) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), bet["kind"], bet["description"], bet.get("sport_group"), bet.get("commence_time"),
             float(bet["odds"]), float(bet["stake"]), bet.get("fair_prob"), json.dumps(bet.get("legs", []))))
        return cur.lastrowid


def list_bets() -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM bets ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["legs"] = json.loads(d["legs"])
        out.append(d)
    return out


def update_bet(bet_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE bets SET {cols} WHERE id = ?", (*fields.values(), bet_id))


def delete_bet(bet_id: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM bets WHERE id = ?", (bet_id,))
