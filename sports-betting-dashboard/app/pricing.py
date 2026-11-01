"""Turns raw odds into priced selections: fair probability, bet365 price, edge and stake."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import median

from . import maths, sports
from .models.hub import ModelHub

# Books whose prices define the "true" market. Weight reflects how much we trust them.
SHARP_WEIGHTS = {
    "pinnacle": 1.0,
    "betfair_ex_uk": 0.8, "betfair_ex_eu": 0.8, "betfair_ex_au": 0.6,
    "matchbook": 0.6, "smarkets": 0.6, "lowvig": 0.4,
}
EXCHANGES = {"betfair_ex_uk", "betfair_ex_eu", "betfair_ex_au", "matchbook", "smarkets"}
BET365 = "bet365"
MAX_SHARP_OVERROUND = 0.06   # ignore illiquid exchange markets with a wide book

PROP_LABELS = {
    "player_pass_yds": "Passing yards", "player_rush_yds": "Rushing yards",
    "player_reception_yds": "Receiving yards", "player_receptions": "Receptions",
    "player_points": "Points", "player_rebounds": "Rebounds", "player_assists": "Assists",
    "player_threes": "3-pointers", "player_shots_on_target": "Shots on target",
}


def market_label(market: str, sport_key: str) -> str:
    fb = sports.is_football(sport_key)
    if market == "h2h":
        return "Match result" if fb else "Moneyline"
    if market == "spreads":
        return "Asian handicap" if fb else "Spread"
    if market == "totals":
        return "Total goals" if fb else "Total points"
    if market == "btts":
        return "Both teams to score"
    return PROP_LABELS.get(market, market.replace("player_", "").replace("_", " ").title())


def _side(market: str, outcome: dict, event: dict) -> str | None:
    name = outcome.get("name", "")
    if market in ("h2h", "spreads"):
        if name == event["home_team"]:
            return "home"
        if name == event["away_team"]:
            return "away"
        if name.lower() == "draw":
            return "draw"
        return None
    low = name.lower()
    return low if low in ("over", "under", "yes", "no") else None


def _line_key(market: str, side: str, point: float | None, desc: str | None):
    if market == "h2h" or market == "btts":
        return (market, None, desc)
    if market == "spreads":
        return (market, point if side == "home" else (-point if point is not None else None), desc)
    return (market, point, desc)


def _fmt_point(p: float) -> str:
    return f"{p:+g}"


def selection_label(market: str, side: str, point: float | None, event: dict, desc: str | None) -> str:
    team = {"home": event["home_team"], "away": event["away_team"]}.get(side)
    if market == "h2h":
        return team or "Draw"
    if market == "spreads":
        return f"{team} {_fmt_point(point)}"
    if market == "btts":
        return f"BTTS {side.title()}"
    if market == "totals":
        return f"{side.title()} {point:g}"
    return f"{desc}: {side.title()} {point:g}" if point is not None else f"{desc}: {side.title()}"


def sel_id(event_id: str, market: str, side: str, point: float | None, desc: str | None) -> str:
    return f"{event_id}|{market}|{side}|{'' if point is None else float(point) + 0.0}|{desc or ''}"


def collect_lines(event: dict) -> dict:
    """{line_key: {book_key: {side: (price, point)}}} across all bookmakers."""
    lines: dict = {}
    for bm in event.get("bookmakers", []):
        for mk in bm.get("markets", []):
            market = mk.get("key")
            for oc in mk.get("outcomes", []):
                side = _side(market, oc, event)
                price = oc.get("price")
                if side is None or not price or price <= 1.0:
                    continue
                point = oc.get("point")
                desc = oc.get("description")
                key = _line_key(market, side, point, desc)
                lines.setdefault(key, {}).setdefault(bm["key"], {})[side] = (float(price), point)
    return lines


def expected_sides(market: str, sport_key: str) -> tuple[str, ...]:
    if market == "h2h":
        return ("home", "draw", "away") if sports.three_way(sport_key) else ("home", "away")
    if market == "spreads":
        return ("home", "away")
    if market in ("totals",) or market.startswith("player_"):
        return ("over", "under")
    if market == "btts":
        return ("yes", "no")
    return ()


def analyse_event(event: dict, hub: ModelHub, settings: dict, manual: dict[str, float],
                  now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    sk = event["sport_key"]
    commence = event["commence_time"]
    if _parse_time(commence) <= now:
        return []
    group = sports.group_for(sk)
    handle = hub.event_model(event)
    conf = handle["confidence"] if handle else 0.0
    out: list[dict] = []
    lines = collect_lines(event)
    fair_by_line: dict = {}

    for (market, line, desc), books in lines.items():
        sides = expected_sides(market, sk)
        if not sides:
            continue
        # Drop books whose market has outcomes that don't belong (e.g. a 3-way regulation-time
        # ice hockey market listed under the 2-way moneyline): their prices aren't comparable.
        books = {b: px for b, px in books.items() if set(px) <= set(sides)}
        if not books:
            continue
        sharp: list[tuple[float, dict[str, float]]] = []
        allp: list[dict[str, float]] = []
        for book, prices in books.items():
            if not all(s in prices for s in sides):
                continue
            px = [prices[s][0] for s in sides]
            probs = dict(zip(sides, maths.devig_power(px)))
            allp.append(probs)
            if book in SHARP_WEIGHTS and maths.overround(px) <= MAX_SHARP_OVERROUND:
                sharp.append((SHARP_WEIGHTS[book], probs))
        if sharp:
            tw = sum(w for w, _ in sharp)
            market_p = {s: sum(w * p[s] for w, p in sharp) / tw for s in sides}
            source = "sharp"
            w_model = settings["model_weight_sharp"]
        elif allp:
            market_p = {s: sum(p[s] for p in allp) / len(allp) for s in sides}
            source = "consensus"
            w_model = settings["model_weight_soft"]
        else:
            market_p = None
            source = "model"
            w_model = 1.0

        model_p = None
        if handle and not market.startswith("player_"):
            vals = {}
            for s in sides:
                pt = _side_point(market, s, line)
                v = hub.prob(handle, market, s, pt)
                if v is None:
                    vals = None
                    break
                vals[s] = v
            if vals:
                tot = sum(vals.values())
                model_p = {s: v / tot for s, v in vals.items()}

        if market_p is None:
            # Prices exist but no book priced the whole market, so there is nothing to check
            # the model against. Skip rather than trust the model alone.
            continue
        elif model_p is None:
            fair = market_p
            w_used = 0.0
        else:
            w = w_used = w_model * conf * agreement(market_p, model_p)
            fair = {s: (1 - w) * market_p[s] + w * model_p[s] for s in sides}
            tot = sum(fair.values())
            fair = {s: v / tot for s, v in fair.items()}
        fair_by_line[(market, line, desc)] = fair

        for s in sides:
            pt = _side_point(market, s, line)
            sid = sel_id(event["id"], market, s, pt, desc)
            soft = sorted(((prices[s][0], b) for b, prices in books.items()
                           if s in prices and b not in SHARP_WEIGHTS and b not in EXCHANGES and b != BET365),
                          reverse=True)
            b365 = books.get(BET365, {}).get(s, (None,))[0]
            out.append({
                "sel_id": sid, "event_id": event["id"], "sport_key": sk,
                "sport_title": event.get("sport_title", sk), "group": group,
                "commence_time": commence, "home": event["home_team"], "away": event["away_team"],
                "event_name": f"{event['home_team']} v {event['away_team']}",
                "market": market, "market_label": market_label(market, sk), "side": s, "point": pt,
                "description": desc, "label": selection_label(market, s, pt, event, desc),
                "fair_prob": fair[s], "market_prob": market_p[s] if market_p else None,
                "model_prob": model_p[s] if model_p else None, "source": source,
                "model_weight": round(w_used, 3),
                "confidence": round(conf, 2),
                "bet365_price": b365, "manual_price": manual.get(sid),
                "best_price": soft[0][0] if soft else None, "best_book": soft[0][1] if soft else None,
                "median_price": median([p for p, _ in soft]) if soft else None, "n_books": len(books),
                "promo_bonus": 0.0,
            })

    # Model-only "price check" lines for football markets the bulk feed doesn't carry.
    if handle and handle["kind"] in ("football", "demo") and sports.is_football(sk):
        for market, line in (("btts", None), ("totals", 2.5)):
            if (market, line, None) in fair_by_line:
                continue
            sides = expected_sides(market, sk)
            vals = {s: hub.prob(handle, market, s, line if market == "totals" else None) for s in sides}
            if any(v is None for v in vals.values()):
                continue
            for s in sides:
                pt = line if market == "totals" else None
                sid = sel_id(event["id"], market, s, pt, None)
                out.append({
                    "sel_id": sid, "event_id": event["id"], "sport_key": sk,
                    "sport_title": event.get("sport_title", sk), "group": group, "commence_time": commence,
                    "home": event["home_team"], "away": event["away_team"],
                    "event_name": f"{event['home_team']} v {event['away_team']}",
                    "market": market, "market_label": market_label(market, sk), "side": s, "point": pt,
                    "description": None, "label": selection_label(market, s, pt, event, None),
                    "fair_prob": vals[s], "market_prob": None, "model_prob": vals[s], "source": "model",
                    "model_weight": 1.0, "confidence": round(conf, 2), "bet365_price": None,
                    "manual_price": manual.get(sid), "best_price": None, "best_book": None,
                    "median_price": None, "n_books": 0, "promo_bonus": 0.0,
                })

    # bet365 2-Goals-Ahead early payout on full-time result bets.
    if settings.get("early_payout") and sk in sports.EARLY_PAYOUT_KEYS:
        h2h = fair_by_line.get(("h2h", None, None))
        over = fair_by_line.get(("totals", 2.5, None))
        if h2h:
            for sel in out:
                if sel["market"] == "h2h" and sel["side"] in ("home", "away"):
                    bonus = hub.two_up(handle, sel["side"], h2h.get("home"), h2h.get("away"),
                                       over.get("over") if over else None)
                    sel["promo_bonus"] = bonus or 0.0

    for sel in out:
        finalise(sel, settings)
    return out


def agreement(market_p: dict[str, float], model_p: dict[str, float]) -> float:
    """Down-weight the model when it contradicts the market badly. A big gap in log-odds
    almost always means the model is wrong (stale ratings, a mismatched team), not the market."""
    gap = max(abs(_logit(market_p[s]) - _logit(model_p[s])) for s in market_p)
    return math.exp(-(gap / 0.8) ** 2)


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def finalise(sel: dict, settings: dict) -> dict:
    """Pick the comparison price and compute edge and stake."""
    if sel["manual_price"]:
        ref, ref_src = sel["manual_price"], "you"
    elif sel["bet365_price"]:
        ref, ref_src = sel["bet365_price"], "bet365"
    elif settings.get("proxy_price") == "best" and sel["best_price"]:
        ref, ref_src = sel["best_price"], f"est ({sel['best_book']})"
    elif sel["median_price"]:
        ref, ref_src = sel["median_price"], "est (market)"
    else:
        ref, ref_src = None, None
    p = sel["fair_prob"]
    eff = min(p + sel.get("promo_bonus", 0.0), 0.999)
    sel["eff_prob"] = eff
    sel["fair_odds"] = maths.fair_odds(p)
    sel["min_price"] = maths.min_price_for_edge(eff, settings["min_edge"])
    sel["ref_price"] = ref
    sel["ref_source"] = ref_src
    sel["confirmed"] = ref_src in ("you", "bet365")
    if ref:
        sel["edge"] = maths.ev(eff, ref)
        sel["stake"] = maths.stake(eff, ref, settings["bankroll"], settings["kelly_fraction"],
                                   settings["max_stake_pct"])
    else:
        sel["edge"] = None
        sel["stake"] = 0.0
    return sel


def _side_point(market: str, side: str, line):
    if market == "spreads":
        return line if side == "home" else (-line if line is not None else None)
    if market in ("h2h", "btts"):
        return None
    return line


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
