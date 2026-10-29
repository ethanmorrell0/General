"""Accumulator optimiser: pick the legs that maximise expected value, including bet365's
Acca Boost (bonus on winnings) and the 2-Up early payout on match-result legs."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import maths, sports

BOOST_MARKETS = {"h2h", "totals", "btts"}
BEAM_WIDTH = 400
MAX_PER_EVENT = 3


def boost_for(legs: list[dict], settings: dict) -> float:
    if not settings.get("acca_boost") or len(legs) < 2:
        return 0.0
    if not all(sports.is_football(l["sport_key"]) and l["market"] in BOOST_MARKETS
               and l["ref_price"] >= settings.get("acca_boost_min_leg_odds", 1.2) for l in legs):
        return 0.0
    table = settings.get("acca_boost_table", {})
    n = len(legs)
    while n >= 2 and str(n) not in table:
        n -= 1
    return float(table.get(str(n), 0.0))


def acca_stats(legs: list[dict], settings: dict, use_promo: bool, use_boost: bool) -> dict:
    prob = math.prod(l["eff_prob"] if use_promo else l["fair_prob"] for l in legs)
    odds = math.prod(l["ref_price"] for l in legs)
    boost = boost_for(legs, settings) if use_boost else 0.0
    payout = 1 + (odds - 1) * (1 + boost)
    ev = prob * payout - 1
    stake = maths.stake(prob, payout, settings["bankroll"], settings["kelly_fraction"],
                        settings.get("acca_max_stake_pct", 0.01))
    return {"win_prob": prob, "odds": odds, "boost": boost, "payout_odds": payout, "ev": ev,
            "stake": stake, "fair_odds": 1 / prob if prob > 0 else None}


def build(selections: list[dict], settings: dict, legs: int, groups: list[str] | None = None,
          markets: list[str] | None = None, min_odds: float = 1.01, max_odds: float = 1000.0,
          start: str | None = None, end: str | None = None, use_boost: bool = True,
          use_early_payout: bool = True, confirmed_only: bool = False, n_results: int = 3) -> dict:
    now = datetime.now(timezone.utc)
    t0 = _dt(start) if start else now
    t1 = _dt(end) if end else None
    pool = []
    for s in selections:
        if not s.get("ref_price"):
            continue
        if groups and s["group"] not in groups:
            continue
        if markets and not (s["market"] in markets or ("props" in markets and s["market"].startswith("player_"))):
            continue
        if not (min_odds <= s["ref_price"] <= max_odds):
            continue
        ct = _dt(s["commence_time"])
        if ct <= max(t0, now) or (t1 and ct > t1):
            continue
        if confirmed_only and not s.get("confirmed"):
            continue
        p = s["eff_prob"] if use_early_payout else s["fair_prob"]
        # Allow slightly negative legs: the acca boost can lift them above break-even.
        if p * s["ref_price"] < 0.97:
            continue
        pool.append((p * s["ref_price"], p, s))

    # Keep the best few selections per event (they're mutually exclusive within an acca).
    by_event: dict[str, list] = {}
    for item in sorted(pool, key=lambda x: -x[0]):
        by_event.setdefault(item[2]["event_id"], [])
        if len(by_event[item[2]["event_id"]]) < MAX_PER_EVENT:
            by_event[item[2]["event_id"]].append(item)
    cands = sorted((it for items in by_event.values() for it in items), key=lambda x: -x[0])

    n_events = len(by_event)
    if n_events < legs:
        return {"accas": [], "candidates": len(cands), "events": n_events,
                "message": f"Only {n_events} events match your filters; need {legs} for a {legs}-fold. "
                           f"Widen the filters or refresh more sports."}

    # Beam search over combinations (indices increasing, one leg per event).
    beam: list[tuple[float, tuple[int, ...], frozenset]] = [(0.0, (), frozenset())]
    for _depth in range(legs):
        nxt = []
        for score, idxs, used in beam:
            start_i = idxs[-1] + 1 if idxs else 0
            for i in range(start_i, len(cands)):
                ev_id = cands[i][2]["event_id"]
                if ev_id in used:
                    continue
                nxt.append((score + math.log(cands[i][0]), idxs + (i,), used | {ev_id}))
        nxt.sort(key=lambda x: -x[0])
        beam = nxt[:BEAM_WIDTH]
        if not beam:
            break

    results = []
    for _score, idxs, _used in beam:
        legs_sel = [cands[i][2] for i in idxs]
        stats = acca_stats(legs_sel, settings, use_early_payout, use_boost)
        results.append((stats["ev"], legs_sel, stats))
    results.sort(key=lambda x: -x[0])

    accas = []
    for ev, legs_sel, stats in results[:n_results]:
        accas.append({**stats, "legs": [_leg(l, use_early_payout) for l in sorted(legs_sel, key=lambda l: l["commence_time"])],
                      "positive": ev > 0})
    msg = None
    if accas and not accas[0]["positive"]:
        msg = ("No +EV acca exists with these filters. The best combination is shown, but the bookmaker margin "
               "outweighs the edge. Consider singles, fewer legs, or wider filters.")
    return {"accas": accas, "candidates": len(cands), "events": n_events, "message": msg}


def _leg(s: dict, promo: bool) -> dict:
    keys = ("sel_id", "event_id", "sport_key", "sport_title", "group", "commence_time", "event_name", "market",
            "market_label", "label", "fair_prob", "eff_prob", "ref_price", "ref_source", "confirmed", "promo_bonus",
            "min_price", "fair_odds")
    leg = {k: s.get(k) for k in keys}
    p = s["eff_prob"] if promo else s["fair_prob"]
    leg["eff_prob"] = p
    leg["leg_ev"] = p * s["ref_price"] - 1
    return leg


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
