"""Lever public Postings API (read-only, no key). Tries the global and EU hosts."""
from __future__ import annotations

from datetime import datetime, timezone

from ..net import get_json
from ..text import html_to_text

HOSTS = ("https://api.lever.co/v0/postings/{slug}", "https://api.eu.lever.co/v0/postings/{slug}")


def normalise(slug: str, j: dict) -> dict:
    cats = j.get("categories") or {}
    loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
    workplace = (j.get("workplaceType") or "").lower()
    parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
    for lst in j.get("lists") or []:
        parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
    parts.append(j.get("additionalPlain") or "")
    created = j.get("createdAt")
    hosted = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j['id']}"
    return {
        "source": "lever",
        "ats": "lever",
        "company_slug": slug,
        "ats_job_id": j["id"],
        "company": slug,
        "title": j.get("text"),
        "location": loc,
        "remote": int(workplace == "remote" or "remote" in (loc or "").lower()),
        "url": hosted,
        "apply_url": j.get("applyUrl") or hosted.rstrip("/") + "/apply",
        "description": "\n\n".join(p for p in parts if p).strip(),
        "posted_at": datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
    }


def fetch_board(slug: str) -> list[dict] | None:
    for host in HOSTS:
        data = get_json(host.format(slug=slug), params={"mode": "json"})
        if isinstance(data, list):
            return [normalise(slug, j) for j in data]
    return None
