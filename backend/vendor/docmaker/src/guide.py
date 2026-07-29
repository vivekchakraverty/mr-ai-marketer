"""Assemble a final guide: match a frame to each step, then caption it.

Takes the LLM's :class:`~src.llm.GuideDraft` (pure text) plus the pool of
extracted :class:`~src.frames.FrameRecord` s and produces a :class:`Guide` where
each step carries the best-matching image and a caption. If a step has a
timestamp but no nearby frame, a fresh frame is pulled from the video so every
step can be illustrated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config, video, vision
from .frames import FrameRecord
from .llm import GuideDraft


@dataclass
class GuideStep:
    heading: str
    text: str
    timestamp: float | None = None
    image_path: str | None = None
    caption: str | None = None


@dataclass
class Guide:
    title: str = "Step-by-Step Guide"
    intro: str = ""
    prerequisites: list[str] = field(default_factory=list)
    steps: list[GuideStep] = field(default_factory=list)


# Relevance weights. Timestamp proximity (transcript/LLM time) is by far the most
# reliable signal for tutorials and is weighted heavily; the BLIP-caption match and
# sharpness only break ties.
_W_PROX, _W_SEM, _W_SHARP = 0.70, 0.18, 0.12
# How far (seconds) a frame may sit from a step's LLM timestamp to be reused.
# Manual frames get a wider window (the user captured them on purpose); scene/auto
# frames must be close, otherwise we extract a fresh frame at the exact step time.
_MANUAL_WINDOW = 20.0
_AUTO_WINDOW = 12.0

_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "or", "in", "on", "at", "for", "with",
    "your", "you", "is", "are", "be", "this", "that", "it", "from", "by", "as",
    "into", "then", "will", "can", "should", "have", "has", "its", "their", "our",
    "up", "out", "off", "over", "we", "i",
}

_SHARPNESS_CACHE: dict[str, float] = {}


def _sharpness(path: str) -> float:
    if path not in _SHARPNESS_CACHE:
        _SHARPNESS_CACHE[path] = vision.frame_score(path)
    return _SHARPNESS_CACHE[path]


def _keywords(text: str | None) -> set[str]:
    return {
        w
        for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def _text_relevance(caption: str | None, step_text: str) -> float:
    """Fraction of the step's keywords that BLIP's caption mentions (0..1).

    This is the BLIP *suggestion* signal: it nudges selection toward a frame
    whose description overlaps the step, without letting BLIP decide alone.
    """
    step_kw = _keywords(step_text)
    cap_kw = _keywords(caption)
    if not step_kw or not cap_kw:
        return 0.0
    return min(len(step_kw & cap_kw) / len(step_kw), 1.0)


def _pick_frame(
    candidates: list[FrameRecord],
    timestamp: float | None,
    step_text: str,
    used: set[str],
    spoken_range: tuple[float, float] | None = None,
) -> FrameRecord | None:
    """Pick the best existing frame for a step, anchored to its LLM timestamp.

    Returns ``None`` when no pool frame sits close enough in time — the caller then
    extracts a fresh frame at the exact step timestamp, which keeps every step's
    image aligned to the narration rather than to an unrelated visual scene change.
    """
    avail = [f for f in candidates if f.path not in used] or candidates
    if not avail:
        return None

    # No LLM timestamp for this step: fall back to caption relevance, then sharpness.
    if timestamp is None:
        return max(avail, key=lambda f: (_text_relevance(f.caption, step_text), _sharpness(f.path)))

    # 1) A manual frame captured near this step wins — it's deliberate user intent.
    manual_near = [
        f for f in avail
        if f.source == "manual" and abs(f.timestamp - timestamp) <= _MANUAL_WINDOW
    ]
    if manual_near:
        return min(manual_near, key=lambda f: abs(f.timestamp - timestamp))

    # 2) Scene/auto frames tightly around the step's LLM time, inside the spoken range.
    near = [f for f in avail if abs(f.timestamp - timestamp) <= _AUTO_WINDOW]
    if spoken_range:
        lo, hi = spoken_range
        in_speech = [f for f in near if lo - 2.0 <= f.timestamp <= hi + 2.0]
        near = in_speech or near
    if not near:
        return None  # -> caller extracts a fresh frame at the exact step time

    sharps = [_sharpness(f.path) for f in near]
    smin, smax = min(sharps), max(sharps)

    def norm_sharp(value: float) -> float:
        return (value - smin) / (smax - smin) if smax > smin else 1.0

    def score(frame: FrameRecord) -> float:
        prox = 1.0 - min(abs(frame.timestamp - timestamp) / _AUTO_WINDOW, 1.0)
        sem = _text_relevance(frame.caption, step_text)
        return _W_PROX * prox + _W_SEM * sem + _W_SHARP * norm_sharp(_sharpness(frame.path))

    return max(near, key=score)


def assemble_guide(
    draft: GuideDraft,
    frames: list[FrameRecord],
    *,
    video_path: str | Path | None = None,
    session_dir: str | Path | None = None,
    do_caption: bool = True,
    token: str | None = None,
    spoken_range: tuple[float, float] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> Guide:
    """Combine a guide draft with frames into a fully illustrated :class:`Guide`.

    When captioning is on, the whole (deduped) frame pool is captioned once so
    BLIP can both *suggest* the most relevant frame per step and supply the
    figure captions. ``spoken_range`` (first/last narration time) keeps selection
    inside the narrated portion of the video.
    """
    frames_sorted = sorted(frames, key=lambda f: f.timestamp)

    # Caption the pool up front (once per frame, context-free to keep the
    # relevance signal unbiased) so captions feed both selection and figures.
    if do_caption and frames_sorted:
        pending = [rec for rec in frames_sorted if rec.caption is None]
        if pending:
            if progress:
                progress(0.05, f"Captioning {len(pending)} frames…")
            # One batched request when the Beam GPU endpoint is configured;
            # caption_batch falls back to per-frame captioning otherwise.
            captions = vision.caption_batch(
                [(rec.path, "") for rec in pending], token=token
            )
            for rec, cap in zip(pending, captions):
                rec.caption = cap or ""

    used: set[str] = set()
    steps: list[GuideStep] = []
    total = max(len(draft.steps), 1)

    for i, sd in enumerate(draft.steps):
        if progress:
            progress(0.5 + 0.5 * (i / total), f"Matching image to step {i + 1}/{total}…")

        step_text = f"{sd.heading} {sd.text}".strip()
        chosen = _pick_frame(frames_sorted, sd.approx_timestamp, step_text, used, spoken_range)

        # No suitable frame nearby — extract one at the step timestamp.
        if (
            chosen is None
            and video_path
            and session_dir
            and sd.approx_timestamp is not None
        ):
            out = Path(session_dir) / "frames" / f"step_{i:03d}_{int(sd.approx_timestamp * 1000):08d}.png"
            try:
                video.extract_frame(video_path, sd.approx_timestamp, out)
                chosen = FrameRecord(path=str(out), timestamp=sd.approx_timestamp, source="auto")
            except Exception:
                chosen = None

        image_path = None
        caption = None
        if chosen is not None:
            used.add(chosen.path)
            image_path = chosen.path
            if chosen.caption is None and do_caption:  # freshly extracted frame
                chosen.caption = (
                    vision.caption_image(chosen.path, token=token, context=step_text) or ""
                )
            caption = chosen.caption or None

        steps.append(
            GuideStep(
                heading=sd.heading,
                text=sd.text,
                timestamp=sd.approx_timestamp if sd.approx_timestamp is not None
                else (chosen.timestamp if chosen else None),
                image_path=image_path,
                caption=caption,
            )
        )

    if progress:
        progress(1.0, "Guide assembled.")

    return Guide(
        title=draft.title,
        intro=draft.intro,
        prerequisites=draft.prerequisites,
        steps=steps,
    )
