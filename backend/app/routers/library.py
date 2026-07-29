from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/library", tags=["library"])


@router.get("")
def list_library() -> dict:
    return {"items": db.list_items(), "count": db.count_items()}


@router.get("/{item_id}")
def get_library_item(item_id: str) -> dict:
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item
