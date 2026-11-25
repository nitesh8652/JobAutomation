"""FastAPI backend and local dashboard API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import repositories as repo, resumes as resume_service
from . import settings as cfg
from .db import get_db, init_engine, ping, session_scope, week_start_kolkata
from .models import Activity, Application, Job, QuotaWeek, Resume
from .platforms import ATS_IDS, platform_infos
from .reporting import generate_report
from .security import setup_logging
from .service import hub, orchestrator

log = logging.getLogger("jobbot.v2.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg.ensure_dirs()
    setup_logging()
    init_engine()
    try:
        with session_scope() as db:
            repo.get_settings(db)
            repo.get_profile(db)
            recovered = repo.recover_stale(db)
            for info in platform_infos():
                repo.platform_state(db, info.id)
            repo.log_activity(db, "startup", result=f"recovered: {recovered}")
    except Exception:
        log.exception("startup database initialisation failed")
    yield
    await orchestrator.shutdown()


app = FastAPI(title="Jobbot Autonomous Agent", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def token_guard(request: Request, call_next):
    token = cfg.api_token()
    if token and request.url.path.startswith("/api"):
        supplied = request.query_params.get("token") or request.headers.get("x-api-token") or ""
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:]
        if supplied != token:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# --------------------------------------------------------------------------- helpers


def _activity(row: Activity) -> dict[str, Any]:
    return {
        "id": row.id, "ts": row.ts.isoformat() if row.ts else None, "level": row.level,
        "tab_id": row.tab_id, "platform": row.platform, "action": row.action,
        "job_id": row.job_id, "job_title": row.job_title, "url": row.url, "result": row.result,
    }


def _resume(row: Resume) -> dict[str, Any]:
    return {
        "id": row.id, "name": row.name, "original_name": row.original_name,
        "size_bytes": row.size_bytes, "is_default": row.is_default,
        "has_text": bool(row.text_content), "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _profile_completeness(profile: dict[str, Any]) -> dict[str, Any]:
    required = ("first_name", "last_name", "email", "phone", "location", "resume_path")
    present = [key for key in required if profile.get(key)]
    return {"present": len(present), "total": len(required), "percent": round(100 * len(present) / len(required))}


# --------------------------------------------------------------------------- health / overview


@app.get("/api/health")
def health():
    return {"ok": True, "database": ping(), "agent": orchestrator.state, "time": datetime.now().isoformat()}


@app.get("/api/platforms")
def platforms(db: Session = Depends(get_db)):
    values = repo.get_settings(db)
    enabled = set(values.get("enabled_platforms") or [])
    targets = cfg.legacy_targets()
    out = []
    for info in platform_infos():
        companies = (targets.get("companies") or {}).get(info.id) or []
        out.append({
            "id": info.id, "label": info.label, "kind": info.kind, "auto_apply": info.auto_apply,
            "home": info.home, "policy": info.policy, "enabled": info.id in enabled,
            "companies_configured": len(companies),
        })
    return {"platforms": out}


@app.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    values = repo.get_settings(db)
    quota = repo.quota_snapshot(db, int(values.get("weekly_cap", 300)))
    job_counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    app_counts = dict(db.execute(select(Application.status, func.count()).group_by(Application.status)).all())
    resume_count = db.scalar(select(func.count()).select_from(Resume)) or 0
    profile = repo.get_profile(db)
    activity = [_activity(row) for row in repo.recent_activity(db, 25)]
    return {
        "agent": orchestrator.snapshot(),
        "quota": quota,
        "job_counts": job_counts,
        "application_counts": app_counts,
        "resume_count": resume_count,
        "profile_completeness": _profile_completeness(profile),
        "activity": activity,
        "platforms": [
            {"id": info.id, "label": info.label, "kind": info.kind, "policy": info.policy}
            for info in platform_infos()
        ],
        "authorization": {
            "dry_run": bool(values.get("dry_run", True)),
            "auto_submit_authorized": bool(values.get("auto_submit_authorized", False)),
        },
    }


# --------------------------------------------------------------------------- agent controls


@app.get("/api/agent/status")
def agent_status():
    return orchestrator.snapshot()


@app.post("/api/agent/start")
async def agent_start(body: dict[str, Any] = Body(default={})):
    try:
        return await orchestrator.start()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/agent/pause")
async def agent_pause():
    try:
        return await orchestrator.pause()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/agent/resume")
async def agent_resume():
    try:
        return await orchestrator.resume()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/agent/stop")
async def agent_stop():
    return await orchestrator.stop()


@app.post("/api/agent/emergency-stop")
async def agent_emergency_stop():
    return await orchestrator.stop(emergency=True)


# --------------------------------------------------------------------------- activity


@app.get("/api/activity")
def activity(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    return {"activity": [_activity(row) for row in repo.recent_activity(db, limit)]}


# --------------------------------------------------------------------------- jobs / applications


@app.get("/api/jobs")
def jobs(
    status: str | None = None,
    platform: str | None = None,
    search: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Job)
    conditions = []
    if status:
        conditions.append(Job.status == status)
    if platform:
        conditions.append(Job.platform == platform)
    if search:
        needle = f"%{search}%"
        conditions.append(Job.title.ilike(needle) | Job.company.ilike(needle) | Job.location.ilike(needle))
    if conditions:
        from sqlalchemy import and_

        stmt = stmt.where(and_(*conditions))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Job.updated_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "jobs": [repo.job_to_dict(row) for row in rows]}


@app.get("/api/applications")
def applications(
    start: date | None = None,
    end: date | None = None,
    status: str | None = None,
    platform: str | None = None,
    company: str | None = None,
    search: str | None = None,
    limit: int = Query(300, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    rows = repo.query_applications(
        db, start=start, end=end, status=status, platform=platform,
        company=company, search=search, limit=limit, offset=offset,
    )
    return {"applications": [repo.application_to_dict(row) for row in rows]}


@app.get("/api/applications/{application_id}")
def application_detail(application_id: int, db: Session = Depends(get_db)):
    row = db.get(Application, application_id)
    if row is None:
        raise HTTPException(status_code=404, detail="application not found")
    return repo.application_to_dict(row)


@app.post("/api/applications/{job_id}/verify")
def verify_application(job_id: int, body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Human verification for an uncertain submission. Confirmed applications consume quota;
    rejected ones release the reservation."""
    confirmed = bool(body.get("confirmed"))
    note = str(body.get("note") or "")
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    from .models import SubmissionReservation

    reservation = db.scalar(select(SubmissionReservation).where(SubmissionReservation.job_id == job_id))
    if confirmed:
        if reservation is not None:
            repo.confirm_submission(db, reservation)
        repo.save_application(
            db, job, status=repo.APPLIED,
            reason=note or "confirmed manually", confirmation=note or "confirmed manually",
            next_action="None",
        )
    else:
        if reservation is not None:
            repo.release_submission(db, reservation)
        repo.save_application(
            db, job, status=repo.FAILED,
            reason=note or "not submitted according to manual verification",
            next_action="Retry only after checking the platform",
        )
    db.commit()
    return {"ok": True, "job_id": job_id, "confirmed": confirmed}


