"""Align → Writing: upload, fingerprint, review and match a creative work."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..services.align_writing import (
    WritingAnalysisError,
    WritingModelError,
    analyze,
    get_book_profile,
    list_book_profiles,
    save_book_profile,
)
from ..services.genqueue import queue_slot

router = APIRouter(prefix="/align/writing", tags=["align-writing"])


@router.get("/books")
def books() -> dict:
    return {"books": list_book_profiles()}


@router.post("/analyze", dependencies=[Depends(queue_slot("model"))])
async def analyze_work(file: UploadFile = File(...), title: str = Form("")) -> dict:
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="That file is empty.")
    try:
        return await run_in_threadpool(
            analyze, file.filename or "creative-work.txt", contents, title_override=title
        )
    except WritingAnalysisError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    except WritingModelError as err:
        raise HTTPException(status_code=err.http_status, detail=err.detail()) from None


class ReviewRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    blurb: str = Field(default="", max_length=20_000)
    subgenres: list[str] = Field(default_factory=list, max_length=50)
    themes: list[str] = Field(default_factory=list, max_length=50)
    tropes: list[str] = Field(default_factory=list, max_length=50)
    tone: str = Field(default="", max_length=1000)
    comps: list[str] = Field(default_factory=list, max_length=50)
    fingerprint: dict


@router.put("/books/{book_id}")
def review_work(book_id: int, body: ReviewRequest) -> dict:
    if get_book_profile(book_id) is None:
        raise HTTPException(status_code=404, detail="That writing profile no longer exists.")
    result = save_book_profile(
        book_id=book_id,
        title=body.title.strip(),
        blurb=body.blurb,
        subgenres=body.subgenres,
        themes=body.themes,
        tropes=body.tropes,
        tone=body.tone,
        comps=body.comps,
        fingerprint=dict(body.fingerprint),
    )
    return {"book": result}


@router.get("/books/{book_id}")
def book(book_id: int) -> dict:
    result = get_book_profile(book_id)
    if result is None:
        raise HTTPException(status_code=404, detail="That writing profile no longer exists.")
    return {"book": result}
