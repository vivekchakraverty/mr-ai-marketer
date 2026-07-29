"""Stage 1: resolve the candidate videos for a topic.

Three tiers, tried in order. The point of tier 1 is *where the request comes from*: the
scrape runs inside this process, so it honours the per-user residential proxy (see
``tools/home_proxy_panel.py``) and exits from the user's own IP instead of a shared
datacenter one — which is what actually attracts rate limits and blocks. Tiers 2 and 3
exist so a missing proxy or a parser break never takes the pipeline down.

1. **Direct scrape** of ``youtube.com/results`` — proxy-aware, costs no API quota.
2. **YouTube Data API v3 ``search.list``** — deterministic and immune to IP blocks, but
   100 quota units per call (10,000/day default ≈ 100 searches), so it's the fallback
   rather than the default.
3. **The ``adarshajay/youtube-search`` Space** — the original path, kept as a last resort.

``enrich_and_filter`` then spends one more quota unit on ``videos.list`` to drop Shorts
and caption-less videos before the expensive sentiment and transcript stages.

Note on proxying: the ``googleapis.com`` calls here are deliberately **not** proxied.
They are key-authenticated and quota'd per key, never blocked by IP, so a home proxy
would only add latency and a failure mode. Scraping goes through the proxy; key-auth
traffic goes direct.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

SEARCH_SPACE = "adarshajay/youtube-search"
SEARCH_API = "/youtube_search"

RESULTS_URL = "https://www.youtube.com/results"
DATA_API_SEARCH = "https://www.googleapis.com/youtube/v3/search"
DATA_API_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

# YouTube's "Type: Video" result filter — keeps channels, playlists and Shorts shelves
# out of the result list.
_SP_VIDEOS_ONLY = "EgIQAQ%3D%3D"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Anything this short is a Short (or a trailer) — never a tutorial worth documenting.
SHORTS_MAX_SECONDS = 60
# How many confirmed caption tracks we need before we trust `contentDetails.caption`
# enough to drop the videos that report none. See _filter_captions.
MIN_CAPTIONED = 2

# 11-char YouTube ids as they appear in watch?v=... or youtu.be/... URLs.
_ID_RE = re.compile(r"(?:v=|youtu\.be/|/watch/|/embed/)([A-Za-z0-9_-]{11})")
# Raw ids in the results page JSON, used only when the ytInitialData walk comes up empty.
_BARE_ID_RE = re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')
# Strip credentials from any proxy URL an exception might echo, so a pasted
# http://user:pass@host proxy never leaks into the UI or logs.
_CRED_RE = re.compile(r"(https?://)[^/@\s]+@")

_ISO_DUR_RE = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


class SearchError(RuntimeError):
    """Raised when every search tier failed."""


def _redact(text) -> str:
    return _CRED_RE.sub(r"\1", str(text))


def _proxies(proxy: str | None) -> dict | None:
    return {"http": proxy, "https": proxy} if proxy else None


# --------------------------------------------------------------------------- tier 1
def _initial_data(html: str) -> dict | None:
    """Pull the ``ytInitialData`` JSON blob out of a results page.

    Brace-matched rather than regex'd: the blob contains plenty of nested braces and
    escaped quotes inside string literals.
    """
    for marker in ('var ytInitialData = ', 'window["ytInitialData"] = ', 'ytInitialData = '):
        i = html.find(marker)
        if i == -1:
            continue
        start = html.find("{", i)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for j in range(start, len(html)):
            ch = html[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:j + 1])
                    except json.JSONDecodeError:
                        break  # try the next marker
    return None


def _walk_renderers(node, out: list) -> None:
    """Collect every ``videoRenderer`` dict anywhere in the response tree."""
    if isinstance(node, dict):
        vr = node.get("videoRenderer")
        if isinstance(vr, dict) and vr.get("videoId"):
            out.append(vr)
        for value in node.values():
            _walk_renderers(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_renderers(value, out)


def _renderer_text(field) -> str:
    """YouTube renders text as either ``simpleText`` or a list of ``runs``."""
    if not isinstance(field, dict):
        return ""
    if field.get("simpleText"):
        return str(field["simpleText"]).strip()
    return "".join(r.get("text", "") for r in field.get("runs") or []).strip()


def _hms_to_seconds(text: str | None) -> int | None:
    """Parse a ``lengthText`` like ``12:34`` or ``1:02:03`` into seconds."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        nums = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    total = 0
    for n in nums:
        total = total * 60 + n
    return total


