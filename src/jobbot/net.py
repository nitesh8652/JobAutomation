"""Polite HTTP client: honest User-Agent, per-host spacing, retry on 429/5xx."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

USER_AGENT = "jobbot/0.1 (personal job-search tool; reads public job listings)"
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"})
_last_hit: dict[str, float] = {}
_robots: dict[str, RobotFileParser | None] = {}


def get(url: str, params: dict | None = None, min_interval: float = 1.5, timeout: int = 30) -> requests.Response:
    host = urlparse(url).netloc
    resp = None
    for attempt in range(3):
        wait = min_interval - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            resp = _session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            log.warning("GET %s failed: %s", url, exc)
            _last_hit[host] = time.time()
            time.sleep(3 * (attempt + 1))
            continue
        _last_hit[host] = time.time()
        if resp.status_code == 429 or resp.status_code >= 500:
            retry = resp.headers.get("Retry-After")
            delay = int(retry) if retry and retry.isdigit() else 10 * (attempt + 1)
            log.info("%s -> %s, backing off %ss", url, resp.status_code, delay)
            time.sleep(min(delay, 120))
            continue
        return resp
    if resp is None:
        raise requests.ConnectionError(f"could not reach {url}")
    return resp


def get_json(url: str, params: dict | None = None, **kw):
    """Parsed JSON, or None when the resource does not exist (4xx) or isn't JSON."""
    resp = get(url, params=params, **kw)
    if 400 <= resp.status_code < 500:
        return None
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return None


def post_json(url: str, payload: dict, min_interval: float = 1.5, timeout: int = 30):
    """POST JSON and parse the reply. None on 4xx or a non-JSON body."""
    host = urlparse(url).netloc
    wait = min_interval - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    try:
        resp = _session.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        log.warning("POST %s failed: %s", url, exc)
        return None
    _last_hit[host] = time.time()
    if 400 <= resp.status_code < 500:
        return None
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return None


def allowed_by_robots(url: str) -> bool:
    """robots.txt check, used before reading arbitrary company career pages."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp: RobotFileParser | None = RobotFileParser()
        try:
            r = _session.get(base + "/robots.txt", timeout=15)
            if r.status_code >= 400:
                rp = None  # no robots.txt -> allowed
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)