# --------------------------------------------------------------------------- settings


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    values = repo.get_settings(db)
    values["available_platforms"] = list(ATS_IDS)
    values["platform_catalog"] = [
        {"id": info.id, "label": info.label, "kind": info.kind, "auto_apply": info.auto_apply, "policy": info.policy}
        for info in platform_infos()
    ]
    values["api_token_required"] = bool(cfg.api_token())
    return values


@app.put("/api/settings")
def update_settings(body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    allowed = {
        "weekly_cap", "daily_cap", "max_tabs", "max_concurrent_submissions", "match_threshold",
        "per_company_30d", "delay_seconds", "dry_run", "auto_submit_authorized", "enabled_platforms",
        "target_roles", "location_policy", "posted_within_days", "ai_provider", "ai_model",
        "ollama_base_url", "cloud_model", "resume_selection", "generate_cover_letter", "schedule",
        "headless", "auto_consent", "eeo_decline", "companies_per_platform", "max_matches_per_pass",
        "idle_passes", "idle_wait_seconds", "dry_run_limit_per_pass",
        "aggregator_discovery", "enabled_aggregators", "aggregator_limit", "dry_run_hold_seconds",
    }
    update = {key: value for key, value in body.items() if key in allowed}
    if "enabled_platforms" in update:
        invalid = [platform for platform in update["enabled_platforms"] if platform not in ATS_IDS]
        if invalid:
            raise HTTPException(status_code=422, detail=f"unsupported platforms: {invalid}")
    if "weekly_cap" in update and not 1 <= int(update["weekly_cap"]) <= 300:
        raise HTTPException(status_code=422, detail="weekly_cap must be between 1 and 300")
    if "max_tabs" in update and not 1 <= int(update["max_tabs"]) <= 6:
        raise HTTPException(status_code=422, detail="max_tabs must be between 1 and 6")
    if "max_concurrent_submissions" in update and not 1 <= int(update["max_concurrent_submissions"]) <= 3:
        raise HTTPException(status_code=422, detail="max_concurrent_submissions must be between 1 and 3")
    if "match_threshold" in update and not 0 <= int(update["match_threshold"]) <= 100:
        raise HTTPException(status_code=422, detail="match_threshold must be between 0 and 100")
    if "daily_cap" in update and not 0 <= int(update["daily_cap"]) <= 300:
        raise HTTPException(status_code=422, detail="daily_cap must be between 0 and 300")
    if "per_company_30d" in update and int(update["per_company_30d"]) < 0:
        raise HTTPException(status_code=422, detail="per_company_30d cannot be negative")
    if "posted_within_days" in update and int(update["posted_within_days"]) < 0:
        raise HTTPException(status_code=422, detail="posted_within_days cannot be negative")
    if "aggregator_limit" in update and not 1 <= int(update["aggregator_limit"]) <= 200:
        raise HTTPException(status_code=422, detail="aggregator_limit must be between 1 and 200")
    if "enabled_aggregators" in update:
        invalid = [name for name in update["enabled_aggregators"] if name not in ("adzuna", "jooble")]
        if invalid:
            raise HTTPException(status_code=422, detail=f"unsupported aggregators: {invalid}")
    current = repo.get_settings(db)
    merged = {**current, **update}
    if merged.get("dry_run") is False and not merged.get("auto_submit_authorized"):
        raise HTTPException(status_code=422, detail="authorize autonomous submission before disabling dry run")
    was_live = current.get("dry_run") is False and bool(current.get("auto_submit_authorized"))
    will_be_live = merged.get("dry_run") is False and bool(merged.get("auto_submit_authorized"))
    if will_be_live and not was_live and str(body.get("confirm") or "").strip().upper() != "AUTHORIZE":
        raise HTTPException(status_code=422, detail='type "AUTHORIZE" in confirm to enable live submissions')
    values = repo.save_settings(db, update)
    db.commit()
    return values


@app.post("/api/automation/authorize")
def authorize(body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    authorized = bool(body.get("authorized"))
    if authorized and str(body.get("confirm") or "").strip().upper() != "AUTHORIZE":
        raise HTTPException(status_code=422, detail='type "AUTHORIZE" in confirm to enable live submissions')
    values = repo.save_settings(
        db,
        {"dry_run": not authorized, "auto_submit_authorized": authorized},
    )
    db.commit()
    repo.log_activity(db, "authorization", level="warning" if authorized else "info",
                      result="live submission authorized" if authorized else "returned to dry run")
    db.commit()
    return {"dry_run": values.get("dry_run"), "auto_submit_authorized": values.get("auto_submit_authorized")}


# --------------------------------------------------------------------------- profile


@app.get("/api/profile")
def get_profile(db: Session = Depends(get_db)):
    profile = repo.get_profile(db)
    return {"profile": profile, "completeness": _profile_completeness(profile)}


@app.put("/api/profile")
def update_profile(body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="profile must be an object")
    profile = repo.save_profile(db, body)
    db.commit()
    return {"profile": profile, "completeness": _profile_completeness(profile)}


# --------------------------------------------------------------------------- resumes


@app.get("/api/resumes")
def resumes(db: Session = Depends(get_db)):
    return {"resumes": [_resume(row) for row in repo.list_resumes(db)]}


@app.post("/api/resumes")
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read(resume_service.MAX_BYTES + 1)
    try:
        row = await resume_service.save_upload(db, file.filename or "resume.pdf", data)
    except resume_service.ResumeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    repo.log_activity(db, "resume_upload", result=row.name)
    db.commit()
    return _resume(row)


@app.post("/api/resumes/{resume_id}/default")
def default_resume(resume_id: int, db: Session = Depends(get_db)):
    row = repo.set_default_resume(db, resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="resume not found")
    db.commit()
    return _resume(row)


@app.get("/api/resumes/{resume_id}/download")
def download_resume(resume_id: int, db: Session = Depends(get_db)):
    row = repo.get_resume(db, resume_id)
    if row is None or not Path(row.path).exists():
        raise HTTPException(status_code=404, detail="resume not found")
    return FileResponse(row.path, filename=row.original_name, media_type="application/pdf")


@app.delete("/api/resumes/{resume_id}")
def remove_resume(resume_id: int, db: Session = Depends(get_db)):
    row = repo.get_resume(db, resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="resume not found")
    path = Path(row.path)
    repo.delete_resume(db, resume_id)
    db.commit()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
    return {"ok": True}


# --------------------------------------------------------------------------- reports


@app.get("/api/reports/excel")
def excel_report(
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    values = repo.get_settings(db)
    buffer, filename = generate_report(db, start, end, int(values.get("weekly_cap", 300)))
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/quota")
def quota(db: Session = Depends(get_db)):
    values = repo.get_settings(db)
    return repo.quota_snapshot(db, int(values.get("weekly_cap", 300)))


# --------------------------------------------------------------------------- websocket


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = cfg.api_token()
    if token and websocket.query_params.get("token") != token:
        await websocket.close(code=4401)
        return
    await hub.connect(websocket)
    try:
        await websocket.send_json({"type": "snapshot", "payload": orchestrator.snapshot()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)


# --------------------------------------------------------------------------- frontend


FRONTEND_DIST = cfg.ROOT / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    @app.get("/")
    def frontend_missing():
        return JSONResponse({
            "message": "Frontend is not built. Run: cd frontend && npm install && npm run build",
            "api_docs": "/docs",
        })


def run() -> None:
    import uvicorn

    uvicorn.run("jobbot.v2.api:app", host=cfg.bind_host(), port=cfg.bind_port(), reload=False)


if __name__ == "__main__":
    run()
