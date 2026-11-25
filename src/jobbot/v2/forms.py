"""Async application-form interpreter.

The DOM extraction script and answer-planning rules are shared with the existing
sync form engine.  Only the Playwright calls are async so several visible tabs can
work independently.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from ..appliers.formfill import (
    _ANALYZE_JS,
    _CAPTCHA_JS,
    _CLOSED,
    _CONSENT,
    _DECLINE,
    _EEO,
    _MARKETING,
    _STANDARD,
    _SUCCESS,
    _SUCCESS_URL,
    SUBMIT_SELECTORS,
    Plan,
    Question,
)
from ..text import best_option, norm

log = logging.getLogger("jobbot.v2.forms")
AskLLM = Callable[[list[dict[str, Any]]], Awaitable[dict[str, dict[str, Any]]]]


async def analyze(page) -> list[Question]:
    raw = await page.evaluate(_ANALYZE_JS)
    questions: list[Question] = []
    groups: dict[str, Question] = {}
    used: set[str] = set()

    def unique_key(label: str, kind: str) -> str:
        base = (norm(label)[:80] or "field") + "|" + kind
        key, n = base, 2
        while key in used:
            key, n = f"{base}#{n}", n + 1
        used.add(key)
        return key

    for field in raw:
        kind = field["type"]
        if kind in ("radio", "checkbox"):
            group_key = f"{kind}:{field['name'] or field['group']}"
            if not (field["name"] or field["group"]):
                group_key = f"{kind}:{field['id']}"
            question = groups.get(group_key)
            if question is None:
                question = Question(
                    key="", label=field["group"] or field["label"], kind=kind,
                    required=field["required"], input_type=kind,
                )
                groups[group_key] = question
                questions.append(question)
            question.options.append(field["label"])
            question.el_ids.append(field["id"])
            question.required = question.required or field["required"]
            if field["value"]:
                question.current = field["label"]
            continue
        if kind == "file":
            qkind = "file"
        elif field["tag"] == "select":
            qkind = "select"
        elif field["tag"] == "textarea":
            qkind = "textarea"
        elif field["role"] == "combobox":
            qkind = "combobox"
        else:
            qkind = "text"
        questions.append(
            Question(
                key="", label=field["label"], kind=qkind, required=field["required"],
                options=field["options"], el_ids=[field["id"]], current=field["value"],
                input_type=kind, hint=field.get("hint", ""),
            )
        )
    for question in questions:
        if question.kind == "checkbox" and len(question.options) == 1:
            question.kind = "checkbox_single"
            if not question.label or question.label == question.options[0]:
                question.label = question.options[0]
        question.key = unique_key(question.label, question.kind)
    for question in questions:
        if question.kind == "combobox" and not question.options:
            question.options = await _peek_combobox_options(page, question)
    return questions


async def _peek_combobox_options(page, question: Question) -> list[str]:
    locator = page.locator(f'[data-jobbot="{question.el_ids[0]}"]')
    try:
        await locator.click(timeout=3000)
        await page.wait_for_timeout(700)
        options = page.locator('[role="option"]:visible')
        texts = [text.strip() for text in (await options.all_inner_texts())[:60] if text.strip()]
        await locator.press("Escape")
        return [] if len(texts) > 50 else texts
    except Exception:
        return []


CHALLENGE_TEXT = re.compile(
    r"one[- ]?time (?:password|code)|verification code|security code|\botp\b|"
    r"two[- ]?factor|2fa|verify (?:that )?you are (?:a )?human|security check|"
    r"complete the (?:captcha|challenge)|are you a robot",
    re.I,
)


async def detect_challenge(page) -> str | None:
    try:
        captcha = await page.evaluate(_CAPTCHA_JS)
        if captcha == "visible":
            return "captcha"
    except Exception:
        pass
    try:
        text = (await page.inner_text("body", timeout=2500))[:8000]
        match = CHALLENGE_TEXT.search(text)
        if match:
            value = match.group(0).lower()
            return "otp" if ("otp" in value or "one-time" in value or "verification code" in value) else "security"
    except Exception:
        pass
    return None


async def detect_captcha(page) -> str | None:
    return await detect_challenge(page)


def _profile_value(profile: dict[str, Any], name: str) -> str | None:
    if name == "full_name":
        return f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() or None
    if name == "country" and not profile.get("country") and profile.get("location"):
        return str(profile["location"]).split(",")[-1].strip()
    value = profile.get(name)
    return None if value in (None, "") else str(value)


def _standard_answer(label: str, profile: dict[str, Any]) -> tuple[str, str] | None:
    lab = norm(label)
    if len(lab.split()) <= 10:
        for pattern, name in _STANDARD:
            if re.search(pattern, lab):
                value = _profile_value(profile, name)
                return (name, value) if value else None
    for answer in profile.get("answers") or []:
        if answer.get("match") and re.search(str(answer["match"]), label, re.I):
            return ("answers", str(answer.get("answer", "")))
    return None


async def plan_answers(
    questions: list[Question],
    job: dict[str, Any],
    cover: str,
    cached: dict[str, Any],
    resume_path: str,
    cover_path: str,
    profile: dict[str, Any],
    ask_llm: AskLLM,
) -> Plan:
    plan = Plan()
    pending: list[Question] = []
    file_questions = [
        question for question in questions
        if question.kind == "file"
        and not re.search(r"autofill|auto-fill|parse", f"{question.label} {question.hint}", re.I)
    ]
    for question in questions:
        label = question.label or ""
        if not question.required and _MARKETING.search(label):
            continue
        if question.key in cached and cached[question.key] not in (None, ""):
            plan.values[question.key] = cached[question.key]
            continue
        if question.kind == "file":
            what = f"{label} {question.hint}"
            if re.search(r"autofill|auto-fill|parse", what, re.I):
                continue
            if re.search(r"resume|\bcv\b|curriculum", what, re.I) or (
                not any(re.search(r"resume|\bcv\b", f"{q.label} {q.hint}", re.I) for q in file_questions)
                and file_questions and question is file_questions[0]
            ):
                plan.values[question.key] = resume_path
            elif re.search(r"cover", what, re.I):
                plan.values[question.key] = cover_path
            elif question.required:
                plan.unresolved.append(f"file upload: {label}")
            continue
        if question.kind == "textarea" and re.search(r"cover letter", label, re.I):
            plan.values[question.key] = cover
            continue
        if question.kind in ("radio", "select", "checkbox", "combobox") and _EEO.search(label):
            if profile.get("eeo_decline", True):
                index = next((i for i, option in enumerate(question.options) if _DECLINE.search(option)), None)
                if index is not None:
                    plan.values[question.key] = question.options[index]
                    continue
            if question.required:
                plan.unresolved.append(label)
            continue
        if question.kind == "text" and _EEO.search(label) and not question.required:
            continue
        if question.kind == "checkbox_single":
            if _CONSENT.search(label) and not _MARKETING.search(label):
                if profile.get("auto_consent", True):
                    plan.values[question.key] = True
                elif question.required:
                    plan.unresolved.append(label)
                continue
            if _MARKETING.search(label):
                continue
        standard = _standard_answer(label, profile)
        if standard:
            _source, value = standard
            if value == "" and not question.required:
                continue
            if question.kind == "combobox" and not question.options and value:
                plan.values[question.key] = value
                continue
            if question.options:
                index = best_option(value, question.options)
                if index is not None:
                    plan.values[question.key] = question.options[index]
                    continue
            elif value != "":
                plan.values[question.key] = value
                continue
        if question.current not in ("", None, False) and question.kind != "checkbox_single":
            continue
        if question.required:
            pending.append(question)

    if pending:
        plan.llm_used = True
        payload = []
        for question in pending:
            data = question.as_dict()
            data["options"] = data["options"][:80]
            payload.append(data)
        answers = await ask_llm(payload)
        for question in pending:
            answer = answers.get(question.key) or {}
            value, confident = answer.get("answer"), bool(answer.get("confident"))
            if confident and value not in (None, "", []):
                if question.options and question.kind != "checkbox":
                    index = best_option(str(value), question.options)
                    if index is None:
                        if question.required:
                            plan.unresolved.append(question.label)
                        continue
                    value = question.options[index]
                plan.values[question.key] = value
            elif question.required:
                plan.unresolved.append(question.label or question.key)
    return plan


async def _loc(page, el_id: str):
    return page.locator(f'[data-jobbot="{el_id}"]')


async def _check(page, el_id: str) -> None:
    locator = page.locator(f'[data-jobbot="{el_id}"]')
    try:
        await locator.check(timeout=3000)
    except Exception:
        try:
            await locator.check(force=True, timeout=3000)
        except Exception:
            await locator.evaluate("el => { el.click(); }")


async def _pick_dropdown_option(page, value: str, fallback_first: bool) -> bool:
    await page.wait_for_timeout(900)
    options = page.locator('[role="option"]:visible')
    try:
        texts = await options.all_inner_texts()
    except Exception:
        texts = []
    if not texts:
        return False
    index = best_option(value, texts)
    if index is None and fallback_first:
        index = 0
    if index is None:
        return False
    await options.nth(index).click(timeout=3000)
    return True


async def fill(page, questions: list[Question], plan: Plan) -> list[str]:
    failures: list[str] = []
    order = sorted(questions, key=lambda question: 0 if question.kind == "file" else 1)
    for question in order:
        if question.key not in plan.values:
            continue
        value = plan.values[question.key]
        try:
            if question.kind == "file":
                await page.locator(f'[data-jobbot="{question.el_ids[0]}"]').set_input_files(value, timeout=10000)
                await page.wait_for_timeout(1500)
            elif question.kind in ("text", "textarea"):
                locator = page.locator(f'[data-jobbot="{question.el_ids[0]}"]')
                await locator.fill(str(value), timeout=5000)
                if re.search(r"location|city", question.label or "", re.I):
                    await _pick_dropdown_option(page, str(value), fallback_first=True)
            elif question.kind == "select":
                await page.locator(f'[data-jobbot="{question.el_ids[0]}"]').select_option(label=str(value), timeout=5000)
            elif question.kind == "combobox":
                locator = page.locator(f'[data-jobbot="{question.el_ids[0]}"]')
                is_place = re.search(r"location|city", question.label or "", re.I) is not None
                typed = str(value).split(",")[0].strip() if is_place else str(value)
                await locator.click(timeout=3000)
                await locator.fill(typed, timeout=3000)
                if is_place:
                    await page.wait_for_timeout(1500)
                if not await _pick_dropdown_option(page, str(value), fallback_first=is_place):
                    await locator.press("Escape")
                    raise ValueError(f"no dropdown option matches {value!r}")
            elif question.kind == "radio":
                index = best_option(str(value), question.options)
                if index is None:
                    raise ValueError(f"no option matches {value!r}")
                await _check(page, question.el_ids[index])
            elif question.kind == "checkbox":
                values = value if isinstance(value, list) else [value]
                for item in values:
                    index = best_option(str(item), question.options)
                    if index is not None:
                        await _check(page, question.el_ids[index])
            elif question.kind == "checkbox_single":
                if value in (True, "true", "True", "yes", "Yes"):
                    await _check(page, question.el_ids[0])
        except Exception as exc:
            log.info("could not fill %r: %s", question.label, exc)
            if question.required:
                failures.append(question.label or question.key)
    return failures


async def page_is_closed(page) -> bool:
    if "error=true" in page.url:
        return True
    try:
        return bool(_CLOSED.search((await page.inner_text("body", timeout=5000))[:5000]))
    except Exception:
        return False


async def click_submit(page) -> bool:
    for selector in SUBMIT_SELECTORS:
        locator = page.locator(selector)
        try:
            if await locator.count() and await locator.last.is_visible():
                await locator.last.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def check_success(page) -> bool:
    try:
        if _SUCCESS_URL.search(page.url):
            return True
        return bool(_SUCCESS.search((await page.inner_text("body", timeout=3000))[:20000]))
    except Exception:
        return False


async def wait_for_result(page, timeout_s: int = 25) -> tuple[str, str]:
    for second in range(timeout_s):
        await page.wait_for_timeout(1000)
        if await check_success(page):
            return "ok", "confirmation detected"
        challenge = await detect_challenge(page)
        if challenge:
            return "captcha", f"a {challenge} challenge appeared after submit"
        if second >= 6:
            try:
                errors = page.locator(
                    '[aria-invalid="true"]:visible, .error:visible, [class*="error"]:visible, [role="alert"]:visible'
                )
                texts = [text.strip() for text in (await errors.all_inner_texts())[:5] if text.strip()]
            except Exception:
                texts = []
            if texts:
                return "invalid", "; ".join(texts)[:300]
    return "unknown", "no confirmation seen"


def form_present(questions: list[Question]) -> bool:
    return any(q.kind == "file" for q in questions) or any("mail" in (q.label or "").lower() for q in questions)
