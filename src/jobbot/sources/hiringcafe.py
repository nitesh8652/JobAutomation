"""Hiring Cafe - https://hiring.cafe  (browser scrape, no public API)"""
from __future__ import annotations

import logging

from ..text import html_to_text

log = logging.getLogger(__name__)

URL = "https://hiring.cafe"


def _normalise(j: dict) -> dict:
    jid = j.get("id") or j.get("_id") or j.get("jobId") or ""
    url = j.get("url") or j.get("applyUrl") or j.get("jobUrl") or f"{URL}/jobs/{jid}"
    return {
        "source": "hiringcafe",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"hiringcafe-{jid}",
        "company": j.get("company") or j.get("companyName"),
        "title": j.get("title") or j.get("jobTitle"),
        "location": j.get("location") or "Remote",
        "remote": 1,
        "url": url,
        "apply_url": url,
        "description": html_to_text(j.get("description") or j.get("jobDescription") or ""),
        "posted_at": j.get("postedAt") or j.get("createdAt"),
    }


def fetch(cfg: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("hiringcafe: playwright not installed")
        return []

    keywords = cfg.get("keywords") or ["software engineer"]
    max_jobs = int(cfg.get("max_jobs", 100))
    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.get("headless", True))
        page = browser.new_page()
        jobs: list[dict] = []

        def _handle_response(resp):
            if "hiring.cafe" in resp.url and resp.status == 200:
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = resp.json()
                        items = data if isinstance(data, list) else (
                            data.get("jobs") or data.get("results") or data.get("data") or []
                        )
                        jobs.extend(items)
                except Exception:
                    pass

        page.on("response", _handle_response)

        for kw in keywords:
            try:
                page.goto(f"{URL}?q={kw.replace(' ', '+')}&remote=true", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                # scroll to trigger lazy-load
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
            except Exception as exc:
                log.warning("hiringcafe: page load failed for %r: %s", kw, exc)

        browser.close()

    seen: set[str] = set()
    for j in jobs:
        norm = _normalise(j)
        if norm["title"] and norm["url"] and norm["ats_job_id"] not in seen:
            seen.add(norm["ats_job_id"])
            out.append(norm)
        if len(out) >= max_jobs:
            break

    log.info("hiringcafe: %d jobs", len(out))
    return out
