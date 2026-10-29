"""Points-based team ratings for NFL, NBA, college football and college basketball.

Each team gets an offensive and defensive rating (points above/below average), fitted by
ridge-regularised least squares on time-decayed results from ESPN's public scoreboard.
Margins and totals are modelled as normal distributions around the rating-implied means.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .. import maths

SPORT_CFG = {
    "americanfootball_nfl": {"espn": "football/nfl", "groups": None, "margin_sd": 13.5, "total_sd": 10.5,
                             "xi": 0.004, "ridge": 3.0, "season_start": (9, 1)},
    "basketball_nba": {"espn": "basketball/nba", "groups": None, "margin_sd": 12.0, "total_sd": 18.0,
                       "xi": 0.008, "ridge": 6.0, "season_start": (10, 15)},
    "americanfootball_ncaaf": {"espn": "football/college-football", "groups": "80", "margin_sd": 15.5,
                               "total_sd": 14.5, "xi": 0.004, "ridge": 2.5, "season_start": (8, 20)},
    "basketball_ncaab": {"espn": "basketball/mens-college-basketball", "groups": "50", "margin_sd": 10.5,
                         "total_sd": 13.5, "xi": 0.008, "ridge": 5.0, "season_start": (11, 1)},
}


@dataclass
class Game:
    day: date
    home: str
    away: str
    hp: int
    ap: int
    neutral: bool = False


@dataclass
class RatingsModel:
    off: dict[str, float]
    dfn: dict[str, float]
    weight: dict[str, float]
    mu: float
    hfa: float
    margin_sd: float
    total_sd: float
    teams: list[str] = field(default_factory=list)

    def means(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        hfa = 0.0 if neutral else self.hfa
        margin = hfa + (self.off[home] + self.dfn[home]) - (self.off[away] + self.dfn[away])
        total = 2 * self.mu + self.off[home] + self.off[away] - self.dfn[home] - self.dfn[away]
        return margin, total

    def confidence(self, home: str, away: str) -> float:
        n = min(self.weight.get(home, 0.0), self.weight.get(away, 0.0))
        return n / (n + 5.0)

    def prob(self, home: str, away: str, market: str, side: str, point: float | None) -> float | None:
        margin, total = self.means(home, away)
        if market == "h2h":
            p_home = 1 - maths.normal_cdf(0.0, margin, self.margin_sd)
            return p_home if side == "home" else 1 - p_home if side == "away" else None
        if market == "spreads" and point is not None and side in ("home", "away"):
            mu = margin if side == "home" else -margin
            return maths.effective_prob(*maths.settle_margin(discrete_normal(mu, self.margin_sd), point))
        if market == "totals" and point is not None:
            dist = discrete_normal(total, self.total_sd)
            if side == "over":
                return maths.effective_prob(*maths.settle_margin(dist, -point))
            if side == "under":
                return maths.effective_prob(*maths.settle_margin({-k: p for k, p in dist.items()}, point))
        return None


def discrete_normal(mu: float, sd: float) -> dict[int, float]:
    lo, hi = int(math.floor(mu - 6 * sd)), int(math.ceil(mu + 6 * sd))
    return {k: maths.normal_cdf(k + 0.5, mu, sd) - maths.normal_cdf(k - 0.5, mu, sd) for k in range(lo, hi + 1)}


def fit(games: list[Game], ref: date, sport_key: str, iters: int = 40) -> RatingsModel:
    cfg = SPORT_CFG[sport_key]
    xi, ridge = cfg["xi"], cfg["ridge"]
    gs = [(g, math.exp(-xi * max((ref - g.day).days, 0))) for g in games if g.day <= ref]
    teams = sorted({g.home for g, _ in gs} | {g.away for g, _ in gs})
    off = {t: 0.0 for t in teams}
    dfn = {t: 0.0 for t in teams}
    weight = {t: 0.0 for t in teams}
    if not gs:
        return RatingsModel(off, dfn, weight, 0.0, 0.0, cfg["margin_sd"], cfg["total_sd"], teams)
    tw = sum(w for _, w in gs)
    mu = sum(w * (g.hp + g.ap) for g, w in gs) / (2 * tw)
    nn = [(g, w) for g, w in gs if not g.neutral]
    hfa = (sum(w * (g.hp - g.ap) for g, w in nn) / sum(w for _, w in nn)) if nn else 0.0
    for g, w in gs:
        weight[g.home] += w
        weight[g.away] += w

    for _ in range(iters):
        num = {t: 0.0 for t in teams}
        for g, w in gs:
            hh = 0.0 if g.neutral else hfa / 2
            num[g.home] += w * (g.hp - mu - hh + dfn[g.away])
            num[g.away] += w * (g.ap - mu + hh + dfn[g.home])
        for t in teams:
            off[t] = num[t] / (weight[t] + ridge)
        num = {t: 0.0 for t in teams}
        for g, w in gs:
            hh = 0.0 if g.neutral else hfa / 2
            num[g.away] += w * (mu + hh + off[g.home] - g.hp)
            num[g.home] += w * (mu - hh + off[g.away] - g.ap)
        for t in teams:
            dfn[t] = num[t] / (weight[t] + ridge)
        resid = 0.0
        for g, w in gs:
            hh = 0.0 if g.neutral else hfa / 2
            resid += w * ((g.hp - hh - off[g.home] + dfn[g.away]) + (g.ap + hh - off[g.away] + dfn[g.home]))
        mu = resid / (2 * tw)
        if nn:
            hfa = sum(w * ((g.hp - g.ap) - (off[g.home] + dfn[g.home]) + (off[g.away] + dfn[g.away]))
                      for g, w in nn) / sum(w for _, w in nn)
    return RatingsModel(off, dfn, weight, mu, hfa, cfg["margin_sd"], cfg["total_sd"], teams)


def parse_espn(payload: dict) -> list[Game]:
    out = []
    for ev in payload.get("events", []):
        try:
            comp = ev["competitions"][0]
            if not comp.get("status", ev.get("status", {})).get("type", {}).get("completed", False):
                continue
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            day = date.fromisoformat(ev["date"][:10])
            out.append(Game(day, teams["home"]["team"]["displayName"], teams["away"]["team"]["displayName"],
                            int(float(teams["home"]["score"])), int(float(teams["away"]["score"])),
                            bool(comp.get("neutralSite", False))))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return out
