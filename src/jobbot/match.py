"""Filtering: cheap rules first, then an AI fit score for what's left."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from . import config, db, llm
from .resume import resume_text
from .text import norm

log = logging.getLogger(__name__)

APPLY_ATS = ("greenhouse", "lever", "ashby", "workable")


def apply_ats(t: dict) -> tuple[str, ...]:
    """ATSes we submit to automatically (`auto_apply_ats`); every other source goes to assist mode."""
    return tuple(t.get("auto_apply_ats") or APPLY_ATS)


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def prefilter(job: dict, t: dict) -> str | None:
    """None if the job passes, otherwise the reason it was dropped."""
    title = (job.get("title") or "").lower()
    if not title:
        return "no title"
    inc = [s.lower() for s in t.get("title_include") or []]
    if inc and not any(s in title for s in inc):
        return "title not in include list"
    for s in t.get("title_exclude") or []:
        if s.lower() in title:
            return f"title excluded ({s.strip()})"
    loc = (job.get("location") or "").lower()
    if t.get("remote_only") and not job.get("remote") and "remote" not in loc and "remote" not in title:
        return "not remote"
    keep = t.get("location_keep")
    if not (keep and re.search(keep, loc, re.I)):
        for pat in t.get("location_exclude") or []:
            if re.search(pat, loc, re.I):
                return f"location excluded ({job.get('location')})"
    days = t.get("posted_within_days")
    posted = _parse_date(job.get("posted_at"))
    if days and posted and posted < datetime.now(timezone.utc) - timedelta(days=int(days)):
        return "too old"
    return None


SCORE_SYSTEM = """You are a strict, honest technical recruiter screening for a candidate.
Score from 0 to 100 how strong a fit the CANDIDATE is for the JOB:
- required skills and stack overlap, seniority / years of experience, domain;
- location eligibility: the candidate lives where their profile says. If the job is only open to
  people in specific countries/time zones the candidate is not in, set location_ok=false.
Be conservative: 70+ means a recruiter would plausibly shortlist them.
Return JSON: {"score": <int>, "location_ok": <bool>, "reason": "<max 25 words>"}"""


def score_job(job: dict) -> dict:
    prof = config.profile()
    user = (
        f"CANDIDATE PROFILE\nLocation: {prof.get('location')}\nYears of experience: {prof.get('years_experience')}\n"
        f"Summary: {prof.get('summary', '')}\n\nRESUME\n{resume_text()[:8000]}\n\n"
        f"JOB\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
        f"Description:\n{(job.get('description') or '')[:6000]}"
    )
    out = llm.json_chat(SCORE_SYSTEM, user)
    try:
        score = int(out.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return {"score": max(0, min(100, score)), "location_ok": bool(out.get("location_ok", True)),
            "reason": str(out.get("reason", ""))[:300]}


def run(conn, limit: int = 150) -> dict:
    t = config.targets()
    auto = apply_ats(t)
    min_score = int((t.get("llm") or {}).get("min_score", 70))
    stats = {"filtered": 0, "duplicate": 0, "scored": 0, "matched": 0, "assist": 0, "low_score": 0}

    # Same company + title already in the pipeline (e.g. listed on two aggregators) -> duplicate.
    inactive = {"new", "filtered", "low_score", "duplicate", "closed"}
    seen = {
        (norm(r["company"]), norm(r["title"]))
        for r in db.jobs(conn) if r["status"] not in inactive
    }
    rows = db.new_jobs_for_scoring(conn, auto)
    to_score = []
    for r in rows:
        job = dict(r)
        reason = prefilter(job, t)
        if reason:
            db.set_job_status(conn, job["id"], "filtered", reason)
            stats["filtered"] += 1
            continue
        key = (norm(job["company"]), norm(job["title"]))
        if key in seen:
            db.set_job_status(conn, job["id"], "duplicate", "same company+title already queued")
            stats["duplicate"] += 1
            continue
        seen.add(key)
        to_score.append(job)
    conn.commit()

    for job in to_score[:limit]:
        try:
            res = score_job(job)
        except SystemExit:
            raise
        except Exception as exc:
            log.warning("scoring job %s failed: %s", job["id"], exc)
            continue
        stats["scored"] += 1
        db.set_job_score(conn, job["id"], res["score"], res["reason"])
        if not res["location_ok"]:
            db.set_job_status(conn, job["id"], "low_score", "location not eligible: " + res["reason"])
            stats["low_score"] += 1
        elif res["score"] < min_score:
            db.set_job_status(conn, job["id"], "low_score", res["reason"])
            stats["low_score"] += 1
        elif job["ats"] in auto:
            db.set_job_status(conn, job["id"], "matched", res["reason"])
            stats["matched"] += 1
        else:
            db.set_job_status(conn, job["id"], "assist", "no auto-apply for this ATS - apply by hand")
            stats["assist"] += 1
        conn.commit()
    stats["left_unscored"] = max(0, len(to_score) - limit)
    return stats
