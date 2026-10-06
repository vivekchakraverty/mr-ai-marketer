"""Persist derived Music and Visual Art reports for reuse in strategy tools."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import align_saved_reports as service

router = APIRouter(prefix="/align/saved-reports", tags=["align-saved-reports"])


class SaveRequest(BaseModel):
    id: str = Field(default="", max_length=64)
    kind: str
    title: str = Field(min_length=1, max_length=180)
    document: dict


@router.get("")
def recent(kind: str) -> dict:
    try:
        return {"reports": service.list_recent(kind)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/{report_id}")
def get(report_id: str) -> dict:
    try:
        return service.get(report_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("")
def save(body: SaveRequest) -> dict:
    try:
        return service.save(body.kind, body.title, body.document, body.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
