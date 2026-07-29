"""Build a library of a site's existing post titles via a cost-conscious tiered fallback:

  1. RSS feed                       (free, structured)
  2. WordPress REST API             (free, structured)
  3. Sitemap                        (free, structured)
  4. Keyword-based blogroll search  (free, heuristic link discovery + pattern extraction)
  5. LLM-assisted HTML discovery    (paid, billed to the user's HF token — last resort)

Each tier validates the *shape* of its response, not just HTTP status code: some catalog
domains are "soft 404s" that return HTTP 200 with a generic redirect-stub body for every
path, including /feed/, /wp-json/..., and /sitemap_index.xml (confirmed against a real
catalog domain, bordersblog.com). A status-only check would falsely "succeed" at tier 1
with zero real titles and never reach a tier that could actually work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from . import cache, config, llm

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GuestPostSuggester/1.0)"}
RSS_PATHS = ["/feed/", "/feed/rss2/", "/rss/", "/feed"]
SITEMAP_PATHS = ["/sitemap_index.xml", "/sitemap.xml"]
# Some parked/redirect-stub domains serve a structurally-valid but degenerate single-entry
# feed/sitemap (e.g. a one-URL "sitemap.xml" pointing at their own landing page) — this
# passes shape checks but isn't a real post listing. Require a minimum title count before
# treating any tier as a genuine success, so those fall through to the next tier instead.
MIN_TITLES_FOR_SUCCESS = 3
# Link text/href keywords that plausibly point at a blog/article listing page.
LISTING_KEYWORDS = [
    "blog", "blogs", "articles", "article", "news", "insights",
    "resources", "posts", "stories", "magazine", "journal",
]


@dataclass
class TitleLibrary:
    titles: List[str]
    tier_used: str  # "rss" | "wp-json" | "sitemap" | "keyword-listing" | "llm-html" | "none"


def _get(url: str, timeout: Optional[float] = None) -> Optional[requests.Response]:
    try:
        r = requests.get(
            url, timeout=timeout or config.CRAWLER_HTTP_TIMEOUT,
            headers=_HEADERS, allow_redirects=True,
        )
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


# --- Tier 1: RSS ---

def _try_rss(domain: str) -> List[str]:
    for path in RSS_PATHS:
        resp = _get(f"https://{domain}{path}")
        if resp is None:
            continue
        parsed = feedparser.parse(resp.content)
        entries = getattr(parsed, "entries", [])
        if not entries:  # shape check: soft-404 stubs won't parse into real entries
            continue
        titles = [e.get("title", "").strip() for e in entries if e.get("title")]
        if titles:
            return titles[: config.CRAWLER_MAX_TITLES]
    return []


# --- Tier 2: WordPress REST API ---

def _try_wp_json(domain: str) -> List[str]:
    titles: List[str] = []
    for page in range(1, config.CRAWLER_MAX_LISTING_PAGES + 1):
        resp = _get(f"https://{domain}/wp-json/wp/v2/posts?per_page=100&page={page}")
        if resp is None:
            break
        try:
            payload = resp.json()
        except ValueError:
            break  # shape check: not JSON at all -> soft-404 stub
        if not isinstance(payload, list) or not payload:
            break
        for post in payload:
            t = (post.get("title") or {}).get("rendered", "").strip()
            if t:
                titles.append(t)
        if len(payload) < 100 or len(titles) >= config.CRAWLER_MAX_TITLES:
            break
    return titles[: config.CRAWLER_MAX_TITLES]


# --- Tier 3: sitemap ---

def _slug_to_title(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug.title()


def _try_sitemap(domain: str) -> List[str]:
    urls: List[str] = []
    for path in SITEMAP_PATHS:
        resp = _get(f"https://{domain}{path}")
        if resp is None:
            continue
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except Exception:
            continue
        locs = soup.find_all("loc")
        if not locs:  # shape check: not real sitemap XML -> soft-404 stub
            continue
        sub_sitemaps = [
            loc.text for loc in locs
            if "post-sitemap" in loc.text or "posts-sitemap" in loc.text
        ]
        if sub_sitemaps:
            sub_resp = _get(sub_sitemaps[0])
            if sub_resp is not None:
                sub_soup = BeautifulSoup(sub_resp.content, "xml")
                urls = [loc.text for loc in sub_soup.find_all("loc")]
        elif soup.find_all("url"):  # a post-level sitemap directly (has <url><loc> entries)
            urls = [loc.text for loc in locs]
        if urls:
            break
    if not urls:
        return []
    titles = []
    for u in urls[: config.CRAWLER_MAX_TITLES]:
        slug = u.rstrip("/").rsplit("/", 1)[-1]
        if slug and not slug.isdigit():
            titles.append(_slug_to_title(u))
    return titles


# --- Tier 4: keyword-based blogroll/listing-page discovery (free, no LLM) ---

def _strip_scripts(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()
    return soup


def _find_listing_link(domain: str, pages_html: List[str]) -> Optional[str]:
    """Score every link across the given pages against LISTING_KEYWORDS; return the best
    same-domain candidate (or None)."""
    candidates: dict[str, int] = {}
    for html_text, base_url in pages_html:
        soup = BeautifulSoup(html_text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if domain not in parsed.netloc:
                continue  # external link, not this site's own blog
            text = (a.get_text() or "").strip().lower()
            path = parsed.path.lower()
            score = 0
            for kw in LISTING_KEYWORDS:
                if kw == text:
                    score += 5
                elif kw in text:
                    score += 2
                if f"/{kw}" in path:
                    score += 3
            if score > 0:
                # prefer shorter, nav-like paths (e.g. "/blog/" over a specific deep post URL)
                score -= path.count("/")
                candidates[absolute] = max(candidates.get(absolute, 0), score)
    if not candidates:
        return None
    return max(candidates, key=candidates.get)


def _extract_titles_from_listing(html_text: str) -> List[str]:
    """Pattern-based title extraction from a discovered listing page — common WordPress
    theme structures first, then a generic fallback."""
    soup = _strip_scripts(BeautifulSoup(html_text, "html.parser"))
    titles: List[str] = []

    # Common WordPress patterns: <article> headings, or well-known entry/post title classes.
    for article in soup.find_all("article"):
        heading = article.find(["h1", "h2", "h3"])
        if heading:
            link = heading.find("a")
            text = (link or heading).get_text().strip()
            if text:
                titles.append(text)
    if titles:
        return titles[: config.CRAWLER_MAX_TITLES]

    for selector in [".entry-title", ".post-title", ".entry-header"]:
        for el in soup.select(selector):
            link = el.find("a")
            text = (link or el).get_text().strip()
            if text:
                titles.append(text)
        if titles:
            return titles[: config.CRAWLER_MAX_TITLES]

    # Generic fallback: multi-word link text, deduped, excluding obvious nav/footer chrome.
    seen = set()
    for a in soup.find_all("a"):
        text = a.get_text().strip()
        if len(text.split()) >= 4 and text.lower() not in seen:
            seen.add(text.lower())
            titles.append(text)
    return titles[: config.CRAWLER_MAX_TITLES]


def _try_keyword_listing(domain: str, guest_posts_url: str) -> List[str]:
    pages: List[tuple[str, str]] = []
    for url in [f"https://{domain}/", guest_posts_url]:
        resp = _get(url)
        if resp is not None:
            pages.append((resp.text, url))
    if not pages:
        return []

    listing_url = _find_listing_link(domain, pages)
    if not listing_url:
        return []

    resp = _get(listing_url)
    if resp is None:
        return []
    return _extract_titles_from_listing(resp.text)


# --- Tier 5: LLM-assisted (paid, only reached if tiers 1-4 all fail) ---

def _try_llm_html(domain: str, guest_posts_url: str, hf_token: str) -> List[str]:
    html_parts = []
    for url in [f"https://{domain}/", guest_posts_url]:
        resp = _get(url)
        if resp is not None:
            soup = _strip_scripts(BeautifulSoup(resp.text, "html.parser"))
            html_parts.append(str(soup)[:6000])  # bounded snippet per page, caps token cost
    if not html_parts:
        return []

    client = llm.make_client(hf_token)
    system = (
        "You extract blog post titles from raw HTML. Given HTML from a website's homepage "
        "and/or guest-post page, return a newline-separated list of actual article/blog post "
        "titles you can find (from headlines, nav links, or 'recent posts' widgets). If you "
        "instead find a link that clearly leads to the full blog/article listing page (e.g. "
        "'/blog/', '/articles/'), return exactly one line starting with 'LISTING_URL: ' "
        "followed by that absolute URL, and nothing else. If you find neither, return nothing."
    )
    user = "\n\n---PAGE---\n\n".join(html_parts)
    reply = llm.chat(
        client, config.MODEL_SUGGEST, system, user,
        max_tokens=1024, temperature=0.2, fallback_model=config.MODEL_SUGGEST_FALLBACK,
    )

    if reply.startswith("LISTING_URL:"):
        listing_url = reply.split(":", 1)[1].strip()
        resp = _get(listing_url)
        if resp is None:
            return []
        snippet = str(_strip_scripts(BeautifulSoup(resp.text, "html.parser")))[:6000]
        reply2 = llm.chat(
            client, config.MODEL_SUGGEST,
            "Extract a newline-separated list of blog post titles from this HTML. "
            "Return only titles, one per line, nothing else.",
            snippet, max_tokens=1024, temperature=0.2,
            fallback_model=config.MODEL_SUGGEST_FALLBACK,
        )
        return [t.strip("-• \t") for t in reply2.splitlines() if t.strip()][
            : config.CRAWLER_MAX_TITLES
        ]

    return [
        t.strip("-• \t") for t in reply.splitlines()
        if t.strip() and not t.startswith("LISTING_URL")
    ][: config.CRAWLER_MAX_TITLES]


# --- orchestration ---

def crawl_title_library(domain: str, guest_posts_url: str, hf_token: Optional[str]) -> TitleLibrary:
    tiers = [
        (lambda: _try_rss(domain), "rss"),
        (lambda: _try_wp_json(domain), "wp-json"),
        (lambda: _try_sitemap(domain), "sitemap"),
        (lambda: _try_keyword_listing(domain, guest_posts_url), "keyword-listing"),
    ]
    for fn, tier_name in tiers:
        titles = fn()
        # Require a minimum count: some parked/redirect domains serve a structurally-valid
        # but degenerate single-entry feed/sitemap that would otherwise false-succeed here.
        if len(titles) >= MIN_TITLES_FOR_SUCCESS:
            return TitleLibrary(titles=titles, tier_used=tier_name)

    if hf_token:
        titles = _try_llm_html(domain, guest_posts_url, hf_token)
        if len(titles) >= MIN_TITLES_FOR_SUCCESS:
            return TitleLibrary(titles=titles, tier_used="llm-html")

    return TitleLibrary(titles=[], tier_used="none")


def get_title_library(site, hf_token: Optional[str]) -> TitleLibrary:
    """Cached wrapper — crawling doesn't depend on the search topic, so results are
    reusable across every user/topic that analyzes the same domain."""
    key = cache.title_library_key(site.domain)
    cached = cache.get(key, "library")
    if cached:
        return TitleLibrary(titles=cached["titles"], tier_used=cached["tier_used"])

    lib = crawl_title_library(site.domain, site.guest_posts_url, hf_token)
    cache.put(key, "library", {
        "titles": lib.titles, "tier_used": lib.tier_used,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
    })
    return lib
