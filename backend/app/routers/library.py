from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(prefix="/library", tags=["library"])


class SaveRequest(BaseModel):
    tool: str
    title: str
    subtitle: str = ""
    content: str = ""
    outputPath: str | None = None


@router.get("")
def list_library() -> dict:
    return {"items": db.list_items(), "count": db.count_items()}


@router.post("")
def save_library_item(body: SaveRequest) -> dict:
    """Save arbitrary generated content.

    Most tools already write to the Library as part of generating — the plan, the blog post,
    the brand document and so on all land there without being asked. This is for the ones
    that produce something worth keeping but had nowhere to put it: hashtag sets, a reply
    drafted in Engage, an analytics summary. It is also what the Save button on every screen
    calls, so a user can keep a result the tool did not decide to keep for them.
    """
    title = body.title.strip()
    content = body.content.strip()
    if not content and not body.outputPath:
        raise HTTPException(status_code=400, detail="Nothing to save.")
    item = db.add_item(
        tool=body.tool.strip() or "Note",
        title=title or "Untitled",
        subtitle=body.subtitle.strip(),
        content=content or None,
        output_path=body.outputPath,
    )
    return {"item": item, "libraryId": item["id"]}


@router.get("/{item_id}")
def get_library_item(item_id: str) -> dict:
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item


@router.delete("/{item_id}")
def delete_library_item(item_id: str) -> dict:
    if not db.get_item(item_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.delete_item(item_id)
    return {"deleted": item_id}
