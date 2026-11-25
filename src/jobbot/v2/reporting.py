"""Multi-sheet Excel report generation (openpyxl)."""
from __future__ import annotations

import io
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import KOLKATA, week_start_kolkata
from .models import Application, Job, QuotaWeek
from .repositories import APPLIED, BLOCKED, DUPLICATE, FAILED, PENDING, REVIEW, SKIPPED

COLUMNS = [
    ("Serial Number", 14),
    ("Application Date & Time", 22),
    ("Job Platform", 16),
    ("Company Name", 28),
    ("Job Title", 36),
    ("Job Location", 24),
    ("Salary Range", 20),
    ("Experience Required", 20),
    ("Job Description URL", 48),
    ("Application URL", 48),
    ("AI Match Score", 14),
    ("Resume Used", 28),
    ("Application Status", 24),
    ("Submission Confirmation", 36),
    ("Failure Reason", 42),
    ("Next Action", 34),
    ("Chrome Tab ID", 14),
    ("Dry Run", 10),
    ("Application Reference ID", 24),
]

STATUS_FILLS = {
    APPLIED: "C6EFCE",
    PENDING: "FFEB9C",
    REVIEW: "FCE4D6",
    FAILED: "FFC7CE",
    DUPLICATE: "D9E1F2",
    SKIPPED: "EDEDED",
    BLOCKED: "E4DFEC",
}


def _local_parts(value: datetime | None) -> tuple[str, str]:
    if value is None:
        return "", ""
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    local = aware.astimezone(KOLKATA)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S")


def _row(app: Application, job: Job, serial: int) -> list[Any]:
    applied = app.applied_at or app.created_at
    day, clock = _local_parts(applied)
    return [
        serial, f"{day} {clock}".strip(), job.platform, job.company, job.title, job.location,
        job.salary or "", job.experience or "", job.url, job.apply_url or "",
        job.score if job.score is not None else "", app.resume_name or "",
        app.status, app.confirmation or "", app.failure_reason or "",
        app.next_action or "", app.tab_id or "", bool(app.dry_run),
        app.reference_id or "",
    ]


def _style_sheet(ws, title: str) -> None:
    ws.freeze_panes = "A2"
    for index, (name, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=index, value=name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F2937")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.row_dimensions[1].height = 30
    ws.title = title


def _write_rows(ws, rows: list[tuple[Application, Job]]) -> None:
    for serial, (app, job) in enumerate(rows, 1):
        ws.append(_row(app, job, serial))
        fill = STATUS_FILLS.get(app.status)
        if fill:
            ws.cell(row=ws.max_row, column=13).fill = PatternFill("solid", fgColor=fill)
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def generate_report(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    cap: int = 300,
) -> tuple[io.BytesIO, str]:
    start = start or week_start_kolkata()
    end = end or (start + timedelta(days=6))
    if end < start:
        start, end = end, start
    stmt = (
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .order_by(Application.updated_at.desc())
    )
    all_rows: list[tuple[Application, Job]] = []
    for app, job in db.execute(stmt).all():
        when = (app.applied_at or app.created_at)
        if when is None:
            continue
        local_day = when.replace(tzinfo=timezone.utc).astimezone(KOLKATA).date()
        if start <= local_day <= end:
            all_rows.append((app, job))

    workbook = Workbook()
    all_sheet = workbook.active
    _style_sheet(all_sheet, "All Applications")
    _write_rows(all_sheet, all_rows)

    groups = {
        "Successful": [row for row in all_rows if row[0].status == APPLIED],
        "Pending": [row for row in all_rows if row[0].status == PENDING],
        "Failures": [row for row in all_rows if row[0].status in (FAILED, REVIEW, BLOCKED, DUPLICATE, SKIPPED)],
    }
    for title, rows in groups.items():
        sheet = workbook.create_sheet(title)
        _style_sheet(sheet, title)
        _write_rows(sheet, rows)

    stats_sheet = workbook.create_sheet("Weekly Statistics")
    stats_sheet.append(["Week Start (Mon)", "Week End (Sun)", "Cap", "Submitted", "Reserved/Pending",
                        "Remaining", "Successful", "Pending Verification", "Failed", "Requires Review",
                        "Duplicates Skipped", "Total Records"])
    for index, width in enumerate([18, 18, 10, 12, 20, 12, 12, 20, 10, 16, 18, 14], 1):
        stats_sheet.column_dimensions[get_column_letter(index)].width = width
        stats_sheet.cell(row=1, column=index).font = Font(bold=True, color="FFFFFF")
        stats_sheet.cell(row=1, column=index).fill = PatternFill("solid", fgColor="1F2937")
    by_week: dict[date, list[Application]] = defaultdict(list)
    for app, _job in all_rows:
        by_week[app.week_start or start].append(app)
    for week in sorted(by_week):
        rows = by_week[week]
        quota = db.get(QuotaWeek, week)
        counts = Counter(row.status for row in rows)
        used = int(quota.used) if quota else counts.get(APPLIED, 0)
        reserved = int(quota.reserved) if quota else counts.get(PENDING, 0)
        stats_sheet.append([
            week.isoformat(), (week + timedelta(days=6)).isoformat(), int(quota.cap) if quota else cap,
            used, reserved, max(0, (int(quota.cap) if quota else cap) - used - reserved),
            counts.get(APPLIED, 0), counts.get(PENDING, 0), counts.get(FAILED, 0),
            counts.get(REVIEW, 0), counts.get(DUPLICATE, 0), len(rows),
        ])
    stats_sheet.freeze_panes = "A2"

    platform_sheet = workbook.create_sheet("Platform Summary")
    platform_sheet.append(["Platform", "Submitted", "Pending Verification", "Failed",
                           "Requires Review", "Duplicate", "Skipped", "Blocked", "Total"])
    for index, width in enumerate([18, 12, 20, 10, 16, 12, 12, 12, 10], 1):
        platform_sheet.column_dimensions[get_column_letter(index)].width = width
        platform_sheet.cell(row=1, column=index).font = Font(bold=True, color="FFFFFF")
        platform_sheet.cell(row=1, column=index).fill = PatternFill("solid", fgColor="1F2937")
    by_platform: dict[str, Counter] = defaultdict(Counter)
    for app, job in all_rows:
        by_platform[job.platform][app.status] += 1
    for platform, counts in sorted(by_platform.items()):
        platform_sheet.append([
            platform, counts.get(APPLIED, 0), counts.get(PENDING, 0), counts.get(FAILED, 0),
            counts.get(REVIEW, 0), counts.get(DUPLICATE, 0), counts.get(SKIPPED, 0),
            counts.get(BLOCKED, 0), sum(counts.values()),
        ])
    platform_sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    name = f"jobbot-applications-{start.isoformat()}-to-{end.isoformat()}.xlsx"
    return buffer, name
