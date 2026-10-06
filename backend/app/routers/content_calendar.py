"""Saved content-calendar suggestions for Research / Strategy."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import content_calendar as service

router = APIRouter(prefix="/content-calendar", tags=["content-calendar"])


class GenerateRequest(BaseModel):
    persona_report_id: str = Field(min_length=1, max_length=64)


@router.get("/personas")
def personas() -> dict:
    return {"reports": service.persona_reports()}


@router.get("/latest")
def latest(persona_report_id: str) -> dict:
    return {"job": service.latest(persona_report_id), "previous_complete": service.latest_complete(persona_report_id)}


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict:
    result = service.get(job_id)
    if result is None:
        raise HTTPException(404, "Content calendar not found.")
    return result


@router.post("/generate", status_code=202)
def generate(body: GenerateRequest) -> dict:
    try:
        return service.submit(body.persona_report_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
