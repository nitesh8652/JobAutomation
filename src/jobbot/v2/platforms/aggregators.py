"""Discovery-only adapters for the aggregator APIs already used by this project.

Adzuna and Jooble do not offer a permitted hosted application flow, so their jobs are
never submitted automatically.  When an aggregator link points at a supported ATS we
re-home the job onto that ATS adapter; otherwise it is recorded as blocked/manual.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from ...sources import AGGREGATORS
from .. import settings as cfg

log = logging.getLogger("jobbot.v2.platforms.aggregators")
SUPPORTED = ("adzuna", "jooble")

_PATTERNS = {
    "greenhouse": [
        re.compile(r"job-boards\.greenhouse\.io/([^/]+)/jobs/(\d+)", re.I),
        re.compile(r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)", re.I),
    ],
    "lever": [re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{8,})", re.I)],
    "ashby": [re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{8,})", re.I)],
    "workable": [re.compile(r"apply\.workable\.com/([^/]+)/j/([A-Z0-9]+)", re.I)],
}


def detect_ats(url: str | None) -> tuple[str, str, str] | None:
    if not url:
        return None
    for platform, patterns in _PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(url)
            if match:
                return platform, match.group(1), match.group(2)
    return None


def _config(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    targets = cfg.legacy_targets()
    config = dict(targets.get(name) or {})
    config.setdefault("enabled", True)
    if name == "adzuna":
        config.setdefault("country", "in")
        config.setdefault("results_per_page", min(50, int(settings.get("aggregator_limit", 40))))
        config.setdefault("max_pages", 1)
    if name == "jooble":
        config.setdefault("location", "India")
    return config


def discover(name: str, settings: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    if name not in SUPPORTED or name not in AGGREGATORS:
        return []
    try:
        jobs = AGGREGATORS[name](_config(name, settings))
    except Exception as exc:
        log.warning("%s discovery failed (%s)", name, type(exc).__name__)
        return []
    return list(jobs or [])[: max(1, int(limit or settings.get("aggregator_limit", 40)))]


def rehome(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (platform, normalised job).  Supported ATS links keep their ATS identity."""
    data = dict(job)
    detected = detect_ats(str(data.get("apply_url") or data.get("url") or ""))
    if detected:
        platform, slug, job_id = detected
        data["platform"] = platform
        data["ats"] = platform
        data["company_slug"] = data.get("company_slug") or slug
        data["ats_job_id"] = data.get("ats_job_id") or job_id
        data["apply_url"] = data.get("apply_url") or data.get("url")
        data["url"] = data["apply_url"]
        return platform, data
    data["platform"] = str(data.get("source") or "aggregator")
    return data["platform"], data
