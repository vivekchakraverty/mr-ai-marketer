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
    #: An /outputs URL for a picture this app generated, to file alongside the text.
    #: A URL rather than a path because that is what the renderer holds; it is resolved
    #: here through the same containment check the share links use, so a caller cannot
    #: name a file outside the outputs tree.
    imageUrl: str | None = None


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

    output_path = body.outputPath
    if body.imageUrl:
        from ..services import share_links

        resolved = share_links.path_from_outputs_url(body.imageUrl)
        if resolved is None:
            raise HTTPException(
                status_code=400,
                detail="That image is not one this app generated, so it cannot be saved.",
            )
        output_path = str(resolved)

    if not content and not output_path:
        raise HTTPException(status_code=400, detail="Nothing to save.")
    item = db.add_item(
        tool=body.tool.strip() or "Note",
        title=title or "Untitled",
        subtitle=body.subtitle.strip(),
        content=content or None,
        output_path=output_path,
    )
    return {"item": item, "libraryId": item["id"]}


@router.get("/{item_id}")
def get_library_item(item_id: str) -> dict:
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item


class UpdateRequest(BaseModel):
    #: Omitted fields are left alone, so an autosaving editor can send content by itself.
    content: str | None = None
    title: str | None = None


@router.patch("/{item_id}")
def update_library_item(item_id: str, body: UpdateRequest) -> dict:
    """Edit a saved item. This is what the Library's editor autosaves into.

    Content is stored exactly as typed, including trailing whitespace and empty strings:
    the save path strips and rejects blank input because it is deciding whether there is
    anything worth keeping, but by the time an item exists that judgement has been made,
    and clearing a box the user deliberately emptied is not this endpoint's call.
    """
    updated = db.update_item(item_id, content=body.content, title=body.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Not found")
    return {"item": updated}


@router.delete("/{item_id}")
def delete_library_item(item_id: str) -> dict:
    if not db.get_item(item_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.delete_item(item_id)
    return {"deleted": item_id}
