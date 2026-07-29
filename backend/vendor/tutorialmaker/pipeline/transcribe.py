"""Stage 4: fetch a timestamped transcript via youtube-transcript-api (no download).

youtube-transcript-api scrapes ``www.youtube.com``, which datacenter IPs (like a Space)
often get blocked from — usually as a TLS reset / SSL EOF rather than a clean 4xx. We
retry a few times for transient failures and, when it's clearly a block, point the user at
the ``YT_PROXY`` fallback. The returned snippets already carry ``start``/``duration``
timestamps, which we normalize into ``{start, end, text}`` segments.
"""
from __future__ import annotations

import re
import time

# Strip credentials from any proxy URL that a library exception might echo, so a pasted
# http://user:pass@host proxy never leaks into the UI/logs.
_CRED_RE = re.compile(r"(https?://)[^/@\s]+@")


def _redact(text: str) -> str:
    return _CRED_RE.sub(r"\1", str(text))


class TranscriptError(RuntimeError):
    """Raised when no usable transcript can be fetched (disabled, blocked, none)."""


def _fetch_raw(video_id: str, langs: list[str], proxy: str | None) -> list[dict]:
    from youtube_transcript_api import YouTubeTranscriptApi

    # --- 1.x instance API ---
    api = None
    if proxy:
        try:
            from youtube_transcript_api.proxies import GenericProxyConfig
            api = YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy))
        except Exception:
            api = YouTubeTranscriptApi()
    else:
        api = YouTubeTranscriptApi()

    if hasattr(api, "fetch"):
        fetched = api.fetch(video_id, languages=langs)
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]

    # --- 0.6.x classmethod API ---
    kwargs = {"languages": langs}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    data = YouTubeTranscriptApi.get_transcript(video_id, **kwargs)
    return [{"text": d["text"], "start": d["start"], "duration": d.get("duration", 0)} for d in data]


def _is_transient(exc: Exception) -> bool:
    s = (type(exc).__name__ + " " + str(exc)).lower()
    return any(k in s for k in ("ssl", "eof", "timed out", "timeout", "reset",
                                "connection", "max retries", "temporarily"))


def get_segments(video_id: str, languages=("en", "en-US", "en-GB"),
                 proxy: str | None = None, attempts: int = 3) -> list[dict]:
    """Return timestamped segments ``[{start, end, text}]`` for ``video_id``.

    Retries transient connection/TLS failures; raises TranscriptError otherwise.
    ``proxy`` (the YT_PROXY secret) routes around datacenter-IP blocks.
    """
    langs = list(languages)
    raw, last = None, None
    for attempt in range(attempts):
        try:
            raw = _fetch_raw(video_id, langs, proxy)
            break
        except Exception as exc:
            last = exc
            if _is_transient(exc) and attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise TranscriptError(_hint(exc)) from exc
    if raw is None:
        raise TranscriptError(_hint(last) if last else "Unknown transcript error.")

    segs = []
    for r in raw:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        start = float(r.get("start") or 0.0)
        dur = float(r.get("duration") or 0.0)
        segs.append({"start": start, "end": start + dur, "text": text})
    if not segs:
        raise TranscriptError("The transcript came back empty for this video.")
    return segs


def transcript_text(segs: list[dict]) -> str:
    """Render segments as ``[mm:ss] text`` lines for the LLM and the UI preview."""
    lines = []
    for s in segs:
        m, sec = divmod(int(s["start"]), 60)
        lines.append(f"[{m:02d}:{sec:02d}] {s['text']}")
    return "\n".join(lines)


def _hint(exc: Exception) -> str:
    name = type(exc).__name__
    msg = _redact(str(exc))
    low = (name + " " + msg).lower()
    # Check specific transcript states first (note: "transcripts" contains "ip").
    if "disabled" in low or "transcriptsdisabled" in low:
        return "This video has transcripts/captions disabled — pick another video."
    if "notranscript" in low or "no transcript" in low:
        return "No transcript is available for this video in the requested languages."
    if "unavailable" in low or "videounavailable" in low:
        return "The video is unavailable (private/removed/region-locked)."
    if any(k in low for k in ("ssl", "eof", "reset", "connection", "max retries",
                              "timed out", "blocked", "forbidden", "too many", "429")):
        return ("YouTube blocked the transcript request from this Space's IP "
                "(datacenter IPs are commonly blocked — seen here as a TLS/SSL reset). "
                "Set a residential proxy as the YT_PROXY Space secret and retry. "
                f"[{name}]")
    return f"Could not fetch transcript: {name}: {msg[:200]}"