def _parse_results_page(html: str) -> list[dict]:
    data = _initial_data(html)
    videos: list[dict] = []
    seen: set[str] = set()

    if data:
        renderers: list[dict] = []
        _walk_renderers(data, renderers)
        for vr in renderers:
            vid = vr.get("videoId")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            videos.append({
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": _renderer_text(vr.get("title")) or vid,
                "channel": _renderer_text(vr.get("ownerText")),
                "duration_s": _hms_to_seconds(_renderer_text(vr.get("lengthText"))),
            })

    if not videos:
        # Parser drift (YouTube reshuffles this tree periodically): fall back to raw ids
        # in page order. Less precise — may catch a shelf or promo — but still usable.
        for vid in dict.fromkeys(_BARE_ID_RE.findall(html)):
            videos.append({
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": vid,
                "channel": "",
                "duration_s": None,
            })
    return videos


def _scrape_search(topic: str, max_results: int, proxy: str | None,
                   timeout: int = 30) -> list[dict]:
    """Fetch and parse the results page ourselves, through ``proxy`` when set."""
    import requests

    url = (f"{RESULTS_URL}?{urllib.parse.urlencode({'search_query': topic})}"
           f"&sp={_SP_VIDEOS_ONLY}")
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "en-US,en;q=0.9",
        # Skip the EU consent interstitial, which otherwise replaces the results page.
        "Cookie": "CONSENT=YES+1; SOCS=CAI",
    }
    resp = requests.get(url, headers=headers, proxies=_proxies(proxy), timeout=timeout)
    resp.raise_for_status()
    return _parse_results_page(resp.text)[:max_results]


# --------------------------------------------------------------------------- tier 2
def _get_json(url: str, params: dict, timeout: int = 30) -> dict:
    """GET a Data API endpoint directly (never through the proxy — see module docstring)."""
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        if "quota" in body.lower():
            raise SearchError(
                "The YouTube Data API key is out of quota for today (search.list costs "
                "100 units of the 10,000/day default)."
            ) from exc
        raise SearchError(
            f"YouTube Data API rejected the request (HTTP {exc.code}): {body[:200]}"
        ) from exc


def _api_search(topic: str, max_results: int, api_key: str) -> list[dict]:
    data = _get_json(DATA_API_SEARCH, {
        "part": "snippet",
        "q": topic,
        "type": "video",
        "maxResults": str(min(max(max_results, 1), 50)),
        "order": "relevance",
        "key": api_key,
    })
    videos = []
    for item in data.get("items", []):
        vid = (item.get("id") or {}).get("videoId")
        if not vid:
            continue
        sn = item.get("snippet") or {}
        videos.append({
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": (sn.get("title") or vid).strip(),
            "channel": (sn.get("channelTitle") or "").strip(),
            "published_at": sn.get("publishedAt", ""),
            "duration_s": None,  # filled in by enrich_and_filter
        })
    return videos


# --------------------------------------------------------------------------- tier 3
def _parse_titles(text: str, ids: list[str]) -> dict[str, str]:
    """Best-effort: map each id to a nearby title line in the result blob.

    The upstream format is not guaranteed, so this is intentionally forgiving: for each
    id we take the longest non-URL line that appears on or just before the line holding
    the id. Anything we can't resolve falls back to the id itself.
    """
    lines = text.splitlines()
    titles: dict[str, str] = {}
    for vid in ids:
        idx = next((i for i, ln in enumerate(lines) if vid in ln), None)
        if idx is None:
            continue
        window = lines[max(0, idx - 1): idx + 1]
        cands = [ln.strip(" -*\t") for ln in window if "http" not in ln and vid not in ln]
        cands = [c for c in cands if c]
        if cands:
            titles[vid] = max(cands, key=len)
    return titles


def _space_search(topic: str, max_results: int) -> list[dict]:
    """The original path: a third-party Space scrapes YouTube from *its* datacenter IP.

    Kept only as a last resort — its ban state, rate limit and uptime are outside our
    control, and its output is an unstructured text blob we have to regex.
    """
    from gradio_client import Client

    client = Client(SEARCH_SPACE)
    raw = client.predict(topic, api_name=SEARCH_API)
    text = raw if isinstance(raw, str) else str(raw)
    ids = list(dict.fromkeys(_ID_RE.findall(text)))[:max_results]
    titles = _parse_titles(text, ids)
    return [
        {
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": titles.get(vid, vid),
            "channel": "",
            "duration_s": None,
        }
        for vid in ids
    ]


