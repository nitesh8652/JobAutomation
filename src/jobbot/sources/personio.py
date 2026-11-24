"""Personio public XML feed (read-only, no key)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..net import get
from ..text import html_to_text

API = "https://{slug}.jobs.personio.de/xml"


def form_url(slug: str, job_id: str) -> str:
    return f"https://{slug}.jobs.personio.de/job/{job_id}"


def normalise(slug: str, p: ET.Element) -> dict:
    job_id = (p.findtext("id") or "").strip()
    office = (p.findtext("office") or "").strip()
    desc = "\n\n".join(html_to_text(d.findtext("value")) for d in p.findall("jobDescriptions/jobDescription"))
    return {
        "source": "personio",
        "ats": "personio",
        "company_slug": slug,
        "ats_job_id": job_id,
        "company": slug,
        "title": (p.findtext("name") or "").strip(),
        "location": office,
        "remote": int("remote" in office.lower()),
        "url": form_url(slug, job_id),
        "apply_url": form_url(slug, job_id),
        "description": desc.strip(),
        "posted_at": p.findtext("createdAt"),
    }


def fetch_board(slug: str) -> list[dict] | None:
    resp = get(API.format(slug=slug), params={"language": "en"})
    if resp.status_code >= 400 or b"<position" not in resp.content[:8000]:
        return None
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return None
    return [normalise(slug, p) for p in root.findall("position")
            if p.findtext("id") and p.findtext("name")]
