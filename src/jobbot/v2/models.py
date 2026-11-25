"""SQLAlchemy models for the durable job/application store.

The schema is intentionally MySQL-first but uses portable types so the same code
can run against SQLite for local dry runs and automated tests.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    platform_job_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    company: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    company_slug: Mapped[str | None] = mapped_column(String(160), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    salary: Mapped[str | None] = mapped_column(String(160), nullable=True)
    experience: Mapped[str | None] = mapped_column(String(160), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    apply_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    worker: Mapped[str | None] = mapped_column(String(80), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    applications: Mapped[list["Application"]] = relationship(back_populates="job")

    __table_args__ = (
        UniqueConstraint("platform", "platform_job_id", name="uq_jobs_platform_job"),
        UniqueConstraint("url", name="uq_jobs_url"),
        Index("ix_jobs_status_platform", "status", "platform"),
    )


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    week_start: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)
    resume_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    cover_letter_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    answers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    confirmation: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(300), nullable=True)
    tab_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="applications")


class SubmissionReservation(Base):
    """Atomic claim + quota reservation for one submission attempt.

    Unique ``fingerprint`` prevents two tabs from submitting the same role/company
    concurrently.  A reservation stays ``uncertain`` (and keeps quota capacity)
    when a submit click cannot be verified.
    """

    __tablename__ = "submission_reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="reserved", index=True)
    tab_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class QuotaWeek(Base):
    __tablename__ = "quota_weeks"

    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    cap: Mapped[int] = mapped_column(Integer, default=300)
    used: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    original_name: Mapped[str] = mapped_column(String(240), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CandidateProfile(Base):
    __tablename__ = "candidate_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Activity(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    tab_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class PlatformState(Base):
    __tablename__ = "platform_state"

    platform: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    applications: Mapped[int] = mapped_column(Integer, default=0)
