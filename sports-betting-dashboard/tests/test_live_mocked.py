"""Live-mode path against mocked Odds API / football-data / ESPN responses."""
from datetime import date, datetime, timedelta, timezone

import httpx

from app import db
from app.models.hub import ModelHub
from app.odds_api import OddsAPI
from app.service import Service

NOW = datetime.now(timezone.utc)
KO = (NOW + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _odds_event(eid, sport, home, away, books):
    return {"id": eid, "sport_key": sport, "sport_title": sport, "commence_time": KO, "home_team": home,
            "away_team": away, "bookmakers": [{"key": k, "title": k, "markets": m} for k, m in books.items()]}


EPL = _odds_event("ev1", "soccer_epl", "Manchester United", "Nottingham Forest", {
    "pinnacle": [{"key": "h2h", "outcomes": [{"name": "Manchester United", "price": 1.80},
                                            {"name": "Nottingham Forest", "price": 4.60},
                                            {"name": "Draw", "price": 3.90}]},
                 {"key": "totals", "outcomes": [{"name": "Over", "price": 1.95, "point": 2.5},
                                               {"name": "Under", "price": 1.93, "point": 2.5}]}],
    "williamhill": [{"key": "h2h", "outcomes": [{"name": "Manchester United", "price": 1.80},
                                               {"name": "Nottingham Forest", "price": 4.50},
                                               {"name": "Draw", "price": 3.70}]}],
    "unibet_eu": [{"key": "h2h", "outcomes": [{"name": "Manchester United", "price": 1.83},
                                             {"name": "Nottingham Forest", "price": 4.33},
                                             {"name": "Draw", "price": 3.75}]}],
})

NFL = _odds_event("ev2", "americanfootball_nfl", "Kansas City Chiefs", "Denver Broncos", {
    "pinnacle": [{"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": 1.50},
                                            {"name": "Denver Broncos", "price": 2.75}]},
                 {"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": 1.95, "point": -6.5},
                                                {"name": "Denver Broncos", "price": 1.91, "point": 6.5}]}],
    "betclic": [{"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": 2.02, "point": -6.5},
                                               {"name": "Denver Broncos", "price": 1.80, "point": 6.5}]}],
})


def _fd_csv():
    rows = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG"]
    teams = ["Man United", "Nott'm Forest", "Arsenal", "Chelsea"]
    d = date.today() - timedelta(days=60)
    for i in range(30):
        h, a = teams[i % 4], teams[(i + 1 + i // 4) % 4]
        if h == a:
            continue
        rows.append(f"E0,{d:%d/%m/%Y},{h},{a},{(i * 7) % 4},{(i * 3) % 3}")
        d += timedelta(days=2)
    return "\n".join(rows)


def _espn():
    evs = []
    d = date.today() - timedelta(days=40)
    for i in range(12):
        home, away = ("Kansas City Chiefs", "Denver Broncos") if i % 2 else ("Denver Broncos", "Kansas City Chiefs")
        evs.append({"date": f"{d.isoformat()}T18:00Z", "competitions": [{
            "neutralSite": False, "status": {"type": {"completed": True}},
            "competitors": [{"homeAway": "home", "score": str(24 + i % 5), "team": {"displayName": home}},
                            {"homeAway": "away", "score": str(17 + i % 3), "team": {"displayName": away}}]}]})
        d += timedelta(days=3)
    return {"events": evs}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    hdr = {"x-requests-remaining": "480", "x-requests-used": "20", "x-requests-last": "2"}
    if "the-odds-api" in url:
        if url.split("?")[0].endswith("/sports"):
            return httpx.Response(200, json=[
                {"key": "soccer_epl", "active": True, "has_outrights": False, "title": "EPL"},
                {"key": "americanfootball_nfl", "active": True, "has_outrights": False, "title": "NFL"},
                {"key": "soccer_efl_champ", "active": True, "has_outrights": False, "title": "Championship"}],
                headers={"x-requests-remaining": "480"})
        if "/events/" in url:
            return httpx.Response(200, json={**EPL, "bookmakers": [{"key": "pinnacle", "markets": [
                {"key": "btts", "outcomes": [{"name": "Yes", "price": 1.85}, {"name": "No", "price": 2.0}]}]}]},
                headers=hdr)
        if "/events" in url:
            sport = url.split("/sports/")[1].split("/")[0]
            evs = {"soccer_epl": [EPL], "americanfootball_nfl": [NFL]}.get(sport, [])
            return httpx.Response(200, json=[{k: e[k] for k in ("id", "commence_time")} for e in evs])
        if "/odds" in url:
            sport = url.split("/sports/")[1].split("/")[0]
            assert "commenceTimeTo" in url and "oddsFormat=decimal" in url
            return httpx.Response(200, json={"soccer_epl": [EPL], "americanfootball_nfl": [NFL]}[sport], headers=hdr)
    if "football-data" in url:
        return httpx.Response(200, text=_fd_csv()) if "/E0.csv" in url else httpx.Response(404)
    if "espn" in url:
        return httpx.Response(200, json=_espn() if "football/nfl" in url else {"events": []})
    return httpx.Response(404)


def _service():
    svc = Service(demo_mode=False)
    svc.api = OddsAPI(api_key="test")
    svc.api.client = httpx.Client(transport=httpx.MockTransport(handler))
    svc.hub = ModelHub(demo=False)
    svc.hub._client = httpx.Client(transport=httpx.MockTransport(handler))
    return svc


def test_live_refresh_models_and_pricing():
    svc = _service()
    plan = svc.plan(["football_eng", "nfl"], db.get_settings())
    by_key = {p["sport_key"]: p for p in plan}
    assert by_key["soccer_epl"]["cost"] == 2 and by_key["soccer_efl_champ"]["events"] == 0
    res = svc.refresh(["football_eng", "nfl"])
    assert res["ok"] and {f["sport_key"] for f in res["fetched"]} == {"soccer_epl", "americanfootball_nfl"}
    assert db.usage_summary()["remaining"] == 480

    svc.hub.warm(["soccer_epl", "americanfootball_nfl"])
    st = svc.hub.status()
    assert "ENG" in st["football"] and "americanfootball_nfl" in st["ratings"]

    sels = svc.selections()
    mu = next(s for s in sels if s["label"] == "Manchester United")
    assert mu["source"] == "sharp" and mu["model_prob"] is not None  # team names matched to football-data
    assert mu["promo_bonus"] > 0  # 2-Up applies to the EPL
    assert any(s["market"] == "btts" and s["source"] == "model" for s in sels)  # price-check line
    kc = next(s for s in sels if s["label"] == "Kansas City Chiefs -6.5")
    assert kc["model_prob"] is not None and kc["best_book"] == "betclic"

    out = svc.fetch_props("ev1")
    assert out["ok"] and out["cost"] == 4  # 2 soccer markets x 2 props regions
    sels = svc.selections()
    btts = next(s for s in sels if s["market"] == "btts" and s["side"] == "yes")
    assert btts["source"] == "sharp"
