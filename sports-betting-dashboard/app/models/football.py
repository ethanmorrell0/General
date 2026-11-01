"""Dixon-Coles style Poisson model for football.

Ratings are fitted per "pool" of linked divisions (e.g. the whole English pyramid), so
promoted/relegated teams tie the divisions together and cup ties between divisions are priced
on one scale. Results come from football-data.co.uk (free CSVs, updated twice a week).
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import maths

FD_URL = "https://football-data.co.uk/mmz4281/{season}/{div}.csv"

POOLS = {
    "ENG": ["E0", "E1", "E2", "E3", "EC"],
    "SCO": ["SC0", "SC1", "SC2", "SC3"],
    "GER": ["D1", "D2"],
    "ESP": ["SP1", "SP2"],
    "ITA": ["I1", "I2"],
    "FRA": ["F1", "F2"],
    "NED": ["N1"],
    "POR": ["P1"],
    "BEL": ["B1"],
    "TUR": ["T1"],
    "GRE": ["G1"],
}

SPORT_POOL = {
    "soccer_spl": "SCO",
    "soccer_germany_bundesliga": "GER", "soccer_germany_bundesliga2": "GER",
    "soccer_spain_la_liga": "ESP", "soccer_spain_segunda_division": "ESP",
    "soccer_italy_serie_a": "ITA", "soccer_italy_serie_b": "ITA",
    "soccer_france_ligue_one": "FRA", "soccer_france_ligue_two": "FRA",
    "soccer_netherlands_eredivisie": "NED", "soccer_portugal_primeira_liga": "POR",
    "soccer_belgium_first_div": "BEL", "soccer_turkey_super_league": "TUR",
    "soccer_greece_super_league": "GRE",
}


def pool_for(sport_key: str) -> str | None:
    from ..sports import is_english_football
    if is_english_football(sport_key):
        return "ENG"
    return SPORT_POOL.get(sport_key)


def season_codes(today: date) -> list[str]:
    start = today.year if today.month >= 7 else today.year - 1
    cur = f"{start % 100:02d}{(start + 1) % 100:02d}"
    prev = f"{(start - 1) % 100:02d}{start % 100:02d}"
    return [prev, cur]


@dataclass
class Match:
    day: date
    home: str
    away: str
    hg: int
    ag: int


def parse_fd_csv(text: str) -> list[Match]:
    out = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for row in reader:
        try:
            d = row.get("Date") or ""
            fmt = "%d/%m/%Y" if len(d.split("/")[-1]) == 4 else "%d/%m/%y"
            day = datetime.strptime(d, fmt).date()
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
            home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if home and away:
            out.append(Match(day, home, away, hg, ag))
    return out


def dc_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lh * la * rho
    if x == 0 and y == 1:
        return 1 + lh * rho
    if x == 1 and y == 0:
        return 1 + la * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


@dataclass
class FootballModel:
    attack: dict[str, float]
    defence: dict[str, float]   # "defensive weakness": higher concedes more
    weight: dict[str, float]    # effective (decayed) matches per team, for confidence
    c: float
    h: float
    rho: float
    max_goals: int = 10
    teams: list[str] = field(default_factory=list)

    def lambdas(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        hadv = 0.0 if neutral else self.h
        lh = math.exp(self.c + hadv + self.attack[home] + self.defence[away])
        la = math.exp(self.c + self.attack[away] + self.defence[home])
        return lh, la

    def confidence(self, home: str, away: str) -> float:
        n = min(self.weight.get(home, 0.0), self.weight.get(away, 0.0))
        return n / (n + 6.0)

    def score_matrix(self, lh: float, la: float) -> list[list[float]]:
        return score_matrix(lh, la, self.rho, self.max_goals)


def score_matrix(lh: float, la: float, rho: float = 0.0, max_goals: int = 10) -> list[list[float]]:
    ph = [maths.poisson_pmf(i, lh) for i in range(max_goals + 1)]
    pa = [maths.poisson_pmf(j, la) for j in range(max_goals + 1)]
    m = [[ph[i] * pa[j] * dc_tau(i, j, lh, la, rho) for j in range(max_goals + 1)] for i in range(max_goals + 1)]
    s = sum(map(sum, m))
    return [[v / s for v in row] for row in m]


def fit(matches: list[Match], ref: date, xi: float = 0.0019, prior: float = 4.0, iters: int = 60) -> FootballModel:
    """Fit attack/defence ratings with exponential time decay and shrinkage towards average.

    Uses the iterative proportional-fitting form of Poisson maximum likelihood (Maher), with
    `prior` pseudo-matches pulling sparse teams (promoted sides, early season) to the mean."""
    ms = [(m, math.exp(-xi * max((ref - m.day).days, 0))) for m in matches if m.day <= ref]
    teams = sorted({m.home for m, _ in ms} | {m.away for m, _ in ms})
    if not ms:
        return FootballModel({}, {}, {}, math.log(1.35), 0.25, -0.06, teams=[])
    tot_w = sum(w for _, w in ms)
    mean_goals = sum(w * (m.hg + m.ag) for m, w in ms) / (2 * tot_w)
    c, h = math.log(max(mean_goals, 0.3)), 0.25
    a = {t: 0.0 for t in teams}
    d = {t: 0.0 for t in teams}
    weight = {t: 0.0 for t in teams}
    for m, w in ms:
        weight[m.home] += w
        weight[m.away] += w

    for _ in range(iters):
        base = math.exp(c)
        gf = {t: 0.0 for t in teams}
        rf = {t: 0.0 for t in teams}
        for m, w in ms:
            gf[m.home] += w * m.hg
            rf[m.home] += w * math.exp(c + h + d[m.away])
            gf[m.away] += w * m.ag
            rf[m.away] += w * math.exp(c + d[m.home])
        for t in teams:
            a[t] = math.log((gf[t] + prior * base) / (rf[t] + prior * base))
        ga = {t: 0.0 for t in teams}
        ra = {t: 0.0 for t in teams}
        for m, w in ms:
            ga[m.away] += w * m.hg
            ra[m.away] += w * math.exp(c + h + a[m.home])
            ga[m.home] += w * m.ag
            ra[m.home] += w * math.exp(c + a[m.away])
        for t in teams:
            d[t] = math.log((ga[t] + prior * base) / (ra[t] + prior * base))
        num = sum(w * m.hg for m, w in ms)
        den = sum(w * math.exp(c + a[m.home] + d[m.away]) for m, w in ms)
        h = math.log(num / den)
        num = sum(w * (m.hg + m.ag) for m, w in ms)
        den = sum(w * (math.exp(h + a[m.home] + d[m.away]) + math.exp(a[m.away] + d[m.home])) for m, w in ms)
        c = math.log(num / den)
        # Centre ratings; shift the means into c so predictions are unchanged.
        ma = sum(a.values()) / len(a)
        md = sum(d.values()) / len(d)
        for t in teams:
            a[t] -= ma
            d[t] -= md
        c += ma + md

    # Dixon-Coles low-score correlation by grid search on the weighted likelihood.
    best_rho, best_ll = 0.0, -float("inf")
    for i in range(-25, 6):
        rho = i / 100
        ll = 0.0
        ok = True
        for m, w in ms:
            if m.hg > 1 or m.ag > 1:
                continue
            lh = math.exp(c + h + a[m.home] + d[m.away])
            la = math.exp(c + a[m.away] + d[m.home])
            tau = dc_tau(m.hg, m.ag, lh, la, rho)
            if tau <= 0:
                ok = False
                break
            ll += w * math.log(tau)
        if ok and ll > best_ll:
            best_rho, best_ll = rho, ll
    return FootballModel(a, d, weight, c, h, best_rho, teams=teams)


# ---------- market probabilities from a score matrix ----------

def margin_dist(matrix: list[list[float]], side: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            k = (i - j) if side == "home" else (j - i)
            out[k] = out.get(k, 0.0) + p
    return out


def total_dist(matrix: list[list[float]]) -> dict[int, float]:
    out: dict[int, float] = {}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            out[i + j] = out.get(i + j, 0.0) + p
    return out


def matrix_prob(matrix: list[list[float]], market: str, side: str, point: float | None) -> float | None:
    """Effective probability for a selection. side: home/away/draw, over/under, yes/no."""
    if market == "h2h":
        md = margin_dist(matrix, "home")
        if side == "home":
            return sum(p for k, p in md.items() if k > 0)
        if side == "away":
            return sum(p for k, p in md.items() if k < 0)
        return md.get(0, 0.0)
    if market == "spreads" and point is not None and side in ("home", "away"):
        return maths.effective_prob(*maths.settle_margin(margin_dist(matrix, side), point))
    if market == "totals" and point is not None:
        td = total_dist(matrix)
        if side == "over":
            dist = {k: p for k, p in td.items()}
            return maths.effective_prob(*maths.settle_margin(dist, -point))
        if side == "under":
            dist = {-k: p for k, p in td.items()}
            return maths.effective_prob(*maths.settle_margin(dist, point))
    if market == "btts":
        yes = sum(p for i, row in enumerate(matrix) for j, p in enumerate(row) if i > 0 and j > 0)
        return yes if side == "yes" else 1 - yes
    return None
