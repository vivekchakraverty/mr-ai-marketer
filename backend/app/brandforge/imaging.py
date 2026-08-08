"""Palette parsing + brand-image prompt composition, ported from the
BrandForge Space (src/imagegen.py) minus the fal.ai call. In mr-ai-marketer
images use the user's provisioned Modal worker, with Hugging Face Inference as
the setup-free fallback.

The Visual Direction Brief section is instructed (sections.SECTION_SPECS) to
end with a fenced ```palette block of JSON swatches; extract_palette() parses
it, and the prompt builder / exports reuse it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .archetypes import ARCHETYPES_BY_ID
from .intake import BrandIntake

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_PALETTE_BLOCK_RE = re.compile(r"```palette\s*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class ColorSwatch:
    hex: str
    name: str = ""
    rationale: str = ""


def extract_palette(brief_markdown: str) -> list[ColorSwatch]:
    """Parse the fenced ```palette JSON block out of the Visual Direction
    Brief. Returns [] on any missing/malformed input — never raises, so a
    model that ignores the palette instruction degrades gracefully."""
    if not brief_markdown:
        return []
    m = _PALETTE_BLOCK_RE.search(brief_markdown)
    if not m:
        return []
    try:
        raw = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    swatches = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        hex_code = str(entry.get("hex", "")).strip()
        if not _HEX_RE.match(hex_code):
            continue
        swatches.append(
            ColorSwatch(
                hex=hex_code.upper(),
                name=str(entry.get("name", "")).strip(),
                rationale=str(entry.get("rationale", "")).strip(),
            )
        )
    return swatches


def strip_palette_block(brief_markdown: str) -> str:
    """Remove the raw fenced palette block (machine syntax) from the brief."""
    return _PALETTE_BLOCK_RE.sub("", brief_markdown or "").rstrip()


def palette_markdown(swatches: list[ColorSwatch]) -> str:
    """Readable palette list for client-facing exports."""
    if not swatches:
        return ""
    lines = ["**Suggested palette**", ""]
    for s in swatches:
        line = f"- {s.name} (`{s.hex}`)" if s.name else f"- `{s.hex}`"
        if s.rationale:
            line += f" — {s.rationale}"
        lines.append(line)
    return "\n".join(lines)


ASSET_PROMPT_SPECS: dict[str, str] = {
    "Logo Mark Concept": (
        "A minimal, iconic logo mark concept on a clean solid background, flat vector style, "
        "strong silhouette, no text, no lettering, centered, generous negative space"
    ),
    "Brand Mood Board": (
        "A brand mood board collage: textures, photography style, color story, and material "
        "references arranged in a clean grid, art-directed, cohesive lighting, no text"
    ),
    "Social Media Header": (
        "A wide social media banner design: abstract brand-graphic composition with clear "
        "focal area and calm margins for overlaid text later, no text in the image, 3:1 feel"
    ),
}


def build_image_prompt(
    asset_type: str,
    intake: BrandIntake,
    brief_markdown: str,
    palette: list[ColorSwatch],
) -> str:
    """Pure prompt composition: asset spec + chosen archetype's emotional
    direction + a condensed slice of the Visual Direction Brief + palette."""
    archetype = ARCHETYPES_BY_ID.get(intake.brand_archetype)
    archetype_part = (
        f"Brand archetype: {archetype.name} — {archetype.description}" if archetype else ""
    )
    palette_part = (
        "Color palette: " + ", ".join(s.hex for s in palette) if palette else ""
    )
    brief_excerpt = strip_palette_block(brief_markdown)[:600]

    parts = [
        ASSET_PROMPT_SPECS[asset_type],
        f"For the brand: {intake.brand_name} — {intake.one_liner}",
        archetype_part,
        palette_part,
        f"Visual direction notes: {brief_excerpt}" if brief_excerpt else "",
        "High quality, professional brand design.",
    ]
    return ". ".join(p for p in parts if p)
