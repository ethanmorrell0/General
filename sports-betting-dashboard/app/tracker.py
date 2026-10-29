"""Bet tracker: P&L, ROI and closing-line value (CLV)."""
from __future__ import annotations

import math
import time

from . import db

RESULT_MULT = {  # profit per unit stake, as a function of odds
    "won": lambda o: o - 1,
    "lost": lambda o: -1.0,
    "void": lambda o: 0.0,
    "half_won": lambda o: (o - 1) / 2,
    "half_lost": lambda o: -0.5,
}


def settle(bet_id: int, status: str) -> None:
    bet = next((b for b in db.list_bets() if b["id"] == bet_id), None)
    if bet is None:
        raise KeyError(bet_id)
    if status == "open":
        db.update_bet(bet_id, status="open", profit=None, settled_at=None)
        return
    profit = round(bet["stake"] * RESULT_MULT[status](bet["odds"]), 2)
    db.update_bet(bet_id, status=status, profit=profit, settled_at=time.time())


def _clv(bet: dict, closing: dict[str, float]) -> float | None:
    """CLV = price taken x closing fair probability - 1 (positive means you beat the close)."""
    probs = []
    for leg in bet["legs"]:
        p = closing.get(leg.get("sel_id"))
        if p is None:
            return None
        probs.append(p)
    if not probs:
        return None
    return bet["odds"] * math.prod(probs) - 1 if bet["kind"] == "single" else \
        math.prod(l["odds"] for l in bet["legs"]) * math.prod(probs) - 1


def bets_with_clv() -> list[dict]:
    bets = db.list_bets()
    ids = [l["sel_id"] for b in bets for l in b["legs"] if l.get("sel_id")]
    closing = db.fair_probs(ids)
    for b in bets:
        b["clv"] = _clv(b, closing)
    return bets


def summary(bets: list[dict]) -> dict:
    settled = [b for b in bets if b["status"] != "open"]
    staked = sum(b["stake"] for b in settled)
    profit = sum(b["profit"] or 0 for b in settled)
    expected = sum(b["stake"] * ((b["fair_prob"] or 0) * b["odds"] - 1) for b in settled if b["fair_prob"])
    clvs = [b["clv"] for b in bets if b["clv"] is not None]
    series, running = [], 0.0
    for b in sorted(settled, key=lambda x: x["settled_at"] or x["created_at"]):
        running += b["profit"] or 0
        series.append({"t": b["settled_at"] or b["created_at"], "pnl": round(running, 2)})
    return {
        "bets": len(bets), "open": len(bets) - len(settled), "settled": len(settled),
        "open_stake": round(sum(b["stake"] for b in bets if b["status"] == "open"), 2),
        "staked": round(staked, 2), "profit": round(profit, 2),
        "roi": profit / staked if staked else None,
        "expected_profit": round(expected, 2),
        "won": sum(1 for b in settled if b["status"] in ("won", "half_won")),
        "avg_clv": sum(clvs) / len(clvs) if clvs else None,
        "clv_positive": sum(1 for c in clvs if c > 0), "clv_count": len(clvs),
        "series": series,
    }
