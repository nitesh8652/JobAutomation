"""Jooble partner API - https://jooble.org/api/about (free key by request)."""
from __future__ import annotations

import logging
import os

from ..net import post_json

log = logging.getLogger(__name__)

API = "https://jooble.org/api/{key}"


def normalise(j: dict) -> dict:
    url = j.get("link")
    loc = j.get("location") or ""
    return {
        "source": "jooble",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"jooble-{j.get('id')}",
        "company": j.get("company"),
        "title": j.get("title"),
        "location": loc,
        "remote": int("remote" in f"{loc} {j.get('title') or ''}".lower()),
        "url": url,
        "apply_url": url,
        "description": j.get("snippet") or "",
        "posted_at": j.get("updated"),
    }


def fetch(cfg: dict) -> list[dict]:
    key = os.environ.get("JOOBLE_KEY")
    if not key:
        log.info("jooble: set JOOBLE_KEY in .env (free key from jooble.org/api/about) - skipping")
        return []
    out: list[dict] = []
    for keywords in cfg.get("keywords") or ["software engineer"]:
        data = post_json(API.format(key=key), {"keywords": keywords, "location": cfg.get("location", "India")},
                         min_interval=3)
        out.extend(normalise(j) for j in (data or {}).get("jobs") or [] if j.get("link") and j.get("title"))
    return out
