"""Write the dashboard's data files (JSON arrays of rows) to fund/state/dash/.

    python -m fund.export
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import config as C
from .model import largest_remainder
from .run import NAMES, SLEEVE, PIES, combined, label_of, load_state, _load_backtest

OUT = Path(__file__).parent / "state" / "dash"
PIE_NAME = {"A": "Pie A · Index +2%", "B": "Pie B · Stock selection", "Fund": "Whole fund"}


def _slices(target: dict) -> dict:
    """Trading 212 pies hold no cash slice: scale the invested part to 100%."""
    inv = {k: v for k, v in target.items() if k != "CASH" and v > 0}
    return largest_remainder(inv) if inv else {}


def _live_stats(nav: list, key: str) -> dict:
    if not nav:
        return {"ret": None, "vol": None, "maxdd": None}
    s = [n[key] for n in nav]
    peak, dd = s[0], 0.0
    for v in s:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    rets = [s[i] / s[i - 1] - 1 for i in range(1, len(s))]
    vol = None
    if len(rets) >= 20:
        m = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
    return {"ret": s[-1] / s[0] - 1, "vol": vol, "maxdd": dd}


def build() -> dict[str, list[dict]]:
    st = load_state()
    log, nav, pies = st["log"], st["nav"], st["pies"]
    latest = log[-1] if log else None
    status = "awaiting_data" if latest is None else ("no_data" if latest["action"] == "NO_DATA" else "ok")
    sig = st.get("signals") or {}
    reg = (sig.get("A") or {}).get("regime", {})

    stats = {k: _live_stats(nav, k) for k in ("A", "B", "total", "bench")}
    lv = nav[-1] if nav else None
    share_a = 100 * lv["A_val"] / (lv["A_val"] + lv["B_val"]) if lv else None
    lp = (latest or {}).get("pies", {})

    def ex(k):
        return stats[k]["ret"] - stats["bench"]["ret"] if nav else None

    summary = [{
        "status": status,
        "run_date": latest["run_date"] if latest else None,
        "data_date": st["last_data_date"],
        "action": latest["action"] if latest else "AWAITING_DATA",
        "a_action": lp.get("A", {}).get("action"), "b_action": lp.get("B", {}).get("action"),
        "split_action": (latest or {}).get("split", {}).get("action"),
        "note": (latest or {}).get("note") or "",
        "reasons": " · ".join((latest or {}).get("reasons", [])),
        "risk_level": pies["A"]["level"], "defensive_pct": reg.get("defensive_pct"),
        "total_return": stats["total"]["ret"], "a_return": stats["A"]["ret"], "b_return": stats["B"]["ret"],
        "bench_return": stats["bench"]["ret"],
        "total_excess": ex("total"), "a_excess": ex("A"), "b_excess": ex("B"),
        "share_a": share_a, "inception": st["inception"],
        "a_last_rebalance": pies["A"]["last_rebalance"], "b_last_rebalance": pies["B"]["last_rebalance"],
        "rebalances": sum(1 for e in log if e["action"] == "REBALANCE"),
        "b_cash": pies["B"]["target"].get("CASH", 0),
    }]

    traded = latest and latest["action"] == "REBALANCE"
    holdings = []
    for p in PIES:
        tgt, cur = pies[p]["target"], pies[p]["current"]
        sl = _slices(tgt)
        tr = lp.get(p, {}).get("trades", {}) if traded else {}
        for k in sorted(set(tgt) | set(tr), key=lambda k: -tgt.get(k, 0)):
            holdings.append({"key": f"{p}:{k}", "pie": p, "id": label_of(k[2:]) if k.startswith("B_") else k,
                             "name": NAMES.get(k, k), "group": SLEEVE.get(k, ""),
                             "target": tgt.get(k, 0), "slice": sl.get(k), "current": round(cur.get(k, 0), 2),
                             "trade": tr.get(k, 0)})
    if lv:
        tot = combined(pies["A"]["target"], pies["B"]["target"], C.SPLIT["A"])
        tot_cur = combined(pies["A"]["current"], pies["B"]["current"], share_a)
        tot_int = largest_remainder(tot)
        tot_sl = _slices(tot_int)
        tr = (latest or {}).get("trades", {}) if traded else {}
        rev = {}
        for p in PIES:
            for k in pies[p]["target"]:
                from .run import key_of
                rev[key_of(k)] = k
        for k in sorted(set(tot) | set(tr), key=lambda k: -tot.get(k, 0)):
            src = rev.get(k, k)
            holdings.append({"key": f"Fund:{k}", "pie": "Fund", "id": label_of(k), "name": NAMES.get(src, k),
                             "group": SLEEVE.get(src, ""), "target": tot_int.get(k, 0), "slice": tot_sl.get(k),
                             "current": round(tot_cur.get(k, 0), 2), "trade": tr.get(k, 0)})

    stat_rows = []
    for k, lab in (("total", "Whole fund"), ("A", "Pie A · Index +2%"), ("B", "Pie B · Stock selection"), ("bench", "FTSE All-World")):
        s = stats[k]
        stat_rows.append({"portfolio": lab, "return": s["ret"], "vs_index": (s["ret"] - stats["bench"]["ret"]) if nav and k != "bench" else None,
                          "volatility": s["vol"], "max_drawdown": s["maxdd"],
                          "holdings": (len(pies["A"]["target"]) + len(pies["B"]["target"])) if k == "total"
                          else len(pies[k]["target"]) if k in pies else None})

    signals = []
    sa = sig.get("A") or {}
    for group, key, rows in (("A · Style", "style", sa.get("factors", [])), ("A · Region", "region", sa.get("regions", [])),
                             ("A · Stock", None, sa.get("stocks", []))):
        for r in rows:
            signals.append({"key": f"{group}:{r['id']}", "group": group, "name": (r.get(key) if key else None) or r["name"],
                            "rank": r["rank"], "momentum": r.get("mom"), "trend": r.get("trend"), "return_12m": r.get("ret_12m"),
                            "status": "Held" if r["id"] in pies["A"]["target"] else ("Qualifies" if r.get("eligible", True) else "Fails trend filter")})
    for r in sig.get("B") or []:
        signals.append({"key": f"B:{r['id']}", "group": f"B · {r['sector']}", "name": r["ticker"], "rank": r["rank"],
                        "momentum": r.get("mom12"), "trend": r.get("trend"), "return_12m": r.get("ret_12m"),
                        "status": "Held" if r["id"] in pies["B"]["target"] else "Likely takeover target" if r.get("pinned")
                        else ("Qualifies" if r.get("eligible") else "Fails trend filter")})

    def trades_text(tr):
        return ", ".join(f"{label_of(k)} {v:+.1f}%" for k, v in sorted(tr.items(), key=lambda x: -abs(x[1])))

    log_rows = [{
        "run_date": e["run_date"], "action": e["action"],
        "a_action": e.get("pies", {}).get("A", {}).get("action", ""), "b_action": e.get("pies", {}).get("B", {}).get("action", ""),
        "trades": trades_text(e.get("trades", {})), "note": e.get("note", ""), "reasons": " · ".join(e.get("reasons", [])),
    } for e in log[::-1][:120]]

    nav_rows = [{"date": n["date"], "total": n["total"], "a": n["A"], "b": n["B"], "bench": n["bench"]} for n in nav]

    w = C.WEIGHTS
    mandate = [
        {"sleeve": "Pie A · Core", "weight": w["core"], "holds": "Vanguard FTSE All-World (VWRP)",
         "rule": "Always held. Spare slots from the other sleeves fall back here."},
        {"sleeve": "Pie A · Styles", "weight": sum(w["factors"]), "holds": "Momentum, Quality, Value, Size ETFs",
         "rule": "Top 3 styles by 6–12 month performance against the All-World index get 12%, 8% and 5%."},
        {"sleeve": "Pie A · Regions", "weight": sum(w["regions"]), "holds": "US, Europe, UK, Japan, Emerging, Asia Pacific ETFs",
         "rule": "Top 2 regions by risk-adjusted momentum, only while above their 200-day average."},
        {"sleeve": "Pie A · Stocks", "weight": sum(w["stocks"]), "holds": f"{len(C.STOCKS)} quality large caps",
         "rule": "Top 3 by risk-adjusted momentum and in an uptrend, 5% each. Kept until they fall out of the top 8."},
        {"sleeve": "Pie A · Defensive", "weight": C.DEFENSIVE_LEVELS[2], "holds": "Physical gold (SGLN) and cash",
         "rule": "15% out of equities when both warning signs are on: All-World below its 200-day average and down over 3 months."},
        {"sleeve": "Pie B · Stocks", "weight": 100, "holds": f"{len(C.B_STOCKS)} global large, mid and small caps",
         "rule": f"Top {C.B['n']} by a blend of 12-month and 6-month risk-adjusted momentum, closeness to the 52-week high and low volatility. "
                 f"New picks must be above their 200-day average. Sized by inverse volatility, {C.B['min_w']}–{C.B['max_w']}% each, "
                 f"at most {C.B['sector_cap']}% per sector. A holding stays while it ranks in the top {C.B['keep_rank']}."},
        {"sleeve": "Pie B · Cash", "weight": 100, "holds": "Cash outside the pie",
         "rule": "When fewer than 12 stocks qualify, the empty slots stay in cash. In a broad sell-off few stocks hold their uptrend, so pie B de-risks by itself."},
    ]

    bt = _load_backtest()
    bt_metrics, bt_curve = [], []
    if bt:
        labels = {"total_fair": "Whole fund, fair test", "total": "Whole fund, as built", "pit": "Pie A, fair test",
                  "b_pit": "Pie B on 2015 large caps", "b": "Pie B, as built", "etf_only": "Pie A, ETFs only"}
        for v, lab in labels.items():
            if v not in bt:
                continue
            m = bt[v]
            for key, name in (("fund_cagr", "Return a year"), ("excess_cagr", "Vs All-World a year"),
                              ("fund_vol", "Volatility"), ("fund_maxdd", "Worst fall"),
                              ("tracking_error", "Tracking error"), ("turnover_per_year", "Turnover a year")):
                bt_metrics.append({"key": f"{v}:{key}", "variant": lab, "metric": name, "value": m[key],
                                   "start": m["start"], "end": m["end"]})
            bt_metrics.append({"key": f"{v}:bench", "variant": lab, "metric": "All-World return a year",
                               "value": m["bench_cagr"], "start": m["start"], "end": m["end"]})
        series = {v: {p["date"]: p["fund"] for p in bt[v]["curve"]} for v in ("total", "total_fair", "b", "pit") if v in bt}
        base = bt["total_fair"]["curve"] if "total_fair" in bt else bt["pit"]["curve"]
        bt_curve = [{"date": p["date"], "total_fair": series.get("total_fair", {}).get(p["date"]),
                     "total": series.get("total", {}).get(p["date"]), "b": series.get("b", {}).get(p["date"]),
                     "a_fair": series.get("pit", {}).get(p["date"]), "bench": p["bench"]} for p in base]

    return {"summary": summary, "holdings": holdings, "stats": stat_rows, "signals": signals, "log": log_rows,
            "nav": nav_rows, "mandate": mandate, "bt_metrics": bt_metrics, "bt_curve": bt_curve}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in build().items():
        (OUT / f"{name}.json").write_text(json.dumps(rows, default=float))
        print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
