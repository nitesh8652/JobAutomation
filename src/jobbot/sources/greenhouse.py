"""Greenhouse public Job Board API (read-only, no key)."""
from __future__ import annotations

from ..net import get_json
from ..text import html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def form_url(slug: str, job_id: str) -> str:
    return f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}"


def normalise(slug: str, j: dict, company: str | None = None) -> dict:
    loc = (j.get("location") or {}).get("name") or ""
    url = form_url(slug, j["id"])
    return {
        "source": "greenhouse",
        "ats": "greenhouse",
        "company_slug": slug,
        "ats_job_id": str(j["id"]),
        "company": j.get("company_name") or company or slug,
        "title": j.get("title"),
        "location": loc,
        "remote": int("remote" in loc.lower()),
        "url": url,
        "apply_url": url,
        "description": html_to_text(j.get("content")),
        "posted_at": j.get("first_published") or j.get("updated_at"),
    }


def fetch_board(slug: str) -> list[dict] | None:
    data = get_json(API.format(slug=slug), params={"content": "true"})
    if not data or "jobs" not in data:
        return None
    return [normalise(slug, j) for j in data["jobs"]]
