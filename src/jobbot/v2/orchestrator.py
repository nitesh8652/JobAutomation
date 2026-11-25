"""Autonomous orchestrator: one visible tab per configured platform, shared quota."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime
from typing import Any, Awaitable, Callable
from sqlalchemy import select

from . import forms, repositories as repo, resumes as resume_service
from . import settings as cfg
from .ai import answer_questions, cover_letter
from .browser import BrowserSession, Control, dismiss_cookies, ensure_form_open, goto, page_is_closed, screenshot
from .db import session_scope
from .discovery import discover_aggregators, discover_and_match
from .models import Job
from .platforms import get_adapter

log = logging.getLogger("jobbot.v2.orchestrator")
EventListener = Callable[[dict[str, Any]], Awaitable[None]] | None


class Orchestrator:
    def __init__(self, listener: EventListener = None):
        self.listener = listener
        self.control = Control()
        self.settings: cfg.RuntimeSettings | None = None
        self.browser: BrowserSession | None = None
        self.state = "idle"
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.message = "Ready"
        self.tabs: dict[str, dict[str, Any]] = {}
        self._tab_pages: dict[str, Any] = {}
        self._tasks: list[asyncio.Task] = []
        self._extra_tasks: list[asyncio.Task] = []
        self._supervisor: asyncio.Task | None = None
        self._aggregator_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(1)
        self._run_lock = asyncio.Lock()
        self.last_quota: dict[str, Any] = {}
        self._quota_reason = "weekly application cap reached"

    # ------------------------------------------------------------------ controls

    @property
    def active(self) -> bool:
        return self.state in ("starting", "running", "pausing", "paused", "stopping")

    async def start(self) -> dict[str, Any]:
        async with self._run_lock:
            if self.active:
                raise ValueError("the agent is already active")
            with session_scope() as db:
                values = repo.get_settings(db)
                self.last_quota = repo.quota_snapshot(db, int(values.get("weekly_cap", 300)))
            self.settings = cfg.RuntimeSettings(values)
            if not self.settings.enabled_platforms:
                raise ValueError("no supported ATS platforms are enabled in Settings")
            if not self.settings.dry_run and not bool(self.settings.get("auto_submit_authorized")):
                raise ValueError(
                    "live submission is not authorized. Keep dry_run=true or explicitly authorize autonomous submission."
                )
            self.control = Control()
            self._semaphore = asyncio.Semaphore(int(self.settings.get("max_concurrent_submissions", 1)))
            self.state = "starting"
            self.message = "Launching visible Chrome"
            self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self.finished_at = None
            self.tabs = {}
            self._tab_pages = {}
            self._tasks = []
            self._extra_tasks = []
            self._aggregator_event = asyncio.Event()
            if self._supervisor is not None and not self._supervisor.done():
                self._supervisor.cancel()
            await self.emit("agent", level="info", result="start requested")
            self.browser = BrowserSession(self.settings.as_dict())
            try:
                await self.browser.start()
            except Exception as exc:
                if self.browser is not None:
                    try:
                        await self.browser.close()
                    except Exception:
                        pass
                self.state = "failed"
                self.message = f"Chrome launch failed: {type(exc).__name__}"
                await self.emit("agent", level="error", result=self.message)
                raise ValueError(
                    "Could not launch visible Google Chrome. Close Chrome windows that use the dedicated profile, "
                    "confirm Google Chrome is installed, and try again."
                ) from exc
            platforms = self.settings.enabled_platforms
            self.state = "running"
            self.message = f"Running {len(platforms)} platform tab(s)"
            if self.settings.get("aggregator_discovery", True):
                self._extra_tasks.append(asyncio.create_task(self._aggregator_pass(), name="jobbot-aggregators"))
            else:
                self._aggregator_event.set()
            for index, platform in enumerate(platforms, 1):
                tab_id = f"tab-{index}"
                self.tabs[tab_id] = {
                    "id": tab_id, "platform": platform, "site": "", "job_title": "",
                    "action": "starting", "result": "", "state": "starting",
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                self._tasks.append(asyncio.create_task(self._worker(platform, tab_id), name=f"jobbot-{platform}"))
            self._supervisor = asyncio.create_task(self._supervise(), name="jobbot-supervisor")
            await self.emit("agent", result=f"started with platforms: {', '.join(platforms)}")
            return self.snapshot()

    async def pause(self) -> dict[str, Any]:
        if not self.active:
            raise ValueError("the agent is not running")
        self.control.pause()
        self.state = "pausing"
        self.message = "Paused at the next safe step"
        await self.emit("pause", result="pause requested")
        return self.snapshot()

    async def resume(self) -> dict[str, Any]:
        if not self.active:
            raise ValueError("the agent is not running")
        if self.control.stopped:
            raise ValueError("the agent is stopping and cannot resume")
        self.control.resume()
        self.state = "running"
        self.message = "Running"
        await self.emit("resume", result="resumed")
        return self.snapshot()

    async def stop(self, emergency: bool = False) -> dict[str, Any]:
        if not self.active and not emergency:
            self.state = "stopped"
            self.message = "Agent is not running"
            return self.snapshot()
        if emergency:
            self.control.stop(emergency=True)
            for task in [*self._tasks, *self._extra_tasks]:
                task.cancel()
            if self._supervisor is not None:
                self._supervisor.cancel()
            if self.browser is not None:
                await self.browser.close()
            self.state = "stopped"
            self.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self.message = "Emergency stop completed"
            await self.emit("emergency_stop", level="warning", result="all browser work cancelled")
            return self.snapshot()
        self.control.stop()
        self.state = "stopping"
        self.message = "Stopping after the current safe step"
        await self.emit("stop", result="stop requested")
        return self.snapshot()

    async def shutdown(self) -> None:
        try:
            self.control.stop(emergency=True)
            for task in [*self._tasks, *self._extra_tasks]:
                task.cancel()
            if self._supervisor is not None:
                self._supervisor.cancel()
            if self.browser is not None:
                await self.browser.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ status

    def snapshot(self) -> dict[str, Any]:
        quota = self.last_quota
        try:
            with session_scope() as db:
                cap = int(self.settings.get("weekly_cap", 300)) if self.settings else 300
                quota = repo.quota_snapshot(db, cap)
                self.last_quota = quota
        except Exception:
            pass
        return {
            "state": self.state,
            "paused": self.control.paused,
            "stopped": self.control.stopped,
            "message": self.message,
            "stage": self.control.stage,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tabs": list(self.tabs.values()),
            "quota": quota,
            "settings": {
                "dry_run": bool(self.settings.dry_run) if self.settings else True,
                "live_authorized": bool(self.settings.live_authorized) if self.settings else False,
                "max_tabs": int(self.settings.get("max_tabs", 6)) if self.settings else 6,
                "platforms": self.settings.enabled_platforms if self.settings else [],
                "ai_provider": self.settings.get("ai_provider") if self.settings else "auto",
            },
        }

    def _tab(self, tab_id: str, **values: Any) -> None:
        if tab_id in self.tabs:
            self.tabs[tab_id].update(values)
            self.tabs[tab_id]["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    async def emit(self, action: str, *, level: str = "info", tab_id: str | None = None,
                   platform: str | None = None, job_id: int | None = None,
                   job_title: str | None = None, url: str | None = None,
                   result: str | None = None) -> None:
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": level, "action": action, "tab_id": tab_id, "platform": platform,
            "job_id": job_id, "job_title": job_title, "url": url, "result": result,
            "state": self.state, "tabs": list(self.tabs.values()),
        }
        if self.listener is not None:
            try:
                await self.listener(payload)
            except Exception:
                log.debug("event listener failed", exc_info=True)
        try:
            with session_scope() as db:
                repo.log_activity(
                    db, action, level=level, tab_id=tab_id, platform=platform,
                    job_id=job_id, job_title=job_title, url=url, result=result,
                )
        except Exception:
            log.debug("activity write failed", exc_info=True)

    # ------------------------------------------------------------------ workers

    async def _supervise(self) -> None:
        browser = self.browser
        results = await asyncio.gather(*self._tasks, *self._extra_tasks, return_exceptions=True)
        reasons = []
        for result in results:
            if isinstance(result, Exception):
                reasons.append(type(result).__name__)
            elif isinstance(result, dict):
                reasons.append(str(result.get("reason", "")))
        if self.control.emergency:
            final = "stopped"
        elif self.control.stopped:
            final = "stopped"
        elif any("cap" in reason for reason in reasons):
            final = "quota_reached"
        elif all("no eligible" in reason or "no jobs" in reason or "no configured" in reason for reason in reasons):
            final = "completed"
        else:
            final = "completed"
        if browser is not None:
            await browser.close()
            if self.browser is browser:
                self.browser = None
        self.state = final
        self.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.message = {
            "quota_reached": "Stopped: application limit reached (weekly or daily cap)",
            "completed": "Completed: no eligible jobs remain",
            "stopped": "Stopped by user",
        }.get(final, final)
        await self.emit("agent_done", result=self.message)

    async def _worker(self, platform: str, tab_id: str) -> dict[str, Any]:
        settings = self.settings
        assert settings is not None
        adapter = get_adapter(platform)
        page = None
        try:
            if self.browser is None:
                raise RuntimeError("browser closed")
            page = await self.browser.acquire_tab()
            self._tab_pages[tab_id] = page
            self._tab(tab_id, site=adapter.home_url, action="restoring session", state="running")
            await goto(page, adapter.home_url)
            await self.emit("tab_ready", tab_id=tab_id, platform=platform, url=adapter.home_url,
                            result=f"{adapter.label} tab ready")
            await self._wait_for_aggregators()
            idle_passes = 0
            max_idle = max(1, int(settings.get("idle_passes", 2)))
            while not self.control.stopped:
                if not await self.control.checkpoint("checking_schedule"):
                    break
                if not cfg.schedule_open(settings):
                    self._tab(tab_id, action="outside operation schedule", state="paused")
                    await self.control.sleep(60)
                    continue
                if not await self._quota_available():
                    self.message = self._quota_reason
                    self._tab(tab_id, action="application cap reached", state="stopped")
                    await self.emit("quota_stop", tab_id=tab_id, platform=platform,
                                    result=self._quota_reason)
                    return {"platform": platform, "reason": self._quota_reason}
                self._tab(tab_id, site=adapter.home_url, action="searching jobs", state="running")
                stats = await discover_and_match(
                    platform, settings.as_dict(),
                    limit=int(settings.get("companies_per_platform", 10)),
                    on_activity=lambda action, **kw: self.emit(
                        action, tab_id=tab_id, platform=kw.get("platform", platform), result=kw.get("result")
                    ),
                )
                jobs = self._select_jobs(platform, settings, dry_run=settings.dry_run)
                if not jobs:
                    idle_passes += 1
                    self._tab(tab_id, action="no eligible jobs", state="idle",
                              result=f"{stats.get('new', 0)} new, {stats.get('filtered', 0)} filtered")
                    if idle_passes >= max_idle:
                        return {"platform": platform, "reason": "no jobs"}
                    await self.control.sleep(float(settings.get("idle_wait_seconds", 45)))
                    continue
                idle_passes = 0
                limit = int(settings.get("dry_run_limit_per_pass", 2)) if settings.dry_run else len(jobs)
                for job_stub in jobs[:limit]:
                    if not await self.control.checkpoint("applying"):
                        break
                    if not settings.dry_run and not await self._quota_available():
                        return {"platform": platform, "reason": self._quota_reason}
                    await self._process_job(platform, tab_id, adapter, job_stub.id, settings)
                    if settings.dry_run:
                        continue
                    delay = settings.get("delay_seconds") or [30, 120]
                    lo, hi = (delay + [delay[-1]])[:2] if isinstance(delay, list) and delay else (30, 120)
                    if not await self.control.sleep(random.uniform(float(lo), float(hi))):
                        break
                if settings.dry_run:
                    # Keep the filled form visible long enough to inspect; Stop ends it early.
                    self._tab(tab_id, action="dry run complete - review the form", state="paused")
                    await self.emit("dry_run_review", tab_id=tab_id, platform=platform,
                                    result="filled forms are visible; press Stop when finished")
                    await self.control.sleep(float(settings.get("dry_run_hold_seconds", 60)))
                    return {"platform": platform, "reason": "dry run complete"}
            return {"platform": platform, "reason": "stopped"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("worker %s failed", platform)
            self._tab(tab_id, action="tab error", state="error", result=f"{type(exc).__name__}: {exc}"[:300])
            await self.emit("tab_error", level="error", tab_id=tab_id, platform=platform,
                            result=f"{type(exc).__name__}: {exc}"[:300])
            return {"platform": platform, "reason": "error"}
        finally:
            if tab_id in self.tabs and self.state in ("completed", "quota_reached", "stopped"):
                self._tab(tab_id, state="done")

    async def _aggregator_pass(self) -> None:
        try:
            settings = self.settings
            if settings is None:
                return
            stats = await discover_aggregators(
                settings.as_dict(),
                on_activity=lambda action, **kw: self.emit(
                    action, platform=kw.get("platform"), result=kw.get("result")
                ),
            )
            await self.emit(
                "aggregator_done",
                result=(
                    f"{stats['sources']} sources, {stats['discovered']} listings, "
                    f"{stats['new']} new, {stats['rehomed']} re-homed to ATS, {stats['blocked']} manual"
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("aggregator discovery failed: %s", exc)
            await self.emit("aggregator_error", level="warning", result=f"{type(exc).__name__}: {exc}"[:300])
        finally:
            self._aggregator_event.set()

    async def _wait_for_aggregators(self) -> None:
        if self._aggregator_event.is_set():
            return
        try:
            await asyncio.wait_for(self._aggregator_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            log.warning("aggregator discovery is taking too long; continuing with ATS boards")

    async def _quota_available(self) -> bool:
        settings = self.settings
        assert settings is not None
        with session_scope() as db:
            snapshot = repo.quota_snapshot(db, settings.weekly_cap)
            today = repo.applications_today(db)
            self.last_quota = snapshot
        if snapshot["remaining"] <= 0:
            self._quota_reason = "weekly application cap reached"
            return False
        daily_cap = int(settings.get("daily_cap", 0) or 0)
        if daily_cap > 0 and today >= daily_cap:
            self._quota_reason = "daily application cap reached"
            return False
        return True

    def _select_jobs(self, platform: str, settings: cfg.RuntimeSettings, *, dry_run: bool) -> list[Job]:
        with session_scope() as db:
            if dry_run:
                rows = repo.jobs_for_platform(db, platform, ("matched",), limit=10)
                return rows
            return repo.matched_jobs(db, platform, int(settings.get("match_threshold", 70)), limit=25)

    # ------------------------------------------------------------------ application flow

    async def _process_job(self, platform: str, tab_id: str, adapter, job_id: int,
                           settings: cfg.RuntimeSettings) -> None:
        data: dict[str, Any] = {}
        clicked = False
        try:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None or job.status != "matched":
                    return
                data = repo.job_to_dict(job)
                profile = repo.get_profile(db)
                resume_text = repo.profile_resume_text(profile)
                resume, resume_path = resume_service.select_resume(db, job, profile, settings.as_dict())
                resume_name = resume.name if resume else (profile.get("resume_path") or "")
                resume_id = resume.id if resume else None
        except resume_service.ResumeError as exc:
            with session_scope() as db:
                current = db.get(Job, job_id)
                if current is not None:
                    repo.save_application(
                        db, current, status=repo.FAILED, reason=str(exc),
                        next_action="Upload a PDF resume in the dashboard, then retry",
                    )
            await self.emit("application_result", level="error", tab_id=tab_id, platform=platform,
                            job_id=job_id, job_title=data.get("title"), url=data.get("url"),
                            result=f"Failed: {exc}")
            self._tab(tab_id, action="resume missing", state="running", result=str(exc)[:200])
            return
        self._tab(tab_id, platform=platform, site=data.get("url", ""), job_title=data.get("title", ""),
                  action="opening application", state="running", result="")
        await self.emit("open_job", tab_id=tab_id, platform=platform, job_id=job_id,
                        job_title=data.get("title"), url=data.get("url"), result=f"score {data.get('score')}")

        reservation = None
        if not settings.dry_run:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                reservation, reason = repo.reserve_submission(
                    db, job, cap=settings.weekly_cap, tab_id=tab_id,
                    per_company_limit=int(settings.get("per_company_30d", 0)),
                    daily_cap=int(settings.get("daily_cap", 0)),
                )
                if reservation is None:
                    if reason and "cap" in reason:
                        self._tab(tab_id, action="quota reached", state="paused", result=reason)
                        await self.emit("quota_stop", level="warning", tab_id=tab_id, platform=platform,
                                        job_id=job_id, job_title=data.get("title"), result=reason)
                        return
                    if reason and "company limit" in reason:
                        job.status = "skipped"
                        job.status_reason = reason
                        repo.save_application(db, job, status=repo.SKIPPED, reason=reason, tab_id=tab_id)
                        self._tab(tab_id, action="company limit", state="running", result=reason)
                        await self.emit("skip", tab_id=tab_id, platform=platform, job_id=job_id,
                                        job_title=data.get("title"), result=reason)
                        return
                    existing = db.scalar(select(repo.Application).where(repo.Application.job_id == job.id))
                    job.status = "duplicate"
                    job.status_reason = reason
                    if existing is None:
                        repo.save_application(db, job, status=repo.DUPLICATE, reason=reason or "duplicate", tab_id=tab_id)
                    self._tab(tab_id, action="duplicate skipped", state="running", result=reason or "")
                    await self.emit("duplicate", tab_id=tab_id, platform=platform, job_id=job_id,
                                    job_title=data.get("title"), result=reason or "duplicate")
                    return
        else:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                claimed = repo.claim_job(db, job.id, tab_id)
                if not claimed:
                    return

        page = None
        try:
            if self.browser is None:
                raise RuntimeError("browser closed")
            page = self._tab_pages.get(tab_id)
            if page is None or page.is_closed():
                page = await self.browser.acquire_tab()
                self._tab_pages[tab_id] = page
            await goto(page, adapter.form_url(data))
            if await page_is_closed(page):
                await self._finish_failure(tab_id, platform, job_id, data, reservation, repo.BLOCKED,
                                           "posting is closed or missing", settings, page)
                return
            await dismiss_cookies(page)
            await ensure_form_open(page)
            self._tab(tab_id, action="reading form", state="running", site=page.url)
            questions = await forms.analyze(page)
            if not forms.form_present(questions):
                await self._finish_failure(tab_id, platform, job_id, data, reservation, repo.FAILED,
                                           "application form not found on page", settings, page)
                return

            cover = await cover_letter(
                data, profile, resume_text, settings.as_dict()
            ) if settings.get("generate_cover_letter", True) else ""
            cover_path = resume_service.write_cover_letter(job_id, cover) if cover else ""

            async def ask(payload):
                return await answer_questions(
                    data, payload, profile, resume_text, cover, settings.as_dict()
                )

            self._tab(tab_id, action="filling form", state="running")
            await self.emit("filling", tab_id=tab_id, platform=platform, job_id=job_id,
                            job_title=data.get("title"), url=data.get("url"), result=f"{len(questions)} fields")
            plan = await forms.plan_answers(
                questions, data, cover, {}, resume_path, cover_path, profile, ask
            )
            failures = await forms.fill(page, questions, plan)
            unresolved = list(dict.fromkeys(plan.unresolved + failures))
            challenge = await forms.detect_challenge(page)
            shot = await screenshot(page, job_id, "filled")
            answers_json = json.dumps(
                {"values": plan.values, "unresolved": unresolved,
                 "labels": {q.key: q.label for q in questions}},
                ensure_ascii=False,
            )
            self._tab(tab_id, action="validating answers", state="running",
                      result="; ".join(unresolved[:3]) if unresolved else "ready")

            if settings.dry_run:
                with session_scope() as db:
                    job = db.get(Job, job_id)
                    if job is not None:
                        job.status = "matched"
                        job.status_reason = "dry run filled; not submitted"
                        repo.save_application(
                            db, job, status=repo.SKIPPED, reason="Dry run: form filled but never submitted",
                            dry_run=True, resume_id=resume_id, resume_name=resume_name,
                            cover_letter_path=cover_path or None, answers_json=answers_json,
                            screenshot=shot, tab_id=tab_id,
                            next_action="Review form; enable live submission when ready",
                        )
                        job.status = "matched"
                        job.status_reason = "dry run filled; not submitted"
                self._tab(tab_id, action="dry run complete", state="running",
                          result="filled; no submission")
                await self.emit("dry_run", tab_id=tab_id, platform=platform, job_id=job_id,
                                job_title=data.get("title"), url=data.get("url"),
                                result=f"filled {len(plan.values)} fields; not submitted")
                return

            if unresolved:
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.REVIEW,
                    "needs answers: " + "; ".join(unresolved[:5]), settings, page,
                    answers_json=answers_json, resume_id=resume_id, resume_name=resume_name,
                    cover_path=cover_path, shot=shot, next_action="Complete this application manually",
                )
                return
            if challenge:
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.REVIEW,
                    f"{challenge.upper()} challenge requires manual completion", settings, page, dry_run=False,
                    answers_json=answers_json, resume_id=resume_id, resume_name=resume_name,
                    cover_path=cover_path, shot=shot, next_action="Open the form and solve the challenge manually",
                )
                return
            if not settings.live_authorized:
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.SKIPPED,
                    "live submission is not authorized", settings, page,
                    answers_json=answers_json, resume_id=resume_id, resume_name=resume_name,
                    cover_path=cover_path, shot=shot, next_action="Authorize autonomous submission in Settings",
                )
                return

            if not await self.control.checkpoint("before_submit"):
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.REVIEW,
                    "stopped before submission", settings, page,
                    answers_json=answers_json, resume_id=resume_id, resume_name=resume_name,
                    cover_path=cover_path, shot=shot, next_action="Review and submit manually",
                )
                return

            self._tab(tab_id, action="submitting", state="running")
            await self.emit("submitting", tab_id=tab_id, platform=platform, job_id=job_id,
                            job_title=data.get("title"), url=data.get("url"), result="clicking submit")
            async with self._semaphore:
                clicked = await forms.click_submit(page)
                if not clicked:
                    await self._finish_failure(
                        tab_id, platform, job_id, data, reservation, repo.REVIEW,
                        "submit button not found", settings, page,
                        answers_json=answers_json, resume_id=resume_id, resume_name=resume_name,
                        cover_path=cover_path, shot=shot, next_action="Submit manually",
                    )
                    return
                result, detail = await forms.wait_for_result(page)
            final_shot = await screenshot(page, job_id, "after-submit") or shot
            reference = await self._reference_id(page)
            if result == "ok":
                with session_scope() as db:
                    current = db.get(Job, job_id)
                    reservation_row = None
                    if current is not None:
                        from .models import SubmissionReservation

                        reservation_row = db.scalar(
                            select(SubmissionReservation).where(SubmissionReservation.job_id == current.id)
                        )
                    if reservation_row is not None:
                        repo.confirm_submission(db, reservation_row)
                    if current is not None:
                        repo.save_application(
                            db, current, status=repo.APPLIED, reason=detail,
                            confirmation=detail, resume_id=resume_id, resume_name=resume_name,
                            cover_letter_path=cover_path or None, answers_json=answers_json,
                            screenshot=final_shot, tab_id=tab_id, reference_id=reference,
                            next_action="None", dry_run=False,
                        )
                self._tab(tab_id, action="submitted", state="running", result=detail)
                await self.emit("submitted", tab_id=tab_id, platform=platform, job_id=job_id,
                                job_title=data.get("title"), url=data.get("url"), result=detail)
            elif result == "invalid":
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.REVIEW,
                    "form rejected: " + detail, settings, page, dry_run=True,
                    answers_json=answers_json, resume_id=resume_id, resume_name=resume_name,
                    cover_path=cover_path, shot=final_shot, next_action="Fix validation errors and submit manually",
                )
            elif result == "captcha":
                # The submit click may or may not have reached the employer; hold capacity until verified.
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.REVIEW,
                    detail + " - verify whether the application was received", settings, page,
                    uncertain=True, answers_json=answers_json, resume_id=resume_id,
                    resume_name=resume_name, cover_path=cover_path, shot=final_shot,
                    next_action="Solve the challenge and verify the submission manually",
                )
            else:
                with session_scope() as db:
                    from .models import SubmissionReservation

                    current = db.get(Job, job_id)
                    reservation_row = db.scalar(
                        select(SubmissionReservation).where(SubmissionReservation.job_id == job_id)
                    ) if current else None
                    if reservation_row is not None:
                        repo.mark_uncertain(db, reservation_row)
                    if current is not None:
                        repo.save_application(
                            db, current, status=repo.PENDING,
                            reason=f"{detail}; verify on the platform before retrying",
                            confirmation=detail, resume_id=resume_id, resume_name=resume_name,
                            cover_letter_path=cover_path or None, answers_json=answers_json,
                            screenshot=final_shot, tab_id=tab_id, next_action="Verify submission confirmation",
                            dry_run=False,
                        )
                self._tab(tab_id, action="pending verification", state="running", result=detail)
                await self.emit("pending", level="warning", tab_id=tab_id, platform=platform,
                                job_id=job_id, job_title=data.get("title"), url=data.get("url"),
                                result=detail)
        except Exception as exc:
            log.exception("application failed for job %s", job_id)
            if clicked:
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.PENDING,
                    f"submit was clicked but the result could not be verified: {type(exc).__name__}",
                    settings, page, uncertain=True,
                    next_action="Verify submission on the platform before retrying",
                )
            else:
                await self._finish_failure(
                    tab_id, platform, job_id, data, reservation, repo.FAILED,
                    f"{type(exc).__name__}: {exc}"[:500], settings, page,
                )
        finally:
            if self.tabs.get(tab_id, {}).get("state") != "paused":
                self._tab(tab_id, action="ready", state="running")

    async def _page_for_tab(self, tab_id: str):
        # Workers create their own page before calling this; subsequent jobs reuse it.
        if self.browser is None:
            return None
        index = int(tab_id.rsplit("-", 1)[-1]) - 1
        self.browser.pages = [page for page in self.browser.pages if not page.is_closed()]
        if index < len(self.browser.pages):
            return self.browser.pages[index]
        return None

    async def _finish_failure(
        self,
        tab_id: str,
        platform: str,
        job_id: int,
        data: dict[str, Any],
        reservation,
        status: str,
        reason: str,
        settings: cfg.RuntimeSettings,
        page=None,
        *,
        dry_run: bool | None = False,
        answers_json: str | None = None,
        resume_id: int | None = None,
        resume_name: str | None = None,
        cover_path: str | None = None,
        shot: str | None = None,
        next_action: str | None = None,
    ) -> None:
        if shot is None and page is not None:
            shot = await screenshot(page, job_id, "failure")
        with session_scope() as db:
            current = db.get(Job, job_id)
            if current is not None:
                if reservation is not None:
                    from .models import SubmissionReservation

                    attached = db.scalar(
                        select(SubmissionReservation).where(SubmissionReservation.job_id == current.id)
                    )
                    if attached is not None:
                        if status == repo.PENDING or uncertain:
                            repo.mark_uncertain(db, attached)
                        else:
                            repo.release_submission(db, attached)
                repo.save_application(
                    db, current, status=status, reason=reason, dry_run=bool(dry_run),
                    resume_id=resume_id, resume_name=resume_name, tab_id=tab_id,
                    cover_letter_path=cover_path, answers_json=answers_json,
                    screenshot=shot, next_action=next_action or "Review manually",
                )
        self._tab(tab_id, action=status.lower(), state="paused" if status == repo.REVIEW else "running",
                  result=reason[:200])
        await self.emit(
            "application_result", level="warning" if status in (repo.REVIEW, repo.BLOCKED, repo.PENDING) else "error",
            tab_id=tab_id, platform=platform, job_id=job_id, job_title=data.get("title"),
            url=data.get("url"), result=f"{status}: {reason}"[:300],
        )
        if status == repo.REVIEW and not self.control.stopped:
            self.control.pause()
            self.state = "paused"
            self.message = "Paused for manual review - finish the form in Chrome, then Resume or Stop"
            await self.emit("manual_review", level="warning", tab_id=tab_id, platform=platform,
                            job_id=job_id, job_title=data.get("title"), result=reason[:300])

    async def _reference_id(self, page) -> str | None:
        try:
            body = (await page.inner_text("body", timeout=3000))[:10000]
        except Exception:
            return None
        patterns = [
            r"(?:application|reference|candidate|confirmation)\s*(?:id|number|no\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-_/]{3,})",
            r"\b([A-Z]{2,}[-_/][A-Z0-9]{3,})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.I)
            if match:
                return match.group(1)[:160]
        return None
