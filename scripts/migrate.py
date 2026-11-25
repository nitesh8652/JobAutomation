"""Run the MySQL migration files in order.

Usage:
    .venv\\Scripts\\python scripts\\migrate.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def statements(sql: str):
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        lines.append(line)
    for chunk in re.split(r";\s*(?:\r?\n|$)", "\n".join(lines)):
        if chunk.strip():
            yield chunk.strip()


def main() -> int:
    url = os.environ.get("JOBBOT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set JOBBOT_DATABASE_URL (for example mysql+pymysql://jobbot:pass@127.0.0.1:3306/jobbot?charset=utf8mb4).")
        return 2
    engine = create_engine(url, pool_pre_ping=True)
    files = sorted((ROOT / "migrations").glob("*.sql"))
    if not files:
        print("No migrations found.")
        return 1
    with engine.begin() as conn:
        for path in files:
            print(f"applying {path.name}")
            for statement in statements(path.read_text(encoding="utf-8")):
                conn.execute(text(statement))
    print(f"applied {len(files)} migration file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
