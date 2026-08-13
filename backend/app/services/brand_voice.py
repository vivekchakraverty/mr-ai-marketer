"""Reuse a finished Brand Studio document as writing instructions for the other tools.

Brand Studio already produces exactly the right artefact for this. Every assemble writes a
`voice-card.md` next to the document — a compact brand brief (tone dimensions, voice traits,
messaging pillars, guardrails, never-sound-like) whose docstring says it is "meant to be
dropped directly into another LLM's system prompt". This module finds those cards and folds
one into a tool's prompt.

It is folded into the tool's own free-text field rather than a system prompt, because none
of the generators expose one. The Blog Writer Space takes a fixed
`(topic, primary, secondary, brief, …)` signature; the Email Writer Space takes a single
`instruction`; Social and Mastodon go through vendor/socialpost, which the repo keeps
unmodified on purpose. The brief/instruction/user_input field is what each of them puts
into its prompt, so that is where the brand context has to go for the model to see it —
without editing three Spaces and a vendored package.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .. import db

VOICE_CARD_FILENAME = "voice-card.md"


def _card_path(item: dict) -> Optional[Path]:
    """Where the voice card for a Library item lives, if it has one.

    assemble() writes brand-document.docx and voice-card.md into the same per-run
    directory, and the Library row records the docx as its output_path — so the card is
    its sibling. Runs from before voice cards were written simply have no file, which is
    why every caller checks existence rather than assuming.
    """
    output_path = (item.get("output_path") or "").strip()
    if not output_path:
        return None
    card = Path(output_path).parent / VOICE_CARD_FILENAME
    return card if card.is_file() else None


def list_voices(limit: int = 40) -> list[dict]:
    """Saved brand documents that can be used as a voice, newest first."""
    voices = []
    for item in db.list_items(limit=200):
        if item.get("tool") != "Brand":
            continue
        if _card_path(item) is None:
            continue
        voices.append(
            {
                "id": item["id"],
                "title": item.get("title") or "Brand document",
                "createdAt": item.get("created_at") or "",
            }
        )
        if len(voices) >= limit:
            break
    return voices


def load_voice(voice_id: str, *, compact: bool = False) -> str:
    """The voice card text for a Library item id, or "" when there isn't one.

    Returning "" rather than raising is deliberate: a stale id in a saved form should
    generate an ordinary post, not fail the request. The tools treat brand voice as an
    enhancement, and an enhancement that can break generation is a bad trade.
    """
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return ""
    try:
        item = db.get_item(voice_id)
    except sqlite3.Error:
        # The lookup itself failing is the same situation as the voice being missing,
        # and the promise above is about the *caller*: a locked, corrupt or not-yet-
        # initialised library should cost the post its brand voice, not the post.
        return ""
    if not item or item.get("tool") != "Brand":
        return ""
    card = _card_path(item)
    if card is None:
        return ""
    try:
        text = card.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return _shrink(text, max_lines=8, keep_pillars=False) if compact else _shrink(text)


# The five headings `to_voice_system_prompt` emits. They are the only reliable structure in
# the file: each one is followed by a whole generated section that carries its own `#` and
# `##` headings, so anything that just scans for "## " sees the *contents* as sections and
# gets the boundaries wrong.
_MARKERS = (
    "## Tone dimensions",
    "## Voice traits",
    "## Messaging pillars",
    "## Guardrails",
    "## Never sound like",
)


def _split_sections(card: str) -> list[tuple[str, list[str]]]:
    """[(heading, body lines)], with the preamble under an empty heading."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in card.splitlines():
        if any(line.startswith(m) for m in _MARKERS):
            sections.append((line, []))
        else:
            sections[-1][1].append(line)
    return sections


def _shrink(card: str, *, max_lines: int = 30, keep_pillars: bool = True) -> str:
    """Bound the card so it informs the prompt instead of burying it.

    The docstring on `to_voice_system_prompt` advertises ~500 tokens. Measured against real
    documents it is nothing of the sort — the cards on this machine run to ~3,200 tokens,
    because each heading is followed by a whole generated section verbatim, and Guardrails
    alone came to 312 lines. Left whole it would dominate any prompt it was folded into and,
    for a 300-character post, outweigh both the author's instruction and the exemplars the
    social generator ranks on.

    So every section is capped rather than trusted. Truncation is by line and from the top,
    which suits these sections: they lead with the traits and the banned words and trail off
    into elaboration. Short-form callers additionally drop the messaging pillars, which are
    a content plan rather than a voice.
    """
    out: list[str] = []
    for heading, body in _split_sections(card):
        if not keep_pillars and heading.startswith("## Messaging pillars"):
            continue
        if heading:
            out.append(heading)
        kept = [l for l in body if l.strip()][:max_lines]
        out.extend(kept)
        if len([l for l in body if l.strip()]) > max_lines:
            out.append("…")
        out.append("")
    return "\n".join(out).strip()


def apply_voice(field: str, voice_id: str, *, compact: bool = False) -> str:
    """Fold the chosen voice card into a tool's free-text field.

    The card goes first and the author's own words last, so that the specific request is
    the most recent thing the model reads and the brand is standing context rather than
    the instruction itself. The delimiters matter for the same reason: without them a
    small model treats "Formal <-> Casual: 5" as something to write about.
    """
    card = load_voice(voice_id, compact=compact)
    if not card:
        return field

    return (
        "[BRAND VOICE — write in this brand's voice. This is context, not the topic.]\n"
        f"{card}\n"
        "[END BRAND VOICE]\n\n"
        f"{field}".strip()
    )
