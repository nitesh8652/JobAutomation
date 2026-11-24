"""Adzuna official jobs API - https://developer.adzuna.com (free app id + key)."""
from __future__ import annotations

import logging
import os

from ..net import get_json

log = logging.getLogger(__name__)

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def normalise(j: dict) -> dict:
    loc = (j.get("location") or {}).get("display_name") or ""
    url = j.get("redirect_url")
    return {
        "source": "adzuna",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"adzuna-{j.get('id')}",
        "company": (j.get("company") or {}).get("display_name"),
        "title": j.get("title"),
        "location": loc,
        "remote": int("remote" in f"{loc} {j.get('title') or ''}".lower()),
        "url": url,
        "apply_url": url,
        "description": j.get("description") or "",
        "posted_at": j.get("created"),
    }


def fetch(cfg: dict) -> list[dict]:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.info("adzuna: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env (free at developer.adzuna.com) - skipping")
        return []
    out: list[dict] = []
    for what in cfg.get("what") or ["software engineer"]:
        for page in range(1, int(cfg.get("max_pages", 1)) + 1):
            data = get_json(API.format(country=cfg.get("country", "in"), page=page), params={
                "app_id": app_id, "app_key": app_key, "what": what,
                "results_per_page": int(cfg.get("results_per_page", 50)),
                "max_days_old": int(cfg.get("max_days_old", 21)), "content_type": "json",
            }, min_interval=3)
            results = (data or {}).get("results") or []
            if not results:
                break
            out.extend(normalise(j) for j in results if j.get("redirect_url") and j.get("title"))
    return out
