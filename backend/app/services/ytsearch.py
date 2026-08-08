"""Video search through yt-dlp's own search, with no third-party instance in the way.

This is the pipeline's first search tier. yt-dlp is already a dependency (the screenshot
stage downloads with it), and its ``ytsearchN:`` pseudo-extractor resolves a query against
YouTube directly — so unlike the Piped and Space tiers there is no instance that has to be
alive, and unlike the Data API tier it costs no quota.

It also exits from **this machine's** connection, which is the same egress the download
stage already uses. That is deliberate: a shared datacenter IP is what actually collects
rate limits and blocks, and having search and download come from the same residential
address is more consistent than routing one through a stranger's server.

``extract_flat`` keeps this to a single request: the search page carries id, title,
duration, channel and view count for every hit, which is everything the downstream filters
need. Resolving each video individually would be ~5x the work for metadata we already have.
"""
from __future__ import annotations

from typing import Optional

from yt_dlp import YoutubeDL

# Live and upcoming streams are never a tutorial worth documenting, and a livestream has no
# stable duration or transcript to work from.
_SKIP_LIVE_STATUS = {"is_live", "is_upcoming", "post_live"}

_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,  # one request for the whole result page
    "skip_download": True,
    "socket_timeout": 20,
    "noplaylist": True,
}


class YtSearchError(RuntimeError):
    """Raised when the search returned nothing usable, so the caller can fall through."""


def _entry_to_video(entry: dict) -> Optional[dict]:
    video_id = entry.get("id") or ""
    if len(video_id) != 11:
        return None  # channels/playlists that share the results feed
    if (entry.get("live_status") or "") in _SKIP_LIVE_STATUS:
        return None

    duration = entry.get("duration")
    duration_s = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None
    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": (entry.get("title") or video_id).strip(),
        "channel": (entry.get("channel") or entry.get("uploader") or "").strip(),
        "duration_s": duration_s,
        # Mirrors vendor/tutorialmaker's own SHORTS_MAX_SECONDS rule so enrich_and_filter
        # sees the same signal it would from any other tier.
        "is_short": duration_s is not None and duration_s <= 60,
        "views": entry.get("view_count"),
    }


def search(topic: str, max_results: int = 5) -> list[dict]:
    """Search YouTube via yt-dlp for ``topic``.

    Over-fetches slightly so that dropping livestreams and non-video hits still leaves
    ``max_results`` candidates. Raises YtSearchError when nothing usable came back.
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("Please enter a topic to search for.")

    query = f"ytsearch{max(max_results * 2, max_results)}:{topic}"
    try:
        with YoutubeDL(_OPTS) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as err:  # noqa: BLE001 — any failure means "try the next tier"
        raise YtSearchError(f"yt-dlp search failed: {type(err).__name__}: {err}") from err

    entries = (info or {}).get("entries") or []
    videos = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        video = _entry_to_video(entry)
        if video and video["video_id"] not in seen:
            seen.add(video["video_id"])
            videos.append(video)

    if not videos:
        raise YtSearchError("yt-dlp search returned no usable videos.")
    return videos[:max_results]
