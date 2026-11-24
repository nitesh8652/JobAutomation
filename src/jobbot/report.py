"""Weekly report: counts + CSV export."""
from __future__ import annotations

import csv
from datetime import datetime

from . import config, db, scheduler


def status_counts(conn) -> dict[str, int]:
    return db.status_counts(conn)


def weekly(conn) -> tuple[dict, str]:
    since = scheduler.week_start_utc()
    rows = db.applications_with_jobs(conn, since)
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    config.ensure_dirs()
    year, week, _ = datetime.now().isocalendar()
    path = config.REPORTS_DIR / f"applications-{year}-W{week:02d}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["when_utc", "status", "reason", "job_id", "company", "title", "ats", "score", "url"])
        for r in rows:
            w.writerow([r["updated_at"], r["status"], r["reason"], r["id"], r["company"], r["title"],
                        r["ats"], r["score"], r["url"]])
    return summary, str(path)
