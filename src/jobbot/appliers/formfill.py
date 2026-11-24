"""Generic application-form engine: read the form, decide answers, fill it, check the result.

Works on any ATS form by reading labels. Anything it can't answer confidently is reported
as `unresolved`, which sends the application to the human review queue instead of guessing."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..text import best_option, norm

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- reading the form

_ANALYZE_JS = r"""
() => {
  const forms = Array.from(document.forms);
  const root = forms.find(f => f.querySelector('input[type=file]')) ||
               forms.find(f => f.querySelector('input[type=email]')) || document;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  const visible = el => {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const isChoiceEl = t => t && (t.type === 'radio' || t.type === 'checkbox');
  // a <label> that names one option of a radio/checkbox group (not the question itself)
  const isOptionLabel = l => l.tagName === 'LABEL' &&
    (isChoiceEl(l.htmlFor && document.getElementById(l.htmlFor)) || !!l.querySelector('input[type=radio],input[type=checkbox]'));
  const labelFor = el => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
      if (clean(t)) return clean(t);
    }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const wrap = el.closest('label');
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    return '';
  };
  const headingIn = (c, el) => Array.from(c.querySelectorAll('legend, label, [class*=question], [class*=label]'))
    .find(x => !x.contains(el) && !isOptionLabel(x) && clean(x.innerText));
  const groupLabel = el => {
    const fs = el.closest('fieldset');
    if (fs) { const h = headingIn(fs, el); if (h) return clean(h.innerText); }
    const rg = el.closest('[role=radiogroup],[role=group]');
    if (rg) {
      const t = rg.getAttribute('aria-label') ||
        ((document.getElementById(rg.getAttribute('aria-labelledby') || '') || {}).innerText);
      if (clean(t)) return clean(t);
    }
    let p = el.parentElement;
    for (let d = 0; p && p !== root && d < 6; d++, p = p.parentElement) {
      const h = headingIn(p, el);
      if (h) return clean(h.innerText);
    }
    return '';
  };
  const GENERIC = /^(attach|upload|upload file|choose file|browse|drop files? here|select file|enter manually)$/i;
  const out = [];
  let i = 0;
  for (const el of root.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (el.disabled) continue;
    const role = (el.getAttribute('role') || '').toLowerCase();
    const isChoice = isChoiceEl(el);
    if (type !== 'file' && !isChoice) {
      if (!visible(el)) continue;
      // helper inputs (e.g. react-select's hidden "required" mirror)
      if (el.getAttribute('aria-hidden') === 'true' || (el.tabIndex === -1 && role !== 'combobox')) continue;
    }
    if (isChoice && !visible(el) && !(el.closest('label') && visible(el.closest('label'))) &&
        !(el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'))) continue;
    const id = 'jb' + (i++);
    el.setAttribute('data-jobbot', id);
    let label = labelFor(el);
    let group = isChoice ? groupLabel(el) : '';
    if ((!label || (type === 'file' && GENERIC.test(label))) && !isChoice) label = groupLabel(el) || label;
    if (!label) label = clean(el.getAttribute('placeholder') || el.getAttribute('name') || '');
    const options = tag === 'select'
      ? Array.from(el.options).map(o => clean(o.text)).filter(t => t && !/^(select|choose|please select|--)/i.test(t))
      : [];
    if (tag === 'select' && options.length) {  // label wrapped the <select>: drop the option text from it
      const cut = label.indexOf(options[0]);
      if (cut > 0) label = clean(label.slice(0, cut).replace(/select\.\.\.\s*$/i, ''));
    }
    const markedReq = /[*✱]/.test(label) || /[*✱]/.test(group);
    label = clean(label.replace(/[*✱]/g, ' ')); group = clean(group.replace(/[*✱]/g, ' '));
    if (type === 'file') label = clean(label.replace(/attach resume\/cv|attach|upload file/ig, ' ')) || label;
    const required = el.required || el.getAttribute('aria-required') === 'true' || markedReq;
    out.push({
      id, tag, type, role, name: el.getAttribute('name') || '', hint: (el.id || '') + ' ' + (el.getAttribute('name') || ''),
      label, group, options, required,
      value: isChoice ? el.checked : (el.value || ''), accept: el.getAttribute('accept') || '',
    });
  }
  return out;
}
"""

_CAPTCHA_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 40 && r.height > 40 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  let found = null;
  for (const f of document.querySelectorAll('iframe')) {
    const s = f.src || '';
    const isCap = /recaptcha|hcaptcha|challenges\.cloudflare|turnstile|arkoselabs|funcaptcha/.test(s);
    if (!isCap) continue;
    const invisibleKind = /size=invisible/.test(s);
    if (vis(f) && !invisibleKind) return 'visible';
    found = 'invisible';
  }
  if (document.querySelector('.g-recaptcha:not([data-size=invisible]), .h-captcha:not([data-size=invisible]), .cf-turnstile')) {
    const el = document.querySelector('.g-recaptcha, .h-captcha, .cf-turnstile');
    if (el && vis(el)) return 'visible';
  }
  return found;
}
"""


@dataclass
class Question:
    key: str
    label: str
    kind: str                      # text|textarea|select|combobox|radio|checkbox|checkbox_single|file
    required: bool
    options: list[str] = field(default_factory=list)
    el_ids: list[str] = field(default_factory=list)
    current: Any = ""
    input_type: str = "text"
    hint: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "kind": self.kind, "required": self.required,
                "options": self.options}


