"""Video helpers: audio extraction, duration probing, single-frame grabbing.

All heavy lifting is delegated to ffmpeg/ffprobe (already on PATH). ffmpeg's
``-ss`` before ``-i`` is both fast and frame-accurate in modern builds, which we
rely on for precise frame extraction at a given timestamp.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe subprocess fails."""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc


def get_duration(video_path: str | Path) -> float:
    """Return the media duration in seconds (0.0 if it cannot be determined)."""
    try:
        proc = _run(
            [
                config.FFPROBE_BIN,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
        )
        return float(proc.stdout.strip())
    except (FFmpegError, ValueError):
        return 0.0


def extract_audio(video_path: str | Path, out_wav: str | Path) -> Path:
    """Extract a 16 kHz mono WAV (the format faster-whisper expects)."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            config.FFMPEG_BIN,
            "-y",
            "-i", str(video_path),
            "-vn",            # drop video
            "-ac", "1",       # mono
            "-ar", "16000",   # 16 kHz
            "-f", "wav",
            str(out_wav),
        ]
    )
    return out_wav


def extract_frame(video_path: str | Path, timestamp: float, out_png: str | Path) -> Path:
    """Save a single frame at ``timestamp`` seconds as a PNG.

    ``-ss`` is placed before ``-i`` for fast, frame-accurate seeking.
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            config.FFMPEG_BIN,
            "-y",
            "-ss", f"{max(timestamp, 0.0):.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(out_png),
        ]
    )
    return out_png
