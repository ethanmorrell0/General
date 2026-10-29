"""Client for The Odds API (v4) with usage tracking.

Costs (per the API docs): /sports and /events are free; /odds costs markets x regions;
event-level /odds (props, BTTS) costs markets x regions per event. Responses with no
events cost nothing.
"""
from __future__ import annotations

import httpx

from . import config, db


class OddsAPIError(RuntimeError):
    pass


class OddsAPI:
    def __init__(self, api_key: str | None = None, base: str | None = None):
        self.key = api_key or config.ODDS_API_KEY
        self.base = base or config.ODDS_API_BASE
        self.client = httpx.Client(timeout=30)

    def _get(self, path: str, params: dict, endpoint: str, sport_key: str | None = None):
        r = self.client.get(f"{self.base}{path}", params={"apiKey": self.key, **params})
        remaining = _int(r.headers.get("x-requests-remaining"))
        used = _int(r.headers.get("x-requests-used"))
        last = _int(r.headers.get("x-requests-last")) or 0
        if remaining is not None or last:
            db.log_usage(endpoint, sport_key, last, remaining, used)
        if r.status_code == 401:
            raise OddsAPIError("The Odds API rejected the key (401). Check ODDS_API_KEY in .env.")
        if r.status_code == 429:
            raise OddsAPIError("Out of API credits or rate-limited (429).")
        if r.status_code == 422:
            raise OddsAPIError(f"Bad request for {sport_key}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()

    def sports(self) -> list[dict]:
        cached = db.kv_get("oddsapi:sports", max_age=6 * 3600)
        if cached is not None:
            return cached
        data = self._get("/sports", {}, "sports")
        db.kv_set("oddsapi:sports", data)
        return data

    def events(self, sport_key: str) -> list[dict]:
        key = f"oddsapi:events:{sport_key}"
        cached = db.kv_get(key, max_age=1800)
        if cached is not None:
            return cached
        try:
            data = self._get(f"/sports/{sport_key}/events", {"dateFormat": "iso"}, "events", sport_key)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                data = []
            else:
                raise
        db.kv_set(key, data)
        return data

    def odds(self, sport_key: str, markets: list[str], regions: str,
             commence_from: str | None = None, commence_to: str | None = None) -> list[dict]:
        params = {"regions": regions, "markets": ",".join(markets), "oddsFormat": "decimal", "dateFormat": "iso"}
        if commence_from:
            params["commenceTimeFrom"] = commence_from
        if commence_to:
            params["commenceTimeTo"] = commence_to
        return self._get(f"/sports/{sport_key}/odds", params, "odds", sport_key)

    def event_odds(self, sport_key: str, event_id: str, markets: list[str], regions: str) -> dict:
        params = {"regions": regions, "markets": ",".join(markets), "oddsFormat": "decimal", "dateFormat": "iso"}
        return self._get(f"/sports/{sport_key}/events/{event_id}/odds", params, "event_odds", sport_key)


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# Two-way player props (Over/Under) and event-only markets per sport.
PROP_MARKETS = {
    "americanfootball_nfl": ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions"],
    "americanfootball_ncaaf": ["player_pass_yds", "player_rush_yds", "player_reception_yds"],
    "basketball_nba": ["player_points", "player_rebounds", "player_assists", "player_threes"],
    "basketball_ncaab": ["player_points", "player_rebounds", "player_assists"],
    "soccer": ["btts", "player_shots_on_target"],
}


def prop_markets_for(sport_key: str) -> list[str]:
    if sport_key.startswith("soccer_"):
        return PROP_MARKETS["soccer"]
    return PROP_MARKETS.get(sport_key, [])
