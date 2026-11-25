"""Durable repositories: settings, profile, jobs, applications, quota, resumes, activity."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..text import norm
from . import settings as cfg
from .db import KOLKATA, utcnow, week_start_kolkata
from .models import (
    Activity,
    Application,
    CandidateProfile,
    Job,
    PlatformState,
    QuotaWeek,
    Resume,
    Setting,
    SubmissionReservation,
)

log = logging.getLogger("jobbot.v2.repo")

APPLIED = "Applied"
PENDING = "Pending Verification"
REVIEW = "Requires Manual Review"
FAILED = "Failed"
DUPLICATE = "Duplicate"
SKIPPED = "Skipped"
BLOCKED = "Blocked by Platform"

FINAL_STATUSES = (APPLIED, PENDING, REVIEW, FAILED, DUPLICATE, SKIPPED, BLOCKED)
CONSUMING_STATUSES = (APPLIED, PENDING)
REPORT_STATUSES = (APPLIED, PENDING, REVIEW, FAILED, DUPLICATE, SKIPPED, BLOCKED)


# --------------------------------------------------------------------------- settings / profile


def get_settings(db: Session) -> dict[str, Any]:
    base = cfg.default_settings()
    row = db.get(Setting, "runtime")
    if row is None:
        row = Setting(key="runtime", value=base)
        db.add(row)
        db.flush()
        return base
    return cfg.merge_settings(base, row.value or {})


def save_settings(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    merged = cfg.merge_settings(get_settings(db), values)
    merged["weekly_cap"] = max(1, min(300, int(merged.get("weekly_cap", 300))))
    merged["daily_cap"] = max(0, min(300, int(merged.get("daily_cap", 45))))
    merged["max_tabs"] = max(1, min(6, int(merged.get("max_tabs", 6))))
    merged["max_concurrent_submissions"] = max(1, min(3, int(merged.get("max_concurrent_submissions", 1))))
    merged["match_threshold"] = max(0, min(100, int(merged.get("match_threshold", 70))))
    merged["per_company_30d"] = max(0, int(merged.get("per_company_30d", 3)))
    merged["posted_within_days"] = max(0, int(merged.get("posted_within_days", 21)))
    row = db.get(Setting, "runtime") or Setting(key="runtime")
    row.value = merged
    db.add(row)
    db.flush()
    return merged


def get_profile(db: Session) -> dict[str, Any]:
    row = db.get(CandidateProfile, 1)
    if row is None:
        seed = cfg.legacy_profile()
        row = CandidateProfile(id=1, data=seed or {})
        db.add(row)
        db.flush()
        return row.data or {}
    return row.data or {}


def save_profile(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    row = db.get(CandidateProfile, 1) or CandidateProfile(id=1, data={})
    row.data = data
    db.add(row)
    db.flush()
    return row.data


def profile_resume_text(profile: dict[str, Any]) -> str:
    text = str(profile.get("resume_text") or "").strip()
    if text:
        return text
    path = profile.get("resume_path")
    if path:
        candidate = cfg.resolve_path(str(path))
        cache = cfg.DATA_DIR / "resume.txt"
        if cache.exists() and candidate.exists() and cache.stat().st_mtime >= candidate.stat().st_mtime:
            return cache.read_text(encoding="utf-8", errors="ignore")
        if candidate.exists() and candidate.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader

                extracted = "\n".join((page.extract_text() or "") for page in PdfReader(str(candidate)).pages).strip()
                cfg.ensure_dirs()
                cache.write_text(extracted, encoding="utf-8")
                return extracted
            except Exception as exc:  # pragma: no cover - malformed PDFs
                log.warning("could not extract legacy resume text: %s", exc)
    return ""


# --------------------------------------------------------------------------- jobs


def fingerprint_for(company: str | None, title: str | None, location: str | None) -> str:
    raw = "|".join(norm(part) for part in (company, title, location))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        from ..db import timestamp

        stamp = timestamp(value)
        if stamp:
            return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    return None


def upsert_job(db: Session, platform: str, data: dict[str, Any]) -> Job | None:
    platform_job_id = str(data.get("ats_job_id") or data.get("platform_job_id") or "") or None
    url = str(data.get("url") or "").strip()
    if not url:
        return None
    existing = None
    if platform_job_id:
        existing = db.scalar(select(Job).where(Job.platform == platform, Job.platform_job_id == platform_job_id))
    if existing is None:
        existing = db.scalar(select(Job).where(Job.url == url))
    company = str(data.get("company") or data.get("company_slug") or "").strip()
    title = str(data.get("title") or "").strip()
    location = str(data.get("location") or "").strip()
    if existing is not None:
        if data.get("description") and len(str(data["description"])) > len(existing.description or ""):
            existing.description = str(data["description"])
        if data.get("apply_url"):
            existing.apply_url = str(data["apply_url"])
        if not existing.salary and data.get("salary"):
            existing.salary = str(data.get("salary"))
        if not existing.experience and data.get("experience"):
            existing.experience = str(data.get("experience"))
        existing.updated_at = utcnow()
        return existing
    job = Job(
        platform=platform,
        platform_job_id=platform_job_id,
        company=company,
        company_slug=str(data.get("company_slug") or "") or None,
        title=title,
        location=location,
        remote=bool(data.get("remote")),
        salary=str(data.get("salary")) if data.get("salary") else None,
        experience=str(data.get("experience")) if data.get("experience") else None,
        description=str(data.get("description") or ""),
        url=url[:512],
        apply_url=str(data.get("apply_url") or url)[:1024],
        fingerprint=fingerprint_for(company, title, location),
        posted_at=_parse_dt(data.get("posted_at")),
        status="new",
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
        return job
    except IntegrityError:
        if platform_job_id:
            return db.scalar(select(Job).where(Job.platform == platform, Job.platform_job_id == platform_job_id))
        return db.scalar(select(Job).where(Job.url == url[:512]))


def claim_job(db: Session, job_id: int, worker: str) -> bool:
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "matched")
        .values(status="applying", worker=worker, claimed_at=utcnow(), updated_at=utcnow())
    )
    return bool(result.rowcount)


def reset_job(db: Session, job_id: int, status: str = "matched", reason: str | None = None) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=status, status_reason=reason, worker=None, claimed_at=None, updated_at=utcnow())
    )


def set_job_status(db: Session, job_id: int, status: str, reason: str | None = None) -> None:
    db.execute(
        update(Job).where(Job.id == job_id).values(status=status, status_reason=reason, updated_at=utcnow())
    )


def jobs_for_platform(db: Session, platform: str, statuses: Iterable[str], limit: int = 50) -> list[Job]:
    return list(
        db.scalars(
            select(Job)
            .where(Job.platform == platform, Job.status.in_(list(statuses)))
            .order_by(Job.score.desc(), Job.posted_at.desc(), Job.id.desc())
            .limit(limit)
        )
    )


def matched_jobs(db: Session, platform: str, threshold: int, limit: int = 25) -> list[Job]:
    return list(
        db.scalars(
            select(Job)
            .where(
                Job.platform == platform,
                Job.status == "matched",
                Job.score.is_not(None),
                Job.score >= threshold,
            )
            .order_by(Job.score.desc(), Job.posted_at.desc(), Job.id.desc())
            .limit(limit)
        )
    )


# --------------------------------------------------------------------------- quota


def _quota_row(db: Session, week: date, cap: int) -> QuotaWeek:
    row = db.get(QuotaWeek, week)
    if row is None:
        row = QuotaWeek(week_start=week, cap=cap, used=0, reserved=0)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.get(QuotaWeek, week)
            assert row is not None
    elif row.cap != cap:
        row.cap = cap
    return row


def quota_snapshot(db: Session, cap: int, week: date | None = None) -> dict[str, Any]:
    week = week or week_start_kolkata()
    row = _quota_row(db, week, cap)
    db.flush()
    pending = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.week_start == week, Application.status == PENDING
        )
    ) or 0
    failed = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.week_start == week, Application.status == FAILED
        )
    ) or 0
    duplicate = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.week_start == week, Application.status == DUPLICATE
        )
    ) or 0
    review = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.week_start == week, Application.status == REVIEW
        )
    ) or 0
    successful = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.week_start == week, Application.status == APPLIED
        )
    ) or 0
    committed = int(row.used) + int(row.reserved)
    return {
        "week_start": week.isoformat(),
        "week_end": (week + timedelta(days=6)).isoformat(),
        "cap": int(row.cap),
        "used": int(row.used),
        "reserved": int(row.reserved),
        "remaining": max(0, int(row.cap) - committed),
        "submitted_this_week": int(row.used),
        "successful": successful,
        "pending_verification": pending,
        "failed": failed,
        "duplicate_skipped": duplicate,
        "requires_review": review,
    }


def _already_applied(db: Session, job: Job) -> str | None:
    existing = db.scalar(select(Application).where(Application.job_id == job.id))
    if existing is not None:
        if existing.status in CONSUMING_STATUSES:
            return f"already recorded as {existing.status}"
        if existing.status == REVIEW:
            return "already waiting for manual review"
    duplicate = db.scalar(
        select(Application)
        .join(Job, Job.id == Application.job_id)
        .where(
            Job.fingerprint == job.fingerprint,
            Application.job_id != job.id,
            Application.status.in_(list(CONSUMING_STATUSES) + [REVIEW]),
        )
    )
    if duplicate is not None:
        return "same company/title/location already applied"
    reservation = db.scalar(
        select(SubmissionReservation).where(
            or_(
                SubmissionReservation.job_id == job.id,
                SubmissionReservation.fingerprint == job.fingerprint,
            ),
            SubmissionReservation.status.in_(("reserved", "confirmed", "uncertain")),
        )
    )
    if reservation is not None:
        return "submission already reserved"
    return None


def applications_today(db: Session) -> int:
    from .db import local_day_bounds_utc

    start, end = local_day_bounds_utc(datetime.now(KOLKATA).date())
    return int(db.scalar(
        select(func.count()).select_from(Application).where(
            Application.status.in_(list(CONSUMING_STATUSES)),
            Application.applied_at >= start,
            Application.applied_at < end,
        )
    ) or 0)


def reserve_submission(
    db: Session, job: Job, *, cap: int, tab_id: str | None = None,
    per_company_limit: int = 0, daily_cap: int = 0,
) -> tuple[SubmissionReservation | None, str | None]:
    """Atomically claim a job and reserve weekly capacity.

    Returns ``(reservation, None)`` or ``(None, reason)``.  The caller must call
    ``confirm_submission``, ``mark_uncertain`` or ``release_submission``.
    """
    week = week_start_kolkata()
    duplicate = _already_applied(db, job)
    if duplicate:
        return None, duplicate
    if daily_cap > 0 and applications_today(db) >= daily_cap:
        return None, "daily application cap reached"
    if per_company_limit > 0 and job.company:
        recent = db.scalar(
            select(func.count())
            .select_from(Application)
            .join(Job, Job.id == Application.job_id)
            .where(
                func.lower(Job.company) == job.company.strip().lower(),
                Application.status.in_(list(CONSUMING_STATUSES)),
                Application.applied_at >= utcnow() - timedelta(days=30),
            )
        ) or 0
        if recent >= per_company_limit:
            return None, f"company limit reached ({per_company_limit} in 30 days)"
    row = _quota_row(db, week, cap)
    if int(row.used) + int(row.reserved) >= int(row.cap):
        return None, "weekly application cap reached"
    reservation = db.scalar(
        select(SubmissionReservation)
        .where(or_(
            SubmissionReservation.job_id == job.id,
            SubmissionReservation.fingerprint == job.fingerprint,
        ))
        .order_by(SubmissionReservation.id.desc())
    )
    if reservation is not None and reservation.status != "released":
        return None, "duplicate submission claim"
    if reservation is None:
        reservation = SubmissionReservation(
            job_id=job.id,
            fingerprint=job.fingerprint,
            week_start=week,
            status="reserved",
            tab_id=tab_id,
        )
        try:
            with db.begin_nested():
                db.add(reservation)
                db.flush()
        except IntegrityError:
            return None, "duplicate submission claim"
    else:
        reservation.job_id = job.id
        reservation.fingerprint = job.fingerprint
        reservation.week_start = week
        reservation.status = "reserved"
        reservation.tab_id = tab_id
        reservation.updated_at = utcnow()
        db.flush()
    result = db.execute(
        update(QuotaWeek)
        .where(QuotaWeek.week_start == week, (QuotaWeek.used + QuotaWeek.reserved) < QuotaWeek.cap)
        .values(reserved=QuotaWeek.reserved + 1, updated_at=utcnow())
    )
    if not result.rowcount:
        db.delete(reservation)
        db.flush()
        return None, "weekly application cap reached"
    claimed = claim_job(db, job.id, tab_id or "worker")
    if not claimed:
        row = db.get(QuotaWeek, week)
        if row is not None:
            row.reserved = max(0, int(row.reserved) - 1)
        db.delete(reservation)
        db.flush()
        return None, "job already claimed by another tab"
    db.flush()
    return reservation, None


def confirm_submission(db: Session, reservation: SubmissionReservation) -> None:
    if reservation.status == "confirmed":
        return
    row = db.get(QuotaWeek, reservation.week_start)
    if row is not None:
        if reservation.status in ("reserved", "uncertain"):
            row.reserved = max(0, int(row.reserved) - 1)
        row.used = int(row.used) + 1
    reservation.status = "confirmed"
    reservation.updated_at = utcnow()
    db.flush()


def mark_uncertain(db: Session, reservation: SubmissionReservation) -> None:
    if reservation.status in ("released", "confirmed"):
        return
    reservation.status = "uncertain"
    reservation.updated_at = utcnow()
    db.flush()


def release_submission(db: Session, reservation: SubmissionReservation) -> None:
    if reservation.status in ("confirmed", "released"):
        return
    row = db.get(QuotaWeek, reservation.week_start)
    if row is not None and reservation.status in ("reserved", "uncertain"):
        row.reserved = max(0, int(row.reserved) - 1)
    reservation.status = "released"
    reservation.updated_at = utcnow()
    db.flush()


def save_application(
    db: Session,
    job: Job,
    *,
    status: str,
    reason: str | None = None,
    week: date | None = None,
    **fields: Any,
) -> Application:
    row = db.scalar(select(Application).where(Application.job_id == job.id))
    if row is None:
        row = Application(job_id=job.id, status=status)
        db.add(row)
    row.status = status
    row.week_start = week or week_start_kolkata()
    row.fingerprint = job.fingerprint
    if status in (APPLIED, PENDING, REVIEW, FAILED, DUPLICATE, SKIPPED, BLOCKED):
        row.applied_at = row.applied_at or utcnow()
    for key in (
        "resume_id", "resume_name", "cover_letter_path", "answers_json", "screenshot",
        "confirmation", "failure_reason", "next_action", "tab_id", "reference_id", "dry_run",
    ):
        if key in fields:
            setattr(row, key, fields[key])
    if reason:
        if status in (APPLIED, PENDING):
            row.confirmation = fields.get("confirmation") or reason
        else:
            row.failure_reason = reason
    row.updated_at = utcnow()
    job_status = {
        APPLIED: "applied", PENDING: "pending", REVIEW: "review", FAILED: "failed",
        DUPLICATE: "duplicate", SKIPPED: "skipped", BLOCKED: "blocked",
    }.get(status, "failed")
    job.status = job_status
    job.status_reason = reason
    job.worker = None
    job.claimed_at = None
    job.updated_at = utcnow()
    db.flush()
    return row


def record_duplicate(db: Session, job: Job, reason: str) -> Application:
    return save_application(db, job, status=DUPLICATE, reason=reason, dry_run=False)


# --------------------------------------------------------------------------- activity


def log_activity(
    db: Session,
    action: str,
    *,
    level: str = "info",
    tab_id: str | None = None,
    platform: str | None = None,
    job_id: int | None = None,
    job_title: str | None = None,
    url: str | None = None,
    result: str | None = None,
) -> Activity:
    row = Activity(
        level=level, tab_id=tab_id, platform=platform, action=action[:120],
        job_id=job_id, job_title=(job_title or "")[:300] or None,
        url=(url or "")[:1024] or None, result=(result or "")[:1000] or None,
    )
    db.add(row)
    db.flush()
    return row


def recent_activity(db: Session, limit: int = 50) -> list[Activity]:
    return list(db.scalars(select(Activity).order_by(Activity.ts.desc(), Activity.id.desc()).limit(limit)))


# --------------------------------------------------------------------------- resumes


def list_resumes(db: Session) -> list[Resume]:
    return list(db.scalars(select(Resume).order_by(Resume.is_default.desc(), Resume.created_at.desc())))


def get_resume(db: Session, resume_id: int) -> Resume | None:
    return db.get(Resume, resume_id)


def add_resume(db: Session, **fields: Any) -> Resume:
    if fields.get("is_default"):
        db.execute(update(Resume).values(is_default=False))
    row = Resume(**fields)
    db.add(row)
    db.flush()
    return row


def set_default_resume(db: Session, resume_id: int) -> Resume | None:
    row = db.get(Resume, resume_id)
    if row is None:
        return None
    db.execute(update(Resume).values(is_default=False))
    row.is_default = True
    db.flush()
    return row


def delete_resume(db: Session, resume_id: int) -> bool:
    row = db.get(Resume, resume_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


# --------------------------------------------------------------------------- applications / reports


def query_applications(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    status: str | None = None,
    platform: str | None = None,
    company: str | None = None,
    search: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[Application]:
    stmt = select(Application).join(Job, Job.id == Application.job_id)
    conditions = []
    if start:
        conditions.append(Application.week_start >= start)
    if end:
        conditions.append(Application.week_start <= end)
    if status:
        conditions.append(Application.status == status)
    if platform:
        conditions.append(Job.platform == platform)
    if company:
        conditions.append(Job.company.ilike(f"%{company}%"))
    if search:
        needle = f"%{search}%"
        conditions.append(or_(Job.title.ilike(needle), Job.company.ilike(needle), Job.location.ilike(needle)))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(Application.updated_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def application_to_dict(app: Application, job: Job | None = None) -> dict[str, Any]:
    job = job or app.job
    return {
        "id": app.id,
        "job_id": app.job_id,
        "status": app.status,
        "applied_at": app.applied_at.isoformat() if app.applied_at else None,
        "week_start": app.week_start.isoformat() if app.week_start else None,
        "platform": job.platform if job else None,
        "company": job.company if job else None,
        "title": job.title if job else None,
        "location": job.location if job else None,
        "salary": job.salary if job else None,
        "experience": job.experience if job else None,
        "url": job.url if job else None,
        "apply_url": job.apply_url if job else None,
        "score": job.score if job else None,
        "score_reason": job.score_reason if job else None,
        "resume_name": app.resume_name,
        "confirmation": app.confirmation,
        "failure_reason": app.failure_reason,
        "next_action": app.next_action,
        "tab_id": app.tab_id,
        "reference_id": app.reference_id,
        "screenshot": app.screenshot,
        "dry_run": app.dry_run,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
    }


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "platform": job.platform,
        "platform_job_id": job.platform_job_id,
        "company": job.company,
        "title": job.title,
        "location": job.location,
        "remote": job.remote,
        "salary": job.salary,
        "experience": job.experience,
        "description": job.description,
        "url": job.url,
        "apply_url": job.apply_url,
        "score": job.score,
        "score_reason": job.score_reason,
        "status": job.status,
        "status_reason": job.status_reason,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "fetched_at": job.fetched_at.isoformat() if job.fetched_at else None,
    }


# --------------------------------------------------------------------------- recovery


def recover_stale(db: Session, older_than_minutes: int = 30) -> dict[str, int]:
    """Called at startup: never assume an interrupted submission succeeded."""
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    stale = list(
        db.scalars(
            select(SubmissionReservation).where(
                SubmissionReservation.status == "reserved",
                SubmissionReservation.updated_at < cutoff,
            )
        )
    )
    for reservation in stale:
        reservation.status = "uncertain"
        reservation.updated_at = utcnow()
        app = db.scalar(select(Application).where(Application.job_id == reservation.job_id))
        job = db.get(Job, reservation.job_id)
        if job is not None:
            save_application(
                db, job, status=PENDING,
                reason="Interrupted during submission - verify on the platform before retrying.",
                week=reservation.week_start,
                next_action="Verify submission manually",
            )
        elif app is not None:
            app.status = PENDING
            app.next_action = "Verify submission manually"
    # A job claimed but never reserved (crash during dry run or before submit) is safe to retry.
    applying = list(db.scalars(
        select(Job).where(Job.status == "applying", Job.claimed_at < cutoff)
    ))
    retried = pending = 0
    for job in applying:
        reservation = db.scalar(
            select(SubmissionReservation).where(
                SubmissionReservation.job_id == job.id,
                SubmissionReservation.status.in_(("reserved", "confirmed", "uncertain")),
            )
        )
        if reservation is not None:
            job.status = "pending"
            job.status_reason = "Interrupted - verify the submission before retrying"
            pending += 1
        else:
            job.status = "matched"
            job.status_reason = "Retrying after an interrupted attempt"
            retried += 1
        job.worker = None
        job.claimed_at = None
        job.updated_at = utcnow()
    db.flush()
    return {
        "uncertain_reservations": len(stale),
        "jobs_moved_to_pending": pending,
        "jobs_retried": retried,
    }


# --------------------------------------------------------------------------- platform state


def platform_state(db: Session, platform: str) -> PlatformState:
    row = db.get(PlatformState, platform)
    if row is None:
        row = PlatformState(platform=platform)
        db.add(row)
        db.flush()
    return row


def update_platform_state(db: Session, platform: str, **values: Any) -> None:
    row = platform_state(db, platform)
    for key, value in values.items():
        setattr(row, key, value)
    db.flush()
