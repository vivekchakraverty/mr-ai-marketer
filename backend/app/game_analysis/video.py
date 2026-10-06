"""Probe and bounded FFmpeg sampling. Raw videos remain local and temporary."""
from __future__ import annotations

import json
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from . import config


class InvalidVideo(ValueError):
    pass


def probe(path: Path) -> dict:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                 "-of", "json", str(path)], capture_output=True, timeout=40, check=True)
        data = json.loads(result.stdout)
        stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
        width, height = int(stream["width"]), int(stream["height"])
        fps = float(Fraction(stream.get("avg_frame_rate") or "0/1"))
        if not 0 < duration <= config.MAX_DURATION or width < 8 or height < 8:
            raise InvalidVideo(f"Upload a decodable video no longer than {config.MAX_DURATION} seconds.")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError,
            json.JSONDecodeError, ZeroDivisionError) as exc:
        if isinstance(exc, InvalidVideo):
            raise
        raise InvalidVideo("FFmpeg could not read this gameplay video.") from exc
    return {"duration": round(duration, 2), "width": width, "height": height,
            "aspect_ratio": round(width / height, 3), "fps": round(fps, 2),
            "codec": stream.get("codec_name"),
            "has_audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
            "file_size": path.stat().st_size}


def chunks(duration: float, sampling_fps: float | None = None) -> list[tuple[float, float]]:
    result = []
    start = 0.0
    fps = sampling_fps or config.SAMPLE_FPS
    # Preserve the requested FPS by shortening a chunk when the frame cap would
    # otherwise turn a 2 FPS selection back into roughly 1 FPS.
    window = min(config.CHUNK_SECONDS, max(config.CHUNK_OVERLAP + 1, config.MAX_FRAMES / fps))
    while start < duration:
        end = min(duration, start + window)
        result.append((round(start, 2), round(end, 2)))
        if end == duration:
            break
        start = end - config.CHUNK_OVERLAP
    return result


def sample(path: Path, start: float, end: float, sampling_fps: float | None = None) -> list[tuple[float, bytes]]:
    fps = min(sampling_fps or config.SAMPLE_FPS, config.MAX_FRAMES / max(end - start, 1))
    with tempfile.TemporaryDirectory(prefix="game-frames-") as folder:
        pattern = str(Path(folder) / "%04d.jpg")
        command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-i", str(path),
                   "-t", str(end - start), "-vf", f"fps={fps:.4f},scale=512:512:force_original_aspect_ratio=decrease",
                   "-frames:v", str(config.MAX_FRAMES), "-q:v", "5", pattern]
        try:
            subprocess.run(command, capture_output=True, timeout=120, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise InvalidVideo("FFmpeg could not decode gameplay frames.") from exc
        files = sorted(Path(folder).glob("*.jpg"))
        if not files:
            raise InvalidVideo("No playable frames were found in the video.")
        return [(round(min(end, start + (i + .5) / fps), 2), frame.read_bytes())
                for i, frame in enumerate(files)]
