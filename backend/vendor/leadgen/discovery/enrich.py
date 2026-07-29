"""Per-company enrichment — fetch a discovered business's own site (homepage + a couple of
likely about/contact pages) and pull out a short description, a contact email, and a contact
name. Light single-page fetching with httpx + BeautifulSoup (the app already depends on
both), NOT a crawler. robots.txt is respected per the compliance guardrails.

Only publicly-available business pages are read, and only to build the `profile_text` the
qualifier reads and the email the finder/verifier work from.
"""

from __future__ import annotations

import logging
import re
from urllib import robotparser
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import RawLead

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; MrAiMarketerLeadGen/1.0; +local outreach research)"
_TIMEOUT = 12
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ROLE_NAME_RE = re.compile(
    r"(?:founder|owner|ceo|director|principal|proprietor)[,:\s\-]+([A-Z][a-z]+ [A-Z][a-z]+)"
)
_GENERIC_LOCALPARTS = {"info", "contact", "hello", "sales", "support", "admin", "office", "mail"}
_CANDIDATE_PATHS = ["", "/about", "/about-us", "/contact", "/team"]

_robots_cache: dict[str, robotparser.RobotFileParser] = {}


def _robots_ok(base: str, path: str) -> bool:
    try:
        rp = _robots_cache.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            rp.set_url(urljoin(base, "/robots.txt"))
            try:
                rp.read()
            except Exception:  # noqa: BLE001 — no/unreadable robots.txt means no restriction
                rp = robotparser.RobotFileParser()
                rp.parse([])
            _robots_cache[base] = rp
        return rp.can_fetch(_UA, urljoin(base, path))
    except Exception:  # noqa: BLE001
        return True


def _fetch(url: str) -> str | None:
    try:
        resp = httpx.get(
            url, headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True
        )
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            return resp.text
    except Exception:  # noqa: BLE001 — unreachable/junk sites are expected; skip quietly
        return None
    return None


def _best_email(emails: list[str], domain: str) -> str | None:
    """Prefer an on-domain, non-generic address; fall back to any on-domain, then any."""
    on_domain = [e for e in emails if e.lower().endswith("@" + domain)]
    named = [e for e in on_domain if e.split("@", 1)[0].lower() not in _GENERIC_LOCALPARTS]
    for pool in (named, on_domain, emails):
        if pool:
            return pool[0].lower()
    return None


def enrich(raw: RawLead) -> RawLead:
    """Fill in profile_text / email / contact_name from the company's own site. Returns the
    same RawLead mutated (and returned for convenience). Never raises."""
    base = raw.website or (f"https://{raw.domain}" if raw.domain else "")
    if not base:
        return raw

    texts: list[str] = []
    emails: list[str] = []
    for path in _CANDIDATE_PATHS:
        if path and not _robots_ok(base, path):
            continue
        html = _fetch(urljoin(base, path) if path else base)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        if path == "":  # homepage: meta description + title carry the pitch
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                texts.append(meta["content"].strip())
            if soup.title and soup.title.string:
                texts.append(soup.title.string.strip())

        page_text = soup.get_text(" ", strip=True)
        emails += _EMAIL_RE.findall(page_text)
        if raw.contact_name is None:
            m = _ROLE_NAME_RE.search(page_text)
            if m:
                raw.contact_name = m.group(1)
        # Keep enrichment cheap: the first informative page is usually enough.
        if len(" ".join(texts)) > 300:
            break

    if raw.domain and (best := _best_email(emails, raw.domain)):
        raw.email = raw.email or best
        raw.extra["email_source"] = "scraped"

    extra_profile = " ".join(dict.fromkeys(t for t in texts if t))[:600]
    if extra_profile:
        raw.profile_text = f"{raw.profile_text}\n{extra_profile}".strip()
    return raw
