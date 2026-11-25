"""Visible, persistent Chrome workflow for the local browser agent.

The worker reuses Jobbot's fetch, match, quota, and ATS form engines. Job-board
search tabs are opened by the extension for the user to operate; automatic form
submission is limited to supported ATS adapters and requires an explicit config opt-in.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from urllib.parse import urlsplit

from . import config, db
from .appliers import Applier

log = logging.getLogger("jobbot.agent")


class BrowserJobAgent:
    def __init__(self):
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._continue = threading.Event()
        self._continue.set()
        self._stop = threading.Event()
        self._emergency = threading.Event()
        self._active = False
        self._state = "idle"
        self._stage = "idle"
        self._role = ""
        self._started_at = None
        self._finished_at = None
        self._tabs: list[dict] = []
        self._logs: list[str] = []
        self._applier: Applier | None = None
        self._worker = threading.Thread(target=self._work, name="jobbot-browser-agent", daemon=True)
        self._worker.start()

    def _log(self, message: str) -> None:
        with self._lock:
            stamp = datetime.now().astimezone().strftime("%H:%M:%S")
            self._logs.append(f"{stamp}  {message}")
            self._logs = self._logs[-100:]
        log.info("%s", message)

    def _set(self, **values) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self, f"_{key}", value)

    def snapshot(self) -> dict:
        with self._lock:
            try:
                target = config.targets()
                setup_error = None
            except SystemExit as exc:
                target = {}
                setup_error = str(exc)
            automation = target.get("automation") or {}
            try:
                max_tabs = max(2, min(6, int(automation.get("max_browser_tabs", 6))))
            except (TypeError, ValueError):
                max_tabs = 6
            return {
                "state": self._state, "stage": self._stage, "role": self._role,
                "started_at": self._started_at, "finished_at": self._finished_at,
                "active": self._active, "tabs": list(self._tabs), "logs": list(self._logs),
                "auto_submit_authorized": automation.get("auto_submit_authorized", False) is True,
                "max_browser_tabs": max_tabs,
                "target_roles": target.get("target_roles") or list(config.DEFAULT_TARGET_ROLES),
                "setup_error": setup_error,
            }

    def start(self, role: str | None = None) -> dict:
        try:
            target = config.targets()
        except SystemExit as exc:
            raise ValueError(str(exc)) from exc
        selected = target.get("target_roles") or list(config.DEFAULT_TARGET_ROLES)
        role = str(role or (selected[0] if selected else "Software Engineer")).strip()
        if selected and role not in selected:
            raise ValueError("choose one of the target roles saved in Jobbot Settings")
        with self._lock:
            if self._active:
                raise ValueError("the browser agent is already active")
            self._active = True
            self._state = "queued"
            self._stage = "starting"
            self._role = role
            self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self._finished_at = None
            self._logs = []
            self._tabs = []
            self._stop.clear()
            self._emergency.clear()
            self._continue.set()
        self._queue.put(("start", role))
        return self.snapshot()

    def pause(self) -> dict:
        with self._lock:
            if not self._active:
                raise ValueError("the browser agent is not running")
            self._state = "pausing"
        self._continue.clear()
        self._log("Pause requested; the current browser action will finish first.")
        return self.snapshot()

    def resume(self) -> dict:
        with self._lock:
            if not self._active:
                raise ValueError("the browser agent is not running")
            if self._stop.is_set():
                raise ValueError("the browser agent is stopping and cannot be resumed")
            self._state = "running"
        self._continue.set()
        self._log("Resumed.")
        return self.snapshot()

    def stop(self, emergency: bool = False) -> dict:
        self._stop.set()
        self._continue.set()
        if emergency:
            self._emergency.set()
        with self._lock:
            if self._active:
                self._state = "emergency_stop_requested" if emergency else "stop_requested"
            elif emergency:
                self._state = "emergency_stop_requested"
                self._queue.put(("emergency", None))
        self._log("Emergency stop requested." if emergency else "Stop requested.")
        return self.snapshot()

    def checkpoint(self, stage: str) -> bool:
        while not self._continue.wait(0.2):
            if self._stop.is_set():
                return False
            self._set(state="paused", stage=stage)
        if self._stop.is_set():
            return False
        self._set(state="running", stage=stage)
        return True

    def browser_page(self, job: dict, page, stage: str) -> None:
        try:
            host = urlsplit(page.url).hostname or job.get("ats") or "application form"
        except Exception:
            host = job.get("ats") or "application form"
        self._set(tabs=[{
            "site": host, "title": str(job.get("title") or "Application")[:160],
            "company": str(job.get("company") or "")[:120], "state": stage,
        }])

    def browser_page_done(self) -> None:
        self._set(tabs=[])

    def _ensure_browser(self) -> None:
        if self._applier is None:
            config.ensure_dirs()
            self._set(stage="launching_chrome")
            self._log("Launching visible Chrome with Jobbot's dedicated persistent profile.")
            self._applier = Applier(headless=False, persistent=True)

    def _prepare_browser(self, role: str) -> None:
        self._ensure_browser()
        self._log(f"Visible Chrome is ready for supported application forms for {role}.")
        self._log("Job-board search pages are opened by the extension for you to review and operate.")

    def _close_browser(self) -> None:
        if self._applier is not None:
            try:
                self._applier.close()
            except Exception as exc:
                self._log(f"Chrome close reported {type(exc).__name__}.")
            self._applier = None
            self._set(tabs=[])

    def _work(self) -> None:
        while True:
            action, role = self._queue.get()
            if action == "shutdown":
                self._close_browser()
                return
            if action == "emergency":
                self._close_browser()
                self._set(state="stopped", stage="idle", finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
                continue
            self._run(role)

    def _run(self, role: str) -> None:
        from . import apply, fetch, match

        try:
            self._set(state="running", stage="launching_chrome")
            self._prepare_browser(role)
            if not self.checkpoint("fetching_jobs"):
                raise InterruptedError("stopped before fetch")
            with db.connect() as conn:
                fetched = fetch.run(conn)
                self._log(f"Fetch step finished across enabled sources: {fetched}.")
                if not self.checkpoint("matching_jobs"):
                    raise InterruptedError("stopped before matching")
                matched = match.run(conn)
                self._log(f"Match step finished for selected roles: {matched}.")
                if not self.checkpoint("preparing_applications"):
                    raise InterruptedError("stopped before application forms")
                automation = config.targets().get("automation") or {}
                if automation.get("auto_submit_authorized") is True:
                    self._log("Live submissions enabled in targets.yaml; applying only through supported ATS adapters.")
                    result = apply.run_auto(conn, headless=False, context=self._applier.context, control=self)
                else:
                    self._log("Dry-run mode: filling eligible supported ATS forms without submitting.")
                    result = apply.run_dry(conn, limit=3, context=self._applier.context, control=self)
                self._log(f"Application step finished: {result}.")
            if self._stop.is_set():
                final_state = "stopped"
            else:
                final_state = "completed"
                self._log("Workflow completed. Chrome remains open for review.")
            self._set(state=final_state, stage="complete", finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        except InterruptedError:
            self._set(state="stopped", stage="stopped", finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            self._log("Stopped at a safe step boundary.")
        except Exception as exc:
            self._set(state="failed", stage="failed", finished_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            self._log(f"Workflow stopped after {type(exc).__name__}. Check Chrome installation, network access, and Jobbot setup.")
            log.error("browser agent failed (%s)", type(exc).__name__)
        finally:
            with self._lock:
                self._active = False
            if self._emergency.is_set():
                self._close_browser()
