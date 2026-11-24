"""Recruitee public careers API (read-only, no key)."""
from __future__ import annotations

from ..net import get_json
from ..text import html_to_text

API = "https://{slug}.recruitee.com/api/offers/"


def _iso(value: str | None) -> str | None:
    """Recruitee dates look like '2026-10-22 06:17:49 UTC'."""
    if not value:
        return None
    return value.replace(" UTC", "+00:00").replace(" ", "T", 1)


def form_url(slug: str, j: dict) -> str:
    return (j.get("careers_apply_url") or j.get("careers_url")
            or f"https://{slug}.recruitee.com/o/{j.get('slug', j['id'])}/c/new")


def normalise(slug: str, j: dict) -> dict:
    loc = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
    url = j.get("careers_url") or f"https://{slug}.recruitee.com/o/{j.get('slug', j['id'])}"
    desc = "\n\n".join(p for p in (html_to_text(j.get("description")), html_to_text(j.get("requirements"))) if p)
    return {
        "source": "recruitee",
        "ats": "recruitee",
        "company_slug": slug,
        "ats_job_id": str(j["id"]),
        "company": j.get("company_name") or slug,
        "title": j.get("title"),
        "location": loc,
        "remote": int(bool(j.get("remote")) or "remote" in (loc or "").lower()),
        "url": url,
        "apply_url": form_url(slug, j),
        "description": desc,
        "posted_at": _iso(j.get("published_at") or j.get("created_at")),
    }


def fetch_board(slug: str) -> list[dict] | None:
    data = get_json(API.format(slug=slug))
    if not isinstance(data, dict) or "offers" not in data:
        return None
    return [normalise(slug, j) for j in data["offers"]
            if j.get("id") and j.get("title") and j.get("status", "published") == "published"]
