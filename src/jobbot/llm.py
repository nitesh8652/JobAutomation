"""Thin OpenAI wrapper returning parsed JSON."""
from __future__ import annotations

import json
import logging
import os

from . import config

log = logging.getLogger(__name__)
_client = None


def _get_client():
    global _client
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set. Put it in .env (see .env.example).")
        from openai import OpenAI
        _client = OpenAI()
    return _client


def model() -> str:
    return (config.targets().get("llm") or {}).get("model", "gpt-4o-mini")


def json_chat(system: str, user: str) -> dict:
    resp = _get_client().chat.completions.create(
        model=model(),
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("LLM returned non-JSON: %.200s", content)
        return {}
