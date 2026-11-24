"""Y Combinator - Work at a Startup - https://www.workatastartup.com  (browser scrape)"""
from __future__ import annotations

import logging

from ..text import html_to_text

log = logging.getLogger(__name__)

URL = "https://www.workatastartup.com/jobs"


def _normalise(j: dict) -> dict:
    jid = str(j.get("id") or "")
    url = j.get("url") or f"https://www.workatastartup.com/jobs/{jid}"
    company = j.get("company") or {}
    company_name = company.get("name") if isinstance(company, dict) else str(company or "")
    locs = j.get("locations") or j.get("location_names") or []
    location = ", ".join(locs) if isinstance(locs, list) else str(locs or "Remote")
    return {
        "source": "ycombinator",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"yc-{jid}",
        "company": company_name or j.get("company_name"),
        "title": j.get("title") or j.get("role"),
        "location": location or "Remote",
        "remote": 1 if j.get("remote") or "remote" in location.lower() else 0,
        "url": url,
        "apply_url": url,
        "description": html_to_text(j.get("description") or ""),
        "posted_at": j.get("created_at") or j.get("updated_at"),
    }


def fetch(cfg: dict) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("ycombinator: playwright not installed")
        return []

    max_jobs = int(cfg.get("max_jobs", 100))
    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.get("headless", True))
        page = browser.new_page()
        jobs: list[dict] = []

        def _handle_response(resp):
            if "workatastartup.com" in resp.url and resp.status == 200:
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = resp.json()
                        items = data if isinstance(data, list) else (
                            data.get("jobs") or data.get("results") or []
                        )
                        jobs.extend(items)
                except Exception:
                    pass

        page.on("response", _handle_response)

        try:
            page.goto(f"{URL}?remote=true&role=eng", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            for _ in range(4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
        except Exception as exc:
            log.warning("ycombinator: page load failed: %s", exc)

        browser.close()

    seen: set[str] = set()
    for j in jobs:
        norm = _normalise(j)
        if norm["title"] and norm["url"] and norm["ats_job_id"] not in seen:
            seen.add(norm["ats_job_id"])
            out.append(norm)
        if len(out) >= max_jobs:
            break

    log.info("ycombinator: %d jobs", len(out))
    return out
