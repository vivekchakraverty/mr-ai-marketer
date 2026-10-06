"""Bounded, opt-in collection through documented APIs and robots-allowed pages."""
from __future__ import annotations

import ipaddress
import hashlib
import json
import re
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import quote, urlparse
from urllib.robotparser import RobotFileParser

import requests

from .core import evidence_unit

USER_AGENT = "MrAIMarketer-PersonaResearch/1.0 (local desktop app; research contact: https://github.com/vivekchakraverty/mr-ai-marketer)"
BLOCKED_PAGE_HOSTS = ("reddit.com", "old.reddit.com", "facebook.com", "instagram.com", "x.com",
                      "twitter.com", "linkedin.com", "trustpilot.com", "g2.com", "capterra.com",
                      "yelp.com", "glassdoor.com", "bsky.app", "youtube.com")
BLOCKED_PAGE_PATHS = re.compile(r"/(?:login|signin|account|checkout|subscribe|members|paywall)(?:/|$)", re.I)
_last_request: dict[str, float] = defaultdict(float)
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _public_host(host: str) -> bool:
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in addresses)


def _get(url: str, *, params: dict | None = None, session: requests.Session | None = None,
         robots: bool = False) -> requests.Response:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not _public_host(parsed.hostname or ""):
        raise ValueError("Only public HTTPS hosts are allowed.")
    host = parsed.hostname or ""
    if robots:
        parser = RobotFileParser()
        parser.set_url(f"https://{host}/robots.txt")
        try:
            rules = _get(f"https://{host}/robots.txt", session=session)
            parser.parse(rules.text.splitlines())
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            parser.parse([])
        if not parser.can_fetch(USER_AGENT, url):
            raise PermissionError("The site's robots.txt disallows this page.")
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]  # type: ignore[return-value]
        wait = max(0, 1.0 - (time.monotonic() - _last_request[host]))
        if wait:
            time.sleep(wait)
        _last_request[host] = time.monotonic()
    requester = session or requests
    response = requester.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=12,
                             allow_redirects=False)
    if response.status_code in (429, 500, 502, 503, 504):
        # One bounded retry; the caller records a source error if it still fails.
        time.sleep(min(float(response.headers.get("Retry-After", 1) or 1), 3))
        response = requester.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=12,
                                 allow_redirects=False)
    response.raise_for_status()
    if 300 <= response.status_code < 400:
        raise ValueError("Redirected sources are not followed.")
    with _lock:
        _cache[key] = (time.time(), response)
    return response


def collect_hacker_news(run_id: str, queries: list[str], *, session=None) -> list[dict]:
    units = []
    for query in queries[:4]:
        data = _get("https://hn.algolia.com/api/v1/search_by_date",
                    params={"query": query[:100], "tags": "(story,comment)", "hitsPerPage": 20}, session=session).json()
        for hit in data.get("hits", [])[:20]:
            text = unescape(re.sub(r"<[^>]+>", " ", str(hit.get("comment_text") or hit.get("story_text") or hit.get("title") or "")))
            unit = evidence_unit(run_id, "Hacker News", text, kind="comment" if hit.get("comment_text") else "post",
                                 date=str(hit.get("created_at") or "")[:10], domain="hn.algolia.com",
                                 engagement=float(hit.get("points") or 0))
            if unit:
                units.append(unit)
    return units


def collect_stack_exchange(run_id: str, queries: list[str], *, session=None) -> list[dict]:
    units = []
    for query in queries[:4]:
        data = _get("https://api.stackexchange.com/2.3/search/advanced",
                    params={"q": query[:100], "site": "stackoverflow", "pagesize": 20, "sort": "relevance"}, session=session).json()
        for item in data.get("items", [])[:20]:
            unit = evidence_unit(run_id, "Stack Exchange", unescape(str(item.get("title") or "")),
                                 kind="post", date=str(item.get("creation_date") or ""),
                                 domain="stackexchange.com", engagement=float(item.get("score") or 0))
            if unit:
                units.append(unit)
        if data.get("backoff"):
            time.sleep(min(int(data["backoff"]), 5))
    return units


