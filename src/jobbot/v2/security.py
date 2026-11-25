"""Small operational-security helpers: log redaction and log file setup.

Secrets must never be written to logs.  This is a last line of defence; callers still
avoid logging request bodies, tokens, cookies and candidate free-text answers.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from . import settings as cfg

_REDACTIONS = [
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)\b(\s*[:=]\s*)([^\s,;&]+)"), r"\1\2***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer ***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "sk-***"),
    (re.compile(r"(?i)([?&](?:token|key|password|secret)=)[^&\s]+"), r"\1***"),
    (re.compile(r"(?i)(x-api-token\s*:\s*)[^\s,;]+"), r"\1***"),
]


def redact(value: object) -> str:
    text = str(value)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = redact(message)
        record.args = ()
        return True


_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    cfg.ensure_dirs()
    handler = RotatingFileHandler(
        cfg.LOG_DIR / "jobbot-v2.log", maxBytes=5_000_000, backupCount=4, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _configured = True
