"""BrandForge — generate a Brand Document (12 sections across 5 phases) from a
structured intake, using the fine-tuned Qwen3 served on a free CPU Space
(GGUF via gradio_client). Section-by-section so each request stays short on CPU;
this side assembles the document (markdown/docx/voice card) and saves it.

Brand images use HF text-to-image, billed to the user's own HF token.
Lives in Research/Strategy on the frontend.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db
from ..brandforge import space
from ..brandforge.archetypes import ARCHETYPES
from ..brandforge.client import BrandForgeError, text_to_image
from ..brandforge.export import to_docx_bytes, to_markdown, to_voice_system_prompt
from ..brandforge.imaging import ASSET_PROMPT_SPECS, ColorSwatch, build_image_prompt, extract_palette
from ..brandforge.intake import TBD, BrandIntake, demo_brand_dict, validate_intake
from ..brandforge.sections import (
    BRAND_CATEGORIES,
    BUSINESS_MODELS,
    CHANNELS,
    PERSONALITY_SLIDERS,
    PHASE_OF,
    PHASES,
    SECTION_NAMES,
    SECTION_SPECS,
)

router = APIRouter(prefix="/brand-forge", tags=["brand-forge"])

# Optional fields whose intake schema default is TBD — an empty box from the UI
# should read as "not provided" (TBD) so the model treats it as missing.
_TBD_IF_BLANK = (
    "founding_story",
    "secondary_audience",
    "geography",
    "differentiation_hypothesis",
    "admired_brands",
    "never_sound_like",
    "existing_assets",
)


# --- request / response models ---------------------------------------------

class PersonalityIn(BaseModel):
    formal_casual: int = 4
    serious_playful: int = 4
    authoritative_friendly: int = 4
    classic_innovative: int = 4
    corporate_rebellious: int = 4


class IntakeIn(BaseModel):
    brand_archetype: str = ""
    secondary_archetype: Optional[str] = None
    brand_category: str = BRAND_CATEGORIES[0]
    brand_name: str = ""
    one_liner: str = ""
    founding_story: str = ""
    primary_audience: str = ""
    secondary_audience: str = ""
    geography: str = ""
    business_model: str = "B2C"
    competitors: list[str] = []
    differentiation_hypothesis: str = ""
    admired_brands: str = ""
    never_sound_like: str = ""
    existing_assets: str = ""
    top_12mo_goal: str = ""
    personality: PersonalityIn = PersonalityIn()
    channels: list[str] = []


class GenerateSectionRequest(BaseModel):
    intake: IntakeIn
    sectionName: str
    spaceId: str = ""
    hfToken: str = ""


class AssembleRequest(BaseModel):
    intake: IntakeIn
    sections: dict[str, str]


class BrandImagesRequest(BaseModel):
    intake: IntakeIn
    visualBrief: str = ""
    hfToken: str = ""
    imageModel: Optional[str] = None


class SectionOut(BaseModel):
    name: str
    phase: str
    content: str


class SwatchOut(BaseModel):
    hex: str
    name: str = ""
    rationale: str = ""


class BrandImageOut(BaseModel):
    assetType: str
    url: Optional[str] = None
    promptUsed: str = ""
    error: Optional[str] = None


class AssembleResponse(BaseModel):
    markdown: str
    voiceCard: str
    palette: list[SwatchOut]
    docxPath: Optional[str]
    docxUrl: Optional[str]
    libraryId: str


class BrandImagesResponse(BaseModel):
    images: list[BrandImageOut]
    palette: list[SwatchOut]


# --- helpers ----------------------------------------------------------------

def _build_intake(payload: IntakeIn) -> BrandIntake:
    """IntakeIn -> validated BrandIntake, mapping blank optional fields to TBD.
    Raises HTTPException(400) with human-readable validation errors."""
    data = payload.model_dump()
    for field in _TBD_IF_BLANK:
        if not str(data.get(field, "")).strip():
            data[field] = TBD
    if not (payload.secondary_archetype or "").strip():
        data["secondary_archetype"] = None
    intake, errors = validate_intake(data)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    assert intake is not None
    return intake


def _swatches_out(swatches: list[ColorSwatch]) -> list[SwatchOut]:
    return [SwatchOut(hex=s.hex, name=s.name, rationale=s.rationale) for s in swatches]


def _outputs_url(path: Path) -> str:
    return "/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()


def _run_dir() -> Path:
    d = config.OUTPUTS_DIR / "brand" / str(uuid.uuid4())
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- endpoints --------------------------------------------------------------

@router.get("/meta")
def meta() -> dict:
    """Everything the intake form needs, plus the section order the frontend
    loops over — single source of truth for the archetype/category/section lists."""
    return {
        "archetypes": [{"id": a.id, "name": a.name, "description": a.description} for a in ARCHETYPES],
        "categories": BRAND_CATEGORIES,
        "channels": CHANNELS,
        "businessModels": BUSINESS_MODELS,
        "personalitySliders": PERSONALITY_SLIDERS,
        "phases": [{"phase": phase, "sections": names} for phase, names in PHASES.items()],
        "sectionNames": SECTION_NAMES,
        "demo": demo_brand_dict(),
    }


@router.post("/section", response_model=SectionOut)
def generate_section(body: GenerateSectionRequest) -> SectionOut:
    """Generate ONE section on the Space. The frontend calls this per section
    (incremental progress) and for single-section regeneration."""
    if body.sectionName not in SECTION_SPECS:
        raise HTTPException(status_code=400, detail=f"Unknown section: {body.sectionName}")
    intake = _build_intake(body.intake)
    try:
        content = space.generate_section(body.spaceId, body.hfToken, intake.model_dump(), body.sectionName)
    except BrandForgeError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return SectionOut(name=body.sectionName, phase=PHASE_OF[body.sectionName], content=content)


@router.post("/assemble", response_model=AssembleResponse)
def assemble(body: AssembleRequest) -> AssembleResponse:
    """Assemble the finished sections into markdown + docx + voice card, extract
    the palette, and save the document to the Library."""
    intake = _build_intake(body.intake)
    sections = {name: body.sections.get(name, "") for name in SECTION_NAMES}
    if not any(v.strip() for v in sections.values()):
        raise HTTPException(status_code=400, detail="No sections to assemble yet.")

    markdown = to_markdown(intake, sections)
    voice_card = to_voice_system_prompt(intake, sections)
    palette = extract_palette(sections.get("Visual Direction Brief", ""))

    run_dir = _run_dir()
    (run_dir / "brand-document.md").write_text(markdown, encoding="utf-8")
    (run_dir / "voice-card.md").write_text(voice_card, encoding="utf-8")
    docx_path = run_dir / "brand-document.docx"
    docx_path.write_bytes(to_docx_bytes(intake, sections))

    item = db.add_item(
        tool="Brand",
        title=f"{intake.brand_name} — Brand Document",
        subtitle="Brand document",
        content=markdown,
        output_path=str(docx_path),
    )

    return AssembleResponse(
        markdown=markdown,
        voiceCard=voice_card,
        palette=_swatches_out(palette),
        docxPath=str(docx_path),
        docxUrl=_outputs_url(docx_path),
        libraryId=item["id"],
    )


@router.post("/images", response_model=BrandImagesResponse)
def generate_images(body: BrandImagesRequest) -> BrandImagesResponse:
    intake = _build_intake(body.intake)
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account.")
    if not body.visualBrief.strip():
        raise HTTPException(status_code=400, detail="Generate the Visual Direction Brief section first — images are grounded in it.")

    model = (body.imageModel or "").strip() or None
    palette = extract_palette(body.visualBrief)
    run_dir = _run_dir()

    images: list[BrandImageOut] = []
    for i, asset_type in enumerate(ASSET_PROMPT_SPECS):
        prompt = build_image_prompt(asset_type, intake, body.visualBrief, palette)
        try:
            image = text_to_image(body.hfToken, prompt, model=model) if model else text_to_image(body.hfToken, prompt)
        except BrandForgeError as err:
            if i == 0:
                raise HTTPException(status_code=502, detail=str(err)) from err
            images.append(BrandImageOut(assetType=asset_type, promptUsed=prompt, error=str(err)))
            continue
        dest = run_dir / (asset_type.lower().replace(" ", "-") + ".png")
        image.save(dest)
        images.append(BrandImageOut(assetType=asset_type, url=_outputs_url(dest), promptUsed=prompt))

    return BrandImagesResponse(images=images, palette=_swatches_out(palette))
