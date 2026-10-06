"""Align → Visual Art."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..services import align_visual_art as art
from ..services.genqueue import queue_slot

router = APIRouter(prefix="/align/visual-art", tags=["align-visual-art"])


@router.get("/choices")
def get_choices() -> dict:
    return art.choices()


@router.post("/classify", dependencies=[Depends(queue_slot("model"))])
async def classify_image(file: UploadFile = File(...)) -> dict:
    if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=400, detail="Use a PNG, JPEG or WebP image.")
    content = await file.read(art.MAX_IMAGE_BYTES + 1)
    try:
        return await run_in_threadpool(art.classify, content)
    except art.VisualArtInferenceError as err:
        raise HTTPException(status_code=err.http_status, detail=str(err)) from None
    except art.VisualArtError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None


class MatchRequest(BaseModel):
    group: str = Field(max_length=80)
    style: str = Field(default="", max_length=80)
    artsy_categories: list[str] = Field(default_factory=list, max_length=4)


@router.post("/match")
def match_art(body: MatchRequest) -> dict:
    try:
        return art.match(body.group, body.style, body.artsy_categories)
    except art.VisualArtError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
