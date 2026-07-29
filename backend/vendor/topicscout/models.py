"""Shared data model.

Lives in its own module so the source collectors (sources.py, social.py) and the
ranking engine can both import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# The five measured evidence families from the TrendScout foundation model. Social
# sources map onto these rather than adding a sixth family: a Reddit thread and a
# Hacker News thread are the same *kind* of claim about the world, and letting each
# new platform mint its own family would inflate corroboration for free.
FAMILY_LABELS = {
    "news": "News momentum",
    "attention": "Public attention",
    "conversation": "Conversation",
    "adoption": "Adoption & institutional",
    "research": "Research momentum",
}


@dataclass
class Evidence:
    title: str
    url: str
    source: str
    family: str
    published: datetime
    strength: float = 1.0
    # Filled in by sentiment.py, and only for evidence that survived into a ranked
    # topic — there is no point paying for inference on candidates nobody will see.
    sentiment_label: str = ""
    sentiment_score: float = 0.0


@dataclass
class SentimentProfile:
    """How the evidence behind one topic reads in tone, as a separate axis to score."""

    label: str = "unknown"  # dominant of positive / negative / neutral
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    polarity: float = 0.0  # -1 (uniformly negative) .. +1 (uniformly positive)
    analyzed: int = 0
    engine: str = ""


@dataclass
class Topic:
    label: str
    query: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    measurements: list = field(default_factory=list)  # list[SignalMeasurement]
    family_scores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    discovery_score: float = 0.0
    tier: str = "Monitor"
    confidence: int = 0
    angle: str = ""
    sentiment: SentimentProfile = field(default_factory=SentimentProfile)
