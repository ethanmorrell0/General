"""Walk-forward backtest of the exact daily rules, with trading costs.

    python -m fund.backtest            # writes fund/state/backtest.json

Pie A: today's stock list (hindsight: survivorship bias), the largest
companies at end-2015 (the fair test of its stock sleeve), and ETFs only.
Pie B: on its own universe (chosen today, so optimistic) and on the 2015
large-cap list. Total: pie A + pie B at 50/50.
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
WINDOW_B = 520  # pie B's takeover check needs ~2 years of prices


def simulate(px: pd.DataFrame, start: str = "2016-01-01", alloc=None, trigger=None, min_days=None, window=WINDOW) -> dict:
    """alloc(window, prev_target, prev_level) -> result dict; defaults to pie A."""
    alloc = alloc or (lambda w, prev, lvl: M.allocate(w, prev=prev, prev_level=lvl))
    idx = px.index
    i0 = max(int(np.searchsorted(idx, pd.Timestamp(start))), window)
    units, target, level = {}, {}, None
    nav, bench0 = 100.0, None
    rows, rebal_dates, turnover = [], [], 0.0
    last_i = None
    for i in range(i0, len(idx)):
        days_since = None if last_i is None else i - last_i
        win = px.iloc[i - window + 1:i + 1]
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
        res = alloc(win, target, level)
        lvl = res["regime"]["level"]
        go, _ = M.decide(res["target"], current, target, level, lvl, days_since, trigger, min_days)
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


def combine(a: pd.DataFrame, b: pd.DataFrame, split=None, drift=None) -> dict:
    """Two pies at 50/50, moved back to 50/50 when one drifts past the band."""
    split = split or C.SPLIT
    drift = drift if drift is not None else C.SPLIT_DRIFT_PP
    df = pd.concat([a["fund"].rename("a"), b["fund"].rename("b"), a["bench"]], axis=1).dropna()
    ra, rb = df["a"].pct_change().fillna(0), df["b"].pct_change().fillna(0)
    va, vb = split["A"], split["B"]
    out, moves = [], 0
    for d in df.index:
        va *= 1 + ra[d]
        vb *= 1 + rb[d]
        tot = va + vb
        if abs(100 * va / tot - split["A"]) >= drift:
            moved = abs(va - tot * split["A"] / 100)
            tot -= moved * 2 * 0.002          # sell in one pie, buy in the other
            va, vb = tot * split["A"] / 100, tot * split["B"] / 100
            moves += 1
        out.append((d, va + vb, df.loc[d, "bench"]))
    curve = pd.DataFrame(out, columns=["date", "fund", "bench"]).set_index("date")
    return {"curve": curve, "rebalances": [None] * moves, "turnover": 0.0}


def run(px: pd.DataFrame, start: str = "2016-01-01") -> dict:
    from . import model_b as MB

    saved = C.STOCKS
    b_cols = [s["id"] for s in C.B_STOCKS]
    pit_cols = [c for c in px if c.startswith("PIT_")]
    a_px = px.drop(columns=[c for c in b_cols + pit_cols if c in px])
    try:
        C.STOCKS = [x for x in saved if x["id"] in px]
        sim_a = simulate(a_px, start)
        full = metrics(sim_a)
        C.STOCKS = [x for x in C.BACKTEST_STOCKS_2015 if x["id"] in px]
        sim_pit_a = simulate(px.drop(columns=[x["id"] for x in saved if x["id"] in px] + [c for c in b_cols if c in px]), start)
        pit = metrics(sim_pit_a)
        C.STOCKS = []
        etf_only = metrics(simulate(a_px, start))
    finally:
        C.STOCKS = saved
    core = [C.CORE["id"]]
    bt = dict(trigger=C.B["trigger"], min_days=C.B["min_days"], window=WINDOW_B)
    sim_b = simulate(px[core + [c for c in b_cols if c in px]], start, alloc=lambda w, prev, lvl: MB.allocate_b(w, prev), **bt)
    b = metrics(sim_b)
    pit_u = [x for x in C.BACKTEST_STOCKS_2015 if x["id"] in px]
    sim_b_pit = simulate(px[core + [x["id"] for x in pit_u]], start,
                         alloc=lambda w, prev, lvl: MB.allocate_b(w, prev, universe=[{**x, "sector": x["id"]} for x in pit_u]), **bt)
    b_pit = metrics(sim_b_pit)
    total = metrics(combine(sim_a["curve"], sim_b["curve"]))
    total_fair = metrics(combine(sim_pit_a["curve"], sim_b_pit["curve"]))
    return {"full": full, "pit": pit, "etf_only": etf_only, "b": b, "b_pit": b_pit, "total": total, "total_fair": total_fair}


if __name__ == "__main__":
    from .data import download

    px, meta = download("2013-06-01", extra=C.BACKTEST_STOCKS_2015 + C.B_STOCKS)
    out = run(px)
    out["meta"] = meta
    OUT.write_text(json.dumps(out, indent=1, default=float))
    for k in ("full", "pit", "etf_only", "b", "b_pit", "total", "total_fair"):
        m = out[k]
        print(f"{k:9s} {m['start']}→{m['end']}  fund {m['fund_cagr']:.2%}  bench {m['bench_cagr']:.2%}  "
              f"excess {m['excess_cagr']:+.2%}  TE {m['tracking_error']:.2%}  maxDD {m['fund_maxdd']:.1%} "
              f"vs {m['bench_maxdd']:.1%}  rebal/yr {m['rebalances_per_year']:.1f}  turnover/yr {m['turnover_per_year']:.0%}")
