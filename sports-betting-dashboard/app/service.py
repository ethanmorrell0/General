"""Orchestration: refresh odds within the credit budget, analyse, fetch props, track CLV."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import budget, config, db, demo, pricing, sports
from .models.hub import ModelHub
from .odds_api import OddsAPI, OddsAPIError, prop_markets_for

log = logging.getLogger(__name__)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Service:
    def __init__(self, demo_mode: bool | None = None):
        self.demo = config.DEMO_MODE if demo_mode is None else demo_mode
        self.api = None if self.demo else OddsAPI()
        self.hub = ModelHub(demo=self.demo)
        self._cache: tuple[str, list[dict]] | None = None
        self._lock = threading.Lock()
        self.refresh_log: list[dict] = []

    # ---------- planning & refresh ----------

    def live_sports(self) -> list[dict] | None:
        if self.demo:
            return None
        try:
            return self.api.sports()
        except Exception as e:
            log.warning("sports list failed: %s", e)
            return None

    def plan(self, groups: list[str], settings: dict) -> list[dict]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=settings["lookahead_hours"])
        regions = [r for r in settings["regions"].split(",") if r.strip()]
        out = []
        live = self.live_sports()
        titles = {s["key"]: s.get("title", s["key"]) for s in (live or [])}
        for group in groups:
            keys = demo.demo_sport_keys(group) if self.demo else sports.scan_keys(group, settings, live)
            markets = settings["markets_by_group"].get(group, ["h2h"])
            for key in keys:
                n_events = None
                if not self.demo:
                    try:
                        evs = self.api.events(key)
                        n_events = sum(1 for e in evs if now < pricing._parse_time(e["commence_time"]) <= horizon)
                    except Exception as e:
                        log.info("events %s failed: %s", key, e)
                mk = markets if not key.startswith("tennis_") else ["h2h"]
                cost = 0 if self.demo or n_events == 0 else len(mk) * len(regions)
                out.append({"sport_key": key, "title": titles.get(key) or demo.TITLES.get(key, key), "group": group,
                            "markets": mk, "events": n_events, "cost": cost})
        return out

    def refresh(self, groups: list[str], force: bool = False, auto: bool = False,
                sport_keys: list[str] | None = None) -> dict:
        settings = db.get_settings()
        plan = self.plan(groups, settings)
        if sport_keys:
            plan = [p for p in plan if p["sport_key"] in sport_keys]
        plan = [p for p in plan if p["events"] != 0]
        cost = sum(p["cost"] for p in plan)
        ok, reason = budget.can_spend(cost, settings, auto=auto)
        if not ok and not force:
            return {"ok": False, "reason": reason, "cost": cost, "plan": plan}
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=settings["lookahead_hours"])
        fetched, errors = [], []
        for p in plan:
            try:
                if self.demo:
                    events = demo.generate(p["sport_key"], now)
                else:
                    events = self.api.odds(p["sport_key"], p["markets"], settings["regions"],
                                           commence_from=_iso(now), commence_to=_iso(horizon))
                db.save_odds(p["sport_key"], events, p["markets"])
                fetched.append({"sport_key": p["sport_key"], "events": len(events)})
            except OddsAPIError as e:
                errors.append(f"{p['sport_key']}: {e}")
                if "401" in str(e) or "429" in str(e):
                    break
            except Exception as e:
                errors.append(f"{p['sport_key']}: {e}")
        self._cache = None
        result = {"ok": not errors or bool(fetched), "cost": cost, "fetched": fetched, "errors": errors,
                  "at": time.time(), "auto": auto}
        self.refresh_log = ([result] + self.refresh_log)[:20]
        threading.Thread(target=self._warm, args=([p["sport_key"] for p in plan],), daemon=True).start()
        return result

    def _warm(self, keys: list[str]) -> None:
        self.hub.warm(keys)
        self._cache = None

    def warm_cached(self) -> None:
        """On startup, fit models for whatever is already in the odds cache."""
        self._warm(list(db.load_all_odds().keys()))

    # ---------- props / event markets ----------

    def find_event(self, event_id: str) -> dict | None:
        for data in db.load_all_odds().values():
            for ev in data["events"]:
                if ev["id"] == event_id:
                    return ev
        return None

    def prop_cost(self, sport_key: str, settings: dict) -> int:
        regions = [r for r in settings["props_regions"].split(",") if r.strip()]
        return len(prop_markets_for(sport_key)) * (0 if self.demo else len(regions))

    def fetch_props(self, event_id: str, force: bool = False) -> dict:
        settings = db.get_settings()
        ev = self.find_event(event_id)
        if not ev:
            return {"ok": False, "reason": "Event not found in the cache. Refresh odds first."}
        markets = prop_markets_for(ev["sport_key"])
        if not markets:
            return {"ok": False, "reason": "No extra markets available for this sport."}
        cost = self.prop_cost(ev["sport_key"], settings)
        ok, reason = budget.can_spend(cost, settings)
        if not ok and not force:
            return {"ok": False, "reason": reason, "cost": cost}
        try:
            data = demo.props(ev) if self.demo else self.api.event_odds(ev["sport_key"], event_id, markets,
                                                                        settings["props_regions"])
        except Exception as e:
            return {"ok": False, "reason": str(e), "cost": cost}
        db.save_props(event_id, ev["sport_key"], data)
        self._cache = None
        return {"ok": True, "cost": cost, "markets": markets}

    # ---------- analysis ----------

    def _signature(self, odds: dict, props: dict, settings: dict, manual: dict) -> str:
        parts = [json.dumps({k: v["fetched_at"] for k, v in odds.items()}, sort_keys=True),
                 json.dumps(sorted(props.keys())), json.dumps(settings, sort_keys=True),
                 json.dumps(manual, sort_keys=True), json.dumps(self.hub.status()["football"], sort_keys=True,
                                                                default=str),
                 json.dumps(self.hub.status()["ratings"], sort_keys=True, default=str),
                 datetime.now(timezone.utc).strftime("%Y%m%d%H%M")]
        return hashlib.md5("|".join(parts).encode()).hexdigest()

    def selections(self) -> list[dict]:
        settings = db.get_settings()
        odds = db.load_all_odds()
        props = db.load_props()
        manual = db.manual_prices()
        sig = self._signature(odds, props, settings, manual)
        with self._lock:
            if self._cache and self._cache[0] == sig:
                return self._cache[1]
            out: list[dict] = []
            now = datetime.now(timezone.utc)
            for data in odds.values():
                for ev in data["events"]:
                    full = _merge_props(ev, props.get(ev["id"]))
                    try:
                        out.extend(pricing.analyse_event(full, self.hub, settings, manual, now))
                    except Exception as e:  # one malformed event shouldn't sink the board
                        log.warning("analyse %s failed: %s", ev.get("id"), e)
            out.sort(key=lambda s: (s["edge"] is None, -(s["edge"] or 0)))
            db.record_fair_probs([(s["sel_id"], s["fair_prob"], s["commence_time"]) for s in out])
            self._cache = (sig, out)
            return out

    def events_index(self) -> dict[str, dict]:
        """Light event list for the UI (props buttons etc.)."""
        settings = db.get_settings()
        props = db.load_props()
        out = {}
        for sk, data in db.load_all_odds().items():
            for ev in data["events"]:
                out[ev["id"]] = {"id": ev["id"], "sport_key": sk, "name": f"{ev['home_team']} v {ev['away_team']}",
                                 "commence_time": ev["commence_time"], "group": sports.group_for(sk),
                                 "has_props": ev["id"] in props,
                                 "prop_cost": self.prop_cost(sk, settings) if prop_markets_for(sk) else None}
        return out


def _merge_props(event: dict, extra: dict | None) -> dict:
    if not extra:
        return event
    books = {b["key"]: {**b, "markets": list(b.get("markets", []))} for b in event.get("bookmakers", [])}
    for b in extra.get("bookmakers", []):
        if b["key"] in books:
            have = {m["key"] for m in books[b["key"]]["markets"]}
            books[b["key"]]["markets"].extend(m for m in b.get("markets", []) if m["key"] not in have)
        else:
            books[b["key"]] = b
    return {**event, "bookmakers": list(books.values())}
