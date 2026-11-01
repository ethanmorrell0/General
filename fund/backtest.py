"""Walk-forward backtest of the exact daily rules, with trading costs.

    python -m fund.backtest            # writes fund/state/backtest.json

Caveat: the stock list is today's list of quality large caps, so the stock
sleeve's history benefits from hindsight (survivorship bias). The ETF-only
variant has no such bias and is the fairer test of the method.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import model as M

OUT = Path(__file__).parent / "state" / "backtest.json"
WINDOW = M.MIN_HISTORY + 30


def simulate(px: pd.DataFrame, start: str = "2016-01-01") -> dict:
    idx = px.index
    i0 = max(int(np.searchsorted(idx, pd.Timestamp(start))), WINDOW)
    units, target, level = {}, {}, None
    nav, bench0 = 100.0, None
    rows, rebal_dates, turnover = [], [], 0.0
    last_i = None
    for i in range(i0, len(idx)):
        days_since = None if last_i is None else i - last_i
        win = px.iloc[i - WINDOW + 1:i + 1]
        last = win.iloc[-1]
        prices = {k: float(last[k]) for k in win.columns if not math.isnan(last[k])}
        if C.CORE["id"] not in prices:
            continue
        if bench0 is None:
            bench0 = prices[C.CORE["id"]]
        if units:
            # A holding with a missing price today keeps yesterday's value.
            for k in units:
                if k != "CASH" and k not in prices:
                    prices[k] = float(px[k].iloc[:i + 1].dropna().iloc[-1])
            current, nav = M.drifted_weights(units, prices)
        else:
            current = {}
        res = M.allocate(win, prev=target, prev_level=level)
        lvl = res["regime"]["level"]
        go, _ = M.decide(res["target"], current, target, level, lvl, days_since)
        if go:
            tgt = res["target"]
            trades = {k: tgt.get(k, 0) - current.get(k, 0) for k in set(tgt) | set(current)}
            if current:
                turnover += sum(abs(v) for v in trades.values()) / 2 / 100
            nav -= M.trade_cost(trades) / 100 * nav
            units = {k: (w / 100 * nav) / (1.0 if k == "CASH" else prices[k]) for k, w in tgt.items()}
            target, level = tgt, lvl
            rebal_dates.append(str(idx[i].date()))
            last_i = i
        rows.append((idx[i], nav, prices[C.CORE["id"]] / bench0 * 100))
    df = pd.DataFrame(rows, columns=["date", "fund", "bench"]).set_index("date")
    return {"curve": df, "rebalances": rebal_dates, "turnover": turnover}


def metrics(sim: dict) -> dict:
    df = sim["curve"]
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    rf, rb = df["fund"].pct_change().dropna(), df["bench"].pct_change().dropna()

    def cagr(s):
        return (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1

    def mdd(s):
        return float((s / s.cummax() - 1).min())

    yearly = df.resample("YE").last()
    first = df.iloc[[0]]
    yearly = pd.concat([first, yearly])
    yr = (yearly / yearly.shift(1) - 1).dropna()
    te = float((rf - rb).std() * math.sqrt(252))
    excess = cagr(df["fund"]) - cagr(df["bench"])
    return {
        "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "years": round(yrs, 1),
        "fund_cagr": cagr(df["fund"]), "bench_cagr": cagr(df["bench"]), "excess_cagr": excess,
        "fund_vol": float(rf.std() * math.sqrt(252)), "bench_vol": float(rb.std() * math.sqrt(252)),
        "tracking_error": te, "info_ratio": excess / te if te else None,
        "fund_maxdd": mdd(df["fund"]), "bench_maxdd": mdd(df["bench"]),
        "rebalances_per_year": len(sim["rebalances"]) / yrs,
        "turnover_per_year": sim["turnover"] / yrs,
        "calendar": [{"year": int(d.year), "fund": float(r["fund"]), "bench": float(r["bench"])}
                     for d, r in yr.iterrows()],
        "curve": [{"date": str(d.date()), "fund": round(float(r["fund"]), 2), "bench": round(float(r["bench"]), 2)}
                  for d, r in df.iloc[::5].iterrows()],
    }


def run(px: pd.DataFrame, start: str = "2016-01-01") -> dict:
    full = metrics(simulate(px, start))
    saved = C.STOCKS
    try:
        C.STOCKS = []
        etf_only = metrics(simulate(px, start))
    finally:
        C.STOCKS = saved
    return {"full": full, "etf_only": etf_only}


if __name__ == "__main__":
    from .data import download

    px, meta = download("2014-01-01")
    out = run(px)
    out["meta"] = meta
    OUT.write_text(json.dumps(out, indent=1, default=float))
    for k in ("full", "etf_only"):
        m = out[k]
        print(f"{k:9s} {m['start']}→{m['end']}  fund {m['fund_cagr']:.2%}  bench {m['bench_cagr']:.2%}  "
              f"excess {m['excess_cagr']:+.2%}  TE {m['tracking_error']:.2%}  maxDD {m['fund_maxdd']:.1%} "
              f"vs {m['bench_maxdd']:.1%}  rebal/yr {m['rebalances_per_year']:.1f}  turnover/yr {m['turnover_per_year']:.0%}")
