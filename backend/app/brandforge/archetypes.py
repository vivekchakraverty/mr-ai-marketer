"""The 12 classic brand archetypes (Mark & Pearson, "The Hero and the Outlaw").

Ported verbatim from the BrandForge Space (src/archetypes.py). The client picks
one as step 1 of the intake — it represents the emotion the brand should evoke
and drives every downstream section.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Archetype:
    id: str
    name: str
    description: str  # one-line emotional description shown on the picker card


ARCHETYPES: list[Archetype] = [
    Archetype("hero", "The Hero", "Proves worth through courage and mastery — evokes determination and triumph."),
    Archetype("outlaw", "The Outlaw", "Breaks the rules to liberate — evokes rebellion and disruption."),
    Archetype("sage", "The Sage", "Seeks truth and shares wisdom — evokes clarity and trusted expertise."),
    Archetype("explorer", "The Explorer", "Finds freedom through discovery — evokes independence and adventure."),
    Archetype("creator", "The Creator", "Crafts things of enduring value — evokes imagination and self-expression."),
    Archetype("ruler", "The Ruler", "Creates order and takes control — evokes authority, prestige, and stability."),
    Archetype("magician", "The Magician", "Makes transformation happen — evokes wonder and the promise of change."),
    Archetype("innocent", "The Innocent", "Keeps life simple and honest — evokes optimism, safety, and purity."),
    Archetype("lover", "The Lover", "Creates intimacy and desire — evokes passion, indulgence, and closeness."),
    Archetype("jester", "The Jester", "Lives in the moment with joy — evokes playfulness and lighthearted fun."),
    Archetype("everyman", "The Everyman", "Belongs and connects as an equal — evokes comfort, honesty, and inclusion."),
    Archetype("caregiver", "The Caregiver", "Protects and cares for others — evokes warmth, reassurance, and service."),
]

ARCHETYPE_IDS: list[str] = [a.id for a in ARCHETYPES]
ARCHETYPES_BY_ID: dict[str, Archetype] = {a.id: a for a in ARCHETYPES}
