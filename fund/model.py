"""Signals, target allocation and rebalance triggers.

All functions take a price table (rows = dates up to and including the decision
day, columns = instrument ids, GBP total-return prices) and never look ahead.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config as C

L = C.LOOKBACK
MIN_HISTORY = L["mom_long"] + max(C.SMOOTH_LAGS) + 5


def _mom(a: np.ndarray, lag: int):
    """Blend of 12-1 month and 6 month return, ending `lag` days ago."""
    n = len(a)
    end = n - 1 - lag
    if end - L["mom_long"] < 0 or np.isnan(a[end - L["mom_long"]]) or np.isnan(a[end]):
        return None
    r12_1 = a[end - L["skip"]] / a[end - L["mom_long"]] - 1
    r6 = a[end] / a[end - L["mom_short"]] - 1
    return 0.5 * r12_1 + 0.5 * r6


def _vol(a: np.ndarray):
    r = np.diff(np.log(a[-L["vol"] - 1:]))
    r = r[~np.isnan(r)]
    return float(np.std(r) * math.sqrt(252)) if len(r) > 20 else None


def _sma_gap(a: np.ndarray):
    w = a[-L["sma"]:]
    if len(w) < L["sma"] or np.isnan(w).any():
        return None
    return float(a[-1] / w.mean() - 1)


def stats(px: pd.DataFrame, col: str, rel_to: str | None = None) -> dict | None:
    a = px[col].to_numpy(dtype=float)
    if rel_to is not None:
        a = a / px[rel_to].to_numpy(dtype=float)
    if len(a) < MIN_HISTORY or np.isnan(a[-MIN_HISTORY:]).any():
        return None
    moms = [_mom(a, lag) for lag in C.SMOOTH_LAGS]
    if any(m is None for m in moms):
        return None
    mom = float(np.mean(moms))
    raw = px[col].to_numpy(dtype=float)
    vol = _vol(raw)
    trend = _sma_gap(raw)
    if vol is None or trend is None:
        return None
    return {
        "mom": mom,
        "vol": vol,
        "trend": trend,
        "score": mom / max(vol, 0.08) if rel_to is None else mom,
        "ret_1m": float(raw[-1] / raw[-22] - 1),
        "ret_12m": float(raw[-1] / raw[-253] - 1),
    }


def largest_remainder(weights: dict[str, float], total: int = 100) -> dict[str, int]:
    weights = {k: v for k, v in weights.items() if v > 1e-9}
    s = sum(weights.values())
    scaled = {k: v * total / s for k, v in weights.items()}
    floor = {k: int(math.floor(v)) for k, v in scaled.items()}
    short = total - sum(floor.values())
    for k in sorted(scaled, key=lambda k: scaled[k] - floor[k], reverse=True)[:short]:
        floor[k] += 1
    return {k: v for k, v in floor.items() if v > 0}


def regime(px: pd.DataFrame, prev_level: int | None = None) -> dict:
    """Count warning signs on the All-World index. A buffer band stops the
    level flipping back and forth when the index sits right on a threshold:
    a warning needs a clear break to switch on, and a clear recovery to switch off."""
    a = px[C.CORE["id"]].to_numpy(dtype=float)
    # Use the 10-day average price so one bad day cannot flip the level.
    a10 = pd.Series(a).rolling(10).mean().to_numpy()
    gap = float(a10[-1] / np.nanmean(a[-L["sma"]:]) - 1)
    ret = float(a10[-1] / a10[-1 - L["regime_ret"]] - 1)
    b = C.REGIME_BAND
    on = bool(prev_level)
    warnings = []
    if gap is not None and gap < (b["sma"] if on else -b["sma"]):
        warnings.append(f"All-World is {abs(gap):.1%} {'below' if gap < 0 else 'above'} its 200-day average"
                        + ("" if gap < 0 else " (not yet a clear recovery)"))
    if ret < (b["ret"] if on else -b["ret"]):
        warnings.append(f"All-World is {'down' if ret < 0 else 'up only'} {abs(ret):.1%} over 3 months")
    return {"level": len(warnings), "warnings": warnings, "sma_gap": gap, "ret_3m": ret,
            "defensive_pct": C.DEFENSIVE_LEVELS[len(warnings)]}


def allocate(px: pd.DataFrame, prev: dict[str, float] | None = None, prev_level: int | None = None) -> dict:
    """Return target weights (whole percent) plus the signals behind them."""
    prev = prev or {}
    core = C.CORE["id"]
    w: dict[str, float] = {core: C.WEIGHTS["core"]}
    notes: list[str] = []

    # 1. Styles: rank by performance relative to the All-World index.
    fac = []
    for f in C.FACTORS:
        if f["id"] in px and core in px:
            s = stats(px, f["id"], rel_to=core)
            if s:
                # Incumbents keep their slot unless clearly beaten.
                adj = s["score"] + C.FACTOR_STICKINESS * prev.get(f["id"], 0) / C.WEIGHTS["factors"][0]
                fac.append({**f, **s, "adj": adj})
    fac.sort(key=lambda x: x["adj"], reverse=True)
    for i, f in enumerate(fac):
        f["rank"] = i + 1
    spare = 0.0
    for i, wt in enumerate(C.WEIGHTS["factors"]):
        if i < len(fac):
            w[fac[i]["id"]] = w.get(fac[i]["id"], 0) + wt
        else:
            spare += wt

    # 2. Regions: strongest risk-adjusted trend, must be above its 200-day average.
    reg = []
    for r in C.REGIONS:
        if r["id"] in px:
            s = stats(px, r["id"])
            if s:
                reg.append({**r, **s})
    reg.sort(key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(reg):
        r["rank"] = i + 1
        r["eligible"] = _eligible(r, prev)
    picks = _pick(reg, prev, len(C.WEIGHTS["regions"]), C.KEEP_RANK["regions"])
    for i, wt in enumerate(C.WEIGHTS["regions"]):
        if i < len(picks):
            w[picks[i]["id"]] = w.get(picks[i]["id"], 0) + wt
        else:
            spare += wt
            notes.append("Not enough regions in an uptrend, so a regional slot stays in the All-World core")

    # 3. Stocks: same idea, with a wider buffer before a holding is sold.
    stk = []
    for st in C.STOCKS:
        if st["id"] in px:
            s = stats(px, st["id"])
            if s:
                stk.append({**st, **s})
    stk.sort(key=lambda x: x["score"], reverse=True)
    for i, st in enumerate(stk):
        st["rank"] = i + 1
        st["eligible"] = _eligible(st, prev)
    spicks = _pick(stk, prev, len(C.WEIGHTS["stocks"]), C.KEEP_RANK["stocks"])
    for i, wt in enumerate(C.WEIGHTS["stocks"]):
        if i < len(spicks):
            w[spicks[i]["id"]] = w.get(spicks[i]["id"], 0) + wt
        else:
            spare += wt
            notes.append("Fewer than 3 stocks pass the trend filter, so the spare slot goes to the core")
    w[core] += spare

    # 4. Defensive overlay.
    reg_state = regime(px, prev_level)
    d = reg_state["defensive_pct"]
    gold_id = C.DEFENSIVE["gold"]["id"]
    gold = stats(px, gold_id) if gold_id in px else None
    gold_held = prev.get(gold_id, 0) > 0
    gold_trending = bool(gold and gold["trend"] > (-C.HOLD_SLACK["trend"] if gold_held else 0))
    if d:
        w = {k: v * (100 - d) / 100 for k, v in w.items()}
        g = round(d * C.GOLD_SHARE_WHEN_TRENDING) if gold_trending else 0
        if g:
            w[gold_id] = g
        if d - g:
            w["CASH"] = d - g

    target = largest_remainder(w)
    return {
        "target": target,
        "factors": fac,
        "regions": reg,
        "stocks": stk,
        "regime": {**reg_state, "gold_trending": gold_trending, "gold": gold},
        "notes": notes,
    }


def _eligible(x: dict, prev: dict) -> bool:
    """New entries need an uptrend. Current holdings get some slack."""
    if prev.get(x["id"], 0) > 0:
        return x["trend"] > -C.HOLD_SLACK["trend"] and x["mom"] > -C.HOLD_SLACK["mom"]
    return x["trend"] > 0 and x["mom"] > 0


def _pick(ranked: list[dict], prev: dict, n: int, keep_rank: int) -> list[dict]:
    """Keep current holdings while they stay eligible and inside keep_rank,
    then fill empty slots with the best eligible names. Output is in rank order."""
    kept = [x for x in ranked if prev.get(x["id"], 0) > 0 and x["eligible"] and x["rank"] <= keep_rank]
    kept = kept[:n]
    for x in ranked:
        if len(kept) >= n:
            break
        if x["eligible"] and x not in kept:
            kept.append(x)
    return sorted(kept, key=lambda x: x["rank"])


def drifted_weights(units: dict[str, float], prices: dict[str, float]) -> tuple[dict[str, float], float]:
    vals = {k: u * (1.0 if k == "CASH" else prices[k]) for k, u in units.items()}
    nav = sum(vals.values())
    return ({k: 100 * v / nav for k, v in vals.items()} if nav else {}), nav


def decide(target: dict[str, int], current: dict[str, float], prev_target: dict[str, int],
           prev_level: int | None, level: int, days_since: int | None = None) -> tuple[bool, list[str]]:
    """Should the fund trade today? Returns (rebalance, reasons)."""
    if not current:
        return True, ["First allocation: build the portfolio from cash"]
    if days_since is not None and days_since < C.MIN_DAYS_BETWEEN and not (prev_level is not None and level > prev_level):
        # Only a rise in market risk can override the cooling-off period.
        return False, []
    reasons = []
    added = sorted(set(target) - set(prev_target))
    removed = sorted(set(prev_target) - set(target))
    if added or removed:
        if added:
            reasons.append("New holding(s): " + ", ".join(added))
        if removed:
            reasons.append("Exit(s): " + ", ".join(removed))
    if prev_level is not None and level != prev_level:
        reasons.append(f"Market risk level changed from {prev_level} to {level} warning sign(s)")
    keys = set(target) | set(current)
    gaps = {k: target.get(k, 0) - current.get(k, 0) for k in keys}
    big = {k: g for k, g in gaps.items()
           if abs(g) >= max(C.TRIGGER["position_gap_pp"], C.TRIGGER["position_gap_rel"] * max(target.get(k, 0), current.get(k, 0)))}
    if big:
        reasons.append("Position(s) well off target: " +
                       ", ".join(f"{k} {g:+.1f}pp" for k, g in sorted(big.items(), key=lambda x: -abs(x[1]))))
    total = sum(abs(g) for g in gaps.values())
    if total >= C.TRIGGER["total_gap_pp"] and not big:
        reasons.append(f"Overall drift reached {total:.1f} points")
    return bool(reasons), reasons


def trade_cost(trades: dict[str, float]) -> float:
    """Cost in percent of NAV for a set of weight changes (percentage points)."""
    uk = {s["id"] for s in C.STOCKS if s["ccy"] == "GBp"}
    fx = {s["id"] for s in C.STOCKS if s["ccy"] != "GBp"}
    cost = 0.0
    for k, g in trades.items():
        if k == "CASH":
            continue
        if k in uk:
            rate = C.COSTS["uk_stock"] if g > 0 else C.COSTS["uk_stock"] - 0.005  # stamp duty on buys only
        elif k in fx:
            rate = C.COSTS["usd_eur_stock"]
        else:
            rate = C.COSTS["etf"]
        cost += abs(g) * rate
    return cost