def analyze(page) -> list[Question]:
    raw = page.evaluate(_ANALYZE_JS)
    questions: list[Question] = []
    groups: dict[str, Question] = {}
    used: set[str] = set()

    def unique_key(label: str, kind: str) -> str:
        base = (norm(label)[:80] or "field") + "|" + kind
        k, n = base, 2
        while k in used:
            k, n = f"{base}#{n}", n + 1
        used.add(k)
        return k

    for f in raw:
        t = f["type"]
        if t in ("radio", "checkbox"):
            gkey = f"{t}:{f['name'] or f['group']}"
            if not (f["name"] or f["group"]):  # lone checkbox, no group
                gkey = f"{t}:{f['id']}"
            q = groups.get(gkey)
            if q is None:
                q = Question(key="", label=f["group"] or f["label"], kind=t, required=f["required"],
                             input_type=t)
                groups[gkey] = q
                questions.append(q)
            q.options.append(f["label"])
            q.el_ids.append(f["id"])
            q.required = q.required or f["required"]
            if f["value"]:
                q.current = f["label"]
            continue
        if t == "file":
            kind = "file"
        elif f["tag"] == "select":
            kind = "select"
        elif f["tag"] == "textarea":
            kind = "textarea"
        elif f["role"] == "combobox":
            kind = "combobox"
        else:
            kind = "text"
        questions.append(Question(key="", label=f["label"], kind=kind, required=f["required"],
                                  options=f["options"], el_ids=[f["id"]], current=f["value"], input_type=t,
                                  hint=f.get("hint", "")))

    for q in questions:
        if q.kind == "checkbox" and len(q.options) == 1:
            q.kind = "checkbox_single"
            if not q.label or q.label == q.options[0]:
                q.label = q.options[0]
        q.key = unique_key(q.label, q.kind)

    # react-select style comboboxes only reveal options when opened: peek at them.
    for q in questions:
        if q.kind == "combobox" and not q.options:
            q.options = _peek_combobox_options(page, q)
    return questions


def _peek_combobox_options(page, q: Question) -> list[str]:
    loc = page.locator(f'[data-jobbot="{q.el_ids[0]}"]')
    try:
        loc.click(timeout=3000)
        page.wait_for_timeout(700)
        opts = page.locator('[role="option"]:visible')
        texts = [t.strip() for t in opts.all_inner_texts()[:60] if t.strip()]
        loc.press("Escape")
        return [] if len(texts) > 50 else texts  # long lists (countries, cities) are typed into
    except Exception:
        return []


