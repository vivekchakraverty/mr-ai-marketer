"""Transcript cleanup + step-structuring via the HuggingFace Inference API.

The LLM turns a rough, timestamped transcript into a structured guide draft
(title, intro, prerequisites, ordered steps). Long transcripts are processed
map-reduce style so prompts stay within the model's context window. All model
ids are config-driven and responses are parsed defensively, because free-tier
model availability and exact output formatting both vary.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config
from .transcribe import Transcript

_SYSTEM = (
    "You are a meticulous technical writer. You convert rough spoken transcripts "
    "from how-to/tutorial videos into clear, accurate, step-by-step instructions. "
    "You never invent actions that are not in the transcript."
)


@dataclass
class StepDraft:
    heading: str
    text: str
    approx_timestamp: float | None = None


@dataclass
class GuideDraft:
    title: str = "Step-by-Step Guide"
    intro: str = ""
    prerequisites: list[str] = field(default_factory=list)
    steps: list[StepDraft] = field(default_factory=list)


# --- Inference client --------------------------------------------------------
def get_client(token: str | None):
    """Build an InferenceClient for the given user-supplied token.

    Creating a client is cheap (no network until a call), so we don't cache —
    this lets the token change at runtime (it comes from the UI field).
    """
    from huggingface_hub import InferenceClient

    kwargs: dict[str, Any] = {"model": config.LLM_MODEL}
    if token:
        kwargs["token"] = token
    if config.LLM_PROVIDER:
        kwargs["provider"] = config.LLM_PROVIDER
    return InferenceClient(**kwargs)


def _chat(client, user_prompt: str, *, max_tokens: int | None = None) -> str:
    try:
        resp = client.chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens or config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
        )
    except Exception as exc:
        raise RuntimeError(
            f"HuggingFace LLM call failed for model '{config.LLM_MODEL}'. "
            f"Check the model is available on your plan or set DOCUMAKER_LLM_MODEL "
            f"to another instruct model.\nDetails: {exc}"
        ) from exc
    return resp.choices[0].message.content or ""


# --- JSON / time parsing -----------------------------------------------------
def _extract_json(text: str) -> Any:
    """Best-effort extraction of a JSON object/array from an LLM response."""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        i, j = text.find(open_ch), text.rfind(close_ch)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except Exception:
                continue
    return None


def _parse_time(value: Any) -> float | None:
    """Parse 'ss', 'mm:ss', or 'hh:mm:ss' (or a number) into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


# --- Prompt builders ---------------------------------------------------------
_JSON_FULL = (
    '{"title": "...", "intro": "...", "prerequisites": ["..."], '
    '"steps": [{"heading": "short title", "text": "what to do", "approx_time": "mm:ss"}]}'
)
_JSON_STEPS = '{"steps": [{"heading": "short title", "text": "what to do", "approx_time": "mm:ss"}]}'


def _full_prompt(timestamped_text: str) -> str:
    return (
        "Convert this timestamped tutorial transcript into a clean step-by-step guide.\n"
        "- Fix obvious speech-to-text errors; remove filler words and repetition.\n"
        "- Write each step as a clear, imperative instruction.\n"
        "- Add a short descriptive title, a 1-2 sentence introduction, and any "
        "prerequisites that are implied (empty list if none).\n"
        "- For each step set \"approx_time\" as \"mm:ss\" from the nearest [mm:ss] marker.\n"
        f"Respond with ONLY JSON in this exact shape:\n{_JSON_FULL}\n\n"
        f"Transcript:\n{timestamped_text}"
    )


def _chunk_prompt(timestamped_text: str) -> str:
    return (
        "From this timestamped transcript excerpt of a tutorial video, extract the "
        "concrete actions as an ordered list of steps.\n"
        "- Fix speech-to-text errors; remove filler and repetition.\n"
        "- Write each step as a clear, imperative instruction.\n"
        "- Set \"approx_time\" as \"mm:ss\" from the nearest [mm:ss] marker.\n"
        f"Respond with ONLY JSON in this exact shape:\n{_JSON_STEPS}\n\n"
        f"Transcript:\n{timestamped_text}"
    )


