"""Paths and YAML config loading."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(os.environ.get("JOBBOT_HOME") or Path(__file__).resolve().parents[2])
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jobbot.json"
SHOTS_DIR = DATA_DIR / "screenshots"
LETTERS_DIR = DATA_DIR / "letters"
REPORTS_DIR = DATA_DIR / "reports"
LOG_DIR = DATA_DIR / "logs"
BROWSER_PROFILE_DIR = DATA_DIR / "chrome-jobbot-profile"

DEFAULT_TARGET_ROLES = [
    "Software Engineer", "Backend Engineer", "Applied AI Engineer",
    "Forward Deployed Engineer", "Full-Stack Web Developer",
    "Automation Engineer", "Automation Specialist",
]

load_dotenv(ROOT / ".env")


def ensure_dirs() -> None:
    for d in (DATA_DIR, SHOTS_DIR, LETTERS_DIR, REPORTS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _load(name: str) -> dict:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run `jobbot init`, then edit that file.")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache
def profile() -> dict:
    return _load("profile")


@lru_cache
def targets() -> dict:
    data = _load("targets")
    automation = data.get("automation") or {}
    automation.setdefault("auto_submit_authorized", False)
    automation.setdefault("max_browser_tabs", 6)
    automation.setdefault("max_concurrent_submissions", 1)
    data["automation"] = automation
    role_path = CONFIG_DIR / "target_roles.json"
    if role_path.exists():
        try:
            roles = json.loads(role_path.read_text(encoding="utf-8"))
            if isinstance(roles, list):
                data["target_roles"] = [str(role).strip() for role in roles if str(role).strip()]
        except (OSError, ValueError):
            pass
    else:
        roles = data.get("target_roles")
        data["target_roles"] = ([str(role).strip() for role in roles if str(role).strip()]
                                if isinstance(roles, list) and roles else list(DEFAULT_TARGET_ROLES))
    return data


def resolve_path(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
