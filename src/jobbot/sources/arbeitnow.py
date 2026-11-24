"""Arbeitnow free job board API - https://www.arbeitnow.com/api/job-board-api"""
from __future__ import annotations

from datetime import datetime, timezone

from ..net import get_json
from ..text import html_to_text

API = "https://www.arbeitnow.com/api/job-board-api"


def normalise(j: dict) -> dict:
    created = j.get("created_at")
    posted = datetime.fromtimestamp(created, timezone.utc).isoformat() if isinstance(created, (int, float)) else created
    return {
        "source": "arbeitnow",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"arbeitnow-{j.get('slug')}",
        "company": j.get("company_name"),
        "title": j.get("title"),
        "location": j.get("location") or ("Remote" if j.get("remote") else ""),
        "remote": int(bool(j.get("remote"))),
        "url": j["url"],
        "apply_url": j["url"],
        "description": html_to_text(j.get("description")),
        "posted_at": posted,
    }


def fetch(cfg: dict) -> list[dict]:
    out: list[dict] = []
    for page in range(1, int(cfg.get("max_pages", 3)) + 1):
        data = get_json(API, params={"page": page}, min_interval=3)
        items = (data or {}).get("data") or []
        if not items:
            break
        for j in items:
            if cfg.get("remote_only", True) and not j.get("remote"):
                continue
            if j.get("url"):
                out.append(normalise(j))
    return out
