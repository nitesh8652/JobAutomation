"""Resume storage, text extraction and relevance-based selection."""
from __future__ import annotations

import hashlib
import io
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..text import norm
from . import repositories as repo
from . import settings as cfg
from .models import Job, Resume

log = logging.getLogger("jobbot.v2.resumes")
MAX_BYTES = 10 * 1024 * 1024


class ResumeError(ValueError):
    pass


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip(" .")
    return (cleaned or "resume.pdf")[:120]


def extract_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:
        log.warning("resume text extraction failed (%s)", type(exc).__name__)
        return ""


async def save_upload(db: Session, filename: str, data: bytes) -> Resume:
    if len(data) > MAX_BYTES:
        raise ResumeError("resume is larger than 10 MB")
    if not data.startswith(b"%PDF"):
        raise ResumeError("only PDF resumes are accepted")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.query(Resume).filter(Resume.sha256 == digest).first()
    if existing is not None:
        return existing
    cfg.ensure_dirs()
    safe = _safe_name(filename)
    path = cfg.RESUME_DIR / f"{uuid.uuid4().hex}-{safe}"
    path.write_bytes(data)
    text = extract_text(data)
    is_default = db.query(Resume).count() == 0
    return repo.add_resume(
        db,
        name=safe,
        original_name=safe,
        path=str(path),
        sha256=digest,
        size_bytes=len(data),
        text_content=text,
        is_default=is_default,
    )


def _tokens(text: str | None) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{2,}", (text or "").lower())
    return {word.strip(".-") for word in words if len(word) > 2}


def select_resume(db: Session, job: Job, profile: dict[str, Any], settings: dict[str, Any]) -> tuple[Resume | None, str]:
    resumes = repo.list_resumes(db)
    if not resumes:
        fallback = profile.get("resume_path")
        if fallback and cfg.resolve_path(str(fallback)).exists():
            return None, str(cfg.resolve_path(str(fallback)))
        raise ResumeError("no resume uploaded; upload a PDF in the dashboard first")
    if str(settings.get("resume_selection", "relevant")) == "default":
        chosen = next((item for item in resumes if item.is_default), resumes[0])
        return chosen, chosen.path
    job_tokens = _tokens(job.description) | _tokens(job.title)
    best: tuple[float, Resume] | None = None
    for resume in resumes:
        tokens = _tokens(resume.text_content)
        if not tokens:
            score = 0.0
        else:
            score = len(tokens & job_tokens) / max(1, len(job_tokens))
        if best is None or score > best[0]:
            best = (score, resume)
    chosen = best[1]
    if chosen.is_default and best and best[0] == 0:
        return chosen, chosen.path
    return chosen, chosen.path


def write_cover_letter(job_id: int, text: str) -> str:
    cfg.ensure_dirs()
    path = cfg.LETTER_DIR / f"job{job_id}-cover-letter.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)
