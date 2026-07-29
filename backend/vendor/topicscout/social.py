"""Social and consumer discovery feeds, adapted from TrendScope.

Source logic ported from github.com/mamboyepez17/trendscope (MIT) — Reddit RSS,
Google Trends RSS, YouTube's internal search endpoint, TikTok Creative Center,
Amazon Best Sellers, and Twitter/X via xactions. TrendScope emits loose dicts keyed
per platform; here each one is re-expressed as TrendScout ``Evidence`` so it goes
through the same relevance, dedupe, capping, and family weighting as a news feed.

Two adaptations were needed and are worth knowing about:

* **Geography and language.** TrendScope is tuned for Colombia and Spanish (geo=CO,
  hl=es, Spanish keyword expansion). This build is niche-first and geo is a setting,
  so the hardcoded locale is replaced by ``config["geo"]``.
* **Dates.** Google Trends, TikTok, and Amazon expose a *current snapshot*, not a
  dated stream, so their evidence is stamped "now". That is honest but it means they
  always sit at the top of the recency curve; they are given a conservative base
  strength to compensate, and they cannot earn the multi-day persistence bonus in
  the ranking model because all their items share one date.
"""

from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from .models import Evidence

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 15

# TrendScope's own category -> Amazon Best Sellers mapping, rekeyed to English terms
# so a free-text niche can be matched against it.
AMAZON_LISTS = [
    (("tech", "software", "ai", "gadget", "developer", "electronics", "hardware"),
     "https://www.amazon.com/Best-Sellers-Electronics/zgbs/electronics/"),
    (("health", "wellness", "fitness", "supplement", "medical", "nutrition"),
     "https://www.amazon.com/Best-Sellers-Health-Personal-Care/zgbs/hpc/"),
    (("sport", "outdoor", "running", "cycling", "gym"),
     "https://www.amazon.com/Best-Sellers-Sports-Outdoors/zgbs/sporting-goods/"),
    (("fashion", "clothing", "apparel", "beauty", "shoes", "jewelry"),
     "https://www.amazon.com/Best-Sellers-Clothing-Shoes-Jewelry/zgbs/fashion/"),
    (("business", "marketing", "startup", "entrepreneur", "finance", "book"),
     "https://www.amazon.com/Best-Sellers-Books-Business/zgbs/books/173514011"),
]
AMAZON_DEFAULT = "https://www.amazon.com/Best-Sellers/zgbs/"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Reddit — conversation
# ---------------------------------------------------------------------------


