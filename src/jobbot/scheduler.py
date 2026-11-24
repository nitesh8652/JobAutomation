"""Pacing and quotas: weekly target, daily cap, per-company limit, random delays."""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone

from . import config, db
from .text import norm

COUNTS_AS_APPLIED = ("submitted", "applied_manual")
COUNTS_AS_ATTEMPT = ("submitted", "queued", "failed")


def _local_midnight(days_back: int = 0) -> datetime:
    local = datetime.now().astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    return start.astimezone(timezone.utc)


def week_start_utc() -> str:
    return _local_midnight(datetime.now().astimezone().weekday()).isoformat(timespec="seconds")


def today_start_utc() -> str:
    return _local_midnight(0).isoformat(timespec="seconds")


def limits() -> dict:
    lim = config.targets().get("limits") or {}
    return {
        "weekly_target": int(lim.get("weekly_target", 200)),
        "daily_cap": int(lim.get("daily_cap", 40)),
        "per_company_30d": int(lim.get("per_company_30d", 3)),
        "delay_seconds": lim.get("delay_seconds", [30, 120]),
        "headless": bool(lim.get("headless", True)),
    }


def applied_this_week(conn) -> int:
    return db.count_applications(conn, COUNTS_AS_APPLIED, "updated_at", week_start_utc())


def attempts_today(conn) -> int:
    return db.count_applications(conn, COUNTS_AS_ATTEMPT, "created_at", today_start_utc())


def budget(conn) -> int:
    lim = limits()
    return max(0, min(lim["daily_cap"] - attempts_today(conn), lim["weekly_target"] - applied_this_week(conn)))


def company_count_30d(conn, company: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    rows = db.application_companies_since(conn, since, ("submitted", "applied_manual", "queued"))
    key = norm(company)
    return sum(1 for name in rows if norm(name) == key)


def company_allowed(conn, company: str) -> bool:
    return company_count_30d(conn, company) < limits()["per_company_30d"]


def pause() -> None:
    lo, hi = limits()["delay_seconds"]
    time.sleep(random.uniform(float(lo), float(hi)))
