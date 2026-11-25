"""Database engine, session lifecycle and schema initialisation."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import settings
from .models import Base

log = logging.getLogger("jobbot.v2.db")
KOLKATA = ZoneInfo("Asia/Kolkata")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update({"pool_recycle": 1800, "pool_size": 5, "max_overflow": 10})
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _record):  # pragma: no cover - driver hook
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


def init_engine(url: str | None = None, *, create_schema: bool = True) -> Engine:
    global _engine, _SessionLocal
    url = url or settings.database_url()
    if _engine is not None and str(_engine.url) == url:
        return _engine
    if _engine is not None:
        _engine.dispose()
    _engine = _build_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    if create_schema:
        Base.metadata.create_all(_engine)
        _ensure_columns(_engine)
    log.info("database ready (%s)", _engine.url.render_as_string(hide_password=True))
    return _engine


def _ensure_columns(engine: Engine) -> None:
    """Tiny forward-compatible migration for SQLite development databases."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)")}
        if "worker" not in cols:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN worker VARCHAR(80)")
        if "claimed_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN claimed_at DATETIME")
        app_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(applications)")}
        if "tab_id" not in app_cols:
            conn.exec_driver_sql("ALTER TABLE applications ADD COLUMN tab_id VARCHAR(80)")


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- time helpers


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def week_start_kolkata(now: datetime | None = None) -> date:
    """Monday 00:00 Asia/Kolkata for the given instant."""
    local = (now or datetime.now(timezone.utc)).astimezone(KOLKATA)
    return (local - timedelta(days=local.weekday())).date()


def week_end_kolkata(week_start: date) -> date:
    return week_start + timedelta(days=6)


def local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=KOLKATA).astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(days=1)
    return start, end


def iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None
