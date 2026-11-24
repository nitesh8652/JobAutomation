"""We Work Remotely public RSS feeds - https://weworkremotely.com"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..net import get
from ..text import html_to_text

FEEDS = ("remote-programming-jobs", "remote-full-stack-programming-jobs",
         "remote-back-end-programming-jobs", "remote-devops-sysadmin-jobs")


def normalise(item: ET.Element) -> dict:
    title = (item.findtext("title") or "").strip()
    company, _, role = title.partition(":")
    if not role:
        company, role = "", title
    region = (item.findtext("region") or "").strip()
    link = (item.findtext("link") or item.findtext("guid") or "").strip()
    posted = item.findtext("pubDate")
    try:
        posted = parsedate_to_datetime(posted).isoformat() if posted else None
    except (TypeError, ValueError):
        posted = None
    return {
        "source": "weworkremotely",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"wwr-{link}",
        "company": company.strip() or None,
        "title": role.strip(),
        "location": region or "Remote",
        "remote": 1,
        "url": link,
        "apply_url": link,
        "description": html_to_text(item.findtext("description")),
        "posted_at": posted,
    }


def fetch(cfg: dict) -> list[dict]:
    out: list[dict] = []
    for feed in cfg.get("feeds") or FEEDS:
        resp = get(f"https://weworkremotely.com/categories/{feed}.rss", min_interval=3)
        if resp.status_code >= 400:
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            continue
        for item in root.findall("./channel/item"):
            job = normalise(item)
            if job["url"] and job["title"]:
                out.append(job)
    return out