def detect_captcha(page) -> str | None:
    """'visible' = a challenge a human must solve; 'invisible' = background check only; None = none."""
    try:
        return page.evaluate(_CAPTCHA_JS)
    except Exception:
        return None


# ---------------------------------------------------------------- deciding answers

_STANDARD = [
    (r"\b(preferred )?first name\b|\bgiven name\b|^preferred name$", "first_name"),
    (r"\blast name\b|\bsurname\b|\bfamily name\b", "last_name"),
    (r"^(full )?name$|\bfull name\b|^your name$|\blegal name\b", "full_name"),
    (r"\be ?mail\b", "email"),
    (r"\bphone\b|\bmobile\b|\btelephone\b|\bcontact number\b", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"portfolio|personal (web)?site|^website|website url|other website|^url$", "website"),
    (r"^(current )?location$|^city$|current city|where are you (currently )?(located|based)|^location city"
     r"|what is your (current )?location", "location"),
    (r"^country$|country of residence|which country|country (in which|where) you (are|live|reside)", "country"),
    (r"current (company|employer)|^company$|current organi[sz]ation|^organi[sz]ation$", "current_company"),
    (r"current (job )?title|^title$|^headline$", "current_title"),
    (r"years of (professional |relevant |work )?experience|how many years", "years_experience"),
]
_EEO = re.compile(r"gender|\brace\b|ethnic|hispanic|latin[oa]|veteran|disabilit|sexual orientation|transgender|lgbt", re.I)
_DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (say|answer|disclose|self)|rather not|choose not", re.I)
_CONSENT = re.compile(r"agree|consent|acknowledge|privacy|terms|accurate|certify|confirm|i have read|gdpr|data (processing|protection)", re.I)
_MARKETING = re.compile(r"newsletter|marketing|updates about|stay up to date|future (job|opportunit)|alerts for similar"
                        r"|job alerts|talent (pool|community)|text me|\bsms\b|whatsapp", re.I)


def _profile_value(name: str) -> str | None:
    p = config.profile()
    if name == "full_name":
        return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    if name == "country" and not p.get("country") and p.get("location"):
        return str(p["location"]).split(",")[-1].strip()
    v = p.get(name)
    return None if v in (None, "") else str(v)


def _standard_answer(label: str) -> tuple[str, str] | None:
    """(source, value) from the profile for a recognised field label."""
    lab = norm(label)
    # Profile fields only for short, field-like labels: "how comfortable are you handling
    # phone calls?" must not get your phone number.
    if len(lab.split()) <= 10:
        for pat, name in _STANDARD:
            if re.search(pat, lab):
                v = _profile_value(name)
                return (name, v) if v else None
    for a in config.profile().get("answers") or []:
        if a.get("match") and re.search(a["match"], label, re.I):
            return ("answers", str(a.get("answer", "")))
    return None


@dataclass
class Plan:
    values: dict[str, Any] = field(default_factory=dict)       # question key -> value to fill
    unresolved: list[str] = field(default_factory=list)        # human-readable labels
    llm_used: bool = False