def _reddit_search_rss(query: str, sort: str, window: str, limit: int) -> list[Evidence]:
    """Reddit's key-free Atom search.

    TrendScout previously used www.reddit.com/search.json, which answers 403/429 to
    unauthenticated desktop clients often enough to be useless. TrendScope's
    old.reddit.com RSS route is the working replacement and needs no credentials.

    Two query parameters matter more than they look. Without ``t`` a ``top`` sort
    ranks over all time, returning years-old threads the date filter then throws
    away, so the source contributes nothing. Without ``type=link`` the feed also
    carries matching *subreddits*, whose "published" date is the subreddit's
    creation date — 2012 entries showing up as this week's conversation.
    """
    url = (
        f"https://old.reddit.com/search.rss?q={requests.utils.quote(query)}"
        f"&sort={sort}&t={window}&type=link&limit={limit}"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/atom+xml,application/xml,text/xml;q=0.9"}

    response = requests.get(url, headers=headers, timeout=TIMEOUT)
    if response.status_code == 429:
        # Reddit throttles anonymous readers hard but briefly. One backoff is worth
        # it; a second would cost more than this source is worth to the ranking.
        time.sleep(8)
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    items: list[Evidence] = []
    for entry in root.findall(".//atom:entry", ns)[:limit]:
        title = (entry.findtext("atom:title", "", ns) or "").strip()
        if not title:
            continue
        link_el = entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        content = entry.findtext("atom:content", "", ns) or ""

        # RSS has no score/comment fields; Reddit sometimes renders them into the HTML
        # content blob, so they are read back out when present. Often they are not,
        # and those items keep the base strength. TrendScope synthesises a score from
        # post age in that case — deliberately not ported, because an invented
        # engagement number would flow into the family weighting as if it were real.
        score_match = re.search(r"(\d[\d,]*)\s*points?", content, re.I)
        comment_match = re.search(r"(\d[\d,]*)\s*comments?", content, re.I)
        score = int(score_match.group(1).replace(",", "")) if score_match else 0
        comments = int(comment_match.group(1).replace(",", "")) if comment_match else 0

        published = _now()
        updated = entry.findtext("atom:updated", "", ns)
        if updated:
            try:
                published = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass

        activity = max(1, score + 2 * comments)
        items.append(Evidence(
            title=title,
            url=link,
            source="Reddit",
            family="conversation",
            published=published,
            strength=1 + min(4, math.log10(activity + 1)),
        ))
    return items


def fetch_reddit(query: str, days: int, config: dict) -> list[Evidence]:
    window = "week" if days <= 7 else "month" if days <= 31 else "year"
    sort = "new" if days <= 14 else "top"
    return _reddit_search_rss(query, sort, window, limit=40)


# ---------------------------------------------------------------------------
# Google Trends — public attention
# ---------------------------------------------------------------------------


def fetch_google_trends(query: str, days: int, config: dict) -> list[Evidence]:
    """Trending searches for the configured geography.

    The feed is a *global* trending list, not a niche query, so almost all of it is
    irrelevant to any given niche — that is fine and intended. The shared relevance
    filter drops the rest; what survives is a genuine "people are searching for this
    right now" signal that no news feed provides.
    """
    geo = str((config or {}).get("geo") or "US").upper()
    response = requests.get(
        f"https://trends.google.com/trending/rss?geo={geo}",
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    ns = {"ht": "https://trends.google.com/trending/rss"}

    items: list[Evidence] = []
    for entry in root.findall(".//item"):
        keyword = (entry.findtext("title", "") or "").strip()
        if not keyword:
            continue
        traffic = entry.findtext("ht:approx_traffic", "", ns) or ""
        # "20,000+" -> 20000; used only to separate a national story from a blip.
        volume = int(re.sub(r"[^\d]", "", traffic) or 0)
        strength = 1 + min(3.0, math.log10(volume + 1) / 2)
        link = (entry.findtext("link", "") or "").strip()

        # The bare keyword is usually too short to survive the title-length filter,
        # so the related headlines carry it. They are attributed to Google Trends,
        # not to their publishers — the claim being made is about search attention.
        for news in entry.findall(".//ht:news_item", ns)[:3]:
            title = (news.findtext("ht:news_item_title", "", ns) or "").strip()
            if title:
                items.append(Evidence(
                    title=title,
                    url=(news.findtext("ht:news_item_url", "", ns) or link).strip(),
                    source="Google Trends",
                    family="attention",
                    published=_now(),
                    strength=strength,
                ))
        items.append(Evidence(
            title=f"{keyword} trending in search",
            url=link,
            source="Google Trends",
            family="attention",
            published=_now(),
            strength=strength,
        ))
    return items


# ---------------------------------------------------------------------------
# YouTube — public attention
# ---------------------------------------------------------------------------

_RELATIVE_UNITS = {
    "second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24,
    "day": 1, "week": 7, "month": 30, "year": 365,
}


def _parse_relative_age(text: str) -> datetime:
    """"3 weeks ago" -> a timestamp. Falls back to now when unparseable."""
    match = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)", (text or "").lower())
    if not match:
        return _now()
    days = int(match.group(1)) * _RELATIVE_UNITS[match.group(2)]
    return _now() - timedelta(days=days)


def _parse_views(text: str) -> int:
    match = re.search(r"([\d.,]+)\s*([KMB]?)", text or "", re.I)
    if not match:
        return 0
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(number * {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(match.group(2).upper(), 1))


def fetch_youtube(query: str, days: int, config: dict) -> list[Evidence]:
    """YouTube's own search endpoint — the one the web player calls. No API key."""
    geo = str((config or {}).get("geo") or "US").upper()
    response = requests.post(
        "https://www.youtube.com/youtubei/v1/search",
        json={
            "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240601.00.00", "hl": "en", "gl": geo}},
            "query": query,
            "params": "EgIQAQ%3D%3D",  # filter to videos
        },
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    sections = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )

    items: list[Evidence] = []
    for section in sections:
        for entry in section.get("itemSectionRenderer", {}).get("contents", []):
            video = entry.get("videoRenderer") or {}
            video_id = video.get("videoId")
            if not video_id:
                continue
            title = (video.get("title", {}).get("runs") or [{}])[0].get("text", "").strip()
            if not title:
                continue
            views = _parse_views(
                video.get("viewCountText", {}).get("simpleText", "")
                or video.get("shortViewCountText", {}).get("simpleText", "")
            )
            items.append(Evidence(
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                source="YouTube",
                family="attention",
                published=_parse_relative_age(video.get("publishedTimeText", {}).get("simpleText", "")),
                strength=1 + min(3.5, math.log10(views + 1) / 2),
            ))
            if len(items) >= 30:
                return items
    return items


