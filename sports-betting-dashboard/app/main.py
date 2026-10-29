"""FastAPI app: JSON API + static dashboard + background auto-refresh."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import acca, budget, config, db, sports, tracker
from .service import Service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dashboard")

service = Service()


async def auto_refresh_loop():
    while True:
        await asyncio.sleep(60)
        try:
            s = db.get_settings()
            if not s["auto_refresh"]:
                continue
            if time.time() - budget.last_auto_refresh() < s["refresh_interval_hours"] * 3600:
                continue
            budget.mark_auto_refresh()
            result = await asyncio.to_thread(service.refresh, s["refresh_groups"], False, True)
            log.info("auto-refresh: %s", {k: result.get(k) for k in ("ok", "cost", "reason")})
        except Exception:
            log.exception("auto-refresh failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    asyncio.get_running_loop().run_in_executor(None, service.warm_cached)
    task = asyncio.create_task(auto_refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="Value Betting Dashboard", lifespan=lifespan)
STATIC = config.BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------- status / settings ----------

@app.get("/api/status")
def status():
    s = db.get_settings()
    odds = db.load_all_odds()
    last = {}
    for key, data in odds.items():
        g = sports.group_for(key)
        last[g] = max(last.get(g, 0), data["fetched_at"])
    return {
        "demo": service.demo,
        "groups": sports.GROUPS,
        "budget": budget.status(s),
        "last_refresh": last,
        "auto_refresh": {"enabled": s["auto_refresh"], "interval_hours": s["refresh_interval_hours"],
                         "groups": s["refresh_groups"], "last": budget.last_auto_refresh()},
        "models": service.hub.status(),
        "refresh_log": service.refresh_log[:5],
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.post("/api/settings")
def post_settings(values: dict):
    return db.update_settings(values)


# ---------- refresh ----------

class RefreshReq(BaseModel):
    groups: list[str]
    sport_keys: list[str] | None = None
    force: bool = False
    dry_run: bool = False


@app.post("/api/refresh")
def refresh(req: RefreshReq):
    unknown = set(req.groups) - set(sports.GROUPS)
    if unknown:
        raise HTTPException(400, f"Unknown groups: {sorted(unknown)}")
    if req.dry_run:
        s = db.get_settings()
        plan = service.plan(req.groups, s)
        cost = sum(p["cost"] for p in plan if p["events"] != 0)
        ok, reason = budget.can_spend(cost, s)
        return {"plan": plan, "cost": cost, "affordable": ok, "reason": reason}
    return service.refresh(req.groups, force=req.force, sport_keys=req.sport_keys)


@app.post("/api/models/rebuild")
def rebuild_models():
    service.hub.rebuild()
    service.warm_cached()
    return service.hub.status()


# ---------- value bets ----------

@app.get("/api/value")
def value(group: str | None = None, market: str | None = None, min_edge: float | None = None,
          show_all: bool = False, confirmed_only: bool = False, limit: int = 500):
    s = db.get_settings()
    threshold = s["min_edge"] if min_edge is None else min_edge
    rows = []
    for sel in service.selections():
        if group and sel["group"] != group:
            continue
        if market and not (sel["market"] == market or (market == "props" and sel["market"].startswith("player_"))):
            continue
        if confirmed_only and not sel["confirmed"]:
            continue
        if not show_all and (sel["edge"] is None or sel["edge"] < threshold):
            continue
        rows.append(sel)
    return {"rows": rows[:limit], "total": len(rows), "min_edge": threshold,
            "events": service.events_index()}


class ManualPrice(BaseModel):
    sel_id: str
    price: float | None


@app.post("/api/manual_price")
def manual_price(req: ManualPrice):
    if req.price is not None and req.price <= 1.0:
        raise HTTPException(400, "Decimal odds must be above 1.0")
    db.set_manual_price(req.sel_id, req.price)
    sel = next((x for x in service.selections() if x["sel_id"] == req.sel_id), None)
    return {"ok": True, "selection": sel}


@app.post("/api/props/{event_id}")
def props(event_id: str, force: bool = False):
    return service.fetch_props(event_id, force=force)


# ---------- acca builder ----------

class AccaReq(BaseModel):
    legs: int = 3
    groups: list[str] | None = None
    markets: list[str] | None = None
    min_odds: float = 1.2
    max_odds: float = 5.0
    start: str | None = None
    end: str | None = None
    use_boost: bool = True
    use_early_payout: bool = True
    confirmed_only: bool = False
    n_results: int = 3


@app.post("/api/acca")
def build_acca(req: AccaReq):
    if not 2 <= req.legs <= 14:
        raise HTTPException(400, "Legs must be between 2 and 14")
    s = db.get_settings()
    return acca.build(service.selections(), s, req.legs, req.groups, req.markets, req.min_odds, req.max_odds,
                      req.start, req.end, req.use_boost and s["acca_boost"],
                      req.use_early_payout and s["early_payout"], req.confirmed_only, req.n_results)


# ---------- bet tracker ----------

class BetReq(BaseModel):
    kind: str = "single"
    description: str
    sport_group: str | None = None
    commence_time: str | None = None
    odds: float
    stake: float
    fair_prob: float | None = None
    legs: list[dict] = []


@app.get("/api/bets")
def bets():
    rows = tracker.bets_with_clv()
    return {"bets": rows, "summary": tracker.summary(rows)}


@app.post("/api/bets")
def add_bet(req: BetReq):
    if req.odds <= 1 or req.stake <= 0:
        raise HTTPException(400, "Odds must be > 1 and stake > 0")
    return {"id": db.add_bet(req.model_dump())}


class SettleReq(BaseModel):
    status: str


@app.post("/api/bets/{bet_id}/settle")
def settle_bet(bet_id: int, req: SettleReq):
    if req.status not in ("open", *tracker.RESULT_MULT):
        raise HTTPException(400, "Invalid status")
    try:
        tracker.settle(bet_id, req.status)
    except KeyError:
        raise HTTPException(404, "Bet not found")
    return {"ok": True}


@app.delete("/api/bets/{bet_id}")
def delete_bet(bet_id: int):
    db.delete_bet(bet_id)
    return {"ok": True}