def _reduce_prompt(steps_json: str) -> str:
    return (
        "You are assembling the final step-by-step guide from steps extracted across "
        "several transcript chunks.\n"
        "- Merge near-duplicates and keep a logical order.\n"
        "- Keep every distinct action; do not invent new steps.\n"
        "- Add a short descriptive title, a 1-2 sentence introduction, and prerequisites "
        "if implied (empty list if none). Preserve each step's \"approx_time\".\n"
        f"Respond with ONLY JSON in this exact shape:\n{_JSON_FULL}\n\n"
        f"Extracted steps:\n{steps_json}"
    )


# --- Chunking ----------------------------------------------------------------
def _timestamped_lines(transcript: Transcript) -> list[str]:
    lines = []
    for seg in transcript.segments:
        mm, ss = divmod(int(seg.start), 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {seg.text.strip()}")
    return lines


def _chunk_transcript(transcript: Transcript, max_chars: int) -> list[str]:
    lines = _timestamped_lines(transcript)
    if not lines:
        return [transcript.text] if transcript.text else []

    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        if current and length + len(line) > max_chars:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


# --- Result parsing ----------------------------------------------------------
def _parse_steps(raw_steps: Any) -> list[StepDraft]:
    steps: list[StepDraft] = []
    if not isinstance(raw_steps, list):
        return steps
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or item.get("title") or "").strip()
        text = str(item.get("text") or item.get("instruction") or "").strip()
        if not (heading or text):
            continue
        ts = _parse_time(item.get("approx_time", item.get("approx_timestamp")))
        steps.append(StepDraft(heading=heading or text[:60], text=text, approx_timestamp=ts))
    return steps


def _parse_guide(data: Any) -> GuideDraft:
    if not isinstance(data, dict):
        return GuideDraft()
    prereqs = data.get("prerequisites") or []
    if not isinstance(prereqs, list):
        prereqs = [str(prereqs)]
    return GuideDraft(
        title=str(data.get("title") or "Step-by-Step Guide").strip(),
        intro=str(data.get("intro") or "").strip(),
        prerequisites=[str(p).strip() for p in prereqs if str(p).strip()],
        steps=_parse_steps(data.get("steps")),
    )


# --- Public entry point ------------------------------------------------------
def build_guide_draft(
    transcript: Transcript,
    *,
    token: str | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> GuideDraft:
    """Turn a transcript into a structured :class:`GuideDraft` via the LLM.

    ``token`` is the user's HuggingFace token (supplied in the UI).
    """
    chunks = _chunk_transcript(transcript, config.LLM_CHUNK_CHARS)
    if not chunks:
        return GuideDraft()

    client = get_client(token)

    if len(chunks) == 1:
        if progress:
            progress(0.1, "Writing the guide…")
        draft = _parse_guide(_extract_json(_chat(client, _full_prompt(chunks[0]))))
        if progress:
            progress(1.0, "Guide drafted.")
        return draft

    # Map: extract steps per chunk.
    all_steps: list[dict] = []
    for i, chunk in enumerate(chunks):
        if progress:
            progress(i / (len(chunks) + 1), f"Structuring part {i + 1}/{len(chunks)}…")
        data = _extract_json(_chat(client, _chunk_prompt(chunk)))
        for step in _parse_steps((data or {}).get("steps") if isinstance(data, dict) else data):
            mm, ss = divmod(int(step.approx_timestamp or 0), 60)
            all_steps.append(
                {"heading": step.heading, "text": step.text, "approx_time": f"{mm:02d}:{ss:02d}"}
            )

    # Reduce: merge into a titled guide.
    if progress:
        progress(len(chunks) / (len(chunks) + 1), "Assembling the final guide…")
    reduced = _extract_json(_chat(client, _reduce_prompt(json.dumps({"steps": all_steps}))))
    draft = _parse_guide(reduced)
    if not draft.steps:  # reduce failed — fall back to the mapped steps
        draft = _parse_guide({"steps": all_steps})
    if progress:
        progress(1.0, "Guide drafted.")
    return draft
