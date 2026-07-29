"""Local speech-to-text via faster-whisper (CTranslate2).

The model is loaded lazily and cached. On a CUDA box we prefer
``int8_float16``; if CUDA is unavailable or its libraries are missing we fall
back to CPU ``int8`` automatically, so transcription always works.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config

# Cached (model, (device, compute_type)).
_MODEL = None
_MODEL_INFO: tuple[str, str] | None = None
# Set once a CUDA runtime failure is seen, to skip the GPU on later calls.
_FORCE_CPU = False


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str = ""
    device: str = ""

    @property
    def text(self) -> str:
        return " ".join(seg.text.strip() for seg in self.segments).strip()

    def to_timestamped_text(self) -> str:
        lines = []
        for seg in self.segments:
            mm, ss = divmod(int(seg.start), 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {seg.text.strip()}")
        return "\n".join(lines)


def _candidate_configs() -> Iterator[tuple[str, str]]:
    """Yield (device, compute_type) attempts in priority order."""
    override = config.WHISPER_COMPUTE_TYPE
    device = config.WHISPER_DEVICE
    if device == "cpu" or _FORCE_CPU:
        yield ("cpu", override or "int8")
        return
    # "cuda" or "auto": try GPU first, then always fall back to CPU int8.
    yield ("cuda", override or "int8_float16")
    yield ("cpu", "int8")


def _is_cuda_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(tok in msg for tok in ("cublas", "cudnn", "cuda", ".dll", "gpu"))


def get_model():
    """Load (once) and return the faster-whisper model with its resolved config."""
    global _MODEL, _MODEL_INFO
    if _MODEL is not None:
        return _MODEL, _MODEL_INFO

    from faster_whisper import WhisperModel

    errors: list[str] = []
    for device, compute_type in _candidate_configs():
        try:
            _MODEL = WhisperModel(
                config.WHISPER_MODEL, device=device, compute_type=compute_type
            )
            _MODEL_INFO = (device, compute_type)
            return _MODEL, _MODEL_INFO
        except Exception as exc:  # CUDA libs missing, OOM, etc.
            errors.append(f"  {device}/{compute_type}: {exc}")

    raise RuntimeError(
        "Could not load the Whisper model. Attempts:\n" + "\n".join(errors)
    )


def _run_transcribe(
    media_path: str | Path, progress: Callable[[float, str], None] | None
) -> Transcript:
    model, info = get_model()
    device = info[0] if info else ""

    segment_iter, meta = model.transcribe(str(media_path), vad_filter=True, beam_size=5)
    total = float(getattr(meta, "duration", 0.0)) or 0.0

    # The CUDA libraries are loaded lazily on first encode, so a missing-cuBLAS
    # error surfaces while consuming this generator — not at model construction.
    segments: list[TranscriptSegment] = []
    for seg in segment_iter:
        segments.append(
            TranscriptSegment(start=float(seg.start), end=float(seg.end), text=seg.text)
        )
        if progress and total:
            frac = min(seg.end / total, 1.0)
            progress(frac, f"Transcribing… {int(frac * 100)}%")

    return Transcript(
        segments=segments,
        language=getattr(meta, "language", "") or "",
        device=device,
    )


def transcribe(
    media_path: str | Path,
    *,
    progress: Callable[[float, str], None] | None = None,
) -> Transcript:
    """Transcribe an audio/video file into timestamped segments.

    Falls back from GPU to CPU automatically if a CUDA runtime error (e.g.
    missing cuBLAS/cuDNN) occurs during inference.
    """
    global _MODEL, _MODEL_INFO, _FORCE_CPU
    try:
        return _run_transcribe(media_path, progress)
    except RuntimeError as exc:
        on_gpu = bool(_MODEL_INFO and _MODEL_INFO[0] == "cuda")
        if not (on_gpu and _is_cuda_error(exc)):
            raise
        # Drop the GPU model and retry once on CPU.
        _MODEL, _MODEL_INFO, _FORCE_CPU = None, None, True
        if progress:
            progress(0.0, "GPU unavailable — falling back to CPU…")
        return _run_transcribe(media_path, progress)
