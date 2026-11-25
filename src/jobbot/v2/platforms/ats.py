"""Adapters for the ATS platforms already configured in this project."""
from __future__ import annotations

import logging
from typing import Any

from ...sources import BOARDS
from .. import settings as cfg
from .base import PlatformInfo

log = logging.getLogger("jobbot.v2.platforms")


class AtsAdapter:
    def __init__(self, platform: str):
        info = cfg.PLATFORM_CATALOG.get(platform)
        if info is None or info["kind"] != "ats" or platform not in BOARDS:
            raise ValueError(f"unsupported ATS platform: {platform}")
        self.id = platform
        self.label = str(info["label"])
        self.kind = "ats"
        self.home_url = str(info["home"])
        self.auto_apply = bool(info["auto_apply"])
        self._info = PlatformInfo(
            id=self.id, label=self.label, kind=self.kind,
            auto_apply=self.auto_apply, home=self.home_url, policy=str(info["policy"]),
        )
        self._fetch = BOARDS[platform]

    def info(self) -> PlatformInfo:
        return self._info

    def companies(self, settings: dict[str, Any]) -> list[str]:
        targets = cfg.legacy_targets()
        configured = (targets.get("companies") or {}).get(self.id) or []
        return [str(slug).strip() for slug in configured if str(slug).strip()]

    def discover(self, settings: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
        """Fetch the configured company boards for this ATS.

        This uses the public read-only API that already exists in ``jobbot.sources``.
        Only a bounded number of companies are checked per worker pass.
        """
        jobs: list[dict[str, Any]] = []
        companies = self.companies(settings)[: max(1, int(limit))]
        for slug in companies:
            try:
                board = self._fetch(slug)
            except Exception as exc:
                log.warning("%s:%s discovery failed (%s)", self.id, slug, type(exc).__name__)
                continue
            if not board:
                continue
            for job in board:
                job = dict(job)
                job.setdefault("company", slug)
                job.setdefault("platform", self.id)
                jobs.append(job)
        return jobs

    def form_url(self, job: dict[str, Any]) -> str:
        if self.id == "greenhouse" and job.get("company_slug") and job.get("ats_job_id"):
            return (
                "https://job-boards.greenhouse.io/embed/job_app"
                f"?for={job['company_slug']}&token={job['ats_job_id']}"
            )
        return str(job.get("apply_url") or job.get("url") or self.home_url)