def plan_answers(questions: list[Question], job: dict, cover: str, cached: dict[str, Any],
                 resume_path: str, cover_path: str, ask_llm) -> Plan:
    prof = config.profile()
    plan = Plan()
    pending: list[Question] = []
    file_qs = [x for x in questions if x.kind == "file"
               and not re.search(r"autofill|auto-fill|parse", f"{x.label} {x.hint}", re.I)]
    for q in questions:
        label = q.label or ""
        if not q.required and _MARKETING.search(label):
            continue  # never opt in to marketing / SMS / job alerts
        if q.key in cached and cached[q.key] not in (None, ""):
            plan.values[q.key] = cached[q.key]
            continue
        if q.kind == "file":
            what = f"{label} {q.hint}"
            if re.search(r"autofill|auto-fill|parse", what, re.I):
                continue  # Ashby's "autofill from resume" helper - the real resume field is separate
            if re.search(r"resume|\bcv\b|curriculum", what, re.I) or (
                    not any(re.search(r"resume|\bcv\b", f"{x.label} {x.hint}", re.I) for x in file_qs)
                    and file_qs and q is file_qs[0]):
                plan.values[q.key] = resume_path
            elif re.search(r"cover", what, re.I):
                plan.values[q.key] = cover_path
            elif q.required:
                plan.unresolved.append(f"file upload: {label}")
            continue
        if q.kind == "textarea" and re.search(r"cover letter", label, re.I):
            plan.values[q.key] = cover
            continue
        if q.kind in ("radio", "select", "checkbox", "combobox") and _EEO.search(label):
            if prof.get("eeo_decline", True):
                idx = next((i for i, o in enumerate(q.options) if _DECLINE.search(o)), None)
                if idx is not None:
                    plan.values[q.key] = q.options[idx]
                    continue
            if q.required:
                plan.unresolved.append(label)
            continue
        if q.kind == "text" and _EEO.search(label) and not q.required:
            continue
        if q.kind == "checkbox_single":
            if _CONSENT.search(label) and not _MARKETING.search(label):
                if prof.get("auto_consent", True):
                    plan.values[q.key] = True
                elif q.required:
                    plan.unresolved.append(label)
                continue
            if _MARKETING.search(label):
                continue  # leave opt-ins unticked
        std = _standard_answer(label)
        if std:
            _src, value = std
            if value == "" and not q.required:
                continue  # deliberately left blank in profile.yaml
            if q.kind == "combobox" and not q.options and value:
                plan.values[q.key] = value  # typed into an autocomplete
                continue
            if q.options:
                idx = best_option(value, q.options)
                if idx is not None:
                    plan.values[q.key] = q.options[idx]
                    continue
                # the configured answer doesn't match the offered options -> the AI maps it below
            elif value != "":
                plan.values[q.key] = value
                continue
        if q.current not in ("", None, False) and q.kind != "checkbox_single":
            continue  # already filled (e.g. by resume parsing)
        if q.required:
            pending.append(q)  # optional unknown questions are left blank rather than guessed

    if pending:
        plan.llm_used = True
        payload = []
        for q in pending:
            d = q.as_dict()
            d["options"] = d["options"][:80]
            payload.append(d)
        answers = ask_llm(payload)
        for q in pending:
            a = answers.get(q.key) or {}
            ans, ok = a.get("answer"), bool(a.get("confident"))
            if ok and ans not in (None, "", []):
                if q.options and q.kind != "checkbox":
                    idx = best_option(str(ans), q.options)
                    if idx is None:
                        if q.required:
                            plan.unresolved.append(q.label)
                        continue
                    ans = q.options[idx]
                plan.values[q.key] = ans
            elif q.required:
                plan.unresolved.append(q.label or q.key)
    return plan


# ---------------------------------------------------------------- filling

def _loc(page, el_id: str):
    return page.locator(f'[data-jobbot="{el_id}"]')


def _check(page, el_id: str) -> None:
    loc = _loc(page, el_id)
    try:
        loc.check(timeout=3000)
    except Exception:
        try:
            loc.check(force=True, timeout=3000)
        except Exception:
            loc.evaluate("el => { el.click(); }")


def _pick_dropdown_option(page, value: str, fallback_first: bool) -> bool:
    page.wait_for_timeout(900)
    opts = page.locator('[role="option"]:visible')
    try:
        texts = opts.all_inner_texts()
    except Exception:
        texts = []
    if not texts:
        return False
    idx = best_option(value, texts)
    if idx is None and fallback_first:
        idx = 0
    if idx is None:
        return False
    opts.nth(idx).click(timeout=3000)
    return True


