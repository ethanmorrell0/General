"""Core betting maths: margin removal, settlement-aware probabilities, Kelly staking."""
from __future__ import annotations

import math
from statistics import median


def implied(prices: list[float]) -> list[float]:
    return [1.0 / p for p in prices]


def overround(prices: list[float]) -> float:
    return sum(implied(prices)) - 1.0


def devig_power(prices: list[float]) -> list[float]:
    """Power method: find k with sum((1/o)^k) = 1. Handles favourite-longshot bias better
    than proportional scaling. Returns fair probabilities that sum to 1."""
    imp = implied(prices)
    total = sum(imp)
    if abs(total - 1.0) < 1e-9:
        return imp
    lo, hi = 0.5, 3.0
    for _ in range(80):
        k = (lo + hi) / 2
        s = sum(q ** k for q in imp)
        # s decreases as k increases (q < 1)
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    probs = [q ** k for q in imp]
    s = sum(probs)
    return [p / s for p in probs]


def devig_multiplicative(prices: list[float]) -> list[float]:
    imp = implied(prices)
    s = sum(imp)
    return [q / s for q in imp]


def effective_prob(win: float, half_win: float, push: float, half_loss: float, loss: float) -> float:
    """Collapse a settlement distribution (Asian lines, pushes) into the single probability p
    such that EV(odds) = p * odds - 1 has the same break-even price.
    EV(o) = (o-1)*W - L with W = win + half_win/2, L = loss + half_loss/2."""
    w = win + 0.5 * half_win
    l = loss + 0.5 * half_loss
    if w + l <= 0:
        return 0.5
    return w / (w + l)


def ev(prob: float, odds: float) -> float:
    return prob * odds - 1.0


def kelly_fraction(prob: float, odds: float) -> float:
    """Full-Kelly fraction of bankroll for decimal odds."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    f = (prob * odds - 1.0) / b
    return max(f, 0.0)


def stake(prob: float, odds: float, bankroll: float, fraction: float, max_pct: float) -> float:
    f = kelly_fraction(prob, odds) * fraction
    f = min(f, max_pct)
    amount = bankroll * f
    # Round to 50p for sensible bet slips.
    return math.floor(amount * 2) / 2 if amount >= 0.5 else 0.0


def min_price_for_edge(prob: float, edge: float) -> float:
    return (1.0 + edge) / prob if prob > 0 else float("inf")


def fair_odds(prob: float) -> float:
    return 1.0 / prob if prob > 0 else float("inf")


def safe_median(values: list[float]) -> float | None:
    return median(values) if values else None


# ---------- distributions ----------

def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def normal_cdf(x: float, mu: float = 0.0, sd: float = 1.0) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sd * math.sqrt(2.0))))


def split_quarter(line: float) -> list[float]:
    """Asian quarter lines (e.g. -0.75) settle as two half-stakes on the neighbouring lines."""
    frac = round(abs(line) * 4) % 4
    if frac in (1, 3):
        return [line - 0.25, line + 0.25]
    return [line]


def settle_margin(margin_dist: dict[int, float], line: float) -> tuple[float, float, float, float, float]:
    """Settle a bet that wins when (margin + line) > 0, over a discrete margin distribution.
    Works for Asian handicaps (margin = selection goals - opponent goals) and totals
    (margin = total - x with sign handled by caller). Returns (win, half_win, push, half_loss, loss)."""
    parts = split_quarter(line)
    win = half_win = push = half_loss = loss = 0.0
    for m, p in margin_dist.items():
        results = []
        for ln in parts:
            v = m + ln
            results.append(1 if v > 1e-9 else (-1 if v < -1e-9 else 0))
        if len(results) == 1:
            r = results[0]
            if r == 1:
                win += p
            elif r == -1:
                loss += p
            else:
                push += p
        else:
            s = sum(results)
            if s == 2:
                win += p
            elif s == -2:
                loss += p
            elif s == 1:
                half_win += p
            elif s == -1:
                half_loss += p
            else:
                push += p
    return win, half_win, push, half_loss, loss