def collect_bluesky(run_id: str, queries: list[str], *, session=None) -> list[dict]:
    units = []
    for query in queries[:4]:
        data = _get("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                    params={"q": query[:100], "limit": 25}, session=session).json()
        for post in data.get("posts", [])[:25]:
            record = post.get("record") or {}
            unit = evidence_unit(run_id, "Bluesky", record.get("text", ""), kind="post",
                                 date=str(record.get("createdAt") or "")[:10], domain="bsky.app",
                                 engagement=float(post.get("likeCount") or 0) + float(post.get("repostCount") or 0))
            if unit:
                units.append(unit)
    return units


def collect_mastodon(run_id: str, queries: list[str], *, host: str, token: str) -> list[dict]:
    if not host or not token:
        raise ValueError("Mastodon search needs a connected account and access token.")
    from app.services.mastodon import search_statuses
    units = []
    for index, query in enumerate(queries[:3]):
        if index:
            time.sleep(1)
        for status in search_statuses(host, query[:100], token, limit=20):
            unit = evidence_unit(run_id, "Mastodon", getattr(status, "text", ""), kind="post",
                                 domain=host, engagement=float(getattr(status, "favourites", 0) or 0))
            if unit:
                units.append(unit)
    return units


def collect_web(run_id: str, urls: list[str], *, session=None) -> list[dict]:
    units = []
    failures = []
    for url in urls[:8]:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_PAGE_HOSTS) or BLOCKED_PAGE_PATHS.search(parsed.path):
                raise PermissionError("Social, review, account and paywalled pages require an official API or your CSV export.")
            response = _get(url, session=session, robots=True)
            if "text/html" not in response.headers.get("content-type", ""):
                continue
            # BeautifulSoup is already a direct backend dependency; parse only page title,
            # headings and FAQ paragraphs, never comments, account pages or scripts.
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text[:300_000], "html.parser")
            for tag in soup(["script", "style", "form", "nav", "footer"]):
                tag.decompose()
            for tag in soup.select("title, h1, h2, h3, main p, article p")[:80]:
                unit = evidence_unit(run_id, "Website", tag.get_text(" ", strip=True), kind="page",
                                     domain=host)
                if unit:
                    units.append(unit)
        except (PermissionError, ValueError, requests.RequestException) as exc:
            failures.append(exc)
    if not units and failures:
        raise failures[0]
    return units


def collect_youtube(run_id: str, video_ids: list[str], *, key: str, session=None) -> list[dict]:
    if not key:
        raise ValueError("A YouTube Data API key is required.")
    units = []
    for video_id in video_ids[:3]:
        if not re.fullmatch(r"[\w-]{11}", video_id):
            continue
        data = _get("https://www.googleapis.com/youtube/v3/commentThreads",
                    params={"part": "snippet", "videoId": video_id, "maxResults": 50,
                            "textFormat": "plainText", "key": key}, session=session).json()
        for item in data.get("items", [])[:50]:
            snippet = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            unit = evidence_unit(run_id, "YouTube", snippet.get("textOriginal", ""), kind="comment",
                                 date=str(snippet.get("publishedAt") or "")[:10], domain="youtube.com",
                                 engagement=float(snippet.get("likeCount") or 0))
            if unit:
                units.append(unit)
    return units


def collect_wikipedia(title: str, *, session=None) -> dict:
    """Aggregate attention trend only; pageviews never count as buyer evidence."""
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    start = end - timedelta(days=59)
    article = quote(title.replace(" ", "_"), safe="")
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
           f"en.wikipedia.org/all-access/user/{article}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    data = _get(url, session=session).json()
    views = [int(item.get("views") or 0) for item in data.get("items", [])]
    midpoint = len(views) // 2
    older, newer = sum(views[:midpoint]), sum(views[midpoint:])
    return {"article": title, "days": len(views), "older_views": older, "newer_views": newer,
            "relative_change": round((newer - older) / older, 3) if older else None,
            "note": "Wikipedia pageviews indicate broad topic attention, not buyer intent."}


def collect_reddit(*_args, **_kwargs) -> list[dict]:
    """Deliberate stub: Reddit access requires a reviewed OAuth/terms integration."""
    raise NotImplementedError("Reddit connector is not configured; import your own CSV export.")


def collect_tumblr(*_args, **_kwargs) -> list[dict]:
    """Deliberate stub: reuse of the posting token requires a privacy review."""
    raise NotImplementedError("Tumblr connector is not configured; import your own CSV export.")
