"""Local web UI for jobbot.

A dependency-free HTTP server (stdlib only) that serves a small single-page app and
reuses the existing modules: reads come straight from the JSON store, while anything
that fetches, scores, applies or writes files is run as a ``jobbot`` subprocess so the
UI never blocks and the CLI keeps a single source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import yaml

from . import config, db, scheduler
from .location import location_bucket

log = logging.getLogger("jobbot.ui")

WEB_DIR = Path(__file__).resolve().parent / "web"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

STATUS_MAP = {
    "applied": "applied_manual",
    "submitted": "submitted",
    "skip": "skipped",
    "closed": "closed",
}

DEFAULT_LIMITS = {
    "weekly_target": 300, "daily_cap": 45, "per_company_30d": 3,
    "delay_seconds": [30, 120], "headless": True,
}


# --------------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_int(value, default: int, lo: int = 0, hi: int = 100000) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def safe_limits() -> dict:
    try:
        return scheduler.limits()
    except SystemExit:
        return dict(DEFAULT_LIMITS)


def _pythonpath() -> str:
    src = str(Path(__file__).resolve().parents[1])
    current = os.environ.get("PYTHONPATH", "")
    parts = [src] + [p for p in current.split(os.pathsep) if p]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return os.pathsep.join(out)


def _task_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ---------------------------------------------------------------- task runner

class Task:
    MAX_LINES = 4000

    def __init__(self, tid: int, name: str, argv: list[str], new_console: bool = False):
        self.id = tid
        self.name = name
        self.argv = argv
        self.new_console = new_console
        self.status = "queued"          # queued | running | done | failed | stopped
        self.created_at = _now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.stop_requested = False
        self.lock = threading.Lock()

    def add(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > self.MAX_LINES:
                del self.lines[: len(self.lines) - self.MAX_LINES]

    def log(self) -> list[str]:
        with self.lock:
            return list(self.lines)

    def to_json(self, tail: int = 0) -> dict:
        lines = self.log()
        out = {
            "id": self.id, "name": self.name, "status": self.status,
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "returncode": self.returncode,
            "command": "jobbot " + " ".join(self.argv), "line_count": len(lines),
        }
        if tail:
            out["lines"] = lines[-tail:]
        return out


class TaskManager:
    def __init__(self):
        self._q: queue.Queue[Task | None] = queue.Queue()
        self._tasks: dict[int, Task] = {}
        self._order: list[int] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="jobbot-tasks", daemon=True)
        self._worker.start()

    def submit(self, name: str, argv: list[str], new_console: bool = False) -> Task:
        with self._lock:
            self._seq += 1
            task = Task(self._seq, name, argv, new_console)
            self._tasks[task.id] = task
            self._order.append(task.id)
        self._q.put(task)
        return task

    def get(self, tid: int) -> Task | None:
        with self._lock:
            return self._tasks.get(tid)

    def list(self, limit: int = 50) -> list[Task]:
        with self._lock:
            ids = self._order[-limit:]
            return [self._tasks[i] for i in reversed(ids)]

    def stop(self, tid: int) -> bool:
        task = self.get(tid)
        if task is None:
            return False
        task.stop_requested = True
        if task.status == "queued":
            task.status = "stopped"
            task.finished_at = _now()
            return True
        if task.status == "running" and task.proc and task.proc.poll() is None:
            try:
                task.proc.terminate()
            except OSError:
                return False
        return True

    def _run(self) -> None:
        while True:
            task = self._q.get()
            if task is None:
                return
            if task.status == "stopped":
                continue
            task.status = "running"
            task.started_at = _now()
            try:
                if task.new_console:
                    self._run_console(task)
                else:
                    self._run_captured(task)
            except Exception as exc:  # never let one task kill the worker
                task.add(f"[ui] task crashed: {exc}")
                task.status = "failed"
                task.returncode = -1
            finally:
                task.finished_at = task.finished_at or _now()
                if task.status == "running":
                    task.status = "done" if task.returncode == 0 else "failed"

    def _run_console(self, task: Task) -> None:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        cmd = [sys.executable, "-m", "jobbot.cli", *task.argv]
        if os.name == "nt":
            cmd = ["cmd.exe", "/k", subprocess.list2cmdline(cmd)]
        task.proc = subprocess.Popen(cmd, cwd=str(config.ROOT), env=_task_env(),
                                     creationflags=flags)
        task.add("[ui] launched in a separate console window - finish the forms there.")
        task.returncode = 0
        task.status = "done"

    def _run_captured(self, task: Task) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        task.proc = subprocess.Popen(
            [sys.executable, "-m", "jobbot.cli", *task.argv],
            cwd=str(config.ROOT), env=_task_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags,
        )
        assert task.proc.stdout is not None
        for line in task.proc.stdout:
            task.add(line.rstrip("\r\n"))
        task.returncode = task.proc.wait()
        if task.stop_requested:
            task.status = "stopped"
        else:
            task.status = "done" if task.returncode == 0 else "failed"


TASKS = TaskManager()
_AGENT = None
_AGENT_LOCK = threading.Lock()


def browser_agent():
    global _AGENT
    with _AGENT_LOCK:
        if _AGENT is None:
            from .agent import BrowserJobAgent
            _AGENT = BrowserJobAgent()
        return _AGENT


# -------------------------------------------------------------- task commands

def _arg_text(value, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > 20000:
        raise ValueError(f"{name} is too long")
    return text


def build_task(key: str, p: dict) -> tuple[str, list[str], bool]:
    if key == "init":
        return "Initialize workspace", ["init"], False
    if key == "fetch":
        return "Fetch new jobs", ["fetch"] + (["--force"] if p.get("force") else []), False
    if key == "match":
        argv = ["match"]
        if p.get("limit"):
            argv += ["--limit", str(_as_int(p["limit"], 150, 1, 2000))]
        return "Score new jobs", argv, False
    if key == "apply_dry":
        argv = ["apply", "--dry-run"]
        if p.get("limit"):
            argv += ["--limit", str(_as_int(p["limit"], 3, 1, 50))]
        if p.get("job"):
            argv += ["--job", str(_as_int(p["job"], 0, 1))]
        return "Dry-run applications", argv, False
    if key == "apply":
        argv = ["apply"]
        if p.get("limit"):
            argv += ["--limit", str(_as_int(p["limit"], 1, 1, 500))]
        if p.get("headful"):
            argv += ["--headful"]
        return "Apply to matched jobs", argv, False
    if key == "review":
        return "Review queued forms", ["review"], os.name == "nt"
    if key == "assist":
        argv = ["assist"]
        if p.get("limit"):
            argv += ["--limit", str(_as_int(p["limit"], 30, 1, 200))]
        if p.get("no_open"):
            argv += ["--no-open"]
        return "Build assist page", argv, False
    if key == "report":
        return "Export weekly report", ["report"], False
    if key == "run_daily":
        return "Run daily pipeline", ["run-daily"] + (["--no-apply"] if p.get("no_apply") else []), False
    if key == "add_job":
        url = _arg_text(p.get("url"), "url")
        argv = ["add-job", url]
        for flag, field in (("--title", "title"), ("--company", "company"),
                            ("--location", "location"), ("--desc", "desc")):
            if str(p.get(field) or "").strip():
                argv += [flag, str(p[field]).strip()]
        return "Track job URL", argv, False
    if key == "add_company":
        target = _arg_text(p.get("target"), "target")
        argv = ["add-company", target]
        if str(p.get("name") or "").strip():
            argv += ["--name", str(p["name"]).strip()]
        return "Add company board", argv, False
    raise ValueError(f"unknown task {key!r}")


# ------------------------------------------------------------------ api reads

def _application_brief(app: dict | None) -> dict | None:
    if not app:
        return None
    return {k: app.get(k) for k in ("status", "reason", "cover_letter", "screenshot",
                                    "created_at", "updated_at")}


def summary_payload() -> dict:
    config.ensure_dirs()
    limits = safe_limits()
    out = {
        "limits": limits,
        "config_ready": (config.CONFIG_DIR / "profile.yaml").exists()
        and (config.CONFIG_DIR / "targets.yaml").exists(),
        "has_profile": (config.CONFIG_DIR / "profile.yaml").exists(),
        "has_targets": (config.CONFIG_DIR / "targets.yaml").exists(),
        "has_resume": any((config.ROOT / "resumes").glob("*.pdf")),
        "has_api_key": bool(os.environ.get("OPENAI_API_KEY")),
        "paths": {
            "root": str(config.ROOT),
            "data": str(config.DATA_DIR),
            "config": str(config.CONFIG_DIR),
            "db": str(config.DB_PATH),
            "reports": str(config.REPORTS_DIR),
            "screenshots": str(config.SHOTS_DIR),
        },
    }
    with db.connect() as conn:
        try:
            out["week_applied"] = scheduler.applied_this_week(conn)
            out["pending_verification"] = scheduler.pending_verification_this_week(conn)
            out["today_attempts"] = scheduler.attempts_today(conn)
            out["budget"] = scheduler.budget(conn)
        except (SystemExit, Exception):
            out["week_applied"] = out["pending_verification"] = out["today_attempts"] = out["budget"] = 0
        out["companies_active"] = db.count_companies(conn, active=1)
        out["counts"] = db.status_counts(conn)
        out["review_count"] = len(db.review_jobs(conn))
        out["assist_count"] = len(db.assist_jobs(conn, limit=100000))
        out["matched_count"] = len(db.matched_jobs(conn, ("greenhouse", "lever", "ashby", "workable")))
        out["total_jobs"] = len(db.jobs(conn))
        out["recent_applications"] = list(reversed(
            db.applications_with_jobs(conn, scheduler.week_start_utc())))[:8]
    out["tasks"] = [t.to_json(tail=2) for t in TASKS.list(limit=6)]
    return out


def jobs_payload(params: dict) -> dict:
    status = (params.get("status") or "all").strip()
    ats = (params.get("ats") or "all").strip()
    query = (params.get("q") or "").strip().lower()
    sort = (params.get("sort") or "newest").strip()
    limit = _as_int(params.get("limit"), 100, 1, 500)
    offset = _as_int(params.get("offset"), 0, 0)
    config.ensure_dirs()
    with db.connect() as conn:
        all_rows = db.jobs(conn)
        counts = db.status_counts(conn)
    atses = sorted({str(r.get("ats") or "other") for r in all_rows})
    rows = all_rows
    if status == "all":
        rows = [r for r in rows if location_bucket(r, config.targets())
                or r.get("status") in ("submitted", "applied_manual")]
    else:
        rows = [r for r in rows if r.get("status") == status]
    if ats != "all":
        rows = [r for r in rows if r.get("ats") == ats]
    if query:
        def hit(r):
            return query in " ".join(str(r.get(k) or "") for k in
                                     ("title", "company", "location", "source", "ats")).lower()
        rows = [r for r in rows if hit(r)]
    def _ts(r):
        v = r.get("posted_at") or r.get("fetched_at") or ""
        return str(v) if not isinstance(v, str) else v

    if sort == "score":
        rows.sort(key=lambda r: (r.get("score") is not None, r.get("score") or 0, _ts(r)), reverse=True)
    elif sort == "company":
        rows.sort(key=lambda r: (str(r.get("company") or "").lower(), r.get("title") or ""))
    elif sort == "status":
        rows.sort(key=lambda r: (str(r.get("status") or ""), r.get("title") or ""))
    else:
        rows.sort(key=_ts, reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit]
    fields = ("id", "title", "company", "location", "remote", "ats", "url", "apply_url",
              "score", "score_reason", "status", "status_reason", "posted_at",
              "fetched_at", "source", "company_slug")
    return {
        "total": total, "offset": offset, "limit": limit, "counts": counts,
        "atses": atses,
        "jobs": [{k: r.get(k) for k in fields} for r in page],
    }


def job_payload(job_id: int) -> dict | None:
    with db.connect() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            return None
        job["application"] = _application_brief(db.get_application(conn, job_id))
        return job


def review_payload() -> list[dict]:
    with db.connect() as conn:
        rows = db.review_jobs(conn)
    rows = [r for r in rows if location_bucket(r, config.targets())]
    fields = ("id", "title", "company", "location", "ats", "url", "score", "status",
              "status_reason", "app_reason", "posted_at")
    return [{k: r.get(k) for k in fields} for r in rows]


def assist_payload(limit: int = 200) -> list[dict]:
    with db.connect() as conn:
        rows = db.assist_jobs(conn, limit=10000)
    rows = [r for r in rows if location_bucket(r, config.targets())][:limit]
    fields = ("id", "title", "company", "location", "ats", "url", "score", "score_reason",
              "status", "cover_letter", "posted_at")
    return [{k: r.get(k) for k in fields} for r in rows]


def companies_payload() -> dict:
    with db.connect() as conn:
        rows = db.companies(conn)
        jobs = db.jobs(conn)
    open_counts: dict[str, int] = {}
    for j in jobs:
        if j.get("status") in ("new", "matched"):
            key = str(j.get("company") or "").lower()
            open_counts[key] = open_counts.get(key, 0) + 1
    for row in rows:
        row["open_jobs"] = open_counts.get(str(row.get("name") or row.get("slug") or "").lower(), 0)
    rows.sort(key=lambda r: (r.get("active", 0) != 1, str(r.get("name") or r.get("slug") or "").lower()))
    return {"companies": rows, "total": len(rows),
            "active": sum(1 for r in rows if r.get("active") == 1)}


def config_payload() -> dict:
    out = {"paths": {"config": str(config.CONFIG_DIR), "data": str(config.DATA_DIR),
                     "root": str(config.ROOT)},
           "limits": safe_limits(), "files": {}}
    for name in ("profile", "targets"):
        path = config.CONFIG_DIR / f"{name}.yaml"
        out["files"][name] = {
            "exists": path.exists(),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
        }
    out["has_api_key"] = bool(os.environ.get("OPENAI_API_KEY"))
    out["has_resume"] = any((config.ROOT / "resumes").glob("*.pdf"))
    return out


def save_config_payload(name: str, content: str) -> dict:
    if name not in ("profile", "targets"):
        raise ValueError("unknown config file")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if parsed is not None and not isinstance(parsed, dict):
        raise ValueError("the file must contain a YAML mapping")
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CONFIG_DIR / f"{name}.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    config.profile.cache_clear()
    config.targets.cache_clear()
    return {"ok": True, "path": str(path)}


_SKILL_ALIASES = {
    "Python": ("python",), "JavaScript": ("javascript", "js"), "TypeScript": ("typescript", "ts"),
    "Java": ("java",), "Go": ("golang", "go language"), "Rust": ("rust",),
    "C++": ("c++", "cpp"), "C#": ("c#", "c sharp"), "Ruby": ("ruby",), "PHP": ("php",),
    "React": ("react",), "Next.js": ("next.js", "nextjs"), "React Native": ("react native",),
    "Vue": ("vue", "vue.js", "vuejs"), "Angular": ("angular",), "Node.js": ("node.js", "nodejs"),
    "Express": ("express.js", "expressjs"), "Django": ("django",), "FastAPI": ("fastapi",),
    "Flask": ("flask",), "Spring Boot": ("spring boot",), "SQL": ("sql",),
    "PostgreSQL": ("postgresql", "postgres"), "MySQL": ("mysql",), "MongoDB": ("mongodb",),
    "Redis": ("redis",), "Elasticsearch": ("elasticsearch", "opensearch"), "Kafka": ("kafka",),
    "AWS": ("aws", "amazon web services"), "Google Cloud": ("gcp", "google cloud", "google cloud platform"),
    "Azure": ("azure",), "Docker": ("docker",), "Kubernetes": ("kubernetes", "k8s"),
    "Terraform": ("terraform",), "CI/CD": ("ci/cd", "continuous integration", "continuous delivery"),
    "GitHub Actions": ("github actions",), "Linux": ("linux",), "Git": ("git",),
    "System design": ("system design",), "Distributed systems": ("distributed systems",),
    "Microservices": ("microservices",), "Event-driven systems": ("event-driven", "event driven"),
    "REST APIs": ("rest api", "restful api"), "GraphQL": ("graphql",), "OAuth": ("oauth",),
    "Automated testing": ("automated testing", "test automation"), "Pytest": ("pytest",),
    "Jest": ("jest",), "Data structures": ("data structures",), "Algorithms": ("algorithms",),
    "Machine learning": ("machine learning", "ml models"), "Deep learning": ("deep learning",),
    "LLMs": ("llm", "large language model"), "RAG": ("rag", "retrieval augmented generation"),
    "Prompt engineering": ("prompt engineering",), "LangChain": ("langchain",),
    "Vector databases": ("vector database", "vector databases", "vector db"),
    "Embeddings": ("embeddings", "embedding models"), "OpenAI API": ("openai api", "openai apis"),
    "PyTorch": ("pytorch",), "TensorFlow": ("tensorflow",),
    "scikit-learn": ("scikit-learn", "sklearn"), "Agile": ("agile",),
}


def skill_gaps_payload() -> dict:
    """Count startup-relevant skills across in-scope job descriptions and compare with the resume."""
    import re as _re
    from .resume import resume_text
    from .match import prefilter

    resume_available = False
    try:
        resume_text.cache_clear()
        resume = resume_text()
        resume_available = bool(resume.strip())
    except (Exception, SystemExit):
        resume = ""
    with db.connect() as conn:
        all_jobs = db.jobs(conn)
    targets = config.targets()
    terminal = {"filtered", "closed", "duplicate", "skipped"}
    eligible = [j for j in all_jobs if j.get("status") not in terminal
                and j.get("description") and prefilter(j, targets) is None]
    total = len(eligible)
    patterns = {
        skill: [_re.compile(r"(?<!\w)" + _re.escape(alias) + r"(?!\w)", _re.I) for alias in aliases]
        for skill, aliases in _SKILL_ALIASES.items()
    }
    counts: dict[str, list[dict]] = {}
    for job in eligible:
        description = str(job.get("description") or "")
        for skill, skill_patterns in patterns.items():
            if any(pattern.search(description) for pattern in skill_patterns):
                counts.setdefault(skill, []).append(job)

    gaps, present = [], []
    for skill, jobs in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0].lower())):
        in_resume = resume_available and any(
            _re.search(r"(?<!\w)" + _re.escape(alias) + r"(?!\w)", resume, _re.I)
            for alias in _SKILL_ALIASES[skill]
        )
        examples = [{"company": j.get("company") or "Unknown company", "title": j.get("title") or "Job"}
                    for j in jobs[:3]]
        entry = {"skill": skill, "jobs": len(jobs),
                 "pct": round(len(jobs) / total * 100) if total else 0,
                 "in_resume": in_resume, "examples": examples}
        if resume_available:
            (present if in_resume else gaps).append(entry)
    return {"gaps": gaps[:25], "present": present[:25], "total_jobs_analysed": total,
            "eligible_job_descriptions": total, "resume_available": resume_available,
            "top_skills": [
                {"skill": skill, "jobs": len(jobs),
                 "pct": round(len(jobs) / total * 100) if total else 0,
                 "examples": [{"company": j.get("company") or "Unknown company",
                               "title": j.get("title") or "Job"} for j in jobs[:3]]}
                for skill, jobs in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0].lower()))[:25]
            ]}


def target_roles_payload() -> dict:
    configured = config.targets().get("target_roles") or list(config.DEFAULT_TARGET_ROLES)
    options = list(dict.fromkeys([*config.DEFAULT_TARGET_ROLES, *configured]))
    return {"options": options, "selected": configured}


def save_target_roles_payload(body: dict) -> dict:
    roles = body.get("roles")
    if not isinstance(roles, list):
        raise ValueError("roles must be a list")
    clean = list(dict.fromkeys(str(role).strip()[:100] for role in roles if str(role).strip()))
    if not clean:
        raise ValueError("select at least one target role")
    if len(clean) > 40:
        raise ValueError("select no more than 40 target roles")
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CONFIG_DIR / "target_roles.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    config.targets.cache_clear()
    return target_roles_payload()


def extension_skill_match_payload(description: str) -> dict:
    from .resume import resume_text

    text = str(description or "")[:20000]
    if not text.strip():
        raise ValueError("job description is required")
    try:
        resume_text.cache_clear()
        resume = resume_text()
    except (Exception, SystemExit):
        resume = ""
    found = []
    in_resume = []
    for skill, aliases in _SKILL_ALIASES.items():
        if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I) for alias in aliases):
            found.append(skill)
            if resume and any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", resume, re.I)
                              for alias in aliases):
                in_resume.append(skill)
    gaps = [skill for skill in found if skill not in in_resume]
    return {"resume_available": bool(resume.strip()), "job_skills": found,
            "resume_skills_in_posting": in_resume, "not_found_in_resume": gaps,
            "match_pct": round(len(in_resume) / len(found) * 100) if found else 0}


def extension_searches_payload(query: str = "software engineer") -> dict:
    """Search links for a user-operated job search; no sites are crawled by this endpoint."""
    import re as _re

    query = " ".join(str(query or "software engineer").split())[:120] or "software engineer"
    q = quote_plus(query)
    slug = _re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "software-engineer"
    hirist_slug = _re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "software-engineer"
    searches = [
        {"site": "Naukri ? India", "url": f"https://www.naukri.com/{slug}-jobs-in-india"},
        {"site": "Indeed ? India", "url": f"https://in.indeed.com/jobs?q={q}&l=India"},
        {"site": "Wellfound", "url": f"https://wellfound.com/jobs?query={q}"},
        {"site": "Y Combinator ? Work at a Startup", "url": f"https://www.workatastartup.com/jobs?query={q}"},
        {"site": "Hirist.tech ? role search", "url": f"https://www.hirist.tech/k/{hirist_slug}-jobs"},
    ]
    automation = config.targets().get("automation") or {}
    try:
        max_tabs = max(2, min(6, int(automation.get("max_browser_tabs", 6))))
    except (TypeError, ValueError):
        max_tabs = 6
    return {
        "query": query,
        "location_rule": "Remote roles: India only · On-site/hybrid roles: Mumbai only",
        "max_browser_tabs": max_tabs,
        "searches": searches[:max_tabs - 1],
    }


def applied_payload() -> dict:
    """Return all confirmed submitted and manually-applied jobs, newest first."""
    with db.connect() as conn:
        rows = db.applications_with_jobs(conn, "")
    rows = [r for r in rows if r.get("status") in ("submitted", "applied_manual")]
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    companies_by_key = {}
    for row in rows:
        company = str(row.get("company") or "Unknown company").strip()
        companies_by_key.setdefault(company.casefold(), company)
    companies = sorted(companies_by_key.values(), key=str.lower)
    return {"applications": rows, "companies": companies, "total": len(rows)}


def add_extension_job(body: dict) -> dict:
    """Import one job only after the extension user explicitly requests it."""
    from urllib.parse import urlsplit

    from .location import location_bucket, location_rejection

    url = str(body.get("url") or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("job URL must be an http(s) URL")
    host = parsed.hostname.lower().removeprefix("www.")
    allowed = ("naukri.com", "indeed.com", "indeed.co.in", "wellfound.com",
               "workatastartup.com", "hirist.tech")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise ValueError("open a job from Naukri, Indeed, Wellfound, Work at a Startup, or Hirist.tech")

    email = str(body.get("hr_email") or "").strip()[:254]
    if email and not re.fullmatch(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", email, re.I):
        email = ""
    job = {
        "source": "browser_extension", "ats": "other", "company_slug": None, "ats_job_id": None,
        "company": str(body.get("company") or "").strip()[:200] or "Unknown company",
        "title": str(body.get("title") or "").strip()[:300],
        "location": str(body.get("location") or "").strip()[:300],
        "remote": 1 if body.get("remote") else 0, "url": url, "apply_url": url,
        "description": str(body.get("description") or "")[:20000], "posted_at": None,
        "hr_email": email or None,
    }
    if not job["title"]:
        raise ValueError("job title is required")
    targets = config.targets()
    scope = location_bucket(job, targets)
    if not scope:
        raise ValueError(location_rejection(job, targets))
    job["remote"] = 1 if scope == "remote_india" else 0

    with db.connect() as conn:
        created = db.upsert_job(conn, job)
        row = db.get_job_by_url(conn, url)
        if row and created:
            db.set_job_status(conn, row["id"], "assist", "tracked from the browser extension")
        conn.commit()
    if not row:
        raise ValueError("could not save this job")
    return {"ok": True, "created": created,
            "job": {"id": row["id"], "title": row.get("title"), "company": row.get("company"),
                    "status": row.get("status")}}


def report_payload() -> dict:
    config.ensure_dirs()
    since = scheduler.week_start_utc()
    with db.connect() as conn:
        rows = db.applications_with_jobs(conn, since)
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    year, week, _ = datetime.now().isocalendar()
    csv_path = config.REPORTS_DIR / f"applications-{year}-W{week:02d}.csv"
    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {"summary": summary, "rows": rows, "csv": str(csv_path) if csv_path.exists() else None}


# ------------------------------------------------------------------ http layer

class Handler(BaseHTTPRequestHandler):
    server_version = "jobbot-ui/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console readable
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- responses
    def _send(self, status: int, body: bytes, content_type: str, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = _as_int(self.headers.get("Content-Length"), 0, 0, 2_000_000)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("request body must be JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    # -- routing
    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _trusted_post_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # local command-line clients may omit Origin
        if origin.startswith("chrome-extension://"):
            return True
        parsed = urlparse(origin)
        try:
            server_port = int(self.server.server_address[1])
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except (TypeError, ValueError):
            return False
        return (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                and origin_port == server_port)

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if method == "POST" and not self._trusted_post_origin():
                return self._error(403, "cross-origin request blocked")
            if path.startswith("/api/"):
                self._api(method, path, parse_qs(parsed.query))
            else:
                self._static(path)
        except ValueError as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - surface a message instead of a stack
            log.exception("request failed: %s %s", method, path)
            self._error(500, str(exc))

    def _api(self, method: str, path: str, query: dict) -> None:
        parts = [p for p in path.strip("/").split("/") if p]
        # /api/<name>/...
        name = parts[1] if len(parts) > 1 else ""
        rest = parts[2:]

        if method == "GET":
            if name == "agent":
                return self._json(browser_agent().snapshot())
            if name == "summary":
                return self._json(summary_payload())
            if name == "jobs" and not rest:
                flat = {k: v[0] for k, v in query.items() if v}
                return self._json(jobs_payload(flat))
            if name == "jobs" and rest:
                job = job_payload(_as_int(rest[0], 0, 0))
                return self._json(job) if job else self._error(404, "job not found")
            if name == "review":
                return self._json({"jobs": review_payload()})
            if name == "assist":
                return self._json({"jobs": assist_payload()})
            if name == "companies":
                return self._json(companies_payload())
            if name == "config":
                return self._json(config_payload())
            if name == "roles":
                return self._json(target_roles_payload())
            if name == "report":
                return self._json(report_payload())
            if name == "skill-gaps":
                return self._json(skill_gaps_payload())
            if name == "applied":
                return self._json(applied_payload())
            if name == "extension" and rest and rest[0] == "searches":
                query_text = (query.get("q") or ["software engineer"])[0]
                return self._json(extension_searches_payload(query_text))
            if name == "extension" and rest and rest[0] == "roles":
                return self._json(target_roles_payload())
            if name == "extension" and rest and rest[0] == "applied":
                return self._json(applied_payload())
            if name == "log":
                tail = _as_int((query.get("tail") or ["300"])[0], 300, 10, 2000)
                try:
                    log_path = config.LOG_DIR / "jobbot.log"
                    if log_path.exists():
                        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                        return self._json({"lines": lines[-tail:], "total": len(lines)})
                    return self._json({"lines": [], "total": 0})
                except Exception as exc:
                    return self._error(500, str(exc))
            if name == "tasks":
                if rest:
                    task = TASKS.get(_as_int(rest[0], 0, 0))
                    if not task:
                        return self._error(404, "task not found")
                    data = task.to_json()
                    data["lines"] = task.log()
                    return self._json(data)
                limit = _as_int((query.get("limit") or ["50"])[0], 50, 1, 200)
                return self._json({"tasks": [t.to_json(tail=2) for t in TASKS.list(limit)]})
            return self._error(404, "not found")

        if method == "POST":
            body = self._body()
            if name == "agent" and rest == ["start"]:
                return self._json(browser_agent().start(body.get("role")), status=202)
            if name == "agent" and rest == ["pause"]:
                return self._json(browser_agent().pause())
            if name == "agent" and rest == ["resume"]:
                return self._json(browser_agent().resume())
            if name == "agent" and rest == ["stop"]:
                return self._json(browser_agent().stop())
            if name == "agent" and rest == ["emergency-stop"]:
                return self._json(browser_agent().stop(emergency=True))
            if name == "jobs" and rest and rest[-1] == "mark":
                job_id = _as_int(rest[0], 0, 0)
                status = str(body.get("status") or "")
                return self._mark(job_id, status)
            if name == "jobs" and rest and rest[-1] == "cover-letter":
                return self._save_cover(_as_int(rest[0], 0, 0), str(body.get("content") or ""))
            if name == "extension" and rest == ["jobs"]:
                return self._json(add_extension_job(body), status=201)
            if name == "extension" and rest == ["skills"]:
                return self._json(extension_skill_match_payload(str(body.get("description") or "")))
            if name == "roles":
                return self._json(save_target_roles_payload(body))
            if name == "tasks":
                if rest and rest[-1] == "stop":
                    ok = TASKS.stop(_as_int(rest[0], 0, 0))
                    return self._json({"ok": ok})
                return self._create_task(body)
            if name == "config":
                return self._json(save_config_payload(str(body.get("name") or ""),
                                                      str(body.get("content") or "")))
            return self._error(404, "not found")

        return self._error(405, "method not allowed")

    def _mark(self, job_id: int, status: str) -> None:
        config.ensure_dirs()
        with db.connect() as conn:
            job = db.get_job(conn, job_id)
            if not job:
                return self._error(404, "job not found")
            if status in STATUS_MAP:
                db.save_application(conn, job_id, STATUS_MAP[status], "marked in the UI")
            elif status in ("new", "matched", "assist"):
                db.set_job_status(conn, job_id, status, "marked in the UI")
            else:
                return self._error(400, "unknown status")
            conn.commit()
        self._json({"ok": True, "job": job_payload(job_id)})

    def _save_cover(self, job_id: int, content: str) -> None:
        config.ensure_dirs()
        with db.connect() as conn:
            job = db.get_job(conn, job_id)
            if not job:
                return self._error(404, "job not found")
            app = db.get_application(conn, job_id) or {}
            status = app.get("status") or "assist"
            reason = app.get("reason") or "edited in the UI"
            db.save_application(conn, job_id, status, reason, cover_letter=content)
            conn.commit()
        self._json({"ok": True})

    def _create_task(self, body: dict) -> None:
        key = str(body.get("task") or "")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        name, argv, new_console = build_task(key, params)
        task = TASKS.submit(name, argv, new_console=new_console)
        self._json({"task": task.to_json()}, status=202)

    # -- static files
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        file = (WEB_DIR / rel).resolve()
        root = WEB_DIR.resolve()
        if root not in file.parents and file != root:
            return self._error(404, "not found")
        if not file.is_file():
            file = root / "index.html"  # single-page app fallback
            if not file.is_file():
                return self._error(404, "not found")
        body = file.read_bytes()
        ctype = MIME.get(file.suffix.lower(), "application/octet-stream")
        self._send(200, body, ctype, cache=file.suffix.lower() in (".css", ".js", ".svg"))


# --------------------------------------------------------------------- server

def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    config.ensure_dirs()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"jobbot UI running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
