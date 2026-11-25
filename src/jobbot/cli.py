"""jobbot command line."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys

from . import config, db


def _setup_logging(verbose: bool) -> None:
    config.ensure_dirs()
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.LOG_DIR / "jobbot.log", encoding="utf-8")]
    handlers[0].setFormatter(logging.Formatter("%(message)s"))
    handlers[1].setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers)
    for noisy in ("urllib3", "httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------- commands

def cmd_init(args) -> None:
    for name in ("profile", "targets"):
        src, dst = config.CONFIG_DIR / f"{name}.example.yaml", config.CONFIG_DIR / f"{name}.yaml"
        if not dst.exists():
            shutil.copy(src, dst)
            print(f"created {dst}")
    env = config.ROOT / ".env"
    if not env.exists():
        shutil.copy(config.ROOT / ".env.example", env)
        print(f"created {env}")
    db.connect().close()
    print(f"data store ready at {config.DB_PATH}")
    print("\nNext:\n  1. edit config/profile.yaml and config/targets.yaml\n"
          "  2. put your resume at resumes/base_resume.pdf\n  3. put OPENAI_API_KEY in .env\n"
          "  4. jobbot fetch && jobbot match && jobbot apply --dry-run")


def cmd_fetch(args) -> None:
    from . import fetch
    with db.connect() as conn:
        print(fetch.run(conn, force=args.force))


def cmd_match(args) -> None:
    from . import match
    with db.connect() as conn:
        print(match.run(conn, limit=args.limit))


def cmd_apply(args) -> None:
    from . import apply
    with db.connect() as conn:
        if args.dry_run:
            apply.run_dry(conn, limit=args.limit or 3, job_id=args.job)
        else:
            print(apply.run_auto(conn, limit=args.limit, headless=False if args.headful else None))


def cmd_review(args) -> None:
    from . import apply
    with db.connect() as conn:
        apply.run_review(conn)


def cmd_assist(args) -> None:
    from . import assist
    with db.connect() as conn:
        print(assist.build(conn, limit=args.limit, open_browser=not args.no_open))


def cmd_add_job(args) -> None:
    from .sources import BOARDS
    from .sources.ats_detect import detect, scan_career_page
    from .location import location_bucket, location_rejection
    d = detect(args.url)
    with db.connect() as conn:
        if d.ats in BOARDS and d.slug:
            db.add_company(conn, d.ats, d.slug, args.company)
            jobs = BOARDS[d.ats](d.slug) or []
            for j in jobs:
                if not location_bucket(j, config.targets()):
                    continue
                if args.company:
                    j["company"] = args.company
                db.upsert_job(conn, j)
            db.set_company_last_fetched(conn, d.ats, d.slug)
            if d.job_id:
                row = db.get_job_by_ats(conn, d.ats, d.slug, d.job_id)
                if row and location_bucket(row, config.targets()):
                    db.set_job_status(conn, row["id"], "matched", "added by you")
                    print(f"#{row['id']} {row['title']} queued for applying ({d.ats})")
                elif row:
                    print(f"Job id is outside India/Mumbai scope: {location_rejection(row, config.targets())}")
                else:
                    print("Job id not on the public board or outside India/Mumbai scope. Company board added.")
            else:
                print(f"Added {d.ats}:{d.slug} board with {len(jobs)} listings.")
            conn.commit()
            return
        if d.ats == "other":
            try:
                boards = scan_career_page(args.url)
            except Exception as exc:
                boards = set()
                print(f"(could not scan page: {exc})")
            for ats, slug in boards:
                if ats in BOARDS and db.add_company(conn, ats, slug, args.company):
                    print(f"found {ats} board '{slug}' on that page - it will be fetched on the next `jobbot fetch`")
        job = {"source": "manual", "ats": d.ats, "company_slug": None, "ats_job_id": None,
               "company": args.company or "?", "title": args.title or "?", "location": args.location or "",
               "remote": 0, "url": args.url, "apply_url": args.url, "description": args.desc or "",
               "posted_at": db.now()}
        reason = location_rejection(job, config.targets())
        if reason:
            print(f"Not tracked: {reason}. Remote roles must confirm India; office/hybrid roles must be in Mumbai.")
            return
        if not db.upsert_job(conn, job):
            print("Already tracked.")
            return
        row = db.get_job_by_url(conn, args.url)
        db.set_job_status(conn, row["id"], "assist", "added by you")
        conn.commit()
        why = "this site doesn't allow automation" if d.ats == "restricted" else "no supported ATS form"
        print(f"#{row['id']} saved for assist mode ({why}). Run `jobbot assist` for a tailored cover letter.")
        if not args.desc:
            print("Tip: pass --desc \"<job description>\" so the cover letter is specific.")


def cmd_add_company(args) -> None:
    from .sources import BOARDS
    from .sources.ats_detect import detect, scan_career_page
    target = args.target
    with db.connect() as conn:
        if ":" in target and not target.startswith("http"):
            ats, slug = target.split(":", 1)
            if ats not in BOARDS:
                raise SystemExit(f"unknown ATS {ats!r}; use one of {', '.join(BOARDS)}")
            found = {(ats, slug)}
        else:
            d = detect(target)
            if d.ats == "restricted":
                raise SystemExit("That site doesn't allow automation; use `jobbot add-job <url>` for single jobs.")
            found = {(d.ats, d.slug)} if d.ats in BOARDS and d.slug else scan_career_page(target)
        if not found:
            print(f"No {', '.join(BOARDS)} board found. Use `jobbot add-job <url>` for individual jobs.")
            return
        for ats, slug in found:
            if BOARDS[ats](slug) is None:
                print(f"{ats}:{slug} - board not found, skipped")
                continue
            db.add_company(conn, ats, slug, args.name)
            print(f"added {ats}:{slug}")
        conn.commit()


def cmd_mark(args) -> None:
    mapping = {"applied": "applied_manual", "submitted": "submitted", "skip": "skipped", "closed": "closed"}
    with db.connect() as conn:
        if not db.get_job(conn, args.job_id):
            raise SystemExit(f"no job #{args.job_id}")
        db.save_application(conn, args.job_id, mapping[args.status], "marked by you")
        conn.commit()
    print(f"#{args.job_id} -> {mapping[args.status]}")


def cmd_status(args) -> None:
    from . import report, scheduler
    with db.connect() as conn:
        lim = scheduler.limits()
        print(f"This week: {scheduler.applied_this_week(conn)}/{lim['weekly_target']} applied | "
              f"today: {scheduler.attempts_today(conn)}/{lim['daily_cap']} attempts | "
              f"companies tracked: {db.count_companies(conn, active=1)}")
        for status, n in report.status_counts(conn).items():
            print(f"  {status:<15} {n}")


def cmd_report(args) -> None:
    from . import report
    with db.connect() as conn:
        summary, path = report.weekly(conn)
    print("This week:", summary or "no applications yet")
    print("CSV:", path)


def cmd_run_daily(args) -> None:
    from . import apply, fetch, match, report
    log = logging.getLogger("jobbot")
    with db.connect() as conn:
        log.info("fetch: %s", fetch.run(conn))
        log.info("match: %s", match.run(conn))
        automation = config.targets().get("automation") or {}
        if not args.no_apply and automation.get("auto_submit_authorized") is True:
            log.info("apply: %s", apply.run_auto(conn, headless=False))
        elif not args.no_apply:
            log.info("automatic submission is disabled; review dry-run forms and explicitly authorize in targets.yaml")
        summary, path = report.weekly(conn)
        log.info("week so far: %s (%s)", summary, path)


def cmd_ui(args) -> None:
    from . import ui
    ui.serve(host=args.host, port=args.port, open_browser=not args.no_open)


# ---------------------------------------------------------------- parser

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jobbot", description="Policy-safe job application automation")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create config files and data store").set_defaults(fn=cmd_init)
    s = sub.add_parser("fetch", help="pull new jobs from APIs and company boards")
    s.add_argument("--force", action="store_true", help="ignore the per-source fetch interval")
    s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("match", help="filter and AI-score new jobs")
    s.add_argument("--limit", type=int, default=150, help="max jobs to AI-score this run")
    s.set_defaults(fn=cmd_match)
    s = sub.add_parser("apply", help="fill and submit forms for matched jobs")
    s.add_argument("--dry-run", action="store_true", help="fill forms in a visible browser, never submit")
    s.add_argument("--limit", type=int)
    s.add_argument("--job", type=int, help="(dry-run) try one specific job id")
    s.add_argument("--headful", action="store_true", help="show the browser")
    s.set_defaults(fn=cmd_apply)
    sub.add_parser("review", help="finish queued forms yourself (CAPTCHA / open questions)").set_defaults(fn=cmd_review)
    s = sub.add_parser("assist", help="page of jobs to apply by hand, with cover letters")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_assist)
    s = sub.add_parser("add-job", help="track a job URL (ATS link -> auto; LinkedIn/Naukri/etc -> assist)")
    s.add_argument("url")
    s.add_argument("--title")
    s.add_argument("--company")
    s.add_argument("--location")
    s.add_argument("--desc", help="job description text (improves the cover letter)")
    s.set_defaults(fn=cmd_add_job)
    s = sub.add_parser("add-company", help="add a board: ats:slug, an ATS URL, or a careers page URL")
    s.add_argument("target")
    s.add_argument("--name", help="display name for the company")
    s.set_defaults(fn=cmd_add_company)
    s = sub.add_parser("mark", help="record what you did with a job")
    s.add_argument("job_id", type=int)
    s.add_argument("status", choices=["applied", "submitted", "skip", "closed"])
    s.set_defaults(fn=cmd_mark)
    sub.add_parser("status", help="quota and pipeline counts").set_defaults(fn=cmd_status)
    sub.add_parser("report", help="this week's applications + CSV").set_defaults(fn=cmd_report)
    s = sub.add_parser("run-daily", help="fetch + match + apply (for Task Scheduler)")
    s.add_argument("--no-apply", action="store_true")
    s.set_defaults(fn=cmd_run_daily)
    s = sub.add_parser("ui", help="open the local web UI")
    s.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    s.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    s.add_argument("--no-open", action="store_true", help="don't open the browser")
    s.set_defaults(fn=cmd_ui)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows console is cp1252
        except (AttributeError, ValueError):
            pass
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    args.fn(args)


if __name__ == "__main__":
    main()
