"""Himalayas public jobs API - https://himalayas.app/jobs/api"""
from __future__ import annotations

from ..net import get_json
from ..text import html_to_text

API = "https://himalayas.app/jobs/api"


def normalise(j: dict) -> dict:
    loc = ", ".join(j.get("locationRestrictions") or []) or "Remote"
    url = j.get("applicationLink") or j.get("guid")
    return {
        "source": "himalayas",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"himalayas-{j.get('guid') or url}",
        "company": j.get("companyName"),
        "title": j.get("title"),
        "location": loc,
        "remote": 1,
        "url": url,
        "apply_url": url,
        "description": html_to_text(j.get("description")),
        "posted_at": j.get("pubDate"),
    }


def fetch(cfg: dict) -> list[dict]:
    out: list[dict] = []
    params = {"limit": int(cfg.get("limit", 100))}
    for _ in range(int(cfg.get("max_pages", 2))):
        data = get_json(API, params=params, min_interval=3)
        jobs = (data or {}).get("jobs") or []
        if not jobs:
            break
        out.extend(normalise(j) for j in jobs if j.get("title") and (j.get("guid") or j.get("applicationLink")))
        cursor = (data or {}).get("nextCursor")
        if not cursor:
            break
        params = {"limit": params["limit"], "cursor": cursor}
    return out
