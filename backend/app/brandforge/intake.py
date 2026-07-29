"""Brand intake schema + validation, ported from the BrandForge Space
(src/interview.py). Pydantic only — no RAG, no DB.
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .archetypes import ARCHETYPE_IDS
from .sections import BRAND_CATEGORIES, CHANNELS

TBD = "TBD — needs founder input"


class PersonalitySliders(BaseModel):
    formal_casual: int = Field(4, ge=1, le=7, description="1=formal, 7=casual")
    serious_playful: int = Field(4, ge=1, le=7, description="1=serious, 7=playful")
    authoritative_friendly: int = Field(4, ge=1, le=7, description="1=authoritative, 7=friendly")
    classic_innovative: int = Field(4, ge=1, le=7, description="1=classic, 7=innovative")
    corporate_rebellious: int = Field(4, ge=1, le=7, description="1=corporate, 7=rebellious")


class BrandIntake(BaseModel):
    brand_archetype: str
    secondary_archetype: Optional[str] = None
    brand_category: str
    brand_name: str = Field(..., min_length=1)
    one_liner: str = Field(..., min_length=1)
    founding_story: str = TBD
    primary_audience: str = Field(..., min_length=1)
    secondary_audience: str = TBD
    geography: str = TBD
    business_model: Literal["B2B", "B2C", "Both"] = "B2C"
    competitors: list[str] = Field(default_factory=list)
    differentiation_hypothesis: str = TBD
    admired_brands: str = TBD
    never_sound_like: str = TBD
    existing_assets: str = TBD
    top_12mo_goal: str = Field(..., min_length=1)

    personality: PersonalitySliders = Field(default_factory=PersonalitySliders)
    channels: list[str] = Field(default_factory=list)

    @field_validator("brand_archetype")
    @classmethod
    def _archetype_known(cls, v: str) -> str:
        if v not in ARCHETYPE_IDS:
            raise ValueError(f"brand_archetype must be one of {ARCHETYPE_IDS}")
        return v

    @field_validator("secondary_archetype")
    @classmethod
    def _secondary_archetype_known(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return None
        if v not in ARCHETYPE_IDS:
            raise ValueError(f"secondary_archetype must be one of {ARCHETYPE_IDS} (or empty)")
        return v

    @field_validator("brand_category")
    @classmethod
    def _category_known(cls, v: str) -> str:
        if v not in BRAND_CATEGORIES:
            raise ValueError(f"brand_category must be one of {BRAND_CATEGORIES}")
        return v

    @field_validator("competitors")
    @classmethod
    def _competitors_range(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v if c.strip()]
        if cleaned and not (3 <= len(cleaned) <= 5):
            raise ValueError("Provide 3-5 competitors (or leave the field empty).")
        return cleaned

    @field_validator("channels")
    @classmethod
    def _channels_known(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(CHANNELS)
        if unknown:
            raise ValueError(f"Unknown channel(s): {sorted(unknown)}. Must be one of {CHANNELS}")
        return v


def validate_intake(data: dict) -> tuple[Optional[BrandIntake], list[str]]:
    """Returns (intake_or_None, human_readable_errors)."""
    try:
        return BrandIntake.model_validate(data), []
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return None, errors


def demo_brand_dict() -> dict:
    """Fictional sample brand (Copper Kettle) for the UI's 'Load demo' button."""
    return json.loads(
        BrandIntake(
            brand_archetype="hero",
            secondary_archetype="explorer",
            brand_category="Food & beverage",
            brand_name="Copper Kettle",
            one_liner="A Kolkata specialty-coffee subscription that roasts small-batch beans from Indian estates within 72 hours of your delivery window.",
            founding_story=(
                "Started in 2023 by two former tea exporters who kept getting asked "
                "why India's own coffee estates never showed up on shelves at home — "
                "only ever exported to Europe and Japan. Copper Kettle roasts what "
                "used to leave the country."
            ),
            primary_audience="Urban professionals aged 26-40 in Kolkata, Bangalore, and Mumbai who already spend on specialty coffee subscriptions or third-wave cafes.",
            secondary_audience="Gift-givers looking for a premium, India-forward alternative to imported coffee gift boxes.",
            geography="India (metro delivery), with Kolkata as the flagship city",
            business_model="B2C",
            competitors=["Blue Tokai", "Third Wave Coffee Roasters", "Sleepy Owl", "Araku Coffee"],
            differentiation_hypothesis="We are the only subscription that names the estate and the roast date on every bag, and roasts after you subscribe rather than before.",
            admired_brands="Blue Bottle (for ritual and pacing), Patagonia (for standing for something beyond the product)",
            never_sound_like="Airport duty-free coffee gift sets; generic 'artisan' marketing speak; imported-is-better condescension",
            existing_assets="Wordmark logo in copper/black exists; no established tagline yet; Instagram handle secured",
            top_12mo_goal="Grow paid monthly subscribers from 800 to 5,000 while keeping churn under 4%/month",
            personality=PersonalitySliders(
                formal_casual=5,
                serious_playful=4,
                authoritative_friendly=5,
                classic_innovative=5,
                corporate_rebellious=5,
            ),
            channels=["website", "Instagram", "X"],
        ).model_dump_json()
    )
