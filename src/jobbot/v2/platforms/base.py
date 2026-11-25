"""Platform adapter interface.  One adapter per configured platform so a site change
cannot break the rest of the agent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PlatformInfo:
    id: str
    label: str
    kind: str                 # ats | aggregator | manual
    auto_apply: bool
    home: str
    policy: str


class PlatformAdapter(Protocol):
    id: str
    label: str
    kind: str
    home_url: str
    auto_apply: bool

    def discover(self, settings: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
        """Return normalised jobs from this platform's permitted public feed."""

    def form_url(self, job: dict[str, Any]) -> str:
        """The hosted application form URL for a normalised job."""

    def info(self) -> PlatformInfo:
        ...
