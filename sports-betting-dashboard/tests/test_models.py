import math
import random
from datetime import date, timedelta

import pytest

from app.models import football, ratings
from app.models.early_payout import implied_lambdas, two_up_bonus
from app.teams import match_team, normalise


def _league(seed=1, n_rounds=4):
    rng = random.Random(seed)
    strength = {f"Team {i}": (i - 5) / 10 for i in range(10)}
    teams = list(strength)
    matches = []
    day = date(2026, 8, 1)
    for _ in range(n_rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                lh = math.exp(0.3 + 0.25 + strength[h] - strength[a])
                la = math.exp(0.3 + strength[a] - strength[h])
                matches.append(football.Match(day, h, a, _pois(rng, lh), _pois(rng, la)))
                day += timedelta(hours=6)
    return matches, strength, day


def _pois(rng, lam):
    k, p, l = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p < l:
            return k
        k += 1


def test_football_fit_recovers_ordering():
    matches, strength, end = _league()
    m = football.fit(matches, end, xi=0.0)
    best, worst = "Team 9", "Team 0"
    assert m.attack[best] > m.attack[worst]
    assert m.defence[best] < m.defence[worst]
    assert 0.1 < m.h < 0.45
    lh, la = m.lambdas(best, worst)
    mat = m.score_matrix(lh, la)
    probs = [football.matrix_prob(mat, "h2h", s, None) for s in ("home", "draw", "away")]
    assert sum(probs) == pytest.approx(1.0)
    assert probs[0] > 0.6


def test_football_market_probs_consistent():
    mat = football.score_matrix(1.6, 1.1, -0.06)
    over = football.matrix_prob(mat, "totals", "over", 2.5)
    under = football.matrix_prob(mat, "totals", "under", 2.5)
    assert over + under == pytest.approx(1.0)
    yes = football.matrix_prob(mat, "btts", "yes", None)
    assert 0.3 < yes < 0.7
    # A home -0.5 handicap is the same bet as home to win.
    assert football.matrix_prob(mat, "spreads", "home", -0.5) == pytest.approx(
        football.matrix_prob(mat, "h2h", "home", None))


def test_parse_fd_csv():
    text = "﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG\nE0,16/08/2025,20:00,Liverpool,Bournemouth,4,2\nE0,bad,,,,,\n"
    ms = football.parse_fd_csv(text)
    assert len(ms) == 1 and ms[0].home == "Liverpool" and ms[0].hg == 4 and ms[0].day == date(2025, 8, 16)


def test_season_codes():
    assert football.season_codes(date(2026, 10, 29)) == ["2526", "2627"]
    assert football.season_codes(date(2027, 3, 1)) == ["2526", "2627"]


def test_ratings_fit_and_probs():
    rng = random.Random(3)
    true = {f"T{i}": (i - 4) * 2.0 for i in range(9)}
    games = []
    day = date(2026, 9, 1)
    for _ in range(6):
        for h in true:
            for a in true:
                if h != a:
                    margin = 2.0 + true[h] - true[a] + rng.gauss(0, 10)
                    total = 45 + rng.gauss(0, 8)
                    games.append(ratings.Game(day, h, a, round((total + margin) / 2), round((total - margin) / 2)))
        day += timedelta(days=7)
    m = ratings.fit(games, day, "americanfootball_nfl")
    assert m.off["T8"] + m.dfn["T8"] > m.off["T0"] + m.dfn["T0"]
    p_home = m.prob("T8", "T0", "h2h", "home", None)
    assert p_home > 0.75
    cover = m.prob("T8", "T0", "spreads", "home", -3.5)
    assert 0 < cover < p_home
    over, under = m.prob("T8", "T0", "totals", "over", 44.5), m.prob("T8", "T0", "totals", "under", 44.5)
    assert over + under == pytest.approx(1.0, abs=1e-6)


def test_parse_espn():
    payload = {"events": [{"date": "2026-10-26T17:00Z", "competitions": [{
        "neutralSite": False, "status": {"type": {"completed": True}},
        "competitors": [{"homeAway": "home", "score": "27", "team": {"displayName": "Kansas City Chiefs"}},
                        {"homeAway": "away", "score": "20", "team": {"displayName": "Denver Broncos"}}]}]},
        {"date": "2026-10-30T17:00Z", "competitions": [{"status": {"type": {"completed": False}}, "competitors": []}]}]}
    gs = ratings.parse_espn(payload)
    assert len(gs) == 1 and gs[0].home == "Kansas City Chiefs" and gs[0].hp == 27


def test_two_up_bonus_bounds():
    for lh, la in [(2.2, 0.7), (1.35, 1.35), (0.8, 1.9)]:
        mat = football.score_matrix(lh, la)
        p_not_win = 1 - football.matrix_prob(mat, "h2h", "home", None)
        b = two_up_bonus(lh, la, "home")
        assert 0.003 < b < p_not_win
    # Symmetric match: same bonus both sides.
    assert two_up_bonus(1.3, 1.3, "home") == pytest.approx(two_up_bonus(1.3, 1.3, "away"))


def test_implied_lambdas_round_trip():
    mat = football.score_matrix(1.7, 1.0)
    ph, pa = football.matrix_prob(mat, "h2h", "home", None), football.matrix_prob(mat, "h2h", "away", None)
    over = football.matrix_prob(mat, "totals", "over", 2.5)
    lh, la = implied_lambdas(ph, pa, p_over25=over)
    assert lh == pytest.approx(1.7, abs=0.05) and la == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize("feed,fd", [
    ("Manchester United", "Man United"), ("Nottingham Forest", "Nott'm Forest"),
    ("Sheffield Wednesday", "Sheffield Weds"), ("Queens Park Rangers", "QPR"),
    ("Wolverhampton Wanderers", "Wolves"), ("Brighton and Hove Albion", "Brighton"),
    ("Borussia Dortmund", "Dortmund"), ("AFC Wimbledon", "AFC Wimbledon"),
])
def test_team_matching(feed, fd):
    candidates = ["Man United", "Man City", "Nott'm Forest", "Sheffield Weds", "Sheffield United", "QPR", "Wolves",
                  "Brighton", "Dortmund", "AFC Wimbledon", "Wimbledon Town", "Brentford", "West Brom"]
    assert match_team(feed, candidates) == fd


def test_team_matching_rejects_unknown():
    assert match_team("Real Madrid", ["Man United", "Arsenal"]) is None
    assert normalise("Man Utd") == "manchester united"
