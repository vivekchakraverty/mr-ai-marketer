from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import email_writer as email_writer_service

router = APIRouter(prefix="/email-writer", tags=["email-writer"])


class GenerateEmailRequest(BaseModel):
    instruction: str
    # Only used on a fresh install, to fetch the CTR model from Hugging Face the first time.
    # The Space call itself is authenticated inside services/email_writer.
    hfToken: str = ""


class GenerateEmailResponse(BaseModel):
    text: str
    libraryId: str
    # A statistical estimate only (see services/ctr_predictor.py for what it's
    # trained on and its real limitations) -- never present this as a guarantee.
    predictedClickRate: float
    ctrBucket: str


@router.post("/generate", response_model=GenerateEmailResponse)
def generate_email(body: GenerateEmailRequest) -> GenerateEmailResponse:
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Tell me what the email should be about.")

    try:
        # Space call + CTR now live in services/email_writer so the Lead Gen Agent can reuse
        # them; the Library save stays here (specific to this tool).
        result = email_writer_service.generate_marketing_email(
            instruction, hf_token=body.hfToken.strip() or None
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    except Exception as err:  # noqa: BLE001 — surface Space/network failures as a clean API error
        raise HTTPException(status_code=502, detail=f"Email generation failed: {err}") from err

    title = instruction if len(instruction) <= 60 else instruction[:57].rstrip() + "…"
    item = db.add_item(tool="Email", title=title, subtitle="Marketing email", content=result["text"])
    return GenerateEmailResponse(
        text=result["text"],
        libraryId=item["id"],
        predictedClickRate=result["predictedClickRate"],
        ctrBucket=result["ctrBucket"],
    )
