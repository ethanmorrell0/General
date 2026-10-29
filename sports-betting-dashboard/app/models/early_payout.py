"""Value of bet365's "2 Goals Ahead Early Payout" and goal-rate inversion from market prices.

If your team goes two goals up at any point, a full-time-result bet is paid as a winner even if
they don't win. The extra value is P(team leads by 2 at some point AND doesn't win), which we get
from a minute-by-minute goal process with the match's expected-goal rates.
"""
from __future__ import annotations

import math
from functools import lru_cache

from .. import maths

MINUTES = 95  # includes typical stoppage time


def two_up_bonus(lh: float, la: float, side: str) -> float:
    """P(`side` goes 2 goals ahead at some point but does not win)."""
    return _two_up(round(lh, 3), round(la, 3), side)


@lru_cache(maxsize=4096)
def _two_up(lh: float, la: float, side: str) -> float:
    ph = 1 - math.exp(-lh / MINUTES)
    pa = 1 - math.exp(-la / MINUTES)
    if side == "away":
        ph, pa = pa, ph
    # state: (lead from side's perspective, reached) -> prob
    states: dict[tuple[int, bool], float] = {(0, False): 1.0}
    for _ in range(MINUTES):
        nxt: dict[tuple[int, bool], float] = {}
        for (lead, reached), p in states.items():
            for dl, q in ((1, ph * (1 - pa)), (-1, pa * (1 - ph)), (0, (1 - ph) * (1 - pa) + ph * pa)):
                if q <= 0:
                    continue
                nl = max(-12, min(12, lead + dl))
                key = (nl, reached or nl >= 2)
                nxt[key] = nxt.get(key, 0.0) + p * q
        states = nxt
    return sum(p for (lead, reached), p in states.items() if reached and lead <= 0)


def implied_lambdas(p_home: float, p_away: float, total_goals: float | None = None,
                    p_over25: float | None = None) -> tuple[float, float]:
    """Expected goals (home, away) consistent with fair 1X2 probabilities and, if known, the
    over-2.5 probability. Assumes independent Poisson goals."""
    if total_goals is None:
        if p_over25 is not None:
            lo, hi = 0.3, 7.0
            for _ in range(60):
                t = (lo + hi) / 2
                p_under = sum(maths.poisson_pmf(k, t) for k in range(3))
                if 1 - p_under < p_over25:
                    lo = t
                else:
                    hi = t
            total_goals = (lo + hi) / 2
        else:
            total_goals = 2.65
    target = p_home - p_away
    lo, hi = -total_goals + 0.02, total_goals - 0.02
    for _ in range(60):
        s = (lo + hi) / 2
        lh, la = (total_goals + s) / 2, (total_goals - s) / 2
        diff = _win_diff(lh, la)
        if diff < target:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return (total_goals + s) / 2, (total_goals - s) / 2


def _win_diff(lh: float, la: float, n: int = 12) -> float:
    ph = [maths.poisson_pmf(i, lh) for i in range(n)]
    pa = [maths.poisson_pmf(j, la) for j in range(n)]
    home = sum(ph[i] * pa[j] for i in range(n) for j in range(n) if i > j)
    away = sum(ph[i] * pa[j] for i in range(n) for j in range(n) if j > i)
    return home - away
