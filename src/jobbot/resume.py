"""Plain-text resume for the AI (from resume_text_path, or extracted from the PDF)."""
from __future__ import annotations

from functools import lru_cache

from . import config


@lru_cache
def resume_text() -> str:
    prof = config.profile()
    if prof.get("resume_text_path"):
        p = config.resolve_path(prof["resume_text_path"])
        if p.exists():
            return p.read_text(encoding="utf-8")
    pdf = resume_pdf()
    cache = config.DATA_DIR / "resume.txt"
    if cache.exists() and cache.stat().st_mtime >= pdf.stat().st_mtime:
        return cache.read_text(encoding="utf-8")
    from pypdf import PdfReader
    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages).strip()
    config.ensure_dirs()
    cache.write_text(text, encoding="utf-8")
    return text


def resume_pdf():
    pdf = config.resolve_path(config.profile().get("resume_path", "resumes/base_resume.pdf"))
    if not pdf.exists():
        raise SystemExit(f"Resume not found at {pdf}. Set resume_path in config/profile.yaml.")
    return pdf