# ---------------------------------------------------------------------------
# TikTok — public attention
# ---------------------------------------------------------------------------


def _tiktok_evidence(hashtag: str, video_count: int) -> Evidence:
    hashtag = hashtag.strip().lstrip("#")
    # camelCase and digit boundaries are the only word separators a hashtag has, and
    # without splitting them every hashtag reads as one unmatched token to the
    # relevance filter.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", hashtag)
    return Evidence(
        title=f"{spaced.lower()} hashtag trending on TikTok",
        url=f"https://www.tiktok.com/tag/{hashtag}",
        source="TikTok",
        family="attention",
        published=_now(),
        strength=1 + min(3.0, math.log10(video_count + 1) / 2),
    )


def _tiktok_scraped() -> list[Evidence]:
    """TrendScope's HTML fallback, for when the JSON endpoint refuses anonymous callers.

    Needs Scrapling and a browser engine, neither of which this app depends on, so an
    install without them raises ImportError and the caller logs a source outage.
    """
    from scrapling.fetchers import DynamicFetcher  # noqa: PLC0415 — optional dependency

    page = DynamicFetcher.fetch(
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/pc/en",
        headless=True,
        network_idle=True,
        timeout=40000,
    )
    seen: set[str] = set()
    items: list[Evidence] = []
    for selector in ("[class*='hashtagName']", "[class*='trend-name']", "[class*='TopicName']", "[class*='hashtag']"):
        for element in page.css(selector):
            text = element.text.strip()
            if len(text) > 2 and text not in seen:
                seen.add(text)
                items.append(_tiktok_evidence(text, 0))
    return items


