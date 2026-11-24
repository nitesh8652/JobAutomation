"""Workable public widget API (read-only, no key)."""
from __future__ import annotations

from ..net import get_json
from ..text import html_to_text

API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


def form_url(slug: str, shortcode: str) -> str:
    return f"https://apply.workable.com/{slug}/j/{shortcode}/apply/"


def normalise(slug: str, j: dict, company: str | None) -> dict:
    loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
    shortcode = j["shortcode"]
    return {
        "source": "workable",
        "ats": "workable",
        "company_slug": slug,
        "ats_job_id": shortcode,
        "company": company or slug,
        "title": j.get("title"),
        "location": loc or ("Remote" if j.get("telecommuting") else ""),
        "remote": int(bool(j.get("telecommuting"))),
        "url": f"https://apply.workable.com/{slug}/j/{shortcode}/",
        "apply_url": form_url(slug, shortcode),
        "description": html_to_text(j.get("description")),
        "posted_at": j.get("published_on") or j.get("created_at"),
    }


def fetch_board(slug: str) -> list[dict] | None:
    data = get_json(API.format(slug=slug), params={"details": "true"})
    if not data or "jobs" not in data:
        return None
    return [normalise(slug, j, data.get("name")) for j in data["jobs"] if j.get("shortcode")]
