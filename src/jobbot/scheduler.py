"""Pacing and quotas: weekly target, daily cap, per-company limit, random delays."""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db
from .text import norm

COUNTS_AS_APPLIED = ("submitted", "applied_manual")
COUNTS_AS_ATTEMPT = ("submitted", "queued", "failed")
KOLKATA = ZoneInfo("Asia/Kolkata")


def _local_midnight(days_back: int = 0) -> datetime:
    local = datetime.now(KOLKATA)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    return start.astimezone(timezone.utc)


def week_start_utc() -> str:
    return _local_midnight(datetime.now(KOLKATA).weekday()).isoformat(timespec="seconds")


def today_start_utc() -> str:
    return _local_midnight(0).isoformat(timespec="seconds")


def limits() -> dict:
    lim = config.targets().get("limits") or {}
    return {
        "weekly_target": int(lim.get("weekly_target", 300)),
        "daily_cap": int(lim.get("daily_cap", 40)),
        "per_company_30d": int(lim.get("per_company_30d", 3)),
        "delay_seconds": lim.get("delay_seconds", [30, 120]),
        "headless": bool(lim.get("headless", True)),
    }


def applied_this_week(conn) -> int:
    return db.count_applications(conn, COUNTS_AS_APPLIED, "updated_at", week_start_utc())


def pending_verification_this_week(conn) -> int:
    since = week_start_utc()
    return sum(1 for row in db.applications(conn)
               if row.get("status") in ("queued", "pending_verification")
               and (row.get("updated_at") or "") >= since
               and any(term in str(row.get("reason") or "").lower()
                       for term in ("submission not confirmed", "check your email", "uncertain outcome")))


def attempts_today(conn) -> int:
    return db.count_applications(conn, COUNTS_AS_ATTEMPT, "created_at", today_start_utc())


def budget(conn) -> int:
    lim = limits()
    weekly_reserved = applied_this_week(conn) + pending_verification_this_week(conn)
    return max(0, min(lim["daily_cap"] - attempts_today(conn), lim["weekly_target"] - weekly_reserved))


def company_count_30d(conn, company: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    rows = db.application_companies_since(conn, since, ("submitted", "applied_manual", "queued"))
    key = norm(company)
    return sum(1 for name in rows if norm(name) == key)


def company_allowed(conn, company: str) -> bool:
    return company_count_30d(conn, company) < limits()["per_company_30d"]


def pause(control=None) -> bool:
    lo, hi = limits()["delay_seconds"]
    until = time.monotonic() + random.uniform(float(lo), float(hi))
    while time.monotonic() < until:
        if control and not control.checkpoint("rate_limit_wait"):
            return False
        time.sleep(min(0.5, max(0.0, until - time.monotonic())))
    return not control or control.checkpoint("applying")
