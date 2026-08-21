"""Control surface for the Tumblr timing watcher.

Small on purpose. The watcher runs itself once it has keys and a watch list; these endpoints
exist to give it those and to report how the collection is going. Nothing here drives a
sweep on a schedule — that is the loop's job, and a collection that only advances while
someone has a screen open is the failure mode this whole design is avoiding.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import tumblr_timing
from ..services.tumblr import Credentials

router = APIRouter(prefix="/tumblr-timing", tags=["tumblr-timing"])


class CredentialsRequest(BaseModel):
    """The four OAuth values, or empty strings to stand the watcher down.

    Sent by the app at launch from the operating system's own credential store. The backend
    keeps them in memory only: a watcher that outlived the app's memory would have to write a
    consumer key to disk, and this app does not do that.
    """

    consumerKey: str = ""
    consumerSecret: str = ""
    oauthToken: str = ""
    oauthTokenSecret: str = ""


@router.post("/credentials")
def set_credentials(body: CredentialsRequest) -> dict:
    ready = tumblr_timing.set_credentials(
        Credentials(
            consumer_key=body.consumerKey.strip(),
            consumer_secret=body.consumerSecret.strip(),
            token=body.oauthToken.strip(),
            token_secret=body.oauthTokenSecret.strip(),
        )
    )
    return {"ready": ready, "watching": tumblr_timing.watch_count()}


class WatchRequest(BaseModel):
    #: Blog names or addresses. Anything unparseable is skipped rather than failing the
    #: batch — a watch list is usually pasted, and one bad line should not reject the rest.
    blogs: list[str] = []


@router.post("/watch")
def watch(body: WatchRequest) -> dict:
    if not body.blogs:
        raise HTTPException(status_code=400, detail="Name at least one blog to watch.")
    return tumblr_timing.watch(body.blogs)


@router.post("/unwatch")
def unwatch(body: WatchRequest) -> dict:
    for blog in body.blogs:
        tumblr_timing.unwatch(blog)
    return {"watching": tumblr_timing.watch_count()}


@router.get("/candidates")
def candidates(limit: int = 150) -> dict:
    """Blogs worth watching, from the collector's catalogue. Changes nothing."""
    found = tumblr_timing.candidates(limit)
    return {"candidates": found, "count": len(found)}


class SeedRequest(BaseModel):
    limit: int = 150


@router.post("/seed")
def seed(body: SeedRequest | None = None) -> dict:
    """Fill the watch list from the catalogue — the step that starts the collection.

    Re-runnable: blogs already watched keep their sweep history, so running this again in a
    few weeks tops the list up with whoever has become active since.
    """
    limit = max(1, min((body.limit if body else 150), 500))
    result = tumblr_timing.seed(limit)
    if result["candidates"] == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No blogs in the collector's catalogue are currently active enough to watch. "
                "Run the Tumblr collector to refresh it, then try again."
            ),
        )
    return result


@router.get("/progress")
def progress() -> dict:
    """How far the collection has got, against what it needs."""
    return tumblr_timing.progress()


@router.post("/sweep")
def sweep_now() -> dict:
    """Read every due blog immediately.

    For checking a fresh setup without waiting for the next tick. It is the same call the
    loop makes, and both are idempotent, so using it changes nothing about the collection
    beyond bringing it forward.
    """
    if not tumblr_timing.has_credentials():
        raise HTTPException(
            status_code=400,
            detail="The watcher has no Tumblr credentials. Connect Tumblr in Settings.",
        )
    return tumblr_timing.sweep().as_dict()
