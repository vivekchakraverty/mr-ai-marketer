"""Small, dependency-free FFmpeg fingerprints for short videos."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

FRAME_POSITIONS = (0.08, 0.27, 0.5, 0.73, 0.92)


class MediaError(ValueError):
    pass


def _run(args: list[str], timeout: int = 45) -> bytes:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"FFmpeg could not process this video: {exc}") from exc
    if result.returncode:
        raise MediaError("The video could not be decoded by FFmpeg.")
    return result.stdout


def probe(path: Path) -> dict:
    raw = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    try:
        data = json.loads(raw)
        stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
        width, height = int(stream["width"]), int(stream["height"])
        if not 0 < duration <= 600 or width < 8 or height < 8:
            raise ValueError("invalid media dimensions or duration")
        rate = stream.get("avg_frame_rate") or "0/1"
        numerator, denominator = (int(x) for x in rate.split("/"))
        fps = numerator / denominator if denominator else 0
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise MediaError("Upload a decodable video of at most 10 minutes.") from exc
    return {
        "duration": round(duration, 3), "width": width, "height": height,
        "fps": round(fps, 3), "codec": stream.get("codec_name", ""),
        "frame_count": int(stream.get("nb_frames") or 0) or round(duration * fps),
        "file_size": path.stat().st_size,
    }


def _frame_signature(pixels: bytes) -> dict:
    if len(pixels) != 72:
        raise MediaError("Could not extract representative video frames.")
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | (pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    # Keep low-resolution luminance too: a flat black and a flat white frame
    # have identical dHashes but must not be treated as the same content.
    luma = bytes(pixels[y * 9 + x] for y in range(8) for x in range(8))
    return {"hash": f"{bits:016x}", "luma": luma.hex()}


def _frame_hash(path: Path, at: float, crop: bool) -> dict:
    filters = "crop=iw*0.78:ih*0.78,scale=9:8,format=gray" if crop else "scale=9:8,format=gray"
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", filters, "-f", "rawvideo", "-pix_fmt", "gray", "-"], timeout=40)
    return _frame_signature(raw)


def fingerprint(path: Path, metadata: dict | None = None, transcript: str = "") -> dict:
    info = metadata or probe(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    frames = []
    for position in FRAME_POSITIONS:
        timestamp = min(info["duration"] * position, max(0, info["duration"] - 0.05))
        frames.append({"full": _frame_hash(path, timestamp, False),
                       "center": _frame_hash(path, timestamp, True)})
    return {"exact_file_hash": digest.hexdigest(), "frames": frames,
            "duration": info["duration"], "aspect_ratio": info["width"] / info["height"],
            "transcript": " ".join(transcript.lower().split())}


def hash_similarity(left: str, right: str) -> float:
    return 1 - ((int(left, 16) ^ int(right, 16)).bit_count() / 64)


def frame_similarity(left: dict, right: dict) -> float:
    a, b = bytes.fromhex(left["luma"]), bytes.fromhex(right["luma"])
    # Featureless frames carry almost no identity information; two solid-color
    # title cards must not certify a near duplicate on their own.
    def variance(values: bytes) -> float:
        mean = sum(values) / len(values)
        return sum((value - mean) ** 2 for value in values) / len(values)
    if variance(a) < 9 and variance(b) < 9:
        return 0.5
    luminance = max(0, 1 - (sum((x - y) ** 2 for x, y in zip(a, b)) / 64) ** .5 / 128)
    return .35 * hash_similarity(left["hash"], right["hash"]) + .65 * luminance


def visual_similarity(left: dict, right: dict) -> float:
    a, b = left.get("frames", []), right.get("frames", [])
    if not a or not b:
        return 0.0
    # Match nearby positions to tolerate a short intro/outro or a slight speed change.
    scores = []
    for i, frame in enumerate(a):
        nearby = b[max(0, i - 1):min(len(b), i + 2)]
        scores.append(max(frame_similarity(frame[variant], candidate[other])
                          for candidate in nearby for variant in ("full", "center")
                          for other in ("full", "center")))
    return sum(scores) / len(scores)
