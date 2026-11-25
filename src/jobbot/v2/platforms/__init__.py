"""Platform registry built only from platforms already configured in this repository."""
from __future__ import annotations

from typing import Any

from .. import settings as cfg
from .ats import AtsAdapter
from .base import PlatformAdapter, PlatformInfo

ATS_IDS = ("greenhouse", "lever", "ashby", "workable", "recruitee", "personio")


def get_adapter(platform: str) -> AtsAdapter:
    return AtsAdapter(platform)


def available_adapters(settings: dict[str, Any]) -> list[PlatformAdapter]:
    enabled = settings.get("enabled_platforms") or []
    return [get_adapter(platform) for platform in enabled if platform in ATS_IDS]


def platform_infos() -> list[PlatformInfo]:
    return [
        PlatformInfo(
            id=key, label=str(value["label"]), kind=str(value["kind"]),
            auto_apply=bool(value["auto_apply"]), home=str(value["home"]),
            policy=str(value["policy"]),
        )
        for key, value in cfg.PLATFORM_CATALOG.items()
    ]


def platform_info(platform: str) -> PlatformInfo | None:
    return next((info for info in platform_infos() if info.id == platform), None)
