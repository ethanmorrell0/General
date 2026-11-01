"""Loads results data, fits the models and answers probability queries for events."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx

from .. import db, sports
from ..teams import match_team
from . import football, ratings
from .early_payout import implied_lambdas, two_up_bonus

log = logging.getLogger(__name__)

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
REFIT_SECONDS = 12 * 3600
LOOKBACK_DAYS = 330


class ModelHub:
    def __init__(self, demo: bool = False):
        self.demo = demo
        self.football: dict[str, tuple[float, football.FootballModel]] = {}
        self.us: dict[str, tuple[float, ratings.RatingsModel]] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 betting-dashboard"})

    # ---------- data loading ----------

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        r = self._client.get(url, params=params)
        r.raise_for_status()
        return r

    def _fd_matches(self, pool: str) -> list[football.Match]:
        today = date.today()
        prev, cur = football.season_codes(today)
        matches: list[football.Match] = []
        for season in (prev, cur):
            for div in football.POOLS[pool]:
                key = f"fd:{season}:{div}"
                text = db.kv_get(key, max_age=None if season == prev else 12 * 3600)
                if text is None:
                    try:
                        text = self._get(football.FD_URL.format(season=season, div=div)).text
                        db.kv_set(key, text)
                    except httpx.HTTPError as e:
                        # A division may not exist for a season (e.g. not yet published).
                        log.info("football-data %s %s: %s", season, div, e)
                        stale = db.kv_get(key)
                        if stale is None:
                            if season == cur:
                                self.errors[f"football:{pool}"] = f"Couldn't download {div} {season}: {e}"
                            continue
                        text = stale
                matches.extend(football.parse_fd_csv(text))
        return matches

    def _espn_day(self, sport_key: str, day: date) -> dict | None:
        """One day's scoreboard. ESPN rejects date ranges, and `limit=1000` silently caps
        some sports at 25 games, so query single days with limit=300."""
        cfg = ratings.SPORT_CFG[sport_key]
        params = {"dates": f"{day:%Y%m%d}", "limit": 300}
        if cfg["groups"]:
            params["groups"] = cfg["groups"]
        payload = self._get(ESPN_URL.format(path=cfg["espn"]), params).json()
        # Keep only what the parser needs, so the cache stays small.
        return {"events": [{"date": e.get("date"), "status": e.get("status"),
                            "season": {"type": e.get("season", {}).get("type")},
                            "competitions": [{
                                "neutralSite": c.get("neutralSite"),
                                "status": c.get("status"),
                                "competitors": [{"homeAway": t.get("homeAway"), "score": t.get("score"),
                                                 "team": {"displayName": t.get("team", {}).get("displayName")}}
                                                for t in c.get("competitors", [])]}
                                for c in e.get("competitions", [])[:1]]}
                           for e in payload.get("events", [])]}

    def _espn_games(self, sport_key: str) -> list[ratings.Game]:
        today = date.today()
        days = [today - timedelta(days=i) for i in range(LOOKBACK_DAYS, -1, -1)]
        payloads: dict[date, dict] = {}
        missing = []
        for d in days:
            closed = d < today - timedelta(days=3)
            cached = db.kv_get(f"espn2:{sport_key}:{d:%Y%m%d}", max_age=None if closed else 6 * 3600)
            if cached is None:
                missing.append(d)
            else:
                payloads[d] = cached
        failures = 0

        def fetch(d: date):
            try:
                return d, self._espn_day(sport_key, d), None
            except (httpx.HTTPError, ValueError) as e:
                return d, None, e

        with ThreadPoolExecutor(max_workers=16) as pool:
            for d, payload, err in pool.map(fetch, missing):
                if payload is None:
                    failures += 1
                    self.errors[f"ratings:{sport_key}"] = f"ESPN fetch failed for {failures} day(s), e.g. {d}: {err}"
                    payload = db.kv_get(f"espn2:{sport_key}:{d:%Y%m%d}") or {"events": []}
                else:
                    db.kv_set(f"espn2:{sport_key}:{d:%Y%m%d}", payload)
                payloads[d] = payload
        if not failures:
            self.errors.pop(f"ratings:{sport_key}", None)
        games: list[ratings.Game] = []
        for d in days:
            games.extend(ratings.parse_espn(payloads.get(d, {"events": []})))
        return games

    # ---------- fitting ----------

    def football_model(self, pool: str) -> football.FootballModel | None:
        with self._lock:
            cached = self.football.get(pool)
            if cached and time.time() - cached[0] < REFIT_SECONDS:
                return cached[1]
            matches = self._fd_matches(pool)
            if not matches:
                return None
            model = football.fit_pool(matches, date.today(), football.POOLS[pool])
            self.football[pool] = (time.time(), model)
            self.errors.pop(f"football:{pool}", None)
            return model

    def ratings_model(self, sport_key: str) -> ratings.RatingsModel | None:
        if sport_key not in ratings.SPORT_CFG:
            return None
        with self._lock:
            cached = self.us.get(sport_key)
            if cached and time.time() - cached[0] < REFIT_SECONDS:
                return cached[1]
            games = self._espn_games(sport_key)
            if not games:
                return None
            model = ratings.fit(games, date.today(), sport_key)
            self.us[sport_key] = (time.time(), model)
            return model

    def warm(self, sport_keys: list[str]) -> None:
        """Fit every model the given sports need (network + CPU; call off the request path)."""
        if self.demo:
            return
        pools = {football.pool_for(k) for k in sport_keys} - {None}
        for pool in pools:
            try:
                self.football_model(pool)
            except Exception as e:  # never let a model failure break odds analysis
                self.errors[f"football:{pool}"] = str(e)
        for k in sport_keys:
            if k in ratings.SPORT_CFG:
                try:
                    self.ratings_model(k)
                except Exception as e:
                    self.errors[f"ratings:{k}"] = str(e)

    def rebuild(self) -> None:
        with self._lock:
            self.football.clear()
            self.us.clear()

    def status(self) -> dict:
        return {
            "demo": self.demo,
            "football": {p: {"teams": len(m.teams), "fitted": t, "home_adv": round(m.h, 3), "rho": m.rho}
                         for p, (t, m) in self.football.items()},
            "ratings": {k: {"teams": len(m.teams), "fitted": t, "hfa": round(m.hfa, 2)}
                        for k, (t, m) in self.us.items()},
            "errors": self.errors,
        }

    # ---------- queries ----------

    def _cached_football(self, sport_key: str):
        pool = football.pool_for(sport_key)
        entry = self.football.get(pool) if pool else None
        return entry[1] if entry else None

    def event_model(self, event: dict) -> dict | None:
        """Resolve an event to a model handle: {'kind', 'model', 'home', 'away', 'confidence'}.
        Only uses already-fitted models so request handling never blocks on the network."""
        sk = event["sport_key"]
        if self.demo:
            truth = event.get("_demo_truth")
            return {"kind": "demo", "truth": truth, "confidence": 0.8} if truth else None
        if sports.is_football(sk):
            m = self._cached_football(sk)
            if not m:
                return None
            h, a = match_team(event["home_team"], m.teams), match_team(event["away_team"], m.teams)
            if not h or not a:
                return None
            return {"kind": "football", "model": m, "home": h, "away": a, "confidence": m.confidence(h, a)}
        entry = self.us.get(sk)
        if entry:
            m = entry[1]
            h, a = match_team(event["home_team"], m.teams), match_team(event["away_team"], m.teams)
            if h and a:
                return {"kind": "ratings", "model": m, "home": h, "away": a, "confidence": m.confidence(h, a)}
        return None

    def prob(self, handle: dict, market: str, side: str, point: float | None) -> float | None:
        kind = handle["kind"]
        if kind == "demo":
            return handle["truth"].get(_demo_key(market, side, point))
        if kind == "football":
            if "_matrix" not in handle:
                m = handle["model"]
                handle["_lambdas"] = m.lambdas(handle["home"], handle["away"])
                handle["_matrix"] = m.score_matrix(*handle["_lambdas"])
            return football.matrix_prob(handle["_matrix"], market, side, point)
        if kind == "ratings":
            return handle["model"].prob(handle["home"], handle["away"], market, side, point)
        return None

    def two_up(self, handle: dict | None, side: str, fair_home: float | None, fair_away: float | None,
               p_over25: float | None) -> float | None:
        """Early-payout bonus probability, from the model's goal rates or the market's."""
        if handle and handle["kind"] == "football":
            if "_lambdas" not in handle:
                handle["_lambdas"] = handle["model"].lambdas(handle["home"], handle["away"])
            lh, la = handle["_lambdas"]
        elif handle and handle["kind"] == "demo" and "lambdas" in handle["truth"]:
            lh, la = handle["truth"]["lambdas"]
        elif fair_home is not None and fair_away is not None:
            lh, la = implied_lambdas(fair_home, fair_away, p_over25=p_over25)
        else:
            return None
        return two_up_bonus(lh, la, side)


def _demo_key(market: str, side: str, point: float | None) -> str:
    return f"{market}|{side}|{'' if point is None else float(point) + 0.0}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
