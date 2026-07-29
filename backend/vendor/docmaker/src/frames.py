"""Frame extraction: automatic (scene detection) and manual (browser capture).

A :class:`FrameRecord` is the common unit passed around the app and into the
guide builder. Automatic frames come from PySceneDetect scene midpoints (with a
uniform-sampling fallback for single-scene videos) and are de-duplicated with a
perceptual hash. Manual frames arrive as base64 data URLs from the browser.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image

from . import config, video


@dataclass
class FrameRecord:
    """One extracted frame plus where it came from in the video."""

    path: str
    timestamp: float
    source: str = "auto"  # "auto" | "manual"
    caption: str | None = None

    @property
    def label(self) -> str:
        mm, ss = divmod(int(self.timestamp), 60)
        return f"{self.source} @ {mm:02d}:{ss:02d}"


def detect_scenes(video_path: str | Path) -> list[tuple[float, float]]:
    """Return a list of (start_sec, end_sec) scene spans via PySceneDetect."""
    try:
        from scenedetect import ContentDetector, detect

        scenes = detect(str(video_path), ContentDetector(threshold=config.SCENE_THRESHOLD))
    except Exception:
        # Detection is best-effort; callers fall back to uniform sampling.
        return []
    return [(start.get_seconds(), end.get_seconds()) for start, end in scenes]


def _scene_timestamps(
    video_path: str | Path,
    max_frames: int,
    spoken_intervals: list[tuple[float, float]] | None = None,
) -> list[float]:
    scenes = detect_scenes(video_path)
    if scenes:
        timestamps = [(start + end) / 2.0 for start, end in scenes]
    else:
        # Single static scene (or detection failed): sample uniformly.
        duration = video.get_duration(video_path) or 60.0
        count = min(max_frames, max(3, int(duration // 5)))
        step = duration / (count + 1)
        timestamps = [step * (i + 1) for i in range(count)]

    # Anchor to the narration: keep only frames within the spoken time range.
    # This drops screen-recorder intro/outro and idle screens (no speech there),
    # which is the main source of irrelevant auto-frames.
    if spoken_intervals:
        lo = min(s for s, _ in spoken_intervals)
        hi = max(e for _, e in spoken_intervals)
        pad = 1.5
        gated = [t for t in timestamps if lo - pad <= t <= hi + pad]
        # If gating removed everything, fall back to the segment start times.
        timestamps = gated or [s for s, _ in spoken_intervals]

    timestamps = sorted(timestamps)
    # Keep an evenly spaced subset if we overshoot the cap.
    if len(timestamps) > max_frames:
        last = len(timestamps) - 1
        picks = sorted({round(i * last / (max_frames - 1)) for i in range(max_frames)})
        timestamps = [timestamps[i] for i in picks]
    return timestamps


def _dedup_close(timestamps: list[float], min_gap: float = 1.5) -> list[float]:
    """Collapse timestamps that are closer than ``min_gap`` seconds."""
    out: list[float] = []
    for t in sorted(timestamps):
        if not out or t - out[-1] >= min_gap:
            out.append(t)
    return out


def extract_auto_frames(
    video_path: str | Path,
    session_dir: str | Path,
    max_frames: int = 40,
    spoken_intervals: list[tuple[float, float]] | None = None,
    step_timestamps: list[float] | None = None,
) -> list[FrameRecord]:
    """Extract representative frames, then dedup.

    Priority of anchors:
    1. ``step_timestamps`` (from the LLM step draft) — extract at the exact moments
       the guide refers to, so the pool matches the steps.
    2. ``spoken_intervals`` (from the transcript) — scene frames gated to the
       narrated time range, dropping recorder intro/idle screens.
    3. Otherwise — scene midpoints (or uniform sampling).
    """
    frames_dir = Path(session_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    if step_timestamps:
        timestamps = _dedup_close([t for t in step_timestamps if t is not None and t >= 0])
        if len(timestamps) > max_frames:
            last = len(timestamps) - 1
            picks = sorted({round(i * last / (max_frames - 1)) for i in range(max_frames)})
            timestamps = [timestamps[i] for i in picks]
    else:
        timestamps = _scene_timestamps(video_path, max_frames, spoken_intervals)

    records: list[FrameRecord] = []
    for i, ts in enumerate(timestamps):
        out = frames_dir / f"auto_{i:03d}_{int(ts * 1000):08d}.png"
        try:
            video.extract_frame(video_path, ts, out)
        except Exception:
            continue
        records.append(FrameRecord(path=str(out), timestamp=ts, source="auto"))
    return dedup_records(records)


def dedup_records(
    records: list[FrameRecord], distance: int | None = None
) -> list[FrameRecord]:
    """Drop near-identical frames using perceptual hashing (pHash)."""
    distance = config.DEDUP_HASH_DISTANCE if distance is None else distance
    kept: list[FrameRecord] = []
    hashes: list[imagehash.ImageHash] = []
    for rec in records:
        try:
            with Image.open(rec.path) as im:
                phash = imagehash.phash(im)
        except Exception:
            continue
        if any((phash - kept_hash) <= distance for kept_hash in hashes):
            Path(rec.path).unlink(missing_ok=True)  # remove the redundant file
            continue
        hashes.append(phash)
        kept.append(rec)
    return kept


def save_manual_frame(
    data_url: str, timestamp: float, session_dir: str | Path
) -> FrameRecord | None:
    """Decode a base64 image data URL captured in the browser and save it."""
    if not data_url:
        return None
    frames_dir = Path(session_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
    try:
        raw = base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return None

    ts = float(timestamp or 0.0)
    out = frames_dir / f"manual_{int(ts * 1000):08d}.png"
    out.write_bytes(raw)
    return FrameRecord(path=str(out), timestamp=ts, source="manual")
