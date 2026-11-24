"""Ashby public Job Posting API (read-only, no key)."""
from __future__ import annotations

from urllib.parse import quote

from ..net import get_json
from ..text import html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def normalise(slug: str, j: dict) -> dict:
    loc = j.get("location") or ""
    secondary = ", ".join(x.get("location", "") for x in j.get("secondaryLocations") or [] if isinstance(x, dict))
    if secondary:
        loc = f"{loc}; {secondary}"
    job_url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{quote(slug)}/{j['id']}"
    return {
        "source": "ashby",
        "ats": "ashby",
        "company_slug": slug,
        "ats_job_id": j["id"],
        "company": slug,
        "title": j.get("title"),
        "location": loc,
        "remote": int(bool(j.get("isRemote")) or (j.get("workplaceType") or "").lower() == "remote"),
        "url": job_url,
        "apply_url": j.get("applyUrl") or job_url.rstrip("/") + "/application",
        "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
        "posted_at": j.get("publishedAt"),
    }


def fetch_board(slug: str) -> list[dict] | None:
    data = get_json(API.format(slug=quote(slug)))
    if not data or "jobs" not in data:
        return None
    return [normalise(slug, j) for j in data["jobs"] if j.get("isListed", True)]
