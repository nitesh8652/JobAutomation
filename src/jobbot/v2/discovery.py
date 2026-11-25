"""Job discovery and AI matching.

Network calls use the existing read-only source adapters.  Scoring runs through the
provider abstraction and every decision (selected or skipped) is persisted with a reason.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import repositories as repo
from .ai import extract_requirements, prefilter_job, score_job
from .db import session_scope
from .models import Job
from .platforms import ATS_IDS, get_adapter
from .platforms import aggregators as agg

log = logging.getLogger("jobbot.v2.discovery")
ActivityHook = Callable[..., Awaitable[None]] | None


async def discover(platform: str, settings: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    adapter = get_adapter(platform)
    return await asyncio.to_thread(adapter.discover, settings, limit)


async def discover_and_match(
    platform: str,
    settings: dict[str, Any],
    *,
    limit: int = 12,
    on_activity: ActivityHook = None,
) -> dict[str, int]:
    stats = {"discovered": 0, "new": 0, "filtered": 0, "scored": 0, "matched": 0, "skipped": 0}
    raw_jobs = await discover(platform, settings, limit=limit)
    stats["discovered"] = len(raw_jobs)
    if not raw_jobs:
        return stats

    new_jobs: list[int] = []
    with session_scope() as db:
        for raw in raw_jobs:
            requirements = extract_requirements(raw.get("description"))
            raw.setdefault("salary", requirements["salary"])
            raw.setdefault("experience", requirements["experience"])
            job = repo.upsert_job(db, platform, raw)
            if job is None:
                continue
            if job.status != "new":
                continue
            stats["new"] += 1
            reason = prefilter_job(
                {"title": job.title, "location": job.location, "remote": job.remote,
                 "description": job.description,
                 "posted_at": job.posted_at.isoformat() if job.posted_at else None},
                settings,
            )
            if reason:
                job.status = "filtered"
                job.status_reason = reason
                stats["filtered"] += 1
            else:
                new_jobs.append(job.id)
        # Aggregator discovery can insert ATS jobs from companies that are not in the
        # configured board list; score every still-new job for this platform.
        extra = list(db.scalars(select(Job.id).where(Job.platform == platform, Job.status == "new")))
        new_jobs.extend(job_id for job_id in extra if job_id not in new_jobs)
        repo.update_platform_state(db, platform, last_run=repo.utcnow(), jobs_found=stats["discovered"], last_error=None)

    if on_activity:
        await on_activity(
            "search", platform=platform,
            result=f"{stats['discovered']} listings, {stats['new']} new, {stats['filtered']} filtered",
        )

    # Scoring is done outside the discovery transaction, in bounded batches.
    batch = max(1, int(settings.get("max_matches_per_pass", 18)))
    profile: dict[str, Any] = {}
    resume_text = ""
    with session_scope() as db:
        profile = repo.get_profile(db)
        resume_text = repo.profile_resume_text(profile)
    threshold = int(settings.get("match_threshold", 70))
    for job_id in new_jobs[:batch]:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "new":
                continue
            data = repo.job_to_dict(job)
        result = await score_job(data, profile, resume_text, settings)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                continue
            job.score = int(result.get("score") or 0)
            job.score_reason = str(result.get("reason") or "")[:1000]
            eligible = bool(result.get("eligible", True)) and bool(result.get("location_ok", True))
            if not eligible:
                job.status = "skipped"
                job.status_reason = str(result.get("reason") or "not eligible")[:1000]
                stats["skipped"] += 1
            elif job.score < threshold:
                job.status = "low_score"
                job.status_reason = f"AI score {job.score} below threshold {threshold}: {job.score_reason}"
                stats["skipped"] += 1
            else:
                job.status = "matched"
                job.status_reason = job.score_reason
                stats["matched"] += 1
            stats["scored"] += 1
    if on_activity:
        await on_activity(
            "match", platform=platform,
            result=f"{stats['scored']} scored, {stats['matched']} matched, {stats['skipped']} skipped",
        )
    return stats


def pending_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(Job.status, Job.id)).all()
    counts: dict[str, int] = {}
    for status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


async def discover_aggregators(
    settings: dict[str, Any],
    *,
    on_activity: ActivityHook = None,
) -> dict[str, int]:
    """Pull India-focused listings from the Adzuna/Jooble APIs already configured.

    This never applies on the aggregator site.  Listings whose apply link resolves to
    Greenhouse/Lever/Ashby/Workable are re-homed to that ATS and become eligible for the
    normal application flow; all others are stored as manual/blocked for review.
    """
    stats = {"sources": 0, "discovered": 0, "new": 0, "rehomed": 0, "blocked": 0, "filtered": 0}
    if not settings.get("aggregator_discovery", True):
        return stats
    limit = max(1, int(settings.get("aggregator_limit", 40)))
    enabled_sources = settings.get("enabled_aggregators") or list(agg.SUPPORTED)
    for name in agg.SUPPORTED:
        if name not in enabled_sources:
            continue
        raw_jobs = await asyncio.to_thread(agg.discover, name, settings, limit)
        stats["sources"] += 1
        stats["discovered"] += len(raw_jobs)
        found = 0
        with session_scope() as db:
            for raw in raw_jobs:
                reason = prefilter_job(
                    {"title": raw.get("title"), "location": raw.get("location"),
                     "remote": raw.get("remote"), "description": raw.get("description"),
                     "posted_at": raw.get("posted_at")},
                    settings,
                )
                if reason:
                    stats["filtered"] += 1
                    continue
                platform, data = agg.rehome(raw)
                job = repo.upsert_job(db, platform, data)
                if job is None:
                    continue
                if job.status != "new":
                    continue
                found += 1
                stats["new"] += 1
                if platform in ATS_IDS:
                    stats["rehomed"] += 1
                else:
                    stats["blocked"] += 1
                    job.status = "blocked"
                    job.status_reason = f"manual application required via {platform}"
            repo.update_platform_state(db, name, last_run=repo.utcnow(), jobs_found=len(raw_jobs), last_error=None)
        if on_activity:
            await on_activity(
                "search", platform=name,
                result=f"{len(raw_jobs)} listings, {found} new, ATS links re-homed",
            )
    return stats
