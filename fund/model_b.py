"""Pie B: stock selection for maximum long-term return, with risk caps.

Signals (price-only, so the backtest uses exactly what the live model sees):
  - 12-1 month momentum and 6 month momentum (the best-documented stock anomaly)
  - closeness to the 52-week high (stocks near highs keep outperforming)
  - volatility (a penalty: the riskiest names need more evidence)
Each is ranked across the universe and blended into one score. New picks
must be above their 200-day average. Weights are inverse-volatility, capped
per stock and per sector. When fewer than `n` stocks qualify, the unfilled
share is held as cash: in a broad bear market, few stocks stay in an
uptrend, so pie B de-risks without any market timing call.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config as C
from .model import L, largest_remainder

MIN_HISTORY = L["mom_long"] + 25


def _stats(a: np.ndarray) -> dict | None:
    if len(a) < MIN_HISTORY or np.isnan(a[-MIN_HISTORY:]).any():
        return None
    moms12, moms6 = [], []
    for lag in C.SMOOTH_LAGS:
        end = len(a) - 1 - lag
        moms12.append(a[end - L["skip"]] / a[end - L["mom_long"]] - 1)
        moms6.append(a[end] / a[end - L["mom_short"]] - 1)
    r = np.diff(np.log(a[-L["vol"] - 1:]))
    vol = float(np.std(r) * math.sqrt(252))
    # Takeover check: an agreed cash bid pins the price, so recent volatility
    # collapses versus the stock's own history. Needs ~2 years of data.
    pinned = False
    if len(a) >= 500 and not np.isnan(a[-500:]).any():
        r40 = np.diff(np.log(a[-41:]))
        r_long = np.diff(np.log(a[-500:-63]))
        long_vol = float(np.std(r_long) * math.sqrt(252))
        pinned = long_vol > 0 and float(np.std(r40) * math.sqrt(252)) < C.B["pinned_ratio"] * long_vol
    sma = float(np.mean(a[-L["sma"]:]))
    hi = float(np.max(a[-252:]))
    return {"mom12": float(np.mean(moms12)), "mom6": float(np.mean(moms6)), "vol": max(vol, 0.10),
            "trend": float(a[-1] / sma - 1), "high52": float(a[-1] / hi - 1),
            "ret_12m": float(a[-1] / a[-253] - 1), "pinned": pinned}


def _z(x: np.ndarray) -> np.ndarray:
    """Rank-based z-score: robust to the huge outliers small caps produce."""
    ranks = pd.Series(x).rank(pct=True).to_numpy()
    return (ranks - 0.5) * math.sqrt(12)


def allocate_b(px: pd.DataFrame, prev: dict[str, float] | None = None, universe=None) -> dict:
    prev = prev or {}
    P = C.B
    universe = universe if universe is not None else C.B_STOCKS
    rows = []
    for s in universe:
        if s["id"] in px:
            st = _stats(px[s["id"]].to_numpy(dtype=float))
            if st:
                rows.append({**s, **st})
    if not rows:
        return {"target": {"CASH": 100}, "ranked": [], "notes": ["No stock has enough price history"]}

    w = P["score"]
    m12 = _z(np.array([r["mom12"] / r["vol"] for r in rows]))
    m6 = _z(np.array([r["mom6"] / r["vol"] for r in rows]))
    hi = _z(np.array([r["high52"] for r in rows]))
    vo = _z(np.array([r["vol"] for r in rows]))
    for i, r in enumerate(rows):
        r["score"] = float(w["mom12"] * m12[i] + w["mom6"] * m6[i] + w["high52"] * hi[i] - w["vol"] * vo[i])
    rows.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        held = prev.get(r["id"], 0) > 0
        r["held"] = held
        r["eligible"] = (r["trend"] > -P["slack_trend"] and r["rank"] <= P["keep_rank"]) if held else (r["trend"] > 0 and r["mom12"] > 0)
        if r["pinned"]:
            r["eligible"] = False  # likely an agreed takeover: little upside left

    # Keep holdings that still qualify, then fill with the best new names,
    # respecting the sector cap (counted in slots of an equal-weight pie).
    max_per_sector = max(1, int(P["sector_cap"] / (100 / P["n"])))
    picks, per_sector = [], {}
    for pool in ([r for r in rows if r["held"] and r["eligible"]], [r for r in rows if not r["held"] and r["eligible"]]):
        for r in pool:
            if len(picks) >= P["n"]:
                break
            if per_sector.get(r["sector"], 0) >= max_per_sector:
                continue
            picks.append(r)
            per_sector[r["sector"]] = per_sector.get(r["sector"], 0) + 1

    notes = []
    pinned = [r.get("ticker", r["id"]) for r in rows if r["pinned"] and r["rank"] <= P["n"] * 2]
    if pinned:
        notes.append("Skipped as likely takeover targets (price pinned, volatility collapsed): " + ", ".join(pinned))
    invested = 100.0 * len(picks) / P["n"]
    if len(picks) < P["n"]:
        notes.append(f"Only {len(picks)} of {P['n']} stock slots have a qualifying stock; the rest is held as cash")
    target: dict[str, float] = {}
    if picks:
        inv = np.array([1 / r["vol"] for r in picks])
        raw = inv / inv.sum() * invested
        # Cap and floor, redistributing the excess, a few passes.
        for _ in range(10):
            raw = np.clip(raw, P["min_w"], P["max_w"])
            raw = raw * invested / raw.sum()
            if raw.max() <= P["max_w"] + 1e-6 and raw.min() >= P["min_w"] - 1e-6:
                break
        target = {r["id"]: float(x) for r, x in zip(picks, raw)}
    if invested < 100:
        target["CASH"] = 100 - invested
    return {"target": largest_remainder(target), "ranked": rows, "notes": notes,
            "regime": {"level": None}}
