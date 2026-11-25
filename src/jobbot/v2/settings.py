"""Runtime configuration for the autonomous agent (v2).

The old ``jobbot`` YAML files remain the source of truth for the candidate profile,
target roles, company lists and platform policy.  This module layers the new
runtime settings on top and persists overrides in MySQL (or SQLite for local tests).
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .. import config as legacy_config

ROOT = legacy_config.ROOT
CONFIG_DIR = legacy_config.CONFIG_DIR
DATA_DIR = legacy_config.DATA_DIR
UPLOAD_DIR = DATA_DIR / "uploads"
RESUME_DIR = UPLOAD_DIR / "resumes"
LETTER_DIR = DATA_DIR / "letters"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
REPORT_DIR = DATA_DIR / "reports"
LOG_DIR = DATA_DIR / "logs"
BROWSER_PROFILE_DIR = legacy_config.BROWSER_PROFILE_DIR

load_dotenv(ROOT / ".env")

if os.environ.get("JOBBOT_BROWSER_PROFILE"):
    _profile = Path(os.environ["JOBBOT_BROWSER_PROFILE"])
    BROWSER_PROFILE_DIR = _profile if _profile.is_absolute() else ROOT / _profile


# --------------------------------------------------------------------------- paths


def ensure_dirs() -> None:
    for directory in (DATA_DIR, UPLOAD_DIR, RESUME_DIR, LETTER_DIR, SCREENSHOT_DIR, REPORT_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


# --------------------------------------------------------------------------- platforms

# Only platforms that already exist in this project are registered.  Adapters marked
# ``auto_apply`` have a supported hosted application form and public board API.
# Naukri / Indeed / Wellfound / Hirist are intentionally manual-only: their terms do
# not permit automated submission.  Adzuna and Jooble are discovery-only APIs.
PLATFORM_CATALOG: dict[str, dict[str, Any]] = {
    "greenhouse": {
        "label": "Greenhouse", "kind": "ats", "auto_apply": True, "home": "https://job-boards.greenhouse.io/",
        "policy": "Public job-board API and hosted application form.",
    },
    "lever": {
        "label": "Lever", "kind": "ats", "auto_apply": True, "home": "https://jobs.lever.co/",
        "policy": "Public postings API and hosted application form.",
    },
    "ashby": {
        "label": "Ashby", "kind": "ats", "auto_apply": True, "home": "https://jobs.ashbyhq.com/",
        "policy": "Public job-posting API and hosted application form.",
    },
    "workable": {
        "label": "Workable", "kind": "ats", "auto_apply": True, "home": "https://apply.workable.com/",
        "policy": "Public widget API and hosted application form.",
    },
    "recruitee": {
        "label": "Recruitee", "kind": "ats", "auto_apply": False, "home": "https://recruitee.com/",
        "policy": "Public careers API; enabled only after a reviewed dry run.",
    },
    "personio": {
        "label": "Personio", "kind": "ats", "auto_apply": False, "home": "https://www.personio.com/",
        "policy": "Public XML feed; enabled only after a reviewed dry run.",
    },
    "adzuna": {
        "label": "Adzuna", "kind": "aggregator", "auto_apply": False, "home": "https://www.adzuna.in/",
        "policy": "Discovery API only; applications happen on the employer's site.",
    },
    "jooble": {
        "label": "Jooble", "kind": "aggregator", "auto_apply": False, "home": "https://in.jooble.org/",
        "policy": "Discovery API only; applications happen on the employer's site.",
    },
    "naukri": {
        "label": "Naukri", "kind": "manual", "auto_apply": False, "home": "https://www.naukri.com/",
        "policy": "Manual browser search only; automated submission is not permitted by the project.",
    },
    "indeed": {
        "label": "Indeed", "kind": "manual", "auto_apply": False, "home": "https://in.indeed.com/",
        "policy": "Manual browser search only; terms prohibit automated submission without permission.",
    },
    "wellfound": {
        "label": "Wellfound", "kind": "manual", "auto_apply": False, "home": "https://wellfound.com/",
        "policy": "Manual browser search only; automated harvesting is restricted.",
    },
    "hirist": {
        "label": "Hirist.tech", "kind": "manual", "auto_apply": False, "home": "https://www.hirist.tech/",
        "policy": "Manual browser search only.",
    },
    "ycombinator": {
        "label": "YC Work at a Startup", "kind": "manual", "auto_apply": False,
        "home": "https://www.workatastartup.com/", "policy": "Manual browser search only.",
    },
}


def platform_catalog() -> list[dict[str, Any]]:
    return [{"id": key, **value} for key, value in PLATFORM_CATALOG.items()]


def legacy_targets() -> dict[str, Any]:
    try:
        return legacy_config.targets()
    except SystemExit:
        return {}


def legacy_profile() -> dict[str, Any]:
    try:
        return legacy_config.profile()
    except SystemExit:
        return {}


def default_settings() -> dict[str, Any]:
    targets = legacy_targets()
    limits = targets.get("limits") or {}
    llm = targets.get("llm") or {}
    automation = targets.get("automation") or {}
    enabled = [str(x) for x in (targets.get("auto_apply_ats") or ["greenhouse", "lever", "ashby", "workable"])]
    return {
        "weekly_cap": int(limits.get("weekly_target", 300)),
        "daily_cap": int(limits.get("daily_cap", 45)),
        "max_tabs": max(2, min(6, int(automation.get("max_browser_tabs", 6)))),
        "max_concurrent_submissions": max(1, min(3, int(automation.get("max_concurrent_submissions", 1)))),
        "match_threshold": int(llm.get("min_score", 70)),
        "per_company_30d": int(limits.get("per_company_30d", 3)),
        "delay_seconds": limits.get("delay_seconds", [30, 120]),
        "dry_run": True,
        "auto_submit_authorized": False,
        "enabled_platforms": [p for p in enabled if p in PLATFORM_CATALOG and PLATFORM_CATALOG[p]["kind"] == "ats"],
        "aggregator_discovery": True,
        "enabled_aggregators": ["adzuna", "jooble"],
        "aggregator_limit": 40,
        "target_roles": targets.get("target_roles") or list(legacy_config.DEFAULT_TARGET_ROLES),
        "title_include": targets.get("title_include") or [],
        "title_exclude": targets.get("title_exclude") or [],
        "location_policy": targets.get("location_policy") or {"remote_countries": ["India"], "office_cities": ["Mumbai"]},
        "posted_within_days": targets.get("posted_within_days", 21),
        "ai_provider": "auto",
        "ai_model": os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "cloud_base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "cloud_model": llm.get("model") or "gpt-4o-mini",
        "resume_selection": "relevant",
        "generate_cover_letter": True,
        "schedule": {"enabled": False, "start": "09:00", "end": "21:00", "days": [0, 1, 2, 3, 4]},
        "headless": False,
        "dry_run_hold_seconds": 60,
        "auto_consent": bool(legacy_profile().get("auto_consent", True)),
        "eeo_decline": bool(legacy_profile().get("eeo_decline", True)),
    }


# --------------------------------------------------------------------------- settings object


def database_url() -> str:
    """MySQL in production; SQLite fallback keeps local dry runs and tests simple."""
    url = os.environ.get("JOBBOT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    ensure_dirs()
    return f"sqlite:///{(DATA_DIR / 'jobbot-v2.db').as_posix()}"


def public_base_url() -> str:
    return os.environ.get("JOBBOT_PUBLIC_BASE_URL", "http://127.0.0.1:8766")


def api_token() -> str | None:
    return os.environ.get("JOBBOT_API_TOKEN") or None


def bind_host() -> str:
    return os.environ.get("JOBBOT_HOST", "127.0.0.1")


def bind_port() -> int:
    return int(os.environ.get("JOBBOT_PORT", "8766"))


def allowed_origins() -> list[str]:
    raw = os.environ.get("JOBBOT_CORS_ORIGINS", "")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if origins:
        return origins
    return ["http://127.0.0.1:8766", "http://localhost:8766", "http://127.0.0.1:5173", "http://localhost:5173"]


@dataclass
class RuntimeSettings:
    """Immutable snapshot used by one agent run."""

    values: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    @property
    def enabled_platforms(self) -> list[str]:
        enabled = [
            p for p in self.get("enabled_platforms", [])
            if p in PLATFORM_CATALOG and PLATFORM_CATALOG[p]["kind"] == "ats"
        ]
        return enabled[: int(self.get("max_tabs", 6))]

    @property
    def weekly_cap(self) -> int:
        return max(1, min(300, int(self.get("weekly_cap", 300))))

    @property
    def dry_run(self) -> bool:
        return bool(self.get("dry_run", True))

    @property
    def live_authorized(self) -> bool:
        return bool(self.get("auto_submit_authorized", False)) and not self.dry_run

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self.values)


def merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_settings(merged[key], value)
        else:
            merged[key] = value
    return merged


def schedule_open(settings: RuntimeSettings, now: datetime | None = None) -> bool:
    schedule = settings.get("schedule") or {}
    if not schedule.get("enabled"):
        return True
    from zoneinfo import ZoneInfo

    local = (now or datetime.now(ZoneInfo("Asia/Kolkata"))).astimezone(ZoneInfo("Asia/Kolkata"))
    if local.weekday() not in [int(x) for x in schedule.get("days", [0, 1, 2, 3, 4])]:
        return False
    start = str(schedule.get("start", "09:00"))
    end = str(schedule.get("end", "21:00"))
    current = local.strftime("%H:%M")
    return start <= current <= end


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
