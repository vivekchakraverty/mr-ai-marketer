"""Buyer Persona API. Protected by the app's existing local-token middleware."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import buyer_personas2 as service

router = APIRouter(prefix="/buyer-personas-2", tags=["buyer-personas-2"])


class GenerateRequest(BaseModel):
    source_key: str = Field(default="", max_length=120)
    project: dict[str, str] = Field(default_factory=dict)
    regenerate: bool = False
    refresh_research: bool = False


class ModelCheckRequest(BaseModel):
    consent: bool = False


@router.post("/model-check", status_code=202)
def model_check(body: ModelCheckRequest) -> dict:
    try:
        return service.submit_model_check(body.consent)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/model-check/{check_id}")
def get_model_check(check_id: str) -> dict:
    check = service.get_model_check(check_id)
    if not check:
        raise HTTPException(404, "Model test not found.")
    return check


@router.get("/sources")
def sources() -> dict:
    return {"sources": service.sources()}


@router.get("/context")
def context(source_key: str = "") -> dict:
    try:
        return service.context(source_key)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/latest")
def latest(source_key: str = "") -> dict:
    return {"job": service.recent(source_key), "previous_report": service.latest(source_key)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = service.get(job_id)
    if not job:
        raise HTTPException(404, "Persona report not found.")
    return job


@router.post("/generate", status_code=202)
def generate(body: GenerateRequest) -> dict:
    try:
        return service.submit(body.source_key, body.project, regenerate=body.regenerate,
                              refresh=body.refresh_research)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
