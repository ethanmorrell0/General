"""Write the dashboard's data files (JSON arrays of rows) to fund/state/dash/.

    python -m fund.export
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config as C
from .run import NAMES, SLEEVE, load_state, _load_backtest

OUT = Path(__file__).parent / "state" / "dash"


def build() -> dict[str, list[dict]]:
    st = load_state()
    log = st["log"]
    latest = log[-1] if log else None
    nav = st["nav"]
    held = set(st["target"])

    if latest is None:
        status = "awaiting_data"
    elif latest["action"] == "NO_DATA":
        status = "no_data"
    else:
        status = "ok"

    fund_ret = nav[-1]["fund"] / 100 - 1 if nav else None
    bench_ret = nav[-1]["bench"] / 100 - 1 if nav else None
    summary = [{
        "status": status,
        "run_date": latest["run_date"] if latest else None,
        "data_date": st["last_data_date"],
        "action": latest["action"] if latest else "AWAITING_DATA",
        "note": (latest or {}).get("note") or "",
        "reasons": " · ".join((latest or {}).get("reasons", [])),
        "risk_level": st.get("level"),
        "defensive_pct": (st.get("signals") or {}).get("regime", {}).get("defensive_pct"),
        "fund_return": fund_ret,
        "bench_return": bench_ret,
        "excess_return": (fund_ret - bench_ret) if nav else None,
        "inception": st["inception"],
        "last_rebalance": st["last_rebalance"],
        "rebalances": sum(1 for e in log if e["action"] == "REBALANCE"),
        "holdings_count": len(st["target"]),
    }]

    trades_today = (latest or {}).get("trades", {}) if latest and latest["action"] == "REBALANCE" else {}
    cur = st.get("current", {})
    holdings = [{
        "id": k, "name": NAMES.get(k, k), "sleeve": SLEEVE.get(k, ""),
        "target": st["target"].get(k, 0), "current": round(cur.get(k, 0), 2),
        "trade": trades_today.get(k, 0),
    } for k in sorted(set(st["target"]) | set(trades_today), key=lambda k: -st["target"].get(k, 0))]

    signals = []
    sig = st.get("signals") or {}
    for group, key, rows in (("Style", "style", sig.get("factors", [])),
                             ("Region", "region", sig.get("regions", [])),
                             ("Stock", None, sig.get("stocks", []))):
        for r in rows:
            signals.append({
                "key": f"{group}:{r['id']}", "group": group, "id": r["id"],
                "name": r.get(key) or r["name"] if key else r["name"],
                "rank": r["rank"], "momentum": r.get("mom"), "trend": r.get("trend"),
                "return_12m": r.get("ret_12m"), "eligible": r.get("eligible", True),
                "held": r["id"] in held,
            })

    log_rows = [{
        "run_date": e["run_date"], "data_date": e.get("data_date"), "action": e["action"],
        "reasons": " · ".join(e.get("reasons", [])), "note": e.get("note", ""),
        "trades": ", ".join(f"{k} {v:+.1f}%" for k, v in sorted(e.get("trades", {}).items(), key=lambda x: -abs(x[1]))),
    } for e in log[::-1][:120]]

    nav_rows = [{"date": n["date"], "fund": n["fund"], "bench": n["bench"]} for n in nav]

    w = C.WEIGHTS
    mandate = [
        {"sleeve": "Core", "weight": w["core"], "holds": "Vanguard FTSE All-World (VWRP)",
         "rule": "Always held. Spare slots from the other sleeves fall back here."},
        {"sleeve": "Styles", "weight": sum(w["factors"]), "holds": "Momentum, Quality, Value, Small-cap ETFs",
         "rule": "Top 3 styles by 6–12 month performance against the All-World index get 12%, 8% and 5%."},
        {"sleeve": "Regions", "weight": sum(w["regions"]), "holds": "US, Europe, UK, Japan, Emerging, Asia Pacific ETFs",
         "rule": "Top 2 regions by risk-adjusted momentum, only while above their 200-day average."},
        {"sleeve": "Stocks", "weight": sum(w["stocks"]), "holds": f"{len(C.STOCKS)} quality large caps",
         "rule": "Top 3 by risk-adjusted momentum and in an uptrend, 5% each. Kept until they fall out of the top 8."},
        {"sleeve": "Defensive", "weight": C.DEFENSIVE_LEVELS[2], "holds": "Physical gold (SGLN) and cash",
         "rule": "0%, 7% or 15% depending on 0, 1 or 2 warning signs on the All-World index."},
    ]

    bt = _load_backtest()
    bt_metrics, bt_curve = [], []
    if bt:
        labels = {"full": "With stocks", "etf_only": "ETFs only"}
        for v, lab in labels.items():
            m = bt[v]
            for key, name in (("fund_cagr", "Fund return a year"), ("bench_cagr", "All-World return a year"),
                              ("excess_cagr", "Excess return a year"), ("tracking_error", "Tracking error"),
                              ("fund_maxdd", "Fund worst fall"), ("bench_maxdd", "All-World worst fall"),
                              ("rebalances_per_year", "Rebalances a year"), ("turnover_per_year", "Turnover a year")):
                bt_metrics.append({"key": f"{v}:{key}", "variant": lab, "metric": name, "value": m[key],
                                   "start": m["start"], "end": m["end"]})
        etf = {p["date"]: p["fund"] for p in bt["etf_only"]["curve"]}
        bt_curve = [{"date": p["date"], "fund": p["fund"], "fund_etf": etf.get(p["date"]), "bench": p["bench"]}
                    for p in bt["full"]["curve"]]

    return {"summary": summary, "holdings": holdings, "signals": signals, "log": log_rows,
            "nav": nav_rows, "mandate": mandate, "bt_metrics": bt_metrics, "bt_curve": bt_curve}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in build().items():
        (OUT / f"{name}.json").write_text(json.dumps(rows, default=float))
        print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
