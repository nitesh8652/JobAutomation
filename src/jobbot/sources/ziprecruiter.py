"""ZipRecruiter - https://www.ziprecruiter.com  (browser scrape, no public API)"""
from __future__ import annotations

import logging

from ..text import html_to_text

log = logging.getLogger(__name__)


def _normalise(j: dict) -> dict:
    jid = str(j.get("id") or j.get("job_id") or "")
    url = j.get("job_url") or j.get("url") or f"https://www.ziprecruiter.com/jobs/{jid}"
    return {
        "source": "ziprecruiter",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"zip-{jid}",
        "company": j.get("hiring_company", {}).get("name") if isinstance(j.get("hiring_company"), dict) else j.get("company"),
        "title": j.get("name") or j.get("title"),
        "location": j.get("location") or "Remote",
        "remote": 1 if j.get("remote_work_allowed") or "remote" in (j.get("location") or "").lower() else 0,
        "url": url,
        "apply_url": url,
        "description": html_to_text(j.get("snippet") or j.get("description") or ""),
        "posted_at": j.get("posted_time") or j.get("posted_at"),
    }


def fetch(cfg: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("ziprecruiter: playwright not installed")
        return []

    keywords = cfg.get("keywords") or ["software engineer"]
    max_jobs = int(cfg.get("max_jobs", 100))
    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.get("headless", True))
        page = browser.new_page()
        jobs: list[dict] = []

        def _handle_response(resp):
            if "ziprecruiter.com" in resp.url and resp.status == 200:
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = resp.json()
                        items = data.get("jobs") or data.get("results") or []
                        jobs.extend(items)
                except Exception:
                    pass

        page.on("response", _handle_response)

        for kw in keywords:
            try:
                page.goto(
                    f"https://www.ziprecruiter.com/jobs-search?search={kw.replace(' ', '+')}&location=Remote",
                    timeout=30000,
                )
                page.wait_for_load_state("networkidle", timeout=15000)
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
            except Exception as exc:
                log.warning("ziprecruiter: page load failed for %r: %s", kw, exc)

        browser.close()

    seen: set[str] = set()
    for j in jobs:
        norm = _normalise(j)
        if norm["title"] and norm["url"] and norm["ats_job_id"] not in seen:
            seen.add(norm["ats_job_id"])
            out.append(norm)
        if len(out) >= max_jobs:
            break

    log.info("ziprecruiter: %d jobs", len(out))
    return out
