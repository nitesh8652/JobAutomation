import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from jobbot import db, match
from jobbot.sources import (AGGREGATORS, BOARDS, himalayas, hn_hiring, jobicy, personio, recruitee,
                            remoteok, weworkremotely)
from jobbot.sources.ats_detect import detect, find_ats_links

TARGETS = {
    "title_include": ["engineer"],
    "title_exclude": ["manager"],
    "remote_only": True,
    "location_keep": r"\b(worldwide|anywhere|india|remote)\b",
    "posted_within_days": 30,
    "llm": {"min_score": 70},
    "auto_apply_ats": ["greenhouse", "lever", "ashby", "workable"],
}


def test_registry_has_new_sources():
    for ats in ("recruitee", "personio"):
        assert ats in BOARDS
    for name in ("remoteok", "himalayas", "jobicy", "weworkremotely", "hn_hiring", "adzuna", "jooble"):
        assert name in AGGREGATORS


def test_recruitee_normalise():
    j = {"id": 7, "title": "Backend Engineer", "company_name": "Channable", "location": "Utrecht, Netherlands",
         "remote": False, "careers_url": "https://jobs.example.com/o/be", "careers_apply_url": "https://jobs.example.com/o/be/c/new",
         "description": "<p>Build things</p>", "created_at": "2026-10-22 06:17:49 UTC", "status": "published"}
    out = recruitee.normalise("channable", j)
    assert out["ats"] == "recruitee" and out["ats_job_id"] == "7"
    assert out["apply_url"] == "https://jobs.example.com/o/be/c/new"
    assert out["description"] == "Build things"
    assert out["posted_at"] == "2026-10-22T06:17:49+00:00"


def test_personio_normalise():
    xml = """<workzag-jobs><position><id>123</id><office>Remote - Germany</office><name>Backend Engineer</name>
    <jobDescriptions><jobDescription><name>x</name><value><![CDATA[<p>Build things</p>]]></value></jobDescription></jobDescriptions>
    <createdAt>2026-11-11T17:43:42+00:00</createdAt></position></workzag-jobs>"""
    p = ET.fromstring(xml).find("position")
    out = personio.normalise("acme", p)
    assert out["ats"] == "personio" and out["remote"] == 1
    assert out["url"] == "https://acme.jobs.personio.de/job/123"
    assert out["description"] == "Build things" and out["posted_at"] == "2026-11-11T17:43:42+00:00"


def test_remoteok_normalise():
    out = remoteok.normalise({"id": 9, "position": "Backend Engineer", "company": "Acme",
                              "url": "https://remoteok.com/remote-jobs/x", "apply_url": "https://acme.test/apply",
                              "location": "Worldwide", "description": "<p>hi</p>", "date": "2026-11-23T09:40:04+00:00"})
    assert out["ats"] == "other" and out["remote"] == 1 and out["description"] == "hi"
    assert out["apply_url"] == "https://acme.test/apply"


def test_himalayas_and_jobicy_normalise():
    h = himalayas.normalise({"title": "Backend Engineer", "companyName": "Acme", "guid": "g1",
                             "applicationLink": "https://acme.test/j", "locationRestrictions": ["India"],
                             "description": "<p>hi</p>", "pubDate": "2026-11-01"})
    assert h["location"] == "India" and h["url"] == "https://acme.test/j" and h["description"] == "hi"
    jc = jobicy.normalise({"id": 3, "url": "https://jobicy.com/jobs/3", "jobTitle": "Backend Engineer",
                           "companyName": "Acme", "jobGeo": "Anywhere", "jobDescription": "<p>hi</p>"})
    assert jc["ats"] == "other" and jc["remote"] == 1 and jc["title"] == "Backend Engineer"


def test_weworkremotely_normalise():
    rss = """<rss><channel><item><title>Acme: Senior Backend Engineer</title>
    <region>Anywhere in the World</region><description><![CDATA[<p>hi</p>]]></description>
    <pubDate>Thu, 05 Nov 2026 17:12:27 +0000</pubDate>
    <link>https://weworkremotely.com/remote-jobs/x</link></item></channel></rss>"""
    out = weworkremotely.normalise(ET.fromstring(rss).find("./channel/item"))
    assert out["company"] == "Acme" and out["title"] == "Senior Backend Engineer"
    assert out["location"] == "Anywhere in the World" and out["posted_at"].startswith("2026-11-05")


def test_hn_hiring_normalise():
    out = hn_hiring.normalise({"id": 42, "created_at": "2026-11-01T00:00:00Z",
                               "text": "Acme | Senior Backend Engineer | Remote (India) | visa: no"})
    assert out["company"] == "Acme" and out["title"] == "Senior Backend Engineer"
    assert out["location"] == "Remote (India)" and out["remote"] == 1
    assert hn_hiring.normalise({"id": 43, "text": "just a comment with no pipes"}) is None


def test_detect_new_boards_and_restricted():
    d = detect("https://acme.recruitee.com/o/backend-engineer")
    assert (d.ats, d.slug, d.job_id) == ("recruitee", "acme", "backend-engineer")
    d = detect("https://acme.jobs.personio.de/job/1234567?language=en")
    assert (d.ats, d.slug, d.job_id) == ("personio", "acme", "1234567")
    assert detect("https://www.apna.co/jobs/x").ats == "restricted"
    assert detect("https://www.wellfound.com/jobs/x").ats == "restricted"
    links = find_ats_links('<a href="https://acme.recruitee.com/o/x">a</a>'
                           '<a href="https://foo.jobs.personio.de/job/1">b</a>')
    assert links == {("recruitee", "acme"), ("personio", "foo")}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(match.config, "targets", lambda: TARGETS)
    c = db.connect(tmp_path / "t.json")
    yield c
    c.close()


def _insert(conn, ats, i, company="Acme"):
    db.upsert_job(conn, {"source": ats, "ats": ats, "company_slug": "acme", "ats_job_id": str(i),
                         "company": company, "title": "Backend Engineer", "location": "Remote - India",
                         "remote": 1, "url": f"https://x/{ats}/{i}", "apply_url": f"https://x/{ats}/{i}",
                         "description": "d", "posted_at": datetime.now(timezone.utc).isoformat()})
    return db.get_job_by_url(conn, f"https://x/{ats}/{i}")["id"]


def test_recruitee_goes_to_assist_until_promoted(conn, monkeypatch):
    monkeypatch.setattr(match, "score_job", lambda job: {"score": 90, "location_ok": True, "reason": "ok"})
    gh = _insert(conn, "greenhouse", 1, "Acme")
    rc = _insert(conn, "recruitee", 2, "Channable")
    match.run(conn)
    status = {r["id"]: r["status"] for r in db.jobs(conn)}
    assert status[gh] == "matched" and status[rc] == "assist"

    monkeypatch.setattr(match.config, "targets",
                        lambda: {**TARGETS, "auto_apply_ats": ["greenhouse", "recruitee"]})
    rc2 = _insert(conn, "recruitee", 3, "Personio")
    match.run(conn)
    assert db.get_job(conn, rc2)["status"] == "matched"
