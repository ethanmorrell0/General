"""Credit budgeting for The Odds API free tier."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import db


def _month_start(reset_day: int, now: datetime) -> datetime:
    y, m = now.year, now.month
    if now.day < reset_day:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return datetime(y, m, min(reset_day, 28), tzinfo=timezone.utc)


def _next_reset(reset_day: int, now: datetime) -> datetime:
    start = _month_start(reset_day, now)
    y, m = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return datetime(y, m, min(reset_day, 28), tzinfo=timezone.utc)


def status(settings: dict) -> dict:
    now = datetime.now(timezone.utc)
    usage = db.usage_summary()
    start = _month_start(settings["credit_reset_day"], now)
    with db.tx() as c:
        spent_month = c.execute("SELECT COALESCE(SUM(cost), 0) AS s FROM api_usage WHERE ts >= ?",
                                (start.timestamp(),)).fetchone()["s"]
    remaining = usage["remaining"]
    if remaining is None:
        remaining = max(settings["monthly_credits"] - spent_month, 0)
    days_left = max((_next_reset(settings["credit_reset_day"], now) - now).total_seconds() / 86400, 0.5)
    usable = max(remaining - settings["credit_reserve"], 0)
    daily = usable / days_left
    return {
        "remaining": remaining,
        "known": usage["remaining"] is not None,
        "spent_month": spent_month,
        "spent_24h": usage["spent_24h"],
        "days_to_reset": round(days_left, 1),
        "daily_allowance": round(daily, 1),
        "reserve": settings["credit_reserve"],
        "monthly": settings["monthly_credits"],
        "last_call": usage["last_call"],
    }


def can_spend(cost: int, settings: dict, auto: bool = False) -> tuple[bool, str]:
    st = status(settings)
    if cost <= 0:
        return True, "free"
    if cost > st["remaining"] - st["reserve"]:
        return False, (f"Needs {cost} credits but only {st['remaining']} remain "
                       f"(keeping {st['reserve']} in reserve).")
    if auto and st["spent_24h"] + cost > st["daily_allowance"]:
        return False, (f"Auto-refresh paused: {cost} credits would exceed today's allowance "
                       f"({st['daily_allowance']}/day to last until the reset).")
    return True, "ok"


def last_auto_refresh() -> float:
    return db.kv_get("auto_refresh_last") or 0.0


def mark_auto_refresh() -> None:
    db.kv_set("auto_refresh_last", time.time())
