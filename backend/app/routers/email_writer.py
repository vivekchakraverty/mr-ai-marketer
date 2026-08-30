from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..services.genqueue import queue_slot
from pydantic import BaseModel

from .. import db
from ..services import brand_voice
from ..services import email_writer as email_writer_service

router = APIRouter(prefix="/email-writer", tags=["email-writer"])


class GenerateEmailRequest(BaseModel):
    instruction: str
    # Optional Library id of a Brand Studio document. When set, its voice card is folded
    # into the instruction so the email is written in that brand's voice.
    brandVoiceId: str = ""
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


@router.post("/generate", response_model=GenerateEmailResponse, dependencies=[Depends(queue_slot("space"))])
def generate_email(body: GenerateEmailRequest) -> GenerateEmailResponse:
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Tell me what the email should be about.")

    # The Space takes one free-text `instruction` and no system prompt, so this is the only
    # place the brand context can reach the model.
    instruction = brand_voice.apply_voice(instruction, body.brandVoiceId)

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


# ---------------------------------------------------------------------------
# Bring-your-own Modal GPU
#
# The Space is the default and always will be: it costs nothing and never runs out. Modal
# is the opt-in alternative for anyone who would rather have an answer in seconds than in
# ninety of them, and pays for that in credits on their own account.
# ---------------------------------------------------------------------------


class ModalProvisionRequest(BaseModel):
    modalTokenId: str = ""
    modalTokenSecret: str = ""
    #: Needed at image-build time: the weights repo is private, so the build downloads them
    #: with this token. Falls back to the backend's own when blank.
    hfToken: str = ""


class ModalStatusOut(BaseModel):
    status: str
    message: str = ""
    elapsedSeconds: int = 0
    appPageUrl: str = ""
    #: Always empty here. Present so this matches the shape the renderer already declares
    #: for Brand Studio's identical status, rather than the type quietly lying about a
    #: field that would come back undefined.
    logsUrl: str = ""
    hint: str = ""


def _modal_runtime():
    """Imported lazily so this router still loads when the modal SDK is missing."""
    try:
        from ..emailwriter import modal_runtime
    except Exception as err:  # noqa: BLE001
        raise HTTPException(
            status_code=501, detail=f"The Modal SDK is not available in this build: {err}"
        ) from err
    return modal_runtime


@router.post("/modal/provision", response_model=ModalStatusOut)
def modal_provision(body: ModalProvisionRequest) -> ModalStatusOut:
    """Deploy the Email Writer GPU backend into the user's own Modal workspace.

    Returns immediately — the first deploy builds a CUDA image and bakes ~4.5 GB of weights
    into it, so it runs on a background thread. Poll /modal/status for progress.
    """
    import os

    runtime = _modal_runtime()
    cfg = runtime.ModalConfig(
        token_id=body.modalTokenId,
        token_secret=body.modalTokenSecret,
        hf_token=body.hfToken.strip() or (os.environ.get("HF_TOKEN") or "").strip(),
    )
    try:
        state = runtime.provision(cfg)
    except runtime.EmailWriterModalError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return ModalStatusOut(**{k: v for k, v in state.items() if k in ModalStatusOut.model_fields})


@router.get("/modal/status", response_model=ModalStatusOut)
def modal_status() -> ModalStatusOut:
    state = _modal_runtime().provision_status()
    return ModalStatusOut(**{k: v for k, v in state.items() if k in ModalStatusOut.model_fields})
