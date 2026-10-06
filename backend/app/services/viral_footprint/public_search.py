"""Bounded public YouTube search using the app's existing yt-dlp integration."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL

from .. import ytsearch

MAX_RESULTS = 40
MAX_COMPARISONS = 12
MAX_VIDEO_BYTES = 100 * 1024 * 1024


class PublicSearchUnavailable(RuntimeError):
    pass


def status() -> dict:
    return {"available": True, "platforms": ["YouTube"],
            "detail": "Public YouTube search is built in; no ViralMint server or API key is required. Search and video access depend on YouTube.",
            "limitations": "TikTok, Douyin, Instagram, and Reddit video search are unavailable in this mode."}


def query_terms(search_terms: str, transcript: str) -> str:
    supplied = " ".join(search_terms.split())[:160]
    if supplied:
        return supplied
    words = re.findall(r"[\w'-]+", transcript, re.UNICODE)
    if len(words) >= 3:
        return " ".join(words[:12])
    raise PublicSearchUnavailable("Add a caption or distinctive search terms for this reel. Public platforms cannot search by video fingerprint alone.")


def search_queries(query: str) -> list[str]:
    # Generic topics mostly return films and long videos. Ask for short-form
    # content as well, while retaining the original phrase for exact titles.
    return [f"{query} (short-video filter)", query]


def search(query: str) -> list[dict]:
    found: dict[str, dict] = {}
    errors = []
    for short_videos in (True, False):
        try:
            for row in ytsearch.search(query, max_results=24, short_videos=short_videos):
                found.setdefault(row["video_id"], row)
        except (ytsearch.YtSearchError, ValueError) as exc:
            errors.append(str(exc))
    if not found:
        raise PublicSearchUnavailable(f"YouTube search could not return candidates: {'; '.join(errors)}")
    return list(found.values())[:MAX_RESULTS]


def select_candidates(rows: list[dict], duration: float) -> tuple[list[dict], list[dict]]:
    """Rank plausible short clips by length without excluding shorter edits."""
    maximum_duration = min(600, max(180, duration * 1.5))
    eligible = [row for row in rows if row.get("direct_reference") or not row.get("duration_s")
                or 0 < float(row["duration_s"]) <= maximum_duration]
    eligible.sort(key=lambda row: (
        not row.get("direct_reference", False),
        abs(float(row["duration_s"]) - duration) / duration if row.get("duration_s") else 2,
    ))
    return eligible[:MAX_COMPARISONS], eligible


def reference_id(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password:
        raise ValueError("Enter a public HTTPS YouTube watch, Shorts, or youtu.be link.")
    if host == "youtu.be":
        video_id = parts.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0]
        elif parts.path.startswith(("/shorts/", "/embed/")):
            video_id = parts.path.split("/")[2]
        else:
            video_id = ""
    else:
        video_id = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Enter a public HTTPS YouTube watch, Shorts, or youtu.be link.")
    return video_id


def reference_candidate(url: str) -> dict:
    video_id = reference_id(url)
    info = video_metadata(video_id)
    return {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": info.get("title") or "Linked YouTube video", "channel": info.get("channel"),
            "duration_s": info.get("duration"), "views": info.get("view_count"),
            "direct_reference": True}


def download_video(video_id: str, directory: Path) -> tuple[Path, dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise PublicSearchUnavailable("Search returned an invalid YouTube video ID.")
    directory.mkdir(parents=True, exist_ok=True)

    def limit_progress(data: dict) -> None:
        if int(data.get("downloaded_bytes") or 0) > MAX_VIDEO_BYTES:
            raise PublicSearchUnavailable("Candidate video exceeds the 100 MB limit.")

    options = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "outtmpl": str(directory / "candidate.%(ext)s"),
        "format": "best[height<=480]/best",
        # The app's pinned yt-dlp version defaults to a YouTube client whose
        # media URLs currently return 403; the Android client yields usable
        # public formats without asking the user for a PO token.
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "merge_output_format": "mp4", "max_filesize": MAX_VIDEO_BYTES,
        "socket_timeout": 15, "retries": 1, "fragment_retries": 1,
        "progress_hooks": [limit_progress],
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True) or {}
    except Exception as exc:
        raise PublicSearchUnavailable(f"YouTube video {video_id} could not be downloaded: {type(exc).__name__}") from exc
    files = [path for path in directory.glob("candidate.*") if path.is_file() and path.stat().st_size]
    if not files:
        raise PublicSearchUnavailable(f"YouTube video {video_id} had no usable media file.")
    path = max(files, key=lambda item: item.stat().st_size)
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise PublicSearchUnavailable("Candidate video exceeds the 100 MB limit.")
    return path, info


def video_metadata(video_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return {}
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                        "noplaylist": True, "socket_timeout": 15}) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False) or {}
    except Exception:
        return {}


def transcript(video_id: str) -> str:
    try:
        from vendor.tutorialmaker.pipeline.transcribe import get_segments, transcript_text
        return transcript_text(get_segments(video_id, attempts=1))
    except Exception:
        return ""
