"""Small text helpers."""
from __future__ import annotations

import difflib
import html
import re
import unicodedata

from bs4 import BeautifulSoup


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    raw = html.unescape(raw)  # Greenhouse double-escapes its HTML
    text = BeautifulSoup(raw, "html.parser").get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


_SUFFIXES = (r"\b(inc|incorporated|llc|ltd|limited|gmbh|corp|corporation|co|plc|ag|bv|sa|pvt|private"
             r"|technologies|technology|labs|hq|group)\b")


def slug_candidates(company: str) -> list[str]:
    """Likely ATS board slugs: 'Acme Labs, Inc.' -> ['acmelabsinc', 'acme-labs-inc', 'acme']."""
    base = norm(company)
    stripped = re.sub(r"\s+", " ", re.sub(_SUFFIXES, " ", base)).strip() or base
    out: list[str] = []
    for variant in (stripped, base):
        for joiner in ("", "-"):
            s = variant.replace(" ", joiner)
            if s and s not in out:
                out.append(s)
    return out[:4]


def best_option(answer: str, options: list[str]) -> int | None:
    """Index of the option that best matches an answer, or None if nothing is close."""
    if not options or answer is None:
        return None
    a = norm(str(answer))
    if not a:
        return None
    normed = [norm(o) for o in options]
    for i, o in enumerate(normed):
        if o == a:
            return i
    for i, o in enumerate(normed):
        if o and (o.startswith(a) or a.startswith(o)):
            return i
    for i, o in enumerate(normed):
        if o and (a in o or o in a):
            return i
    scores = [difflib.SequenceMatcher(None, a, o).ratio() for o in normed]
    best = max(range(len(scores)), key=scores.__getitem__)
    return best if scores[best] >= 0.6 else None
