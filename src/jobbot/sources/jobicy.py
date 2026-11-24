"""Jobicy public remote jobs API - https://jobicy.com/api/v2/remote-jobs
Terms: credit Jobicy and send applications to the original job url (we do)."""
from __future__ import annotations

from ..net import get_json
from ..text import html_to_text

API = "https://jobicy.com/api/v2/remote-jobs"


def normalise(j: dict) -> dict:
    return {
        "source": "jobicy",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"jobicy-{j.get('id')}",
        "company": j.get("companyName"),
        "title": j.get("jobTitle"),
        "location": j.get("jobGeo") or "Remote",
        "remote": 1,
        "url": j["url"],
        "apply_url": j["url"],
        "description": html_to_text(j.get("jobDescription") or j.get("jobExcerpt")),
        "posted_at": j.get("pubDate"),
    }


def fetch(cfg: dict) -> list[dict]:
    data = get_json(API, params={"count": int(cfg.get("count", 50)), "geo": cfg.get("geo", "anywhere")},
                    min_interval=3)
    return [normalise(j) for j in (data or {}).get("jobs") or [] if j.get("url") and j.get("jobTitle")]
