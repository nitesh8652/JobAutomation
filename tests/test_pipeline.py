from datetime import datetime, timezone

import pytest

from jobbot import config, db, scheduler
from jobbot.match import prefilter
from jobbot.sources import ashby, greenhouse, lever, remotive

TARGETS = {
    "title_include": ["engineer", "developer"],
    "title_exclude": ["manager", "intern"],
    "remote_only": True,
    "location_exclude": [r"\b(us|usa|united states)\b"],
    "location_keep": r"\b(worldwide|anywhere|india)\b",
    "posted_within_days": 30,
    "limits": {"weekly_target": 5, "daily_cap": 2, "per_company_30d": 1, "delay_seconds": [0, 0]},
}


def job(**kw):
    base = {"title": "Backend Engineer", "location": "Remote - Worldwide", "remote": 1,
            "posted_at": datetime.now(timezone.utc).isoformat(), "company": "Acme"}
    base.update(kw)
    return base


def test_prefilter():
    assert prefilter(job(), TARGETS) is None
    assert prefilter(job(title="Engineering Manager"), TARGETS).startswith("title excluded")
    assert prefilter(job(title="Designer"), TARGETS) == "title not in include list"
    assert prefilter(job(location="Remote - US"), TARGETS).startswith("location excluded")
    assert prefilter(job(location="US or India"), TARGETS) is None
    assert prefilter(job(location="Berlin", remote=0), TARGETS) == "not remote"
    assert prefilter(job(posted_at="2020-01-01T00:00:00Z"), TARGETS) == "too old"


def test_normalisers():
    gh = greenhouse.normalise("acme", {"id": 1, "title": "Dev", "location": {"name": "Remote"},
                                       "content": "&lt;p&gt;hi&lt;/p&gt;", "updated_at": "2026-01-01"})
    assert gh["url"] == "https://job-boards.greenhouse.io/acme/jobs/1" and gh["remote"] == 1 and gh["description"] == "hi"
    lv = lever.normalise("acme", {"id": "u1", "text": "Dev", "categories": {"location": "Pune"},
                                  "workplaceType": "remote", "hostedUrl": "https://jobs.lever.co/acme/u1",
                                  "descriptionPlain": "d", "createdAt": 1700000000000})
    assert lv["apply_url"] == "https://jobs.lever.co/acme/u1/apply" and lv["remote"] == 1
    ab = ashby.normalise("acme", {"id": "a1", "title": "Dev", "location": "Remote", "isRemote": True,
                                  "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "descriptionPlain": "x"})
    assert ab["apply_url"] == "https://jobs.ashbyhq.com/acme/a1/application"
    rm = remotive.normalise({"id": 9, "url": "https://remotive.com/x", "title": "Dev", "company_name": "Co"})
    assert rm["ats"] == "other" and rm["remote"] == 1


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "targets", lambda: TARGETS)
    c = db.connect(tmp_path / "t.json")
    yield c
    c.close()


def _insert(conn, i, company="Acme"):
    db.upsert_job(conn, {"source": "greenhouse", "ats": "greenhouse", "company_slug": "acme", "ats_job_id": str(i),
                         "company": company, "title": "Dev", "location": "Remote", "remote": 1,
                         "url": f"https://x/{i}", "apply_url": f"https://x/{i}", "description": "", "posted_at": None})
    return db.get_job_by_url(conn, f"https://x/{i}")["id"]


def test_dedupe_and_application_status(conn):
    jid = _insert(conn, 1)
    assert not db.upsert_job(conn, {"source": "greenhouse", "ats": "greenhouse", "company_slug": "acme",
                                    "ats_job_id": "1", "url": "https://other/1"})
    db.save_application(conn, jid, "queued", "captcha", cover_letter="hi")
    db.save_application(conn, jid, "submitted", "ok")
    app = db.get_application(conn, jid)
    assert app["status"] == "submitted" and app["cover_letter"] == "hi"
    assert db.get_job(conn, jid)["status"] == "submitted"


def test_budget_and_company_limit(conn):
    assert scheduler.budget(conn) == 2
    a = _insert(conn, 1)
    b = _insert(conn, 2, company="Other")
    assert scheduler.company_allowed(conn, "Acme")
    db.save_application(conn, a, "submitted")
    assert not scheduler.company_allowed(conn, "ACME")
    db.save_application(conn, b, "queued")
    assert scheduler.attempts_today(conn) == 2
    assert scheduler.budget(conn) == 0
    assert scheduler.applied_this_week(conn) == 1
