"""Source-backed listener research for Align Music."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.music_audience import AudienceSourceError, explore_music_audience

router = APIRouter(prefix="/align/music", tags=["align-music"])


class AudienceRequest(BaseModel):
    reference_artists: str = Field(min_length=1, max_length=240)


@router.post("/audience")
def audience(body: AudienceRequest) -> dict:
    names = list(dict.fromkeys(part.strip() for part in body.reference_artists.split(",") if part.strip()))
    if not 1 <= len(names) <= 3 or any(len(name) > 80 for name in names):
        raise HTTPException(status_code=400, detail="Enter one to three artist names, separated by commas.")
    try:
        return explore_music_audience(names)
    except AudienceSourceError as err:
        raise HTTPException(status_code=502, detail=str(err)) from None
