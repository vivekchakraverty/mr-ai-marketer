"""Editorial, community, and institutional discovery feeds.

Ported from TrendScout's engine.py unchanged in behaviour. These are the sources
that nominate candidate stories; they do not by themselves prove acceleration —
that is signals.py's job.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from .models import Evidence

USER_AGENT = "TopicScout/1.0 (Mr AI Marketer, local desktop research tool)"
TIMEOUT = 10


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = TIMEOUT) -> dict:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def as_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = datetime.fromisoformat(value)
        elif "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            parsed = parsedate_to_datetime(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)


def fetch_google_news(query: str, days: int, config: dict) -> list[Evidence]:
    url = (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}+when%3A{days}d&hl=en-US&gl=US&ceid=US%3Aen"
    )
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=TIMEOUT) as response:
        root = ET.fromstring(response.read())
    entries = root.findall(".//item")
    return [
        Evidence(
            title=(item.findtext("title") or "").rsplit(" - ", 1)[0],
            url=item.findtext("link") or "",
            source="Google News",
            family="news",
            published=as_date(item.findtext("pubDate")),
            strength=1.0,
        )
        for item in entries[:40] if item.findtext("title")
    ]


def fetch_rss(
    url: str,
    source: str,
    family: str = "news",
    limit: int = 40,
    strength: float = 1.0,
) -> list[Evidence]:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    })
    with urlopen(request, timeout=TIMEOUT) as response:
        root = ET.fromstring(response.read())
    items: list[Evidence] = []
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for entry in entries[:limit]:
        title = (
            entry.findtext("title")
            or entry.findtext("{http://www.w3.org/2005/Atom}title")
            or ""
        ).strip()
        if title:
            link = (entry.findtext("link") or "").strip()
            if not link:
                atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.get("href", "").strip() if atom_link is not None else ""
            published = (
                entry.findtext("pubDate")
                or entry.findtext("{http://www.w3.org/2005/Atom}published")
                or entry.findtext("{http://www.w3.org/2005/Atom}updated")
            )
            items.append(Evidence(
                title=title,
                url=link,
                source=source,
                family=family,
                published=as_date(published),
                strength=strength,
            ))
    return items


def fetch_bing_news(query: str, days: int, config: dict) -> list[Evidence]:
    # Bing's RSS endpoint does not expose a reliable date-window parameter, so
    # the shared preprocessing stage applies the requested cutoff.
    url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt=en-US"
    return fetch_rss(url, "Bing News")


def fetch_ap_news(query: str, days: int, config: dict) -> list[Evidence]:
    # AP is a source-of-record input, so it receives a small quality lift. Niche
    # relevance and the selected time window are still enforced downstream.
    try:
        return fetch_rss("https://apnews.com/index.rss", "AP News", strength=1.15)
    except Exception:
        return fetch_rss("https://apnews.com/hub/apf-topnews?output=rss", "AP News", strength=1.15)


def fetch_abc_australia(query: str, days: int, config: dict) -> list[Evidence]:
    return fetch_rss(
        "https://www.abc.net.au/news/feed/media/11999118/atom",
        "ABC News Australia",
        strength=1.10,
    )


def fetch_techmeme(query: str, days: int, config: dict) -> list[Evidence]:
    return fetch_rss("https://www.techmeme.com/feed.xml", "Techmeme", strength=1.10)


def fetch_mediagazer(query: str, days: int, config: dict) -> list[Evidence]:
    return fetch_rss("https://mediagazer.com/feed.xml", "Mediagazer", strength=1.05)


def fetch_wesmirch(query: str, days: int, config: dict) -> list[Evidence]:
    # Useful for culture/buzz discovery, but algorithmic rather than a source of
    # record, so it is not given an editorial-quality lift.
    return fetch_rss("https://www.wesmirch.com/feed.xml", "WeSmirch")


def fetch_sciencedaily(query: str, days: int, config: dict) -> list[Evidence]:
    return fetch_rss("https://www.sciencedaily.com/rss/all.xml", "ScienceDaily", limit=60, strength=1.05)


def fetch_phys_org(query: str, days: int, config: dict) -> list[Evidence]:
    return fetch_rss("https://phys.org/rss-feed/", "Phys.org", limit=60, strength=1.05)


def fetch_gdelt(query: str, days: int, config: dict) -> list[Evidence]:
    start = (datetime.now(timezone.utc) - timedelta(days=min(days, 90))).strftime("%Y%m%d%H%M%S")
    data = get_json("https://api.gdeltproject.org/api/v2/doc/doc", {
        "query": query, "mode": "artlist", "maxrecords": 75,
        "format": "json", "sort": "datedesc", "startdatetime": start,
    })
    return [
        Evidence(
            title=item.get("title", ""),
            url=item.get("url", ""),
            source="GDELT",
            family="news",
            published=as_date(item.get("seendate")),
            strength=1.05,
        )
        for item in data.get("articles", []) if item.get("title")
    ]


def fetch_hacker_news(query: str, days: int, config: dict) -> list[Evidence]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    data = get_json("https://hn.algolia.com/api/v1/search_by_date", {
        "query": query, "tags": "story", "numericFilters": f"created_at_i>{cutoff}",
        "hitsPerPage": 40,
    })
    results = []
    for item in data.get("hits", []):
        title = item.get("title") or ""
        if not title:
            continue
        activity = max(1, (item.get("points") or 0) + 2 * (item.get("num_comments") or 0))
        results.append(Evidence(
            title=title,
            url=item.get("url") or f"https://news.ycombinator.com/item?id={item.get('objectID', '')}",
            source="Hacker News",
            family="conversation",
            published=as_date(item.get("created_at")),
            strength=1 + min(4, math.log10(activity + 1)),
        ))
    return results


def fetch_github(query: str, days: int, config: dict) -> list[Evidence]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    token = str((config or {}).get("github_token") or "").strip()
    data = get_json(
        "https://api.github.com/search/repositories",
        {"q": f"{query} created:>{since}", "sort": "stars", "order": "desc", "per_page": 30},
        {"Authorization": f"Bearer {token}"} if token else None,
    )
    return [
        Evidence(
            title=(item.get("name", "") + ": " + (item.get("description") or "")).strip(": "),
            url=item.get("html_url", ""),
            source="GitHub",
            family="adoption",
            published=as_date(item.get("created_at")),
            strength=1 + min(5, math.log10(item.get("stargazers_count", 0) + 1)),
        )
        for item in data.get("items", []) if item.get("name")
    ]


def fetch_openalex(query: str, days: int, config: dict) -> list[Evidence]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    params = {
        "search": query,
        "filter": f"from_publication_date:{since}",
        "sort": "cited_by_count:desc",
        "per-page": 30,
    }
    email = str((config or {}).get("contact_email") or "").strip()
    if email:
        params["mailto"] = email
    data = get_json("https://api.openalex.org/works", params)
    return [
        Evidence(
            title=item.get("display_name", ""),
            url=(item.get("primary_location") or {}).get("landing_page_url") or item.get("id", ""),
            source="OpenAlex",
            family="research",
            published=as_date(item.get("publication_date")),
            strength=1 + min(4, math.log10(item.get("cited_by_count", 0) + 1)),
        )
        for item in data.get("results", []) if item.get("display_name")
    ]


EDITORIAL_FETCHERS = {
    "Google News": fetch_google_news,
    "Bing News": fetch_bing_news,
    "GDELT": fetch_gdelt,
    "AP News": fetch_ap_news,
    "ABC News Australia": fetch_abc_australia,
    "Techmeme": fetch_techmeme,
    "Mediagazer": fetch_mediagazer,
    "WeSmirch": fetch_wesmirch,
    "ScienceDaily": fetch_sciencedaily,
    "Phys.org": fetch_phys_org,
    "Hacker News": fetch_hacker_news,
    "GitHub": fetch_github,
    "OpenAlex": fetch_openalex,
}