# --------------------------------------------------------------------------- public
def search_top5(topic: str, max_results: int = 5, proxy: str | None = None,
                api_key: str | None = None) -> list[dict]:
    """Return up to ``max_results`` unique videos for ``topic``.

    Each item: ``{"video_id", "url", "title", "channel", "duration_s", "tier"}``.
    ``proxy`` routes the tier-1 scrape through the user's residential IP; ``api_key``
    enables the tier-2 Data API fallback. Raises SearchError only if every tier failed.
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("Please enter a topic to search for.")

    tiers: list[tuple[str, Callable[[], list[dict]]]] = [
        ("direct scrape" + (" via your proxy" if proxy else " (no proxy configured)"),
         lambda: _scrape_search(topic, max_results, proxy)),
    ]
    if api_key:
        tiers.append(("YouTube Data API search.list",
                      lambda: _api_search(topic, max_results, api_key)))
    tiers.append((f"the {SEARCH_SPACE} Space", lambda: _space_search(topic, max_results)))

    failures: list[str] = []
    for label, fetch in tiers:
        try:
            videos = fetch()
        except Exception as exc:  # noqa: BLE001 - any tier may fail; try the next
            failures.append(f"{label}: {type(exc).__name__}: {_redact(exc)[:160]}")
            continue
        if videos:
            for v in videos:
                v["tier"] = label
            return videos[:max_results]
        failures.append(f"{label}: no results")

    raise SearchError(
        f"Could not find videos for '{topic}'. Every search route failed:\n- "
        + "\n- ".join(failures)
    )


def _iso8601_seconds(text: str | None) -> int | None:
    if not text:
        return None
    m = _ISO_DUR_RE.fullmatch(str(text).strip())
    if not m:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _attach_stats(videos: list[dict], api_key: str) -> None:
    """Fill in duration, caption availability and engagement via ``videos.list``.

    One quota unit per batch of 50 ids — negligible next to search.list's 100.
    """
    by_id: dict[str, dict] = {}
    ids = [v["video_id"] for v in videos]
    for i in range(0, len(ids), 50):
        data = _get_json(DATA_API_VIDEOS, {
            "part": "contentDetails,statistics",
            "id": ",".join(ids[i:i + 50]),
            "key": api_key,
        })
        for item in data.get("items", []):
            if item.get("id"):
                by_id[item["id"]] = item

    for v in videos:
        item = by_id.get(v["video_id"])
        if not item:
            continue
        details = item.get("contentDetails") or {}
        stats = item.get("statistics") or {}
        duration = _iso8601_seconds(details.get("duration"))
        if duration is not None:
            v["duration_s"] = duration
        v["has_captions"] = str(details.get("caption", "")).lower() == "true"
        try:
            v["view_count"] = int(stats.get("viewCount") or 0)
            v["like_count"] = int(stats.get("likeCount") or 0)
        except (TypeError, ValueError):
            v["view_count"], v["like_count"] = 0, 0


def _is_short(video: dict) -> bool:
    duration = video.get("duration_s")
    return duration is not None and duration < SHORTS_MAX_SECONDS


def _filter_captions(kept: list[dict]) -> tuple[list[dict], str | None]:
    """Prefer videos with a caption track, without over-trusting the API's flag.

    ``contentDetails.caption`` under-reports auto-generated (ASR) tracks, which
    youtube-transcript-api can still fetch — so a blanket drop on ``caption == false``
    would throw away perfectly usable videos. We only drop when enough candidates
    positively confirm a caption track; below that we just order them first.
    """
    captioned = [v for v in kept if v.get("has_captions") is True]
    if not captioned or len(captioned) == len(kept):
        return kept, None
    if len(captioned) >= MIN_CAPTIONED:
        return captioned, f"Dropped {len(kept) - len(captioned)} video(s) with no caption track."
    ordered = captioned + [v for v in kept if v.get("has_captions") is not True]
    return ordered, ("Too few confirmed caption tracks to filter on — ordered captioned "
                     "videos first instead of dropping the rest.")


def enrich_and_filter(videos: list[dict], api_key: str | None = None) -> tuple[list[dict], list[str]]:
    """Drop weak candidates before the expensive sentiment and transcript stages.

    Returns ``(kept, notes)``. Without ``api_key`` only the durations the scrape already
    supplied are available, so filtering degrades gracefully instead of failing.
    """
    if not videos:
        return videos, []

    notes: list[str] = []
    if api_key:
        try:
            _attach_stats(videos, api_key)
        except Exception as exc:  # noqa: BLE001 - enrichment is an optimisation, not a gate
            notes.append(f"Candidate stats unavailable ({type(exc).__name__}) — skipping filters.")

    kept = [v for v in videos if not _is_short(v)]
    if len(kept) < len(videos):
        notes.append(f"Dropped {len(videos) - len(kept)} Short(s) under {SHORTS_MAX_SECONDS}s.")
    if not kept:
        # Never starve the pipeline on a filter — a bad duration read shouldn't end the run.
        kept = list(videos)
        notes.append("Every candidate looked like a Short — keeping them all.")

    kept, caption_note = _filter_captions(kept)
    if caption_note:
        notes.append(caption_note)
    return kept, notes
