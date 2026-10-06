"""Read-only choices retained for the Marketing Plan's saved persona selector."""
from fastapi import APIRouter

from ..services import saved_personas

# Keep the existing options URL used by Marketing Plan. Wizard endpoints are removed.
router = APIRouter(prefix="/personas", tags=["saved-personas"])


@router.get("/options")
def persona_options() -> dict:
    return {"options": saved_personas.options()}
