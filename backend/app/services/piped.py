"""Video search through Piped, with automatic instance selection.

Piped is a privacy-preserving YouTube frontend whose public instances expose a JSON API.
Searching through one costs no YouTube Data API quota and never touches youtube.com from
this machine, which is why it runs ahead of the scrape/API tiers in vendor/tutorialmaker.

**Instances are chosen automatically, never configured.** The public Piped network has
thinned out badly — of the fifteen instances the project's own documentation lists, a live
probe found one still serving its API (several return 502, most no longer resolve, and at
least one now serves the frontend HTML from its `pipedapi.` host). So a hardcoded instance
would be a guaranteed outage, and even the official `pipedapi.kavin.rocks` is currently
down. Instead:

1. The health-checked list at ``piped-instances.kavin.rocks`` is read first — it carries
   real uptime figures, so candidates are ordered by 24h uptime.
2. The documented instance list is merged in behind it, so a failure of that one endpoint
   doesn't leave us with nothing to try.
3. Each candidate is probed with the actual search being requested, and the first that
   answers with usable JSON wins. A probe is the real query rather than a ping because an
   instance that is up but broken (HTML, empty items, an error body) is exactly the case a
   ping would miss.
4. The winner is cached for the rest of the session, so this costs one round-trip per
   search once warm, and re-probes only when the cached instance stops answering.

Callers get the same dict shape as vendor/tutorialmaker's own search tiers, so the results
flow into enrich_and_filter/sentiment unchanged.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import requests

# Health-checked list with uptime data. The richest source, and the only one that reflects
# what is actually up right now rather than what was up when a doc was last edited.
INSTANCE_LIST_URL = "https://piped-instances.kavin.rocks/"

# Merged in behind the live list purely as a safety net for that endpoint being down.
# Deliberately not ranked: at the time of writing most of these are dead, and the probe
# below is what decides. Kept so the feature degrades to "try harder" rather than "fail".
FALLBACK_INSTANCES = (
    "https://api.piped.private.coffee",
    "https://pipedapi.kavin.rocks",
    "https://pipedapi-libre.kavin.rocks",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.nosebs.ru",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
    "https://api.piped.yt",
    "https://pipedapi.drgns.space",
    "https://pipedapi.owo.si",
    "https://pipedapi.ducks.party",
    "https://piped-api.codespace.cz",
    "https://pipedapi.reallyaweso.me",
    "https://pipedapi.darkness.services",
)

# How long a working instance is trusted before it is re-validated. Short enough that an
# instance going down mid-session self-corrects, long enough that a burst of searches
# doesn't re-probe the network each time.
_CACHE_TTL_SECONDS = 900
# Per-instance probe budget. Generous enough for a cold instance, short enough that
# walking a list of mostly-dead hosts stays bounded.
_PROBE_TIMEOUT = 12
# Ceiling on how many instances one search will try before giving up to the next tier.
_MAX_CANDIDATES = 8

_lock = threading.Lock()
_cached_instance: Optional[str] = None
_cached_at: float = 0.0


class PipedError(RuntimeError):
    """Raised when no Piped instance could answer the search."""


def _discover_instances() -> list[str]:
    """Candidate API base URLs, best-known-uptime first."""
    ordered: list[str] = []
    try:
        resp = requests.get(INSTANCE_LIST_URL, timeout=_PROBE_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, list):
            rows = [r for r in rows if isinstance(r, dict) and r.get("api_url")]
            rows.sort(key=lambda r: float(r.get("uptime_24h") or 0), reverse=True)
            ordered = [str(r["api_url"]).strip().rstrip("/") for r in rows]
    except Exception:  # noqa: BLE001 — the fallback list exists for exactly this
        ordered = []

    for url in FALLBACK_INSTANCES:
        if url not in ordered:
            ordered.append(url)
    return ordered


def _parse_items(payload) -> list[dict]:
    """Normalises a Piped search body into the pipeline's own video dict shape.

    Returns [] for anything that isn't a usable result set — an instance serving HTML, an
    error object, or a stream/playlist-only page all land here, and the caller moves on to
    the next candidate rather than treating "responded" as "works".
    """
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []

    videos: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if "watch?v=" not in url:
            continue  # channels and playlists share the results feed
        video_id = url.split("watch?v=", 1)[1].split("&", 1)[0]
        if len(video_id) != 11:
            continue
        duration = item.get("duration")
        videos.append(
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": (item.get("title") or video_id).strip(),
                "channel": (item.get("uploaderName") or "").strip(),
                "duration_s": int(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
                "is_short": bool(item.get("isShort")),
                "views": item.get("views"),
            }
        )
    return videos


def _search_on(instance: str, topic: str, timeout: int = _PROBE_TIMEOUT) -> list[dict]:
    resp = requests.get(
        f"{instance}/search",
        params={"q": topic, "filter": "videos"},
        timeout=timeout,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    # An instance that has been repurposed to serve its frontend answers 200 with HTML —
    # .json() would raise, which is caught by the caller and treated as "not usable".
    return _parse_items(resp.json())


def _cached() -> Optional[str]:
    with _lock:
        if _cached_instance and time.time() - _cached_at < _CACHE_TTL_SECONDS:
            return _cached_instance
    return None


def _remember(instance: str) -> None:
    global _cached_instance, _cached_at
    with _lock:
        _cached_instance, _cached_at = instance, time.time()


def _forget() -> None:
    global _cached_instance, _cached_at
    with _lock:
        _cached_instance, _cached_at = None, 0.0


def search(topic: str, max_results: int = 5) -> tuple[list[dict], str]:
    """Search Piped for ``topic``; returns ``(videos, instance_used)``.

    Raises PipedError when every candidate failed, which is the signal for the caller to
    fall through to the next search tier rather than fail the request.
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("Please enter a topic to search for.")

    warm = _cached()
    if warm:
        try:
            videos = _search_on(warm, topic)
            if videos:
                return videos[:max_results], warm
        except Exception:  # noqa: BLE001
            pass
        _forget()  # cached instance went bad — fall through to a fresh probe

    failures: list[str] = []
    for instance in _discover_instances()[:_MAX_CANDIDATES]:
        if instance == warm:
            continue  # already tried it above
        try:
            videos = _search_on(instance, topic)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{instance}: {type(exc).__name__}")
            continue
        if not videos:
            failures.append(f"{instance}: no usable results")
            continue
        _remember(instance)
        return videos[:max_results], instance

    raise PipedError(
        "No live Piped instance could answer the search. Tried: " + "; ".join(failures[:6])
    )
