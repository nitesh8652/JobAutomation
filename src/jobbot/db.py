"""JSON-file storage: jobs, applications, companies, discovery probes, meta.

Everything lives in a single JSON document (data/jobbot.json). It is loaded into memory
when you call :func:`connect`, changed through the helpers below, and written back
atomically on ``commit()``. No SQL and no database server.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import config

JOB_COLS = ("source", "ats", "company_slug", "ats_job_id", "company", "title", "location",
            "remote", "url", "apply_url", "description", "posted_at")

APP_FIELDS = ("cover_letter", "answers_json", "screenshot")

_JOB_DEFAULTS = {
    "source": None, "ats": "other", "company_slug": None, "ats_job_id": None, "company": None,
    "title": None, "location": None, "remote": 0, "url": None, "apply_url": None,
    "description": None, "posted_at": None, "fetched_at": None, "status": "new",
    "status_reason": None, "score": None, "score_reason": None,
}

_EMPTY = {"jobs": [], "applications": [], "companies": [], "probes": {},
          "meta": {}, "seq": {"jobs": 0, "applications": 0}}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_text(value) -> str:
    return "" if value is None else str(value)


def timestamp(value) -> float:
    """Sortable epoch for an ISO/RFC date or an epoch number (seconds or milliseconds)."""
    if value is None or value == "" or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 1e11 else number
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        return number / 1000.0 if number > 1e11 else number
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def score_value(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return -1


def _normalise_ts(value):
    """Store epoch timestamps as ISO strings so every source sorts the same way."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        number = number / 1000.0 if number > 1e11 else number
        try:
            return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


class Store:
    """In-memory view of the JSON file with a commit/close lifecycle."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self._load()
        self._dirty = False

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
        else:
            data = {}
        for key, default in _EMPTY.items():
            data.setdefault(key, json.loads(json.dumps(default)))
        return data

    def _touch(self) -> None:
        self._dirty = True

    def commit(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False

    def close(self) -> None:
        self.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> bool:
        self.commit()
        return False

    # ------------------------------------------------------------- internals
    def _next_id(self, table: str) -> int:
        seq = self.data["seq"]
        seq[table] = int(seq.get(table, 0)) + 1
        return seq[table]

    def _find(self, table: str, **match):
        for row in self.data[table]:
            if all(row.get(k) == v for k, v in match.items()):
                return row
        return None


def connect(path=None) -> Store:
    if path is None:
        config.ensure_dirs()
        path = config.DB_PATH
    return Store(path)


# ------------------------------------------------------------------- jobs

def upsert_job(store: Store, job: dict) -> bool:
    """Insert a job if unseen. Returns True when it was new."""
    url = job.get("url")
    if url and store._find("jobs", url=url):
        return False
    ats, slug, ats_id = job.get("ats"), job.get("company_slug"), job.get("ats_job_id")
    if ats_id is not None and slug is not None and store._find(
            "jobs", ats=ats, company_slug=slug, ats_job_id=ats_id):
        return False
    record = {key: job.get(key, default) for key, default in _JOB_DEFAULTS.items()}
    record["remote"] = 1 if job.get("remote") else 0
    record["posted_at"] = _normalise_ts(job.get("posted_at"))
    record["fetched_at"] = now()
    record["id"] = store._next_id("jobs")
    store.data["jobs"].append(record)
    store._touch()
    return True


def get_job(store: Store, job_id: int):
    row = store._find("jobs", id=job_id)
    return dict(row) if row else None


def get_job_by_url(store: Store, url: str):
    row = store._find("jobs", url=url)
    return dict(row) if row else None


def get_job_by_ats(store: Store, ats: str, company_slug: str, ats_job_id: str):
    row = store._find("jobs", ats=ats, company_slug=company_slug, ats_job_id=ats_job_id)
    return dict(row) if row else None


def jobs(store: Store, **match) -> list[dict]:
    return [dict(row) for row in store.data["jobs"]
            if all(row.get(k) == v for k, v in match.items())]


def set_job_status(store: Store, job_id: int, status: str, reason: str | None = None) -> None:
    row = store._find("jobs", id=job_id)
    if row is not None:
        row["status"] = status
        row["status_reason"] = reason
        store._touch()


def set_job_score(store: Store, job_id: int, score: int | None, reason: str | None) -> None:
    row = store._find("jobs", id=job_id)
    if row is not None:
        row["score"] = score
        row["score_reason"] = reason
        store._touch()


def status_counts(store: Store) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in store.data["jobs"]:
        status = row.get("status")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def new_jobs_for_scoring(store: Store, auto_ats) -> list[dict]:
    """New jobs, auto-appliable ATSes first, newest postings first."""
    rows = jobs(store, status="new")
    rows.sort(key=lambda j: j.get("posted_at") or "", reverse=True)
    rows.sort(key=lambda j: 0 if j.get("ats") in auto_ats else 1)
    return rows


def matched_jobs(store: Store, supported) -> list[dict]:
    rows = [j for j in jobs(store, status="matched") if j.get("ats") in supported]

    def key(j):
        score = j.get("score")
        return (score is not None, score or 0, j.get("posted_at") or "")

    rows.sort(key=key, reverse=True)
    return rows


def close_missing_board_jobs(store: Store, ats: str, slug: str, live_ids) -> int:
    """Close queued jobs that no longer appear on the company's board."""
    live = set(live_ids)
    n = 0
    for row in store.data["jobs"]:
        if (row.get("ats") == ats and row.get("company_slug") == slug
                and row.get("status") in ("new", "matched")
                and row.get("ats_job_id") not in live):
            row["status"] = "closed"
            row["status_reason"] = "removed from board"
            n += 1
    if n:
        store._touch()
    return n


