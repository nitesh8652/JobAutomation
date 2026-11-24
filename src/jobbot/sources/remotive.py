"""Remotive public API - https://remotive.com/api/remote-jobs
Terms: keep it to a few calls a day and credit Remotive as the source (the job url points back to it)."""
from __future__ import annotations

from ..net import get_json
from ..text import html_to_text

API = "https://remotive.com/api/remote-jobs"


def normalise(j: dict) -> dict:
    return {
        "source": "remotive",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"remotive-{j['id']}",
        "company": j.get("company_name"),
        "title": j.get("title"),
        "location": j.get("candidate_required_location") or "Remote",
        "remote": 1,
        "url": j["url"],
        "apply_url": j["url"],
        "description": html_to_text(j.get("description")),
        "posted_at": j.get("publication_date"),
    }


def fetch(cfg: dict) -> list[dict]:
    out: list[dict] = []
    for cat in cfg.get("categories") or ["software-dev"]:
        data = get_json(API, params={"category": cat}, min_interval=5)
        out.extend(normalise(j) for j in (data or {}).get("jobs", []) if j.get("url"))
    return out
