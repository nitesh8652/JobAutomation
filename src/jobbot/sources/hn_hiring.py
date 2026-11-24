"""Hacker News "Who is hiring?" threads via the Algolia HN API (public)."""
from __future__ import annotations

import logging

from ..net import get_json
from ..text import html_to_text

log = logging.getLogger(__name__)

SEARCH = "https://hn.algolia.com/api/v1/search"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"


def normalise(comment: dict) -> dict | None:
    text = html_to_text(comment.get("text"))
    first = next((line for line in text.splitlines() if line.strip()), "")
    if "|" not in first:
        return None
    parts = [p.strip() for p in first.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    loc = parts[2] if len(parts) > 2 else ""
    url = f"https://news.ycombinator.com/item?id={comment['id']}"
    return {
        "source": "hn_hiring",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"hn-{comment['id']}",
        "company": parts[0][:120],
        "title": parts[1][:160],
        "location": loc or "Remote",
        "remote": int("remote" in f"{loc} {parts[1]}".lower()),
        "url": url,
        "apply_url": url,
        "description": text[:6000],
        "posted_at": comment.get("created_at"),
    }


def fetch(cfg: dict) -> list[dict]:
    hits = (get_json(SEARCH, params={"tags": "story,author_whoishiring", "query": "Who is hiring",
                                     "hitsPerPage": 10}, min_interval=2) or {}).get("hits") or []
    hits = [h for h in hits if (h.get("title") or "").startswith("Ask HN: Who is hiring")]
    if not hits:
        log.info("hn_hiring: no recent thread found")
        return []
    hits.sort(key=lambda h: h.get("created_at") or "", reverse=True)
    thread = get_json(ITEM.format(id=hits[0]["objectID"]), min_interval=2) or {}
    out: list[dict] = []
    for comment in (thread.get("children") or [])[: int(cfg.get("max_items", 150))]:
        job = normalise(comment) if comment.get("text") else None
        if job:
            out.append(job)
    log.info("hn_hiring: %d postings from %s", len(out), hits[0].get("title"))
    return out
