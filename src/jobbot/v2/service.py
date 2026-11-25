"""Process-wide service singletons."""
from __future__ import annotations

from .orchestrator import Orchestrator
from .ws import ConnectionManager

hub = ConnectionManager()
orchestrator = Orchestrator(listener=hub.broadcast)
