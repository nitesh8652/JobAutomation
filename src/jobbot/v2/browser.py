"""Visible persistent Chrome session and cooperative agent controls."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

from ..appliers.formfill import _CLOSED
from . import settings as cfg

log = logging.getLogger("jobbot.v2.browser")

COOKIE_BUTTONS = [
    'button:has-text("Accept all")', 'button:has-text("Accept All")', 'button:has-text("Accept")',
    'button:has-text("Allow all")', 'button:has-text("I agree")',
]
OPEN_FORM_BUTTONS = [
    'a:has-text("Apply for this job")', 'button:has-text("Apply for this job")',
    'button:has-text("Apply now")', 'a:has-text("Apply now")', 'button:has-text("Apply")',
]


class Control:
    def __init__(self) -> None:
        self._running = asyncio.Event()
        self._running.set()
        self._stop = asyncio.Event()
        self._emergency = asyncio.Event()
        self.stage = "idle"

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def emergency(self) -> bool:
        return self._emergency.is_set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set() and not self.stopped

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        if not self.stopped:
            self._running.set()

    def stop(self, emergency: bool = False) -> None:
        self._stop.set()
        if emergency:
            self._emergency.set()
        self._running.set()

    async def checkpoint(self, stage: str) -> bool:
        self.stage = stage
        if self.stopped:
            return False
        while not self._running.is_set():
            if self.stopped:
                return False
            await asyncio.sleep(0.2)
        return not self.stopped

    async def sleep(self, seconds: float) -> bool:
        end = asyncio.get_running_loop().time() + max(0.0, seconds)
        while asyncio.get_running_loop().time() < end:
            if self.stopped:
                return False
            if not self._running.is_set():
                return await self.checkpoint(self.stage)
            await asyncio.sleep(min(0.5, max(0.01, end - asyncio.get_running_loop().time())))
        return not self.stopped


class BrowserSession:
    """One dedicated persistent Chrome profile, separate from the user's normal Chrome."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings
        self._pw = None
        self.context = None
        self.pages: list[Any] = []
        self._assigned: set[int] = set()
        self._closed = False
        self.channel = os.environ.get("JOBBOT_BROWSER_CHANNEL", "chrome")

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        cfg.ensure_dirs()
        self._pw = await async_playwright().start()
        args = [
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        launch = dict(
            user_data_dir=str(cfg.BROWSER_PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 960},
            locale="en-US",
            accept_downloads=True,
            args=args,
        )
        try:
            self.context = await self._pw.chromium.launch_persistent_context(channel=self.channel, **launch)
        except Exception:
            if os.environ.get("JOBBOT_BROWSER_CHANNEL"):
                await self._pw.stop()
                raise
            log.warning("Google Chrome channel unavailable; falling back to Playwright Chromium (still visible).")
            self.context = await self._pw.chromium.launch_persistent_context(**launch)
        self.context.set_default_timeout(15000)
        self.pages = [page for page in self.context.pages if not page.is_closed()]
        log.info("visible Chrome started with %d restored tab(s)", len(self.pages))

    def _max_tabs(self) -> int:
        return max(1, min(6, int(self.settings.get("max_tabs", 6))))

    async def acquire_tab(self) -> Any:
        if self.context is None:
            raise RuntimeError("browser is not running")
        for page in list(self.pages):
            if page.is_closed():
                self.pages.remove(page)
                self._assigned.discard(id(page))
        for page in self.pages:
            if id(page) not in self._assigned:
                self._assigned.add(id(page))
                return page
        if len(self.pages) < self._max_tabs():
            page = await self.context.new_page()
            self.pages.append(page)
            self._assigned.add(id(page))
            return page
        raise RuntimeError("maximum browser tabs are already in use")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.context is not None:
                await self.context.close()
        except Exception as exc:
            log.info("browser close reported %s", type(exc).__name__)
        finally:
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
            self.context = None
            self.pages = []
            self._assigned = set()


async def dismiss_cookies(page) -> None:
    for selector in COOKIE_BUTTONS:
        try:
            locator = page.locator(selector)
            if await locator.count() and await locator.first.is_visible():
                await locator.first.click(timeout=2000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def ensure_form_open(page) -> None:
    if await page.locator("input[type=file]").count():
        return
    for selector in OPEN_FORM_BUTTONS:
        try:
            locator = page.locator(selector)
            if await locator.count() and await locator.first.is_visible():
                await locator.first.click(timeout=4000)
                await page.wait_for_timeout(2000)
                if await page.locator("input[type=file]").count():
                    return
        except Exception:
            continue


async def goto(page, url: str, attempts: int = 3):
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            return response
        except Exception as exc:
            last = exc
            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(2 ** attempt)
    assert last is not None
    raise last


async def page_is_closed(page) -> bool:
    if "error=true" in page.url:
        return True
    try:
        return bool(_CLOSED.search((await page.inner_text("body", timeout=5000))[:5000]))
    except Exception:
        return False


async def screenshot(page, job_id: int, tag: str) -> str | None:
    cfg.ensure_dirs()
    path = cfg.SCREENSHOT_DIR / f"job{job_id}-{tag}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return None


def stamp() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S")
