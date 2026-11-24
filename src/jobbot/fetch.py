"""Fetch pipeline: aggregators -> company discovery -> ATS boards -> link duplicates."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import config, db
from .match import prefilter
from .sources import AGGREGATORS, BOARDS
from .text import norm, slug_candidates

log = logging.getLogger(__name__)


def _due(conn, key: str, min_hours: float, force: bool) -> bool:
    if force:
        return True
    last = db.meta_get(conn, key)
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(hours=min_hours)


def seed_companies(conn) -> None:
    for ats, slugs in (config.targets().get("companies") or {}).items():
        if ats in BOARDS:
            for slug in slugs or []:
                db.add_company(conn, ats, str(slug))


def fetch_aggregators(conn, force: bool = False) -> dict[str, int]:
    stats: dict[str, int] = {}
    t = config.targets()
    for name, fn in AGGREGATORS.items():
        cfg = t.get(name) or {}
        if not cfg.get("enabled", True):
            continue
        key = f"last_fetch_{name}"
        if not _due(conn, key, float(cfg.get("min_hours_between_fetches", 12)), force):
            log.info("%s: fetched recently, skipping (use --force to override)", name)
            continue
        try:
            jobs = fn(cfg)
        except Exception as exc:  # one bad source must not stop the run
            log.warning("%s fetch failed: %s", name, exc)
            continue
        stats[name] = sum(db.upsert_job(conn, j) for j in jobs)
        db.meta_set(conn, key, db.now())
        conn.commit()
        log.info("%s: %d listings, %d new", name, len(jobs), stats[name])
    return stats


def _titles_overlap(board_jobs: list[dict], titles: set[str]) -> bool:
    return any(norm(j.get("title")) in titles for j in board_jobs)


def discover_companies(conn, max_companies: int) -> int:
    """Find the ATS board behind companies seen on aggregators.

    A board only counts as a match when it lists one of the same job titles, so
    `acme` on Lever is not mistaken for a different Acme."""
    t = config.targets()
    rows = [r for r in db.jobs(conn, ats="other", status="new") if r["source"] in AGGREGATORS]
    by_company: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for r in rows:
        if prefilter(dict(r), t) is not None:
            continue  # don't spend requests on companies whose jobs we'd skip anyway
        key = norm(r["company"])
        if not key:
            continue
        by_company.setdefault(key, set()).add(norm(r["title"]))
        names[key] = r["company"]
    known = db.probe_names(conn)
    found = 0
    for key in [k for k in by_company if k not in known][:max_companies]:
        result = ""
        for slug in slug_candidates(names[key])[:2]:
            for ats in BOARDS:
                try:
                    board = BOARDS[ats](slug)
                except Exception as exc:
                    log.debug("probe %s:%s failed: %s", ats, slug, exc)
                    continue
                if board and _titles_overlap(board, by_company[key]):
                    result = f"{ats}:{slug}"
                    break
            if result:
                break
        db.set_probe(conn, key, result)
        if result:
            ats, slug = result.split(":", 1)
            db.add_company(conn, ats, slug, names[key])
            found += 1
            log.info("discovered %s -> %s", names[key], result)
        conn.commit()
    return found


def fetch_boards(conn, force: bool = False) -> dict[str, int]:
    stats = {"boards": 0, "listings": 0, "new": 0, "closed": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    for c in db.companies(conn, active=1):
        if c["ats"] not in BOARDS:
            continue
        if not force and c["last_fetched"] and c["last_fetched"] > cutoff:
            continue
        try:
            jobs = BOARDS[c["ats"]](c["slug"])
        except Exception as exc:
            log.warning("%s:%s fetch failed: %s", c["ats"], c["slug"], exc)
            continue
        if jobs is None:
            log.warning("%s:%s board not found - deactivating", c["ats"], c["slug"])
            db.set_company_active(conn, c["ats"], c["slug"], False)
            conn.commit()
            continue
        for j in jobs:
            if c["name"]:
                j["company"] = c["name"]
            stats["new"] += db.upsert_job(conn, j)
        live = [j["ats_job_id"] for j in jobs]
        # Jobs that vanished from the board are closed; don't try to apply to them.
        stats["closed"] += db.close_missing_board_jobs(conn, c["ats"], c["slug"], live)
        db.set_company_last_fetched(conn, c["ats"], c["slug"])
        conn.commit()
        stats["boards"] += 1
        stats["listings"] += len(jobs)
    return stats


def link_duplicates(conn) -> int:
    """Aggregator listings that also exist on a company's ATS board become 'duplicate'
    (the ATS copy is the one we can actually apply to)."""
    links = db.probe_links(conn)
    board_titles: dict[tuple[str, str], int] = {}
    for r in db.jobs(conn):
        if r["ats"] in ("greenhouse", "lever", "ashby", "workable"):
            board_titles[(f"{r['ats']}:{r['company_slug']}", norm(r["title"]))] = r["id"]
    n = 0
    for r in db.jobs(conn, ats="other"):
        if r["status"] not in ("new", "matched", "assist"):
            continue
        link = links.get(norm(r["company"]))
        target = board_titles.get((link, norm(r["title"]))) if link else None
        if target:
            db.set_job_status(conn, r["id"], "duplicate", f"same as job #{target}")
            n += 1
    conn.commit()
    return n


def run(conn, force: bool = False) -> dict:
    t = config.targets()
    seed_companies(conn)
    conn.commit()
    stats: dict = {"aggregators": fetch_aggregators(conn, force)}
    disc = t.get("discovery") or {}
    if disc.get("enabled", True):
        stats["discovered"] = discover_companies(conn, int(disc.get("max_companies_per_run", 25)))
    stats["boards"] = fetch_boards(conn, force)
    stats["duplicates"] = link_duplicates(conn)
    return stats
