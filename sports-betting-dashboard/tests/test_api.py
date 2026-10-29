"""End-to-end flow in demo mode."""
from fastapi.testclient import TestClient

ALL = ["football_eng", "nfl", "nba", "ncaaf", "ncaab", "other"]


def _client():
    from app import main
    from app.service import Service
    main.service = Service(demo_mode=True)
    return TestClient(main.app)


def test_full_flow():
    c = _client()
    with c:
        plan = c.post("/api/refresh", json={"groups": ALL, "dry_run": True}).json()
        assert plan["cost"] == 0 and plan["plan"]
        r = c.post("/api/refresh", json={"groups": ALL}).json()
        assert r["ok"] and sum(f["events"] for f in r["fetched"]) > 50

        allrows = c.get("/api/value?show_all=true").json()
        assert allrows["total"] > 300
        groups = {row["group"] for row in allrows["rows"]}
        assert groups == set(ALL)

        value = c.get("/api/value").json()
        for row in value["rows"]:
            assert row["edge"] >= value["min_edge"]

        # Manual bet365 price
        sel = next(r for r in allrows["rows"] if r["market"] == "h2h" and r["group"] == "nba")
        res = c.post("/api/manual_price", json={"sel_id": sel["sel_id"], "price": 50.0}).json()
        assert res["selection"]["ref_source"] == "you" and res["selection"]["edge"] > 0

        # Props
        ev = next(e for e in allrows["events"].values() if e["sport_key"] == "basketball_nba")
        assert c.post(f"/api/props/{ev['id']}").json()["ok"]
        props = c.get("/api/value?market=props&show_all=true").json()
        assert props["total"] > 0

        # Acca
        acca = c.post("/api/acca", json={"legs": 3, "groups": ["football_eng"], "max_odds": 3.0}).json()
        assert acca["accas"] and len(acca["accas"][0]["legs"]) == 3

        # Tracker
        bet_id = c.post("/api/bets", json={"description": "x", "odds": 2.5, "stake": 10, "fair_prob": 0.45,
                                           "legs": [{"sel_id": sel["sel_id"], "odds": 2.5}]}).json()["id"]
        c.post(f"/api/bets/{bet_id}/settle", json={"status": "won"})
        bets = c.get("/api/bets").json()
        assert bets["summary"]["profit"] == 15.0 and bets["bets"][0]["clv"] is not None

        # Settings round-trip
        s = c.post("/api/settings", json={"bankroll": 2500, "nonsense": 1}).json()
        assert s["bankroll"] == 2500 and "nonsense" not in s


def test_unknown_group_rejected():
    with _client() as c:
        assert c.post("/api/refresh", json={"groups": ["cricket"]}).status_code == 400