# ------------------------------------------------------------- applications

def _find_application(store: Store, job_id: int):
    return store._find("applications", job_id=job_id)


def save_application(store: Store, job_id: int, status: str, reason: str | None = None, **fields) -> None:
    """Create/update the application row for a job and mirror the status onto the job."""
    cols = {k: v for k, v in fields.items() if k in APP_FIELDS and v is not None}
    ts = now()
    row = _find_application(store, job_id)
    if row is None:
        row = {"id": store._next_id("applications"), "job_id": job_id, "status": status,
               "reason": reason, "cover_letter": None, "answers_json": None,
               "screenshot": None, "created_at": ts, "updated_at": ts}
        store.data["applications"].append(row)
    row.update(cols)
    row["status"] = status
    row["reason"] = reason
    row["updated_at"] = ts
    store._touch()
    set_job_status(store, job_id, status, reason)


def get_application(store: Store, job_id: int):
    row = _find_application(store, job_id)
    return dict(row) if row else None


def applications(store: Store, **match) -> list[dict]:
    return [dict(row) for row in store.data["applications"]
            if all(row.get(k) == v for k, v in match.items())]


def count_applications(store: Store, statuses, field: str, since: str) -> int:
    return sum(1 for row in store.data["applications"]
               if row.get("status") in statuses and (row.get(field) or "") >= since)


def application_companies_since(store: Store, since: str, statuses) -> list[str]:
    companies = []
    for row in store.data["applications"]:
        if row.get("status") not in statuses or (row.get("created_at") or "") < since:
            continue
        job = store._find("jobs", id=row.get("job_id"))
        if job and job.get("company"):
            companies.append(job["company"])
    return companies


def applications_with_jobs(store: Store, since: str) -> list[dict]:
    out = []
    for app in store.data["applications"]:
        if (app.get("updated_at") or "") < since:
            continue
        job = store._find("jobs", id=app.get("job_id")) or {}
        out.append({
            "updated_at": app.get("updated_at"), "status": app.get("status"),
            "reason": app.get("reason"), "id": job.get("id"), "company": job.get("company"),
            "title": job.get("title"), "ats": job.get("ats"), "score": job.get("score"),
            "url": job.get("url"),
        })
    out.sort(key=lambda r: r.get("updated_at") or "")
    return out


def review_jobs(store: Store) -> list[dict]:
    apps = [a for a in store.data["applications"] if a.get("status") in ("queued", "failed")]
    apps.sort(key=lambda a: a.get("updated_at") or "")
    out = []
    for app in apps:
        job = store._find("jobs", id=app.get("job_id"))
        if job:
            merged = dict(job)
            merged["app_reason"] = app.get("reason")
            out.append(merged)
    return out


def assist_jobs(store: Store, limit: int = 30) -> list[dict]:
    rows = jobs(store, status="assist")

    def key(j):
        score = j.get("score")
        return (score is not None, score or 0, j.get("posted_at") or "")

    rows.sort(key=key, reverse=True)
    apps = {a.get("job_id"): a for a in store.data["applications"]}
    for row in rows:
        row["cover_letter"] = (apps.get(row["id"]) or {}).get("cover_letter")
    return rows[:limit]


# --------------------------------------------------------------- companies

def add_company(store: Store, ats: str, slug: str, name: str | None = None) -> bool:
    if store._find("companies", ats=ats, slug=slug):
        return False
    store.data["companies"].append({
        "ats": ats, "slug": slug, "name": name, "active": 1,
        "added_at": now(), "last_fetched": None,
    })
    store._touch()
    return True


def companies(store: Store, active: int | None = None) -> list[dict]:
    return [dict(row) for row in store.data["companies"]
            if active is None or row.get("active") == active]


def count_companies(store: Store, active: int | None = None) -> int:
    return len(companies(store, active=active))


def set_company_active(store: Store, ats: str, slug: str, active: bool) -> None:
    row = store._find("companies", ats=ats, slug=slug)
    if row is not None:
        row["active"] = 1 if active else 0
        store._touch()


def set_company_last_fetched(store: Store, ats: str, slug: str, when: str | None = None) -> None:
    row = store._find("companies", ats=ats, slug=slug)
    if row is not None:
        row["last_fetched"] = when or now()
        store._touch()


# ------------------------------------------------------------------- probes

def probe_names(store: Store) -> set[str]:
    return set(store.data["probes"].keys())


def set_probe(store: Store, name: str, result: str) -> None:
    store.data["probes"][name] = {"name": name, "result": result, "checked_at": now()}
    store._touch()


def probe_links(store: Store) -> dict[str, str]:
    return {name: p.get("result") for name, p in store.data["probes"].items() if p.get("result")}


# --------------------------------------------------------------------- meta

def meta_get(store: Store, key: str) -> str | None:
    return store.data["meta"].get(key)


def meta_set(store: Store, key: str, value: str) -> None:
    store.data["meta"][key] = value
    store._touch()
