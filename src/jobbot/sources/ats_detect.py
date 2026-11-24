"""Work out which ATS a URL belongs to, and scan career pages for ATS links."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from ..net import allowed_by_robots, get

# Sites whose terms forbid automation: we only ever *track* these, never open them.
RESTRICTED_HOSTS = ("linkedin.com", "naukri.com", "indeed.", "workindia.in", "glassdoor.",
                    "instagram.com", "facebook.com", "fb.com", "monster.", "foundit.in", "shine.com",
                    "apna.co", "wellfound.", "instahyre.", "cutshort.", "hirist.", "ziprecruiter.",
                    "dice.com", "upwork.", "bayt.", "naukrigulf", "timesjobs.", "seek.com")


@dataclass
class Detected:
    ats: str                 # greenhouse|lever|ashby|workable|recruitee|personio|restricted|other
    slug: str | None = None
    job_id: str | None = None


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?!embed/)([\w-]+)(?:/jobs/(\d+))?", re.I)),
    ("lever", re.compile(rf"jobs\.(?:eu\.)?lever\.co/([\w.-]+)(?:/({_UUID}))?", re.I)),
    ("ashby", re.compile(rf"jobs\.ashbyhq\.com/([^/?#\"'\s]+)(?:/({_UUID}))?", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?!j/|api/)([\w-]+)(?:/j/([0-9A-Z]+))?", re.I)),
]
_GH_EMBED = re.compile(r"greenhouse\.io/embed/job_(?:app|board)(?:/js)?\?([^\"'\s<>]+)", re.I)
_NOT_SLUGS = {"embed", "api", "v1", "j", "jobs", "careers", "www", "app"}
# Boards that live on a company subdomain rather than a path.
_HOST_PATTERNS = [
    ("recruitee", re.compile(r"^(?P<slug>[\w-]+)\.recruitee\.com$", re.I)),
    ("personio", re.compile(r"^(?P<slug>[\w-]+)\.jobs\.personio\.(?:de|com)$", re.I)),
]
_HOST_JOB_ID = {
    "recruitee": re.compile(r"/o/([\w-]+)"),
    "personio": re.compile(r"/job/(\d+)"),
}
_HOST_LINKS = [
    ("recruitee", re.compile(r"https?://([\w-]+)\.recruitee\.com", re.I)),
    ("personio", re.compile(r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)", re.I)),
]


def detect(url: str) -> Detected:
    host = (urlparse(url).netloc or "").lower()
    if any(h in host for h in RESTRICTED_HOSTS):
        return Detected("restricted", host)
    m = _GH_EMBED.search(url)
    if m:
        qs = parse_qs(m.group(1).replace("&amp;", "&"))
        slug = (qs.get("for") or [None])[0]
        if slug:
            return Detected("greenhouse", slug, (qs.get("token") or [None])[0])
    for ats, pat in _HOST_PATTERNS:
        m = pat.match(host)
        if m and m.group("slug").lower() not in _NOT_SLUGS:
            jm = _HOST_JOB_ID[ats].search(urlparse(url).path)
            return Detected(ats, m.group("slug"), jm.group(1) if jm else None)
    for ats, pat in _PATTERNS:
        m = pat.search(url)
        if m and m.group(1).lower() not in _NOT_SLUGS:
            return Detected(ats, unquote(m.group(1)), m.group(2))
    return Detected("other")


def find_ats_links(html: str) -> set[tuple[str, str]]:
    """All (ats, slug) boards referenced in a page's HTML."""
    found: set[tuple[str, str]] = set()
    for m in _GH_EMBED.finditer(html):
        slug = (parse_qs(m.group(1).replace("&amp;", "&")).get("for") or [None])[0]
        if slug:
            found.add(("greenhouse", slug))
    for ats, pat in _HOST_LINKS:
        for m in pat.finditer(html):
            if m.group(1).lower() not in _NOT_SLUGS:
                found.add((ats, m.group(1)))
    for ats, pat in _PATTERNS:
        for m in pat.finditer(html):
            slug = unquote(m.group(1))
            if slug.lower() not in _NOT_SLUGS:
                found.add((ats, slug))
    return found


def scan_career_page(url: str) -> set[tuple[str, str]]:
    """Read a company careers page once (respecting robots.txt) and return the ATS boards it links to."""
    if detect(url).ats == "restricted":
        return set()
    if not allowed_by_robots(url):
        raise PermissionError(f"robots.txt disallows {url}")
    resp = get(url, min_interval=3)
    if resp.status_code >= 400:
        return set()
    return find_ats_links(resp.text)
