"""Browser applier: opens a hosted ATS form, fills it, and submits only when it's safe to.

No stealth, no CAPTCHA solving: a plain Playwright browser with honest defaults. Anything that
needs a human (a CAPTCHA, an unanswerable question, an unconfirmed submit) goes to the review queue."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import config, db, tailor
from ..resume import resume_pdf
from . import formfill

log = logging.getLogger(__name__)

SUPPORTED = ("greenhouse", "lever", "ashby", "workable", "recruitee", "personio")

COOKIE_BUTTONS = ['button:has-text("Accept all")', 'button:has-text("Accept All")', 'button:has-text("Accept")',
                  'button:has-text("Allow all")', 'button:has-text("I agree")']
OPEN_FORM_BUTTONS = ['a:has-text("Apply for this job")', 'button:has-text("Apply for this job")',
                     'button:has-text("Apply now")', 'a:has-text("Apply now")', 'button:has-text("Apply")']


@dataclass
class Outcome:
    status: str                    # submitted|queued|failed|closed|dry_run|filled
    reason: str | None = None
    screenshot: str | None = None
    answers: dict[str, Any] = field(default_factory=dict)
    cover_letter: str | None = None
    page: Any = None               # kept open in interactive mode


def form_url(job: dict) -> str:
    return job.get("apply_url") or job["url"]


def greenhouse_embed_url(job: dict) -> str:
    return f"https://job-boards.greenhouse.io/embed/job_app?for={job['company_slug']}&token={job['ats_job_id']}"


class Applier:
    def __init__(self, headless: bool = True, context=None, persistent: bool = False):
        from playwright.sync_api import sync_playwright
        self._owns_context = context is None
        self._pw = sync_playwright().start() if self._owns_context else None
        if context is not None:
            self.context = context
            self.browser = context.browser
        elif persistent:
            try:
                self.context = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(config.BROWSER_PROFILE_DIR), channel="chrome", headless=headless,
                    viewport={"width": 1280, "height": 900}, locale="en-US",
                )
            except Exception:
                self._pw.stop()
                raise
            self.browser = self.context.browser
        else:
            try:
                self.browser = self._pw.chromium.launch(headless=headless)
                self.context = self.browser.new_context(
                    viewport={"width": 1280, "height": 900}, locale="en-US",
                )
            except Exception:
                self._pw.stop()
                raise
        self.context.set_default_timeout(15000)

    def close(self) -> None:
        if not self._owns_context:
            return
        try:
            self.context.close()
        finally:
            self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    def _dismiss_cookies(self, page) -> None:
        for sel in COOKIE_BUTTONS:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _ensure_form_open(self, page) -> None:
        if page.locator("input[type=file]").count():
            return
        for sel in OPEN_FORM_BUTTONS:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=4000)
                    page.wait_for_timeout(2000)
                    if page.locator("input[type=file]").count():
                        return
            except Exception:
                continue

    def _screenshot(self, page, job_id: int, tag: str) -> str | None:
        config.ensure_dirs()
        path = config.SHOTS_DIR / f"job{job_id}-{tag}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return None

    def _goto(self, page, url: str):
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        return resp

    def open_form(self, page, job: dict) -> bool:
        """Navigate to the job's application form. False if the posting is closed."""
        resp = self._goto(page, form_url(job))
        if (resp and resp.status >= 400) or formfill.page_is_closed(page):
            return False
        if job["ats"] == "greenhouse" and "greenhouse.io" not in page.url:
            # The board redirects to the company's own site; use Greenhouse's embeddable form instead.
            self._goto(page, greenhouse_embed_url(job))
            if formfill.page_is_closed(page):
                return False
        self._dismiss_cookies(page)
        self._ensure_form_open(page)
        return True

    # ------------------------------------------------------------------
    def apply(self, conn, job: dict, mode: str = "auto", control=None) -> Outcome:
        """mode: 'auto' (submit if safe), 'dry' (fill, never submit), 'interactive' (fill, leave page open)."""
        if job["ats"] not in SUPPORTED:
            return Outcome("failed", f"unsupported ATS {job['ats']}")
        page = None
        reused_blank_page = False
        if not self._owns_context:
            for candidate in self.context.pages:
                if candidate.url == "about:blank" and not candidate.is_closed():
                    page = candidate
                    reused_blank_page = True
                    break
        if page is None:
            page = self.context.new_page()
        keep_open = mode == "interactive"
        try:
            if not self.open_form(page, job):
                return Outcome("closed", "posting is closed or missing")
            if control and hasattr(control, "browser_page"):
                control.browser_page(job, page, "reading application form")
            questions = formfill.analyze(page)
            if not any(q.kind == "file" for q in questions) and not any("mail" in (q.label or "").lower() for q in questions):
                return Outcome("failed", "application form not found on page",
                               screenshot=self._screenshot(page, job["id"], "noform"))

            prev = db.get_application(conn, job["id"])
            cover = (prev["cover_letter"] if prev and prev["cover_letter"] else None) or tailor.cover_letter(job)
            cached = json.loads(prev["answers_json"]).get("values", {}) if prev and prev["answers_json"] else {}
            letter_path = config.LETTERS_DIR / f"job{job['id']}-cover-letter.txt"
            config.ensure_dirs()
            letter_path.write_text(cover, encoding="utf-8")

            plan = formfill.plan_answers(
                questions, job, cover, cached, str(resume_pdf()), str(letter_path),
                ask_llm=lambda qs: tailor.answer_questions(job, qs, cover),
            )
            failures = formfill.fill(page, questions, plan)
            unresolved = list(dict.fromkeys(plan.unresolved + failures))
            answers = {"values": plan.values, "unresolved": unresolved,
                       "labels": {q.key: q.label for q in questions}}
            captcha = formfill.detect_captcha(page)
            shot = self._screenshot(page, job["id"], "filled")
            if control and hasattr(control, "browser_page"):
                control.browser_page(job, page, "form filled; validating answers")
            needs = []
            if unresolved:
                needs.append("needs answers: " + "; ".join(u[:80] for u in unresolved[:6]))
            if captcha == "visible":
                needs.append("CAPTCHA on form")
            reason = " | ".join(needs) or None

            if mode == "dry":
                return Outcome("dry_run", reason or "ready to submit", shot, answers, cover)
            if mode == "interactive":
                return Outcome("filled", reason, shot, answers, cover, page=page)
            if needs:
                return Outcome("queued", reason, shot, answers, cover)

            if control and not control.checkpoint("before_submit"):
                return Outcome("queued", "stopped before submission", shot, answers, cover)

            if not formfill.click_submit(page):
                return Outcome("queued", "submit button not found", shot, answers, cover)
            result, detail = formfill.wait_for_result(page)
            final = self._screenshot(page, job["id"], "after-submit") or shot
            if result == "ok":
                return Outcome("submitted", detail, final, answers, cover)
            if result == "captcha":
                return Outcome("queued", detail, final, answers, cover)
            if result == "invalid":
                return Outcome("queued", "form rejected: " + detail, final, answers, cover)
            return Outcome("queued", "submission not confirmed - check your email before re-submitting",
                           final, answers, cover)
        except Exception as exc:
            log.exception("apply failed for job %s", job["id"])
            return Outcome("failed", f"{type(exc).__name__}: {exc}"[:300],
                           screenshot=self._screenshot(page, job["id"], "error"))
        finally:
            if control and hasattr(control, "browser_page_done"):
                control.browser_page_done()
            if not keep_open:
                if reused_blank_page and not page.is_closed():
                    try:
                        page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
                    except Exception:
                        page.close()
                else:
                    page.close()


def record(conn, job_id: int, out: Outcome) -> None:
    db.save_application(
        conn, job_id, out.status, out.reason,
        cover_letter=out.cover_letter,
        answers_json=json.dumps(out.answers, ensure_ascii=False) if out.answers else None,
        screenshot=out.screenshot,
    )
    conn.commit()
