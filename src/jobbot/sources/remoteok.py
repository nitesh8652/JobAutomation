"""Remote OK public API - https://remoteok.com/api
Terms: link back to Remote OK as the source (the job url does)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..net import get_json
from ..text import html_to_text


def normalise(j: dict) -> dict:
    url = j.get("url") or f"https://remoteok.com/remote-jobs/{j.get('slug')}"
    posted = j.get("date")
    if not posted and isinstance(j.get("epoch"), (int, float)):
        posted = datetime.fromtimestamp(j["epoch"], timezone.utc).isoformat()
    return {
        "source": "remoteok",
        "ats": "other",
        "company_slug": None,
        "ats_job_id": f"remoteok-{j.get('id')}",
        "company": j.get("company"),
        "title": j.get("position"),
        "location": j.get("location") or "Remote",
        "remote": 1,
        "url": url,
        "apply_url": j.get("apply_url") or url,
        "description": html_to_text(j.get("description")),
        "posted_at": posted,
    }


def fetch(cfg: dict) -> list[dict]:
    data = get_json("https://remoteok.com/api", min_interval=3)
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for j in data:
        if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
            continue  # the first element is the API terms notice
        out.append(normalise(j))
    return out
