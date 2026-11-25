from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jobbot.v2 import repositories as repo
from jobbot.v2 import resumes as resume_service
from jobbot.v2.db import init_engine, session_scope, week_start_kolkata
from jobbot.v2.models import Application, Job, QuotaWeek
from jobbot.v2.reporting import generate_report
from jobbot.v2.security import redact


@pytest.fixture()
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'jobbot-test.db').as_posix()}"
    monkeypatch.setenv("JOBBOT_DATABASE_URL", url)
    init_engine(url)
    session = session_scope()
    store = session.__enter__()
    try:
        yield store
    finally:
        session.__exit__(None, None, None)


def make_job(db, key: str, *, company: str | None = None, title: str | None = None, location: str = "Remote, India") -> Job:
    job = repo.upsert_job(
        db,
        "greenhouse",
        {
            "ats_job_id": key,
            "company": company or f"Acme {key}",
            "title": title or "Software Engineer",
            "location": location,
            "remote": True,
            "url": f"https://example.com/jobs/{key}",
            "apply_url": f"https://example.com/jobs/{key}/apply",
            "description": "Python FastAPI React SQL",
        },
    )
    assert job is not None
    job.status = "matched"
    job.score = 90
    db.flush()
    return job


def test_week_boundary_is_monday_kolkata():
    assert week_start_kolkata(datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)) == date(2026, 11, 23)
    assert week_start_kolkata(datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)) == date(2026, 11, 16)


def test_quota_reservation_confirm_and_uncertain(db):
    job = make_job(db, "q1")
    reservation, reason = repo.reserve_submission(db, job, cap=300)
    assert reason is None and reservation is not None
    repo.confirm_submission(db, reservation)
    repo.save_application(db, job, status=repo.APPLIED, confirmation="thanks")
    db.flush()
    quota = repo.quota_snapshot(db, 300)
    assert quota["used"] == 1
    assert quota["reserved"] == 0
    assert quota["successful"] == 1
    assert quota["remaining"] == 299

    job2 = make_job(db, "q2")
    reservation2, reason2 = repo.reserve_submission(db, job2, cap=300)
    assert reason2 is None and reservation2 is not None
    repo.mark_uncertain(db, reservation2)
    repo.save_application(db, job2, status=repo.PENDING, reason="no confirmation")
    db.flush()
    quota = repo.quota_snapshot(db, 300)
    assert quota["used"] == 1
    assert quota["reserved"] == 1
    assert quota["pending_verification"] == 1
    assert quota["remaining"] == 298


def test_quota_cap_is_atomic(db):
    job1 = make_job(db, "cap1")
    reservation, reason = repo.reserve_submission(db, job1, cap=1)
    assert reason is None and reservation is not None
    job2 = make_job(db, "cap2", company="Other", title="Backend Engineer")
    blocked, reason = repo.reserve_submission(db, job2, cap=1)
    assert blocked is None
    assert reason == "weekly application cap reached"


def test_duplicate_fingerprint_is_rejected(db):
    job1 = make_job(db, "dup1", company="Acme", title="Engineer", location="Remote, India")
    reservation, _ = repo.reserve_submission(db, job1, cap=300)
    repo.confirm_submission(db, reservation)
    repo.save_application(db, job1, status=repo.APPLIED)
    job2 = make_job(db, "dup2", company="Acme", title="Engineer", location="Remote, India")
    blocked, reason = repo.reserve_submission(db, job2, cap=300)
    assert blocked is None
    assert "already applied" in (reason or "")


def test_settings_and_profile_roundtrip(db):
    values = repo.get_settings(db)
    assert values["weekly_cap"] == 300
    updated = repo.save_settings(db, {"weekly_cap": 42, "dry_run": True})
    assert updated["weekly_cap"] == 42
    profile = repo.get_profile(db)
    profile["first_name"] = "Test"
    repo.save_profile(db, profile)
    db.flush()
    assert repo.get_profile(db)["first_name"] == "Test"


def test_resume_upload_rejects_non_pdf(db):
    with pytest.raises(resume_service.ResumeError):
        asyncio.run(resume_service.save_upload(db, "x.pdf", b"not a pdf"))
    data = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    row = asyncio.run(resume_service.save_upload(db, "resume.pdf", data))
    assert row.sha256
    assert repo.list_resumes(db)[0].id == row.id


