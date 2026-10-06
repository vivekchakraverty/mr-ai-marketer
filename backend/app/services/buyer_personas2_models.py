"""Stored contract for the evidence-based persona report."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    id: str
    type: Literal["project", "align", "external", "inference"]
    claim: str
    source_name: str = ""
    source_url: str = ""
    publisher: str = ""
    published_at: str = ""
    retrieved_at: str = ""
    relevance: str = ""
    source_quality: int = Field(default=0, ge=0, le=100)
    confidence: Literal["Low", "Medium", "High"] = "Low"


class RankedItem(BaseModel):
    text: str
    importance: Literal["High", "Medium", "Low"] = "Medium"


class Motivation(BaseModel):
    name: str
    rank: int = Field(ge=1, le=10)
    why: str


class Objection(BaseModel):
    objection: str
    response: str


class JobsToBeDone(BaseModel):
    functional: list[str]
    emotional: list[str]
    social: list[str]


class AdoptionTriggers(BaseModel):
    investigate: list[str]
    try_it: list[str]
    purchase: list[str]
    recommend: list[str]
    return_to_it: list[str]


class JourneyStage(BaseModel):
    stage: Literal["Awareness", "Interest", "Evaluation", "Conversion", "Retention", "Advocacy"]
    touchpoints: list[str]
    questions: list[str]
    content: list[str]
    channels: list[str]
    proof: str
    friction: str


class Channel(BaseModel):
    name: str
    affinity: Literal["High", "Medium", "Low"]
    why: str
    evidence_ids: list[str] = Field(default_factory=list)


class Affinity(BaseModel):
    item: str
    category: str
    why: str
    source: str
    confidence: Literal["Low", "Medium", "High"]
    evidence_ids: list[str] = Field(default_factory=list)


class Reach(BaseModel):
    place: str
    kind: str
    why: str
    source_url: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class ContentPreferences(BaseModel):
    types: list[str]
    length: str
    hooks: list[str]
    tone: str
    topics: list[str]
    visual_style: str
    detail_level: str
    proof: str
    trusted_sources: list[str]
    conversion_examples: list[str] = Field(min_length=1, max_length=5)


class Messaging(BaseModel):
    core_message: str
    value_proposition: str
    pillars: list[str] = Field(min_length=1, max_length=5)
    resonant_words: list[str]
    avoid_words: list[str]
    hooks: list[str] = Field(min_length=1, max_length=6)


class Persona(BaseModel):
    id: str
    name: str
    archetype: str
    description: str
    priority: Literal["Primary", "Secondary", "Niche / Experimental"] = "Secondary"
    relevance_score: int = Field(default=0, ge=0, le=100)
    score_components: dict[str, int] = Field(default_factory=dict)
    confidence: Literal["Low", "Medium", "High"] = "Low"
    cluster_ids: list[str] = Field(default_factory=list)
    signal_ids: list[str] = Field(default_factory=list)
    snapshot: dict[str, str]
    jobs_to_be_done: JobsToBeDone
    motivations: list[Motivation]
    pain_points: list[RankedItem]
    adoption_triggers: AdoptionTriggers
    objections: list[Objection]
    discovery_journey: list[JourneyStage]
    channels: list[Channel]
    content_preferences: ContentPreferences
    messaging: Messaging
    affinities: list[Affinity]
    reach: list[Reach]
    evidence_ids: list[str]
    strategic_recommendations: list[str]


class Strategy(BaseModel):
    audience_priorities: list[str]
    positioning: list[str]
    product: list[str]
    content: list[str]
    channels: list[str]
    community: list[str]
    launch: list[str]
    risks: list[str]
    experiments: list[str] = Field(min_length=1, max_length=7)


class Synthesis(BaseModel):
    personas: list[Persona] = Field(min_length=1, max_length=6)
    strategy: Strategy
    conflicts: list[str]
    warnings: list[str]


class Report(Synthesis):
    id: str
    created_at: str
    researched_at: str
    source_key: str
    source_snapshot: dict
    evidence: list[Evidence]
    methodology: str
    model: str
    usage: dict = Field(default_factory=dict)
