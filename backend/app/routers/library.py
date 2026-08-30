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
    #: An /outputs URL for an uploaded clip, filed the same way and into the same slot.
    #: Not the YouTube link a composer may also carry — that one lives in the text.
    videoFileUrl: str | None = None


def _attachment_output_path(url: str, noun: str) -> str:
    """The local file an /outputs URL names, refusing anything outside the tree.

    Shared by save and update, and by both kinds of attachment, so a picture or a clip
    filed against an existing row goes through the same containment check as one filed at
    save time. `noun` only shapes the refusal message.
    """
    from ..services import share_links

    resolved = share_links.path_from_outputs_url(url)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=f"That {noun} is not one this app is holding, so it cannot be saved.",
        )
    return str(resolved)


def _attachment_from(image_url: str | None, video_file_url: str | None) -> str | None:
    """The single file a row carries, from whichever attachment the caller named.

    A row holds one file, and a post carries one embed — the distribution send path
    refuses an image and a video together for exactly that reason — so the two share the
    slot rather than needing a column each. Naming both here is a caller bug, not a
    choice to resolve silently.
    """
    if image_url and video_file_url:
        raise HTTPException(
            status_code=400,
            detail="An entry holds one file: attach either an image or a video, not both.",
        )
    if image_url:
        return _attachment_output_path(image_url, "image")
    if video_file_url:
        return _attachment_output_path(video_file_url, "video")
    return None


def _absorb_companion_image(output_path: str | None, keep_id: str) -> int:
    """Fold an auto-filed companion image into the entry that now carries it.

    Called wherever an entry gains an attachment, which is the moment the standalone row
    for that picture stops being the only place it lives. See db.delete_superseded_companion_images
    for why this matches on subtitle as well as on the file.
    """
    if not output_path:
        return 0
    from ..services import image_prompt

    return db.delete_superseded_companion_images(
        output_path, keep_id, set(image_prompt.COMPANION_SUBTITLES)
    )


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

    output_path = _attachment_from(body.imageUrl, body.videoFileUrl) or body.outputPath

    if not content and not output_path:
        raise HTTPException(status_code=400, detail="Nothing to save.")
    item = db.add_item(
        tool=body.tool.strip() or "Note",
        title=title or "Untitled",
        subtitle=body.subtitle.strip(),
        content=content or None,
        output_path=output_path,
    )
    _absorb_companion_image(output_path, item["id"])
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
    #: Set when a caller is finishing a row a tool filed for it — see the composition save
    #: in the post composers, which turns the bare generation into the finished post.
    subtitle: str | None = None
    #: An /outputs URL for a picture to file against this row, resolved through the same
    #: containment check as the save path.
    imageUrl: str | None = None
    #: An /outputs URL for an uploaded clip, sharing the row's single file slot.
    videoFileUrl: str | None = None


@router.patch("/{item_id}")
def update_library_item(item_id: str, body: UpdateRequest) -> dict:
    """Edit a saved item. This is what the Library's editor autosaves into.

    Content is stored exactly as typed, including trailing whitespace and empty strings:
    the save path strips and rejects blank input because it is deciding whether there is
    anything worth keeping, but by the time an item exists that judgement has been made,
    and clearing a box the user deliberately emptied is not this endpoint's call.
    """
    updated = db.update_item(
        item_id,
        content=body.content,
        title=body.title,
        subtitle=body.subtitle,
        output_path=_attachment_from(body.imageUrl, body.videoFileUrl),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Not found")
    _absorb_companion_image(updated["output_path"], item_id)
    return {"item": updated}


@router.delete("/{item_id}")
def delete_library_item(item_id: str) -> dict:
    if not db.get_item(item_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.delete_item(item_id)
    return {"deleted": item_id}
