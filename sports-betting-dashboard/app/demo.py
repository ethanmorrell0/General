"""Synthetic but realistic markets so the dashboard runs without an API key.

Each event has a hidden "true" probability. Sharp books price close to it with a thin margin;
soft books (and bet365) add noise and a bigger margin, which is what creates the occasional
real edge. The "model" sees the truth with its own error.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone

from .models.football import matrix_prob, score_matrix
from .models.hub import _demo_key
from .models.ratings import discrete_normal
from . import maths

TEAMS = {
    "soccer_epl": ["Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton and Hove Albion", "Chelsea",
                   "Crystal Palace", "Everton", "Fulham", "Liverpool", "Manchester City", "Manchester United",
                   "Newcastle United", "Nottingham Forest", "Tottenham Hotspur", "West Ham United",
                   "Wolverhampton Wanderers", "Leeds United", "Burnley", "Sunderland"],
    "soccer_efl_champ": ["Leicester City", "Ipswich Town", "Southampton", "Sheffield United", "Middlesbrough",
                         "Coventry City", "West Bromwich Albion", "Norwich City", "Hull City", "Millwall",
                         "Watford", "Bristol City", "Stoke City", "Swansea City", "Preston North End", "Derby County",
                         "Queens Park Rangers", "Portsmouth", "Blackburn Rovers", "Wrexham", "Charlton Athletic",
                         "Birmingham City", "Oxford United", "Sheffield Wednesday"],
    "soccer_england_league1": ["Huddersfield Town", "Bolton Wanderers", "Stockport County", "Wycombe Wanderers",
                               "Reading", "Barnsley", "Peterborough United", "Lincoln City", "Wigan Athletic",
                               "Mansfield Town", "Exeter City", "Leyton Orient", "Rotherham United", "Burton Albion",
                               "Stevenage", "Blackpool", "Luton Town", "Plymouth Argyle"],
    "soccer_england_league2": ["Doncaster Rovers", "Port Vale", "Bradford City", "Walsall", "Notts County",
                               "Chesterfield", "MK Dons", "Gillingham", "Salford City", "Grimsby Town",
                               "Swindon Town", "Colchester United", "Crewe Alexandra", "Barrow", "Tranmere Rovers",
                               "Cheltenham Town"],
    "americanfootball_nfl": ["Kansas City Chiefs", "Buffalo Bills", "Baltimore Ravens", "Philadelphia Eagles",
                             "Detroit Lions", "San Francisco 49ers", "Green Bay Packers", "Dallas Cowboys",
                             "Cincinnati Bengals", "Houston Texans", "Los Angeles Rams", "Miami Dolphins",
                             "Minnesota Vikings", "Pittsburgh Steelers", "Tampa Bay Buccaneers", "Washington Commanders"],
    "basketball_nba": ["Boston Celtics", "Oklahoma City Thunder", "Denver Nuggets", "Cleveland Cavaliers",
                       "New York Knicks", "Minnesota Timberwolves", "Milwaukee Bucks", "Dallas Mavericks",
                       "Los Angeles Lakers", "Golden State Warriors", "Phoenix Suns", "Miami Heat", "Indiana Pacers",
                       "Houston Rockets", "Memphis Grizzlies", "Sacramento Kings"],
    "americanfootball_ncaaf": ["Georgia Bulldogs", "Ohio State Buckeyes", "Texas Longhorns", "Alabama Crimson Tide",
                               "Oregon Ducks", "Penn State Nittany Lions", "Notre Dame Fighting Irish",
                               "Michigan Wolverines", "LSU Tigers", "Ole Miss Rebels", "Miami Hurricanes",
                               "Clemson Tigers", "Tennessee Volunteers", "USC Trojans"],
    "basketball_ncaab": ["Duke Blue Devils", "UConn Huskies", "Houston Cougars", "Kansas Jayhawks",
                         "Purdue Boilermakers", "Auburn Tigers", "Gonzaga Bulldogs", "Kentucky Wildcats",
                         "North Carolina Tar Heels", "Arizona Wildcats", "Baylor Bears", "Tennessee Volunteers"],
    "icehockey_nhl": ["Florida Panthers", "Edmonton Oilers", "Dallas Stars", "Colorado Avalanche",
                      "New York Rangers", "Carolina Hurricanes", "Vegas Golden Knights", "Toronto Maple Leafs",
                      "Winnipeg Jets", "Boston Bruins"],
    "soccer_germany_bundesliga": ["Bayern Munich", "Bayer Leverkusen", "Borussia Dortmund", "RB Leipzig",
                                  "VfB Stuttgart", "Eintracht Frankfurt", "SC Freiburg", "Werder Bremen"],
    "tennis_atp_paris_masters": ["Jannik Sinner", "Carlos Alcaraz", "Alexander Zverev", "Novak Djokovic",
                                 "Taylor Fritz", "Daniil Medvedev", "Casper Ruud", "Alex de Minaur",
                                 "Holger Rune", "Ben Shelton", "Jack Draper", "Tommy Paul"],
}

TITLES = {
    "soccer_epl": "EPL", "soccer_efl_champ": "Championship", "soccer_england_league1": "League 1",
    "soccer_england_league2": "League 2", "americanfootball_nfl": "NFL", "basketball_nba": "NBA",
    "americanfootball_ncaaf": "NCAAF", "basketball_ncaab": "NCAAB", "icehockey_nhl": "NHL",
    "soccer_germany_bundesliga": "Bundesliga - Germany", "tennis_atp_paris_masters": "ATP Paris Masters",
}

US_CFG = {  # margin sd, rating spread, avg total, total sd
    "americanfootball_nfl": (13.5, 6.0, 44.5, 10.5),
    "basketball_nba": (12.0, 6.5, 226.0, 18.0),
    "americanfootball_ncaaf": (15.5, 12.0, 52.0, 14.5),
    "basketball_ncaab": (10.5, 8.0, 145.0, 13.5),
}

SHARP_BOOKS = [("pinnacle", "Pinnacle", 0.02, 0.025), ("betfair_ex_eu", "Betfair", 0.025, 0.012)]
SOFT_BOOKS = [("williamhill", "William Hill", 0.06, 0.06), ("unibet_eu", "Unibet", 0.05, 0.055),
              ("betclic", "Betclic", 0.06, 0.065), ("marathonbet", "Marathon Bet", 0.05, 0.045),
              ("sport888", "888sport", 0.06, 0.07), ("betsson", "Betsson", 0.05, 0.06)]
BET365 = ("bet365", "Bet365", 0.06, 0.05)


def demo_sport_keys(group: str) -> list[str]:
    return {
        "football_eng": ["soccer_epl", "soccer_efl_champ", "soccer_england_league1", "soccer_england_league2"],
        "nfl": ["americanfootball_nfl"], "nba": ["basketball_nba"], "ncaaf": ["americanfootball_ncaaf"],
        "ncaab": ["basketball_ncaab"],
        "other": ["icehockey_nhl", "soccer_germany_bundesliga", "tennis_atp_paris_masters"],
    }.get(group, [])


def _rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _logit(p):
    return math.log(p / (1 - p))


def _price_market(truth: dict[str, float], rng: random.Random, noise: float, margin: float) -> dict[str, float]:
    """Perturb true probabilities, renormalise, then add the bookmaker margin."""
    view = {s: 1 / (1 + math.exp(-(_logit(min(max(p, 0.005), 0.995)) + rng.gauss(0, noise)))) for s, p in truth.items()}
    tot = sum(view.values())
    view = {s: v / tot for s, v in view.items()}
    # Real books load more margin onto longshots: implied = p^k with sum = 1 + margin.
    lo, hi = 0.5, 1.0
    for _ in range(50):
        k = (lo + hi) / 2
        if sum(v ** k for v in view.values()) > 1 + margin:
            lo = k
        else:
            hi = k
    return {s: max(round(1 / (v ** k), 2), 1.01) for s, v in view.items()}


def _noisy(probs: dict[str, float], rng: random.Random, noise: float) -> dict[str, float]:
    view = {s: 1 / (1 + math.exp(-(_logit(min(max(p, 0.005), 0.995)) + rng.gauss(0, noise)))) for s, p in probs.items()}
    tot = sum(view.values())
    return {s: v / tot for s, v in view.items()}


def generate(sport_key: str, now: datetime | None = None, n_events: int | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    teams = TEAMS.get(sport_key)
    if not teams:
        return []
    rng = _rng(sport_key, day)
    strength = {t: rng.gauss(0, 1) for t in teams}
    order = teams[:]
    rng.shuffle(order)
    n = n_events or len(order) // 2
    events = []
    for i in range(n):
        home, away = order[2 * i], order[2 * i + 1]
        start = (now + timedelta(hours=6 + rng.randint(0, 120))).replace(minute=rng.choice([0, 15, 30, 45]),
                                                                          second=0, microsecond=0)
        eid = hashlib.md5(f"{sport_key}{day}{home}{away}".encode()).hexdigest()[:20]
        lines, model = _truth(sport_key, strength[home], strength[away], rng)
        events.append(_event(eid, sport_key, home, away, start, lines, model, rng))
    return events


def _truth(sport_key: str, sh: float, sa: float, rng: random.Random):
    """Returns ({(market, point): {side: prob}}, model_truth_dict)."""
    lines: dict = {}
    model: dict = {}
    if sport_key.startswith("soccer_"):
        lh = math.exp(math.log(1.35) + 0.22 + 0.32 * (sh - sa))
        la = math.exp(math.log(1.35) + 0.32 * (sa - sh))
        # The model sees slightly wrong goal rates.
        mlh, mla = lh * math.exp(rng.gauss(0, 0.08)), la * math.exp(rng.gauss(0, 0.08))
        true_m, model_m = score_matrix(lh, la, -0.06), score_matrix(mlh, mla, -0.06)
        ah = -round((lh - la) * 0.9 * 4) / 4
        for market, sides, pt in (("h2h", ("home", "draw", "away"), None), ("totals", ("over", "under"), 2.5),
                                  ("spreads", ("home", "away"), ah), ("btts", ("yes", "no"), None)):
            pts = {s: (pt if market != "spreads" else (pt if s == "home" else -pt)) for s in sides}
            lines[(market, pt)] = {s: matrix_prob(true_m, market, s, pts[s]) for s in sides}
            for s in sides:
                model[_demo_key(market, s, pts[s])] = matrix_prob(model_m, market, s, pts[s])
        model["lambdas"] = (mlh, mla)
        return lines, model
    if sport_key in US_CFG:
        msd, spread, avg_total, tsd = US_CFG[sport_key]
        hfa = 2.0
        mean = hfa + spread * (sh - sa) / 1.4
        total = avg_total + rng.gauss(0, tsd / 3)
        mmean, mtotal = mean + rng.gauss(0, 1.5), total + rng.gauss(0, 2.0)
        line = -round(mean * 2) / 2
        if line == int(line):
            line += 0.5 if mean < 0 else -0.5
        tline = round(total) + 0.5
        p_home = 1 - maths.normal_cdf(0, mean, msd)
        lines[("h2h", None)] = {"home": p_home, "away": 1 - p_home}
        md = discrete_normal(mean, msd)
        p_cover = maths.effective_prob(*maths.settle_margin(md, line))
        lines[("spreads", line)] = {"home": p_cover, "away": 1 - p_cover}
        p_over = 1 - maths.normal_cdf(tline, total, tsd)
        lines[("totals", tline)] = {"over": p_over, "under": 1 - p_over}
        mp_home = 1 - maths.normal_cdf(0, mmean, msd)
        mp_cover = maths.effective_prob(*maths.settle_margin(discrete_normal(mmean, msd), line))
        mp_over = 1 - maths.normal_cdf(tline, mtotal, tsd)
        model.update({_demo_key("h2h", "home", None): mp_home, _demo_key("h2h", "away", None): 1 - mp_home,
                      _demo_key("spreads", "home", line): mp_cover, _demo_key("spreads", "away", -line): 1 - mp_cover,
                      _demo_key("totals", "over", tline): mp_over, _demo_key("totals", "under", tline): 1 - mp_over})
        return lines, model
    # Two-way h2h (tennis, NHL incl. OT): no model, market only.
    p = 1 / (1 + math.exp(-(0.9 * (sh - sa) + (0.15 if sport_key.startswith("icehockey") else 0))))
    lines[("h2h", None)] = {"home": p, "away": 1 - p}
    return lines, {}


def _event(eid, sport_key, home, away, start, lines, model, rng) -> dict:
    names = {"home": home, "away": away, "draw": "Draw", "over": "Over", "under": "Under", "yes": "Yes", "no": "No"}
    books = list(SHARP_BOOKS) + rng.sample(SOFT_BOOKS, k=rng.randint(3, len(SOFT_BOOKS)))
    if rng.random() < 0.6:
        books.append(BET365)
    bookmakers = []
    for key, title, noise, margin in books:
        markets = []
        for (market, pt), truth in lines.items():
            prices = _price_market(truth, rng, noise, margin)
            outcomes = []
            for s, price in prices.items():
                oc = {"name": names[s], "price": price}
                if market == "spreads":
                    oc["point"] = pt if s == "home" else -pt
                elif market == "totals":
                    oc["point"] = pt
                outcomes.append(oc)
            if market == "btts":
                continue  # BTTS only comes via the per-event endpoint (props), like the real API
            markets.append({"key": market, "outcomes": outcomes})
        bookmakers.append({"key": key, "title": title, "markets": markets})
    return {
        "id": eid, "sport_key": sport_key, "sport_title": TITLES.get(sport_key, sport_key),
        "commence_time": start.isoformat().replace("+00:00", "Z"), "home_team": home, "away_team": away,
        "bookmakers": bookmakers, "_demo_truth": model or None,
        "_demo_btts": lines.get(("btts", None)),
    }


PLAYER_STUBS = {
    "americanfootball_nfl": [("player_pass_yds", 245.5, 38), ("player_rush_yds", 62.5, 22),
                             ("player_reception_yds", 54.5, 21)],
    "basketball_nba": [("player_points", 24.5, 6.5), ("player_rebounds", 8.5, 3.0), ("player_assists", 6.5, 2.5)],
    "americanfootball_ncaaf": [("player_pass_yds", 255.5, 45), ("player_rush_yds", 75.5, 28)],
    "basketball_ncaab": [("player_points", 16.5, 5.0), ("player_rebounds", 7.5, 2.8)],
}


def props(event: dict) -> dict:
    """Synthetic per-event markets: player props for US sports, BTTS for football."""
    rng = _rng("props", event["id"])
    sk = event["sport_key"]
    bookmakers = []
    lines = []
    if sk.startswith("soccer_") and event.get("_demo_btts"):
        lines.append(("btts", None, None, event["_demo_btts"]))
    for market, base, sd in PLAYER_STUBS.get(sk, []):
        for team in (event["home_team"], event["away_team"]):
            player = f"{team.split()[-1]} {('QB' if 'pass' in market else 'Star')} #{rng.randint(1, 99)}"
            pt = round(base + rng.gauss(0, sd / 3)) + 0.5
            p_over = 1 - maths.normal_cdf(pt, base + rng.gauss(0, sd / 4), sd)
            lines.append((market, pt, player, {"over": p_over, "under": 1 - p_over}))
    books = list(SHARP_BOOKS[:1]) + rng.sample(SOFT_BOOKS, k=3) + ([BET365] if rng.random() < 0.5 else [])
    for key, title, noise, margin in books:
        mkts: dict[str, list] = {}
        for market, pt, player, truth in lines:
            prices = _price_market(truth, rng, noise, margin)
            for s, price in prices.items():
                oc = {"name": s.title(), "price": price}
                if pt is not None:
                    oc["point"] = pt
                if player:
                    oc["description"] = player
                mkts.setdefault(market, []).append(oc)
        bookmakers.append({"key": key, "title": title,
                           "markets": [{"key": k, "outcomes": v} for k, v in mkts.items()]})
    return {**{k: v for k, v in event.items() if k != "bookmakers"}, "bookmakers": bookmakers}
