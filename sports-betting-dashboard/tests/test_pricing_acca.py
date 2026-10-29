from datetime import datetime, timedelta, timezone

import pytest

from app import acca, config, db, pricing
from app.models.hub import ModelHub

SETTINGS = dict(config.DEFAULT_SETTINGS)


def _event(eid="e1", sport="basketball_nba", hours=24, pin=(1.80, 2.10), b365=(1.95, 1.90), soft=(1.85, 2.00)):
    start = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    def book(key, prices):
        return {"key": key, "title": key, "markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": prices[0]}, {"name": "Away", "price": prices[1]}]}]}
    return {"id": eid, "sport_key": sport, "sport_title": "NBA", "commence_time": start, "home_team": "Home",
            "away_team": "Away", "bookmakers": [book("pinnacle", pin), book("bet365", b365), book("williamhill", soft),
                                                book("unibet_eu", soft)]}


def test_sharp_devig_and_bet365_edge():
    hub = ModelHub(demo=False)
    sels = pricing.analyse_event(_event(), hub, SETTINGS, {})
    home = next(s for s in sels if s["side"] == "home")
    fair = pricing.maths.devig_power([1.80, 2.10])[0]
    assert home["source"] == "sharp"
    assert home["fair_prob"] == pytest.approx(fair)
    assert home["ref_source"] == "bet365" and home["ref_price"] == 1.95
    assert home["edge"] == pytest.approx(fair * 1.95 - 1)
    assert home["edge"] > 0.03 and home["stake"] > 0
    away = next(s for s in sels if s["side"] == "away")
    assert away["edge"] < 0 and away["stake"] == 0


def test_manual_price_overrides_feed():
    hub = ModelHub(demo=False)
    sid = pricing.sel_id("e1", "h2h", "away", None, None)
    sels = pricing.analyse_event(_event(), hub, SETTINGS, {sid: 2.30})
    away = next(s for s in sels if s["side"] == "away")
    assert away["ref_source"] == "you" and away["confirmed"] and away["edge"] > 0


def test_started_events_skipped():
    assert pricing.analyse_event(_event(hours=-1), ModelHub(demo=False), SETTINGS, {}) == []


def test_consensus_when_no_sharp_book():
    ev = _event()
    ev["bookmakers"] = [b for b in ev["bookmakers"] if b["key"] != "pinnacle"]
    sels = pricing.analyse_event(ev, ModelHub(demo=False), SETTINGS, {})
    assert {s["source"] for s in sels} == {"consensus"}


def _sel(eid, p, price, sport="soccer_epl", market="h2h", hours=24):
    start = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {"sel_id": f"{eid}|x", "event_id": eid, "sport_key": sport, "sport_title": "EPL", "group": "football_eng",
            "commence_time": start, "event_name": eid, "market": market, "market_label": "Match result",
            "label": eid, "fair_prob": p, "eff_prob": p, "ref_price": price, "ref_source": "bet365",
            "confirmed": True, "promo_bonus": 0.0, "min_price": 1.0, "fair_odds": 1 / p}


def test_acca_picks_best_legs_one_per_event():
    sels = [_sel("a", 0.55, 2.0), _sel("b", 0.50, 2.1), _sel("c", 0.45, 2.0), _sel("d", 0.60, 1.8),
            {**_sel("a", 0.40, 2.6), "sel_id": "a|y"}]
    res = acca.build(sels, SETTINGS, legs=2, use_boost=False)
    best = res["accas"][0]
    assert {l["event_id"] for l in best["legs"]} == {"a", "d"}
    assert best["ev"] == pytest.approx(0.55 * 2.0 * 0.60 * 1.8 - 1)
    for a in res["accas"]:
        assert len({l["event_id"] for l in a["legs"]}) == 2


def test_acca_boost_applies_to_football_only():
    sels = [_sel("a", 0.55, 2.0), _sel("b", 0.55, 2.0), _sel("c", 0.55, 2.0)]
    res = acca.build(sels, SETTINGS, legs=3, use_boost=True)
    a = res["accas"][0]
    assert a["boost"] == 0.10
    assert a["payout_odds"] == pytest.approx(1 + 7 * 1.10)
    nba = [_sel(x, 0.55, 2.0, sport="basketball_nba") for x in "abc"]
    assert acca.build(nba, SETTINGS, legs=3, use_boost=True)["accas"][0]["boost"] == 0.0


def test_acca_not_enough_events():
    res = acca.build([_sel("a", 0.5, 2.1)], SETTINGS, legs=3)
    assert res["accas"] == [] and "Only 1" in res["message"]


def test_acca_flags_negative_ev():
    sels = [_sel(x, 0.49, 2.0) for x in "abc"]
    res = acca.build(sels, SETTINGS, legs=2, use_boost=False)
    assert not res["accas"][0]["positive"] and res["message"]


def test_budget_blocks_overspend():
    from app import budget
    db.log_usage("odds", "soccer_epl", 10, 30, 470)
    ok, reason = budget.can_spend(10, SETTINGS)
    assert not ok and "reserve" in reason
    db.log_usage("odds", "soccer_epl", 1, 400, 100)
    assert budget.can_spend(10, SETTINGS)[0]
