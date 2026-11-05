"""Daily fund run for two pies.

    python -m fund.run daily              # fetch prices, update both pies, write fund/state/data.json
    python -m fund.run annotate "text"    # attach the plain-English note to today's entry
    python -m fund.run unavailable "why"  # record a day with no data (dashboard shows it)

Pie A: index-plus-2% strategy (model.py). Pie B: stock selection (model_b.py).
The two pies are held 50/50 and moved back when either drifts 5+ points.
Trade suggestions are worked out on the whole fund, netted by instrument.
State lives in fund/state/state.json and is committed after each run.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from . import config as C
from . import model as M
from . import model_b as MB

STATE = Path(__file__).parent / "state" / "state.json"
DATA = Path(__file__).parent / "state" / "data.json"

A_ITEMS = [C.CORE] + C.FACTORS + C.REGIONS + C.STOCKS + [C.DEFENSIVE["gold"], C.DEFENSIVE["cash"]]
NAMES = {it["id"]: it["name"] for it in A_ITEMS}
NAMES.update({s["id"]: s["ticker"] for s in C.B_STOCKS})
SLEEVE = {C.CORE["id"]: "Core", C.DEFENSIVE["gold"]["id"]: "Defensive", "CASH": "Cash",
          **{f["id"]: "Style" for f in C.FACTORS}, **{r["id"]: "Region" for r in C.REGIONS},
          **{s["id"]: "Stock" for s in C.STOCKS}, **{s["id"]: s["sector"] for s in C.B_STOCKS}}
PIES = ("A", "B")


def key_of(i: str) -> str:
    """Instrument key shared by both pies, so the same stock nets out."""
    for s in C.STOCKS:
        if s["id"] == i:
            return s["tickers"][0]
    return i[2:] if i.startswith("B_") else i


def label_of(k: str) -> str:
    for s in C.STOCKS:
        if s["tickers"][0] == k:
            return s["id"]
    return k


def _empty_pie():
    return {"units": {}, "target": {}, "level": None, "last_rebalance": None, "current": {}, "days": 0}


def load_state() -> dict:
    if STATE.exists():
        st = json.loads(STATE.read_text())
        if st.get("version") == 2:
            return st
    return {"version": 2, "inception": None, "last_data_date": None, "bench_start": None,
            "pies": {p: _empty_pie() for p in PIES}, "index": {"A": 100.0, "B": 100.0, "total": 100.0},
            "nav": [], "log": [], "signals": {}, "meta": {}}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1, default=float))
    DATA.write_text(json.dumps(dashboard_payload(state), indent=1, default=float))


def _value(units: dict, prices: dict) -> float:
    return sum(u * (1.0 if k == "CASH" else prices[k]) for k, u in units.items())


def _set_units(target: dict, value: float, prices: dict) -> dict:
    return {k: (w / 100 * value) / (1.0 if k == "CASH" else prices[k]) for k, w in target.items()}


def combined(weights_a: dict, weights_b: dict, share_a: float) -> dict[str, float]:
    """Whole-fund weights (%), netted by instrument key."""
    out: dict[str, float] = {}
    for w, share in ((weights_a, share_a), (weights_b, 100 - share_a)):
        for k, v in w.items():
            kk = key_of(k)
            out[kk] = out.get(kk, 0) + v * share / 100
    return out


def daily(today: str | None = None) -> dict:
    from .data import download

    state = load_state()
    px, meta = download()
    px = px.dropna(subset=[C.CORE["id"]])
    data_date = str(px.index[-1].date())
    run_date = today or dt.date.today().isoformat()

    if state["last_data_date"] == data_date:
        # Nothing new to decide. Keep the latest decision (and its note) on the
        # dashboard untouched; only record that we checked.
        state["last_checked"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_run_action"] = "NO_NEW_DATA"
        save(state)
        return {"run_date": run_date, "data_date": data_date, "action": "NO_NEW_DATA",
                "reasons": [f"No new closing prices since {data_date}; the latest decision stands"],
                "pies": {}, "trades": {}, "note": ""}

    last = px.iloc[-1]
    prices = {k: float(last[k]) for k in px.columns if last.notna()[k]}
    pies = state["pies"]
    first = not pies["A"]["units"]
    for p in PIES:   # a holding with no price today keeps its last known price
        for k in pies[p]["units"]:
            if k != "CASH" and k not in prices:
                prices[k] = float(px[k].dropna().iloc[-1])

    # Value of each pie before any trading today.
    if first:
        vals = {p: float(C.SPLIT[p]) for p in PIES}
        state["inception"] = data_date
        state["bench_start"] = prices[C.CORE["id"]]
    else:
        vals = {p: _value(pies[p]["units"], prices) for p in PIES}
        prev = state["nav"][-1]
        for p in PIES:
            if prev[f"{p}_val"]:
                state["index"][p] *= vals[p] / prev[f"{p}_val"]
        state["index"]["total"] *= sum(vals.values()) / (prev["A_val"] + prev["B_val"])
    total_before = sum(vals.values())
    current = {p: (M.drifted_weights(pies[p]["units"], prices)[0] if pies[p]["units"] else {}) for p in PIES}
    current_total = combined(current["A"], current["B"], 100 * vals["A"] / total_before)

    # Each pie's own decision.
    res = {"A": M.allocate(px, prev=pies["A"]["target"], prev_level=pies["A"]["level"]),
           "B": MB.allocate_b(px, prev=pies["B"]["target"])}
    params = {"A": {}, "B": {"trigger": C.B["trigger"], "min_days": C.B["min_days"]}}
    min_days = {"A": C.MIN_DAYS_BETWEEN, "B": C.B["min_days"]}
    decision = {}
    for p in PIES:
        d = pies[p]
        days_since = d["days"] + 1 if d["last_rebalance"] else None
        lvl = res[p]["regime"]["level"]
        go, why = M.decide(res[p]["target"], current[p], d["target"], d["level"], lvl, days_since, **params[p])
        if not go:
            if days_since is not None and days_since < min_days[p]:
                why = [f"Cooling-off period: {days_since} of {min_days[p]} trading days since the last rebalance"]
            else:
                why = ["Within tolerance; no trades needed"]
            why += res[p]["notes"]
            pending = {k: v for k, v in res[p]["target"].items() if d["target"].get(k) != v}
            gone = [k for k in d["target"] if k not in res[p]["target"]]
            if pending or gone:
                parts = [f"{NAMES.get(k, k)} {d['target'].get(k, 0)}→{v}%" for k, v in pending.items()]
                parts += [f"{NAMES.get(k, k)} {d['target'][k]}→0%" for k in gone]
                why.append("Model would change " + ", ".join(parts[:6]) + "; not enough to act on yet")
        decision[p] = {"go": go, "why": why}

    # Split between the pies.
    share_a = 100 * vals["A"] / total_before
    split_go = first or abs(share_a - C.SPLIT["A"]) >= C.SPLIT_DRIFT_PP
    split_why = [] if first or not split_go else [
        f"Pie A is {share_a:.1f}% of the fund (target {C.SPLIT['A']}%): move money back to 50/50"]
    if split_go:
        for p in PIES:
            decision[p]["go"] = True
            if not first:
                decision[p]["why"] = split_why + [w for w in decision[p]["why"] if not w.startswith(("Within", "Cooling"))]
        new_vals = {p: total_before * C.SPLIT[p] / 100 for p in PIES}
    else:
        new_vals = dict(vals)

    # Apply trades, pie by pie, and charge costs to the pie that trades.
    pie_trades = {}
    for p in PIES:
        d = pies[p]
        if decision[p]["go"]:
            tgt = res[p]["target"]
            scale = new_vals[p] / vals[p] if vals[p] else 1
            # Trades as points of the pie's new value.
            cur_scaled = {k: v / scale for k, v in current[p].items()}
            tr = {k: round(tgt.get(k, 0) - cur_scaled.get(k, 0), 2) for k in set(tgt) | set(cur_scaled)}
            tr = {k: v for k, v in tr.items() if abs(v) >= 0.05}
            pie_trades[p] = tr
            new_vals[p] -= M.trade_cost(tr) / 100 * new_vals[p]
            d["units"] = _set_units(tgt, new_vals[p], prices)
            d["target"] = tgt
            d["level"] = res[p]["regime"]["level"]
            d["last_rebalance"] = data_date
            d["days"] = 0
            d["current"] = {k: float(v) for k, v in tgt.items()}
        else:
            pie_trades[p] = {}
            d["days"] += 1
            d["current"] = {k: round(v, 2) for k, v in current[p].items()}

    after = {p: _value(pies[p]["units"], prices) for p in PIES}
    total_after = sum(after.values())
    new_total = combined(pies["A"]["current"], pies["B"]["current"], 100 * after["A"] / total_after)
    any_trade = any(decision[p]["go"] for p in PIES)
    trades_total = {}
    if any_trade:
        keys = set(new_total) | set(current_total)
        trades_total = {k: round(new_total.get(k, 0) - current_total.get(k, 0), 2) for k in keys}
        trades_total = {k: v for k, v in trades_total.items() if abs(v) >= 0.05}

    if first:
        state["index"] = {"A": 100.0, "B": 100.0, "total": 100.0}
    bench = prices[C.CORE["id"]] / state["bench_start"] * 100
    state["nav"].append({"date": data_date, "A": round(state["index"]["A"], 4), "B": round(state["index"]["B"], 4),
                         "total": round(state["index"]["total"], 4), "bench": round(bench, 4),
                         "A_val": after["A"], "B_val": after["B"]})
    state["last_data_date"] = data_date
    state["signals"] = {"A": _signal_snapshot_a(res["A"]), "B": _signal_snapshot_b(res["B"])}
    state["meta"] = meta
    reasons = (["First allocation: build both pies from cash"] if first else
               split_why + [f"Pie {p}: " + w for p in PIES for w in decision[p]["why"] if not w.startswith("Pie A is")])
    entry = {"run_date": run_date, "data_date": data_date, "action": "REBALANCE" if any_trade else "HOLD",
             "pies": {p: {"action": "REBALANCE" if decision[p]["go"] else "HOLD", "reasons": decision[p]["why"],
                          "trades": pie_trades[p]} for p in PIES},
             "split": {"action": "REBALANCE" if split_go and not first else "HOLD", "share_a": round(share_a, 2)},
             "reasons": reasons, "trades": trades_total, "note": "", "regime_level": res["A"]["regime"]["level"]}
    _append_log(state, entry)
    state["last_checked"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["last_run_action"] = entry["action"]
    save(state)
    return entry


def _append_log(state, entry):
    state["log"] = [e for e in state["log"] if e["run_date"] != entry["run_date"]] + [entry]
    state["log"] = state["log"][-400:]


def _r(v):
    return round(v, 4) if isinstance(v, float) else v


def _signal_snapshot_a(res: dict) -> dict:
    def slim(rows, keys):
        return [{k: _r(r.get(k)) for k in keys} for r in rows]

    reg = res["regime"]
    gold = reg.get("gold") or {}
    return {
        "regime": {"level": reg["level"], "warnings": reg["warnings"], "sma_gap": reg["sma_gap"],
                   "ret_3m": reg["ret_3m"], "defensive_pct": reg["defensive_pct"],
                   "gold_trending": reg["gold_trending"], "gold_trend": gold.get("trend")},
        "factors": slim(res["factors"], ["id", "name", "style", "rank", "mom", "trend", "ret_12m"]),
        "regions": slim(res["regions"], ["id", "name", "region", "rank", "score", "mom", "trend", "ret_12m", "eligible"]),
        "stocks": slim(res["stocks"][:12], ["id", "name", "rank", "score", "mom", "vol", "trend", "ret_12m", "eligible"]),
    }


def _signal_snapshot_b(res: dict) -> list:
    keys = ["id", "ticker", "sector", "rank", "score", "mom12", "mom6", "high52", "vol", "trend", "ret_12m", "eligible", "held", "pinned"]
    return [{k: _r(r.get(k)) for k in keys} for r in res["ranked"][:40]]


def annotate(text: str) -> None:
    state = load_state()
    if not state["log"]:
        raise SystemExit("Nothing to annotate yet")
    if state.get("last_run_action") in ("NO_NEW_DATA", "NO_DATA"):
        print("Last run made no new decision; the existing note is kept.")
        return
    state["log"][-1]["note"] = text.strip()
    save(state)


def unavailable(why: str) -> None:
    """Record a failed download without hiding the latest real decision."""
    state = load_state()
    today = dt.date.today().isoformat()
    state["last_checked"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["last_run_action"] = "NO_DATA"
    state["last_error"] = why
    if not state["log"] or state["log"][-1]["run_date"] != today:
        _append_log(state, {"run_date": today, "data_date": state.get("last_data_date"),
                            "action": "NO_DATA", "reasons": [why], "pies": {}, "trades": {}, "note": ""})
    save(state)


def dashboard_payload(state: dict) -> dict:
    return {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "inception": state["inception"],
        "last_data_date": state["last_data_date"],
        "latest": state["log"][-1] if state["log"] else None,
        "pies": {p: {"target": state["pies"][p]["target"], "current": state["pies"][p]["current"],
                     "last_rebalance": state["pies"][p]["last_rebalance"]} for p in PIES},
        "nav": state["nav"][-5:],
        "signals": state.get("signals"),
        "log": state["log"][-10:][::-1],
        "names": NAMES,
    }


def _load_backtest():
    p = Path(__file__).parent / "state" / "backtest.json"
    return json.loads(p.read_text()) if p.exists() else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if cmd == "daily":
        print(json.dumps(daily(), indent=1, default=float))
    elif cmd == "annotate":
        annotate(" ".join(sys.argv[2:]))
    elif cmd == "unavailable":
        unavailable(" ".join(sys.argv[2:]) or "Market data could not be downloaded")
    else:
        raise SystemExit(__doc__)