def fill(page, questions: list[Question], plan: Plan) -> list[str]:
    """Fill planned values. Files go first (some ATSs parse the resume and prefill fields).
    Returns labels of required fields that could not be filled."""
    failures: list[str] = []
    order = sorted(questions, key=lambda q: 0 if q.kind == "file" else 1)
    for q in order:
        if q.key not in plan.values:
            continue
        value = plan.values[q.key]
        try:
            if q.kind == "file":
                _loc(page, q.el_ids[0]).set_input_files(value, timeout=10000)
                page.wait_for_timeout(1500)
            elif q.kind in ("text", "textarea"):
                loc = _loc(page, q.el_ids[0])
                loc.fill(str(value), timeout=5000)
                if re.search(r"location|city", q.label or "", re.I):
                    _pick_dropdown_option(page, str(value), fallback_first=True)
            elif q.kind == "select":
                _loc(page, q.el_ids[0]).select_option(label=str(value), timeout=5000)
            elif q.kind == "combobox":
                loc = _loc(page, q.el_ids[0])
                is_place = re.search(r"location|city", q.label or "", re.I) is not None
                typed = str(value).split(",")[0].strip() if is_place else str(value)
                loc.click(timeout=3000)
                loc.fill(typed, timeout=3000)
                if is_place:
                    page.wait_for_timeout(1500)  # place autocompletes query a geocoder
                if not _pick_dropdown_option(page, str(value), fallback_first=is_place):
                    loc.press("Escape")
                    raise ValueError(f"no dropdown option matches {value!r}")
            elif q.kind == "radio":
                idx = best_option(str(value), q.options)
                if idx is None:
                    raise ValueError(f"no option matches {value!r}")
                _check(page, q.el_ids[idx])
            elif q.kind == "checkbox":
                vals = value if isinstance(value, list) else [value]
                for v in vals:
                    idx = best_option(str(v), q.options)
                    if idx is not None:
                        _check(page, q.el_ids[idx])
            elif q.kind == "checkbox_single":
                if value in (True, "true", "True", "yes", "Yes"):
                    _check(page, q.el_ids[0])
        except Exception as exc:
            log.info("could not fill %r: %s", q.label, exc)
            if q.required:
                failures.append(q.label or q.key)
    return failures


# ---------------------------------------------------------------- submitting

SUBMIT_SELECTORS = [
    'button:has-text("Submit application")',
    "#btn-submit",
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Submit")',
    'button:has-text("Apply")',
]
_SUCCESS = re.compile(
    r"thank(s| you)[^.]{0,60}(appl|submi|interest)|application (has been |was )?(submitted|received|sent)"
    r"|we('ve| have) received your application|successfully (submitted|applied)|your application is in", re.I)
_SUCCESS_URL = re.compile(r"confirm|thank|success|submitted|applied", re.I)
_CLOSED = re.compile(r"no longer (accepting|available|open)|position (has been )?(filled|closed)|job (is )?not found"
                     r"|this job (has )?(expired|closed)|page (you('re| are) looking for|not found)", re.I)


def page_is_closed(page) -> bool:
    if "error=true" in page.url:
        return True
    try:
        return bool(_CLOSED.search(page.inner_text("body", timeout=5000)[:5000]))
    except Exception:
        return False


def click_submit(page) -> bool:
    for sel in SUBMIT_SELECTORS:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.last.is_visible():
                loc.last.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def check_success(page) -> bool:
    try:
        if _SUCCESS_URL.search(page.url):
            return True
        return bool(_SUCCESS.search(page.inner_text("body", timeout=3000)[:20000]))
    except Exception:
        return False


def wait_for_result(page, timeout_s: int = 25) -> tuple[str, str]:
    """After clicking submit: ('ok'|'captcha'|'invalid'|'unknown', detail)."""
    for second in range(timeout_s):
        page.wait_for_timeout(1000)
        if check_success(page):
            return "ok", "confirmation detected"
        if detect_captcha(page) == "visible":
            return "captcha", "a CAPTCHA challenge appeared after submit"
        if second >= 6:
            try:
                errs = page.locator('[aria-invalid="true"]:visible, .error:visible, [class*="error"]:visible, [role="alert"]:visible')
                texts = [t.strip() for t in errs.all_inner_texts()[:5] if t.strip()]
            except Exception:
                texts = []
            if texts:
                return "invalid", "; ".join(texts)[:300]
    return "unknown", "no confirmation seen"
