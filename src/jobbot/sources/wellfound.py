"""Wellfound (formerly AngelList Talent) - https://wellfound.com  (browser scrape)"""
from __future__ import annotations

import logging

from ..text import html_to_text

log = logging.getLogger(__name__)

URL = "https://wellfound.com/jobs"


def _normalise(j: dict) -> dict:
    jid = str(j.get("id") or j.get("jobId") or "")
    slug = j.get("slug") or jid
    url = j.get("url") or f"https://wellfound.com/jobs/{slug}"
    startup = j.get("startup") or j.get("company") or {}
    company = startup.get("name") if isinstance(startup, dict) else str(startup or "")
    loc_list = j.get("locationNames") or j.get("locations") or []
    location = ", ".join(loc_list) if isinstance(loc_list, list) else str(loc_list or "Remote")
    return {
        "source": "wellfound",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"wellfound-{jid or slug}",
        "company": company or j.get("companyName"),
        "title": j.get("title") or j.get("role"),
        "location": location or "Remote",
        "remote": 1 if j.get("remote") or "remote" in location.lower() else 0,
        "url": url,
        "apply_url": url,
        "description": html_to_text(j.get("description") or ""),
        "posted_at": j.get("liveStartAt") or j.get("createdAt"),
    }


def fetch(cfg: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("wellfound: playwright not installed")
        return []

    roles = cfg.get("roles") or ["software-engineer"]
    max_jobs = int(cfg.get("max_jobs", 100))
    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.get("headless", True))
        page = browser.new_page()
        jobs: list[dict] = []

        def _handle_response(resp):
            if "wellfound.com" in resp.url and resp.status == 200:
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = resp.json()
                        # GraphQL response shape
                        edges = (
                            (data.get("data") or {})
                            .get("talent_jobListings", {})
                            .get("edges") or []
                        )
                        for edge in edges:
                            node = edge.get("node") or {}
                            if node:
                                jobs.append(node)
                        # flat list fallback
                        items = data.get("jobs") or data.get("results") or []
                        jobs.extend(items)
                except Exception:
                    pass

        page.on("response", _handle_response)

        for role in roles:
            try:
                page.goto(f"https://wellfound.com/role/r/{role}?remote=true", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
            except Exception as exc:
                log.warning("wellfound: page load failed for %r: %s", role, exc)

        browser.close()

    seen: set[str] = set()
    for j in jobs:
        norm = _normalise(j)
        if norm["title"] and norm["url"] and norm["ats_job_id"] not in seen:
            seen.add(norm["ats_job_id"])
            out.append(norm)
        if len(out) >= max_jobs:
            break

    log.info("wellfound: %d jobs", len(out))
    return out
