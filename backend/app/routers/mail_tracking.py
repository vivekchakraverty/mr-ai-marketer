"""Read surface for the Analytics 'Email' tab, plus the on-demand sync trigger.

Kept separate from routers/mail.py — that router's own docstring scopes it
specifically to the Mail Composer tool, but this data spans both SMTP send
paths (Composer and the Lead Gen Agent's outreach), matching how leadgen.py is
already its own router even though leadgen also sends mail.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..services import mail_tracking as mail_tracking_service

router = APIRouter(prefix="/mail-tracking", tags=["mail-tracking"])


@router.get("/messages")
def list_messages(source: str | None = None, limit: int = 100) -> list[dict]:
    return db.list_mail_messages(source=source, limit=limit)


@router.get("/stats")
def stats(source: str | None = None) -> dict:
    return db.mail_tracking_stats(source=source)


@router.post("/sync")
def sync() -> dict:
    """Pull new opens/clicks from the tracking Space right now, rather than
    waiting for the background loop's next tick — backs the Email tab's
    'Sync now' button."""
    return mail_tracking_service.sync_from_space()
