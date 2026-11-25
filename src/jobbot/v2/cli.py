"""Small operational CLI for the v2 agent."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import repositories as repo
from . import settings as cfg
from .db import init_engine, session_scope
from .reporting import generate_report


def cmd_init(_args) -> int:
    cfg.ensure_dirs()
    init_engine()
    with session_scope() as db:
        repo.get_settings(db)
        repo.get_profile(db)
        recovered = repo.recover_stale(db)
        repo.log_activity(db, "startup", result=f"init; recovered {recovered}")
    print("database initialised")
    return 0


def cmd_platforms(_args) -> int:
    for key, info in cfg.PLATFORM_CATALOG.items():
        print(f"{key:14} {info['kind']:10} auto_apply={info['auto_apply']}  {info['policy']}")
    return 0


def cmd_report(args) -> int:
    init_engine()
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    with session_scope() as db:
        values = repo.get_settings(db)
        buffer, name = generate_report(db, start, end, int(values.get("weekly_cap", 300)))
    out = Path(args.out or (cfg.REPORT_DIR / name))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buffer.getvalue())
    print(out)
    return 0


def cmd_recover(_args) -> int:
    init_engine()
    with session_scope() as db:
        print(repo.recover_stale(db))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("jobbot.v2.api:app", host=args.host or cfg.bind_host(), port=args.port or cfg.bind_port(), reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobbot-v2", description="Autonomous job application agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create tables and seed settings/profile").set_defaults(func=cmd_init)
    sub.add_parser("platforms", help="list configured platforms").set_defaults(func=cmd_platforms)
    report = sub.add_parser("report", help="write an Excel report")
    report.add_argument("--start")
    report.add_argument("--end")
    report.add_argument("--out")
    report.set_defaults(func=cmd_report)
    sub.add_parser("recover", help="mark stale submissions as pending verification").set_defaults(func=cmd_recover)
    serve = sub.add_parser("serve", help="run the FastAPI dashboard")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
