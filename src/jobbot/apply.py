"""Apply loop (automatic) and review loop (you finish the form in a visible browser)."""
from __future__ import annotations

import logging

from . import db, scheduler
from .appliers import SUPPORTED, Applier, formfill, record

log = logging.getLogger(__name__)


def _candidates(conn):
    return db.matched_jobs(conn, SUPPORTED)


def run_auto(conn, limit: int | None = None, headless: bool | None = None) -> dict:
    stats = {"submitted": 0, "queued": 0, "failed": 0, "closed": 0, "skipped_company_limit": 0}
    remaining = scheduler.budget(conn)
    if limit is not None:
        remaining = min(remaining, limit)
    if remaining <= 0:
        log.info("Daily cap or weekly target reached - nothing to do.")
        return stats
    jobs = _candidates(conn)
    if not jobs:
        return stats
    if headless is None:
        headless = scheduler.limits()["headless"]
    first = True
    with Applier(headless=headless) as ap:
        for row in jobs:
            if remaining <= 0:
                break
            job = dict(row)
            if not scheduler.company_allowed(conn, job["company"]):
                stats["skipped_company_limit"] += 1
                continue
            if not first:
                scheduler.pause()
            first = False
            log.info("Applying: #%s %s @ %s (%s)", job["id"], job["title"], job["company"], job["ats"])
            out = ap.apply(conn, job, mode="auto")
            record(conn, job["id"], out)
            stats[out.status] = stats.get(out.status, 0) + 1
            log.info("  -> %s%s", out.status, f" ({out.reason})" if out.reason else "")
            if out.status in scheduler.COUNTS_AS_ATTEMPT:
                remaining -= 1
    return stats


def run_dry(conn, limit: int = 3, job_id: int | None = None) -> None:
    rows = ([db.get_job(conn, job_id)] if job_id else _candidates(conn)[:limit])
    rows = [r for r in rows if r]
    if not rows:
        print("No matched jobs to try. Run `jobbot fetch` and `jobbot match` first.")
        return
    with Applier(headless=False) as ap:
        for row in rows:
            job = dict(row)
            out = ap.apply(conn, job, mode="dry")
            print(f"\n#{job['id']} {job['title']} @ {job['company']} [{job['ats']}]")
            print(f"  result     : {out.status} - {out.reason}")
            print(f"  screenshot : {out.screenshot}")
            for k, v in (out.answers.get("values") or {}).items():
                label = (out.answers.get("labels") or {}).get(k, k)
                print(f"    {label[:60]:<60} = {str(v)[:70]}")


def run_review(conn) -> None:
    rows = db.review_jobs(conn)
    if not rows:
        print("Review queue is empty.")
        return
    print(f"{len(rows)} application(s) need you. A browser window will open with each form pre-filled.")
    with Applier(headless=False) as ap:
        for i, row in enumerate(rows, 1):
            job = dict(row)
            print(f"\n[{i}/{len(rows)}] #{job['id']} {job['title']} @ {job['company']}")
            print(f"  why: {job['app_reason']}")
            choice = input("  Enter = open form | s = skip for now | x = drop this job | q = quit: ").strip().lower()
            if choice == "q":
                break
            if choice == "s":
                continue
            if choice == "x":
                db.save_application(conn, job["id"], "skipped", "dropped in review")
                conn.commit()
                continue
            out = ap.apply(conn, job, mode="interactive")
            if out.status == "closed":
                record(conn, job["id"], out)
                print("  posting is closed - marked closed")
                continue
            if out.status == "failed" or out.page is None:
                print(f"  could not open form: {out.reason}")
                continue
            unresolved = out.answers.get("unresolved") or []
            if unresolved:
                print("  Please fill these yourself:")
                for u in unresolved:
                    print(f"   - {u}")
            print("  Check everything, solve any CAPTCHA, and click Submit in the browser.")
            ans = input("  Press Enter once submitted (n = didn't submit, x = drop job): ").strip().lower()
            page = out.page
            if ans == "x":
                out.status, out.reason = "skipped", "dropped in review"
            elif ans == "n":
                out.status, out.reason = "queued", job["app_reason"]
            else:
                ok = formfill.check_success(page)
                if not ok:
                    ok = input("  No confirmation seen on the page. Mark as submitted anyway? [y/N]: ").strip().lower() == "y"
                out.status, out.reason = ("submitted", "submitted in review") if ok else ("queued", job["app_reason"])
            out.screenshot = ap._screenshot(page, job["id"], "review") or out.screenshot
            page.close()
            record(conn, job["id"], out)
            print(f"  -> {out.status}")
