"""Apply loop (automatic) and review loop (you finish the form in a visible browser)."""
from __future__ import annotations

import logging

from . import config, db, scheduler
from .appliers import SUPPORTED, Applier, formfill, record
from .location import location_rejection

log = logging.getLogger(__name__)


def _candidates(conn):
    rows = db.matched_jobs(conn, SUPPORTED)
    eligible = []
    for row in rows:
        reason = location_rejection(row, config.targets())
        if reason:
            db.set_job_status(conn, row["id"], "filtered", reason)
            log.info("Skipping #%s outside location scope: %s", row["id"], reason)
        else:
            eligible.append(row)
    conn.commit()
    return eligible


def run_auto(conn, limit: int | None = None, headless: bool | None = None,
             context=None, control=None) -> dict:
    stats = {"submitted": 0, "queued": 0, "failed": 0, "closed": 0, "skipped_company_limit": 0}
    automation = config.targets().get("automation") or {}
    if automation.get("auto_submit_authorized", False) is not True:
        stats["blocked"] = "live submissions are disabled; authorize automation.auto_submit_authorized in targets.yaml"
        log.warning(stats["blocked"])
        return stats
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
        headless = False

    def run(ap):
        nonlocal remaining
        first = True
        for row in jobs:
            if control and not control.checkpoint("applying"):
                break
            if remaining <= 0:
                break
            job = dict(row)
            if not scheduler.company_allowed(conn, job["company"]):
                stats["skipped_company_limit"] += 1
                continue
            if not first and not scheduler.pause(control=control):
                break
            first = False
            log.info("Applying: #%s %s @ %s (%s)", job["id"], job["title"], job["company"], job["ats"])
            out = ap.apply(conn, job, mode="auto", control=control)
            record(conn, job["id"], out)
            stats[out.status] = stats.get(out.status, 0) + 1
            log.info("  -> %s%s", out.status, f" ({out.reason})" if out.reason else "")
            if out.status in scheduler.COUNTS_AS_ATTEMPT:
                remaining -= 1
    if context is not None:
        run(Applier(headless=False, context=context))
    else:
        with Applier(headless=headless) as ap:
            run(ap)
    return stats


def run_dry(conn, limit: int = 3, job_id: int | None = None, context=None, control=None) -> dict:
    rows = ([db.get_job(conn, job_id)] if job_id else _candidates(conn)[:limit])
    rows = [r for r in rows if r]
    eligible = []
    for row in rows:
        reason = location_rejection(row, config.targets())
        if reason:
            db.set_job_status(conn, row["id"], "filtered", reason)
            log.info("Skipping #%s outside location scope: %s", row["id"], reason)
        else:
            eligible.append(row)
    conn.commit()
    rows = eligible
    if not rows:
        print("No matched jobs to try. Run `jobbot fetch` and `jobbot match` first.")
        return {"dry_run": 0, "failed": 0, "closed": 0}
    stats = {"dry_run": 0, "failed": 0, "closed": 0}

    def run(ap):
        for row in rows:
            if control and not control.checkpoint("dry_run"):
                break
            job = dict(row)
            out = ap.apply(conn, job, mode="dry", control=control)
            stats[out.status] = stats.get(out.status, 0) + 1
            print(f"\n#{job['id']} {job['title']} @ {job['company']} [{job['ats']}]")
            print(f"  result     : {out.status} - {out.reason}")
            print(f"  screenshot : {out.screenshot}")
            for k, v in (out.answers.get("values") or {}).items():
                label = (out.answers.get("labels") or {}).get(k, k)
                print(f"    {label[:60]:<60} = {str(v)[:70]}")
    if context is not None:
        run(Applier(headless=False, context=context))
    else:
        with Applier(headless=False) as ap:
            run(ap)
    return stats


def run_review(conn) -> None:
    rows = db.review_jobs(conn)
    eligible = []
    for row in rows:
        reason = location_rejection(row, config.targets())
        if reason:
            print(f"Skipping out-of-scope review job #{row['id']}: {reason}")
        else:
            eligible.append(row)
    rows = eligible
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
