"""Daily fund run.

    python -m fund.run daily            # fetch prices, update the model, write fund/state/data.json
    python -m fund.run annotate "text"  # attach the plain-English note to today's entry
    python -m fund.run unavailable "why"  # record a day with no data (dashboard shows it)

State lives in fund/state/state.json and is committed after each run.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from . import config as C
from . import model as M

STATE = Path(__file__).parent / "state" / "state.json"
DATA = Path(__file__).parent / "state" / "data.json"
NAMES = {it["id"]: it["name"] for it in [C.CORE] + C.FACTORS + C.REGIONS + C.STOCKS
         + [C.DEFENSIVE["gold"], C.DEFENSIVE["cash"]]}
SLEEVE = {C.CORE["id"]: "Core", C.DEFENSIVE["gold"]["id"]: "Defensive", "CASH": "Defensive",
          **{f["id"]: "Style" for f in C.FACTORS}, **{r["id"]: "Region" for r in C.REGIONS},
          **{s["id"]: "Stock" for s in C.STOCKS}}
TICKER_T212 = {C.CORE["id"]: "VWRP", "CASH": "—"}


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"inception": None, "units": {}, "target": {}, "level": None, "last_rebalance": None,
            "last_data_date": None, "nav": [], "log": []}


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=1, default=float))
    DATA.write_text(json.dumps(dashboard_payload(state), indent=1, default=float))


def _round(d, n=4):
    return {k: round(v, n) for k, v in d.items()}


def daily(today: str | None = None) -> dict:
    from .data import download

    state = load_state()
    px, meta = download()
    px = px.dropna(subset=[C.CORE["id"]])
    data_date = str(px.index[-1].date())
    run_date = today or dt.date.today().isoformat()

    if state["last_data_date"] == data_date:
        entry = {"run_date": run_date, "data_date": data_date, "action": "NO_NEW_DATA",
                 "reasons": ["No new closing prices since the last run (market holiday or weekend)"],
                 "trades": {}, "note": ""}
        _append_log(state, entry)
        save(state)
        return entry

    last = px.iloc[-1]
    prices = {k: float(last[k]) for k in px.columns if last.notna()[k]}
    current, nav = M.drifted_weights(state["units"], prices) if state["units"] else ({}, 0.0)

    res = M.allocate(px, prev=state["target"], prev_level=state["level"])
    target = res["target"]
    level = res["regime"]["level"]
    days_since = None
    if state["last_rebalance"]:
        days_since = sum(1 for n in state["nav"] if n["date"] > state["last_rebalance"]) + 1
    rebalance, reasons = M.decide(target, current, state["target"], state["level"], level, days_since)

    if not state["units"]:
        nav = 100.0
        state["inception"] = data_date
        state["bench_start"] = prices[C.CORE["id"]]

    trades = {}
    if rebalance:
        keys = set(target) | set(current)
        trades = {k: round(target.get(k, 0) - current.get(k, 0), 2) for k in keys}
        trades = {k: v for k, v in trades.items() if abs(v) >= 0.05}
        cost = M.trade_cost(trades) / 100 * nav
        nav -= cost
        state["units"] = {k: (wt / 100 * nav) / (1.0 if k == "CASH" else prices[k]) for k, wt in target.items()}
        state["target"] = target
        state["level"] = level
        state["last_rebalance"] = data_date
        final = {k: float(v) for k, v in target.items()}
    else:
        if days_since is not None and days_since < C.MIN_DAYS_BETWEEN:
            reasons = [f"Cooling-off period: {days_since} of {C.MIN_DAYS_BETWEEN} trading days since the last rebalance"]
        else:
            reasons = ["Signals and weights are within tolerance; no trades needed"]
        reasons += res["notes"]
        pending = {k: v for k, v in target.items() if state["target"].get(k) != v}
        if pending:
            reasons.append("Model would change: " + ", ".join(
                f"{k} {state['target'].get(k, 0)}→{v}%" for k, v in pending.items())
                + "; not enough to act on yet")
        final = current

    bench = prices[C.CORE["id"]] / state["bench_start"] * 100
    state["nav"].append({"date": data_date, "fund": round(nav, 4), "bench": round(bench, 4)})
    state["last_data_date"] = data_date
    state["current"] = _round(final, 2)
    state["signals"] = _signal_snapshot(res)
    state["meta"] = meta
    entry = {"run_date": run_date, "data_date": data_date, "action": "REBALANCE" if rebalance else "HOLD",
             "reasons": reasons, "trades": trades, "note": "", "regime_level": level}
    _append_log(state, entry)
    save(state)
    return entry


def _append_log(state, entry):
    state["log"] = [e for e in state["log"] if e["run_date"] != entry["run_date"]] + [entry]
    state["log"] = state["log"][-400:]


def _signal_snapshot(res: dict) -> dict:
    def slim(rows, keys):
        return [{k: (round(r[k], 4) if isinstance(r.get(k), float) else r.get(k)) for k in keys} for r in rows]

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


def annotate(text: str) -> None:
    state = load_state()
    if not state["log"]:
        raise SystemExit("Nothing to annotate yet")
    state["log"][-1]["note"] = text.strip()
    save(state)


def unavailable(why: str) -> None:
    state = load_state()
    _append_log(state, {"run_date": dt.date.today().isoformat(), "data_date": state.get("last_data_date"),
                        "action": "NO_DATA", "reasons": [why], "trades": {}, "note": ""})
    save(state)


def dashboard_payload(state: dict) -> dict:
    holdings = [{"id": k, "name": NAMES.get(k, k), "sleeve": SLEEVE.get(k, ""),
                 "target": state["target"].get(k, 0), "current": state.get("current", {}).get(k, 0)}
                for k in sorted(set(state["target"]) | set(state.get("current", {})),
                                key=lambda k: -state["target"].get(k, 0))]
    nav = state["nav"]
    perf = None
    if nav:
        perf = {"fund": nav[-1]["fund"] / 100 - 1, "bench": nav[-1]["bench"] / 100 - 1,
                "since": state["inception"]}
    rebalances = [e for e in state["log"] if e["action"] == "REBALANCE"]
    return {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "inception": state["inception"],
        "last_data_date": state["last_data_date"],
        "last_rebalance": state["last_rebalance"],
        "latest": state["log"][-1] if state["log"] else None,
        "holdings": holdings,
        "signals": state.get("signals"),
        "perf": perf,
        "nav": nav[-260:],
        "log": state["log"][-90:][::-1],
        "rebalance_count": len(rebalances),
        "names": NAMES,
        "backtest": _load_backtest(),
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