def test_excel_report_has_required_sheets_and_columns(db):
    job = make_job(db, "report1")
    repo.save_application(
        db, job, status=repo.APPLIED, confirmation="Application received", reference_id="REF-123",
        resume_name="resume.pdf", next_action="None", tab_id="tab-1", dry_run=False,
    )
    db.flush()
    buffer, name = generate_report(db, None, None, 300)
    assert name.endswith(".xlsx")
    from openpyxl import load_workbook

    workbook = load_workbook(buffer)
    assert {"All Applications", "Successful", "Pending", "Failures", "Weekly Statistics", "Platform Summary"} <= set(workbook.sheetnames)
    sheet = workbook["All Applications"]
    assert sheet.cell(row=1, column=1).value == "Serial Number"
    assert sheet.cell(row=1, column=2).value == "Application Date & Time"
    assert sheet.cell(row=1, column=19).value == "Application Reference ID"
    assert sheet.cell(row=2, column=19).value == "REF-123"
    assert sheet.cell(row=1, column=17).value == "Chrome Tab ID"
    assert sheet.cell(row=1, column=18).value == "Dry Run"


def test_api_smoke_without_browser(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    monkeypatch.setenv("JOBBOT_DATABASE_URL", url)
    from jobbot.v2.api import app

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        overview = client.get("/api/overview").json()
        assert overview["quota"]["cap"] == 300
        assert overview["authorization"]["dry_run"] is True
        settings = client.get("/api/settings").json()
        assert "greenhouse" in settings["enabled_platforms"]
        response = client.put("/api/settings", json={"weekly_cap": 150})
        assert response.status_code == 200
        assert response.json()["weekly_cap"] == 150
        bypass = client.put("/api/settings", json={"dry_run": False, "auto_submit_authorized": True})
        assert bypass.status_code == 422
        denied = client.post("/api/automation/authorize", json={"authorized": True, "confirm": "nope"})
        assert denied.status_code == 422
        allowed = client.post("/api/automation/authorize", json={"authorized": True, "confirm": "AUTHORIZE"})
        assert allowed.status_code == 200
        assert allowed.json()["dry_run"] is False
        report = client.get("/api/reports/excel")
        assert report.status_code == 200
        assert report.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_log_redaction():
    text = "token=abc123 password: hunter2 Authorization: Bearer eyJhbGciOi.xx sk-live-abcdefghijklmnop"
    cleaned = redact(text)
    assert "abc123" not in cleaned and "hunter2" not in cleaned
    assert "eyJhbGciOi" not in cleaned and "sk-live" not in cleaned
    assert "***" in cleaned


def test_aggregator_link_is_rehomed_to_supported_ats():
    from jobbot.v2.platforms.aggregators import detect_ats, rehome

    assert detect_ats("https://job-boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme", "123")
    assert detect_ats("https://jobs.lever.co/acme/1234abcd-0000-0000-0000-000000000000")[0] == "lever"
    platform, data = rehome(
        {"title": "Engineer", "company": "Acme", "url": "https://www.adzuna.in/details/1",
         "apply_url": "https://jobs.ashbyhq.com/acme/1234abcd-0000-0000-0000-000000000000"}
    )
    assert platform == "ashby"
    assert data["url"].startswith("https://jobs.ashbyhq.com/")
    assert data["ats_job_id"]
    platform, data = rehome({"title": "Engineer", "url": "https://example.com/job", "source": "jooble"})
    assert platform == "jooble"


def test_aggregator_discovery_rehomes_and_blocks(db, monkeypatch):
    from jobbot.v2.discovery import discover_aggregators
    from jobbot.v2.platforms import aggregators as agg

    def fake_discover(name, settings, limit=None):
        return [
            {"title": "Backend Engineer", "company": "Acme", "location": "Remote - India", "remote": 1,
             "source": "adzuna", "url": "https://www.adzuna.in/details/1",
             "apply_url": "https://job-boards.greenhouse.io/acme/jobs/999", "description": "Python"},
            {"title": "Backend Engineer", "company": "Other", "location": "Remote - India", "remote": 1,
             "source": "adzuna", "url": "https://www.adzuna.in/details/2", "apply_url": "https://other.example/apply",
             "description": "Python"},
        ]

    monkeypatch.setattr(agg, "discover", fake_discover)
    values = repo.get_settings(db)
    values["enabled_aggregators"] = ["adzuna"]
    db.commit()
    stats = asyncio.run(discover_aggregators(values))
    assert stats["new"] == 2
    assert stats["rehomed"] == 1
    assert stats["blocked"] == 1
    db.expire_all()
    rehomed = db.scalar(select(Job).where(Job.platform == "greenhouse", Job.platform_job_id == "999"))
    assert rehomed is not None and rehomed.status == "new"
    blocked = db.scalar(select(Job).where(Job.platform == "adzuna", Job.url == "https://www.adzuna.in/details/2"))
    assert blocked is not None and blocked.status == "blocked"


def test_company_limit_blocks_second_role(db):
    job1 = make_job(db, "co1", company="Acme", title="Engineer", location="Remote - India")
    reservation, _ = repo.reserve_submission(db, job1, cap=300, per_company_limit=1)
    repo.confirm_submission(db, reservation)
    repo.save_application(db, job1, status=repo.APPLIED)
    job2 = make_job(db, "co2", company="Acme", title="Senior Engineer", location="Remote - India")
    blocked, reason = repo.reserve_submission(db, job2, cap=300, per_company_limit=1)
    assert blocked is None
    assert "company limit" in (reason or "")


def test_manual_confirmation_converts_uncertain_reservation_to_used(db):
    job = make_job(db, "uncertain-confirm")
    reservation, _ = repo.reserve_submission(db, job, cap=300)
    repo.mark_uncertain(db, reservation)
    repo.save_application(db, job, status=repo.PENDING, reason="unknown")
    db.flush()
    assert repo.quota_snapshot(db, 300)["reserved"] == 1
    repo.confirm_submission(db, reservation)
    repo.save_application(db, job, status=repo.APPLIED, reason="manually confirmed")
    db.flush()
    quota = repo.quota_snapshot(db, 300)
    assert quota["used"] == 1 and quota["reserved"] == 0 and quota["successful"] == 1


def test_confirmation_after_release_consumes_capacity(db):
    job = make_job(db, "released-confirm")
    reservation, _ = repo.reserve_submission(db, job, cap=300)
    repo.release_submission(db, reservation)
    repo.save_application(db, job, status=repo.REVIEW, reason="manual review")
    db.flush()
    assert repo.quota_snapshot(db, 300)["reserved"] == 0
    repo.confirm_submission(db, reservation)
    repo.save_application(db, job, status=repo.APPLIED, reason="manually confirmed")
    db.flush()
    quota = repo.quota_snapshot(db, 300)
    assert quota["used"] == 1 and quota["successful"] == 1


def test_browser_tab_acquisition_is_unique_and_bounded():
    from jobbot.v2.browser import BrowserSession

    class Page:
        def __init__(self):
            self.closed = False
        def is_closed(self):
            return self.closed

    class Context:
        def __init__(self):
            self.pages = []
        async def new_page(self):
            page = Page()
            self.pages.append(page)
            return page

    session = BrowserSession({"max_tabs": 2})
    session.context = Context()
    first = asyncio.run(session.acquire_tab())
    second = asyncio.run(session.acquire_tab())
    assert first is not second
    try:
        asyncio.run(session.acquire_tab())
    except RuntimeError as exc:
        assert "maximum" in str(exc)
    else:
        raise AssertionError("expected a max-tabs error")


def test_heuristic_scoring_never_calls_network(db):
    from jobbot.v2.ai import score_job

    values = repo.get_settings(db)
    values["ai_provider"] = "heuristic"
    result = asyncio.run(score_job(
        {"title": "Backend Engineer", "company": "Acme", "location": "Remote - India", "remote": 1,
         "description": "Python FastAPI SQL Docker"},
        {"summary": "Python backend engineer", "skills": ["Python", "FastAPI"]},
        "Python FastAPI SQL Docker AWS", values,
    ))
    assert result["provider"] == "heuristic"
    assert result["eligible"] is True and result["location_ok"] is True
    assert 0 <= result["score"] <= 100


def test_v2_prefilter_rejects_stale_and_off_location(db):
    from jobbot.v2.ai import prefilter_job

    values = repo.get_settings(db)
    base = {"title": "Backend Engineer", "location": "Remote - India", "remote": 1,
            "posted_at": datetime.now(timezone.utc).isoformat(), "description": "Python"}
    assert prefilter_job(base, values) is None
    old = {**base, "posted_at": "2020-01-01T00:00:00Z"}
    assert prefilter_job(old, values) == "too old"
    assert prefilter_job({**base, "location": "Remote - Worldwide"}, values).startswith("remote eligibility")


def test_daily_cap_is_enforced(db):
    job1 = make_job(db, "day1", company="Acme", title="Engineer")
    reservation, _ = repo.reserve_submission(db, job1, cap=300, daily_cap=1)
    repo.confirm_submission(db, reservation)
    repo.save_application(db, job1, status=repo.APPLIED)
    job2 = make_job(db, "day2", company="Other", title="Engineer")
    blocked, reason = repo.reserve_submission(db, job2, cap=300, daily_cap=1)
    assert blocked is None
    assert reason == "daily application cap reached"


def test_released_reservation_can_be_reused_for_a_retry(db):
    job = make_job(db, "retry", company="Acme", title="Engineer")
    first, _ = repo.reserve_submission(db, job, cap=300)
    repo.release_submission(db, first)
    repo.save_application(db, job, status=repo.FAILED, reason="temporary failure")
    repo.reset_job(db, job.id)
    second, reason = repo.reserve_submission(db, job, cap=300)
    assert second is not None and reason is None
    repo.confirm_submission(db, second)
    quota = repo.quota_snapshot(db, 300)
    assert quota["used"] == 1 and quota["reserved"] == 0