def fetch_tiktok(query: str, days: int, config: dict) -> list[Evidence]:
    """Trending hashtags from the Creative Center.

    Like Google Trends this is an unfiltered popularity list; the shared relevance
    filter is what makes it niche-specific.

    The JSON endpoint answers HTTP 200 with an in-body error code when it does not
    like an anonymous caller, so the status line cannot be trusted — the body has to
    be checked. As of this writing it returns 40101 "no permission" for unsigned
    requests, which is why the Scrapling fallback exists.
    """
    geo = str((config or {}).get("geo") or "US").upper()
    response = requests.get(
        "https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list",
        params={"page": 1, "limit": 50, "period": 7 if days <= 14 else 30, "country_code": geo, "sort_by": "popular"},
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://ads.tiktok.com/business/creativecenter/inspiration/popular/pc/en",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    code = payload.get("code")

    if code in (0, None):
        entries = payload.get("data", {}).get("list", [])
        items = [
            _tiktok_evidence(entry["hashtag_name"], int(entry.get("video_count") or 0))
            for entry in entries
            if (entry.get("hashtag_name") or "").strip()
        ]
        if items:
            return items
        raise ValueError("Creative Center returned no hashtags")

    try:
        return _tiktok_scraped()
    except ImportError:
        raise ValueError(
            f"Creative Center refused an anonymous request ({code}: {payload.get('msg')}); "
            "the HTML fallback needs Scrapling installed"
        ) from None


# ---------------------------------------------------------------------------
# Amazon — adoption (commercial)
# ---------------------------------------------------------------------------


def fetch_amazon(query: str, days: int, config: dict) -> list[Evidence]:
    """Best Sellers for the closest matching department.

    Requires Scrapling (and a browser engine) because Amazon blocks plain HTTP
    clients. It is not a dependency of this app, so an install without it raises
    ImportError and the caller records that as an ordinary source outage.
    """
    from scrapling.fetchers import StealthyFetcher  # noqa: PLC0415 — optional dependency

    niche = query.lower()
    url = next((u for terms, u in AMAZON_LISTS if any(t in niche for t in terms)), AMAZON_DEFAULT)
    page = StealthyFetcher.fetch(url, headless=True, network_idle=True, auto_match=True)

    items: list[Evidence] = []
    for card in page.css(".zg-grid-general-faceout")[:20]:
        title_els = card.css(
            "._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, .p13n-sc-truncated, "
            "[class*='p13n-sc-truncate'], .a-size-base"
        )
        if not title_els:
            continue
        items.append(Evidence(
            title=title_els[0].text.strip(),
            url=url,
            source="Amazon Best Sellers",
            family="adoption",
            published=_now(),
            strength=1.0,
        ))
    return items


# ---------------------------------------------------------------------------
# Twitter/X — conversation
# ---------------------------------------------------------------------------


def fetch_twitter(query: str, days: int, config: dict) -> list[Evidence]:
    """Search via xactions, using session cookies the user pasted into Settings.

    Off unless both cookies are present. TrendScope ships xactions vendored; here it
    is an optional import for the same reason as Scrapling.
    """
    auth_token = str((config or {}).get("twitter_auth_token") or "").strip()
    ct0 = str((config or {}).get("twitter_ct0") or "").strip()
    if not auth_token or not ct0:
        raise ValueError("Twitter/X needs auth_token and ct0 cookies in Settings")

    from xactions.scraper.scrapers import search_tweets_sync  # noqa: PLC0415 — optional dependency

    tweets = search_tweets_sync(
        cookies=f"auth_token={auth_token}; ct0={ct0}",
        query=f"{query} lang:en",
        limit=30,
        mode="Top",
    )
    items: list[Evidence] = []
    for tweet in tweets:
        text = (tweet.get("text") or "").strip()
        if not text:
            continue
        activity = max(1, (tweet.get("likes") or 0) + 2 * (tweet.get("retweets") or 0) + 2 * (tweet.get("replies") or 0))
        items.append(Evidence(
            title=text[:200],
            url=tweet.get("url", ""),
            source="Twitter/X",
            family="conversation",
            published=_now(),
            strength=1 + min(4, math.log10(activity + 1)),
        ))
    return items


SOCIAL_FETCHERS = {
    "Reddit": fetch_reddit,
    "Google Trends": fetch_google_trends,
    "YouTube": fetch_youtube,
    "TikTok": fetch_tiktok,
    "Amazon Best Sellers": fetch_amazon,
    "Twitter/X": fetch_twitter,
}

# Sources whose evidence is a snapshot of "right now" rather than a dated stream.
# The engine uses this to keep them out of the persistence bonus.
SNAPSHOT_SOURCES = {"Google Trends", "TikTok", "Amazon Best Sellers", "Twitter/X"}
