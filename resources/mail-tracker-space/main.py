"""Mail Tracker — the public half of Mr AI Marketer's email open/click tracking.

Serves a 1x1 pixel for open tracking and a redirect for click tracking, both
reachable from anywhere (a recipient's mail client is never on the same machine
as the desktop app that sent the mail), plus a secret-gated /events endpoint the
local app polls to pull new hits back down. Storage is a local SQLite file on
this Space's own container disk — survives normal sleep/wake, but not a factory
reboot or a redeploy (rebuilding the image wipes it), an accepted tradeoff for a
free, personal-scale tool; the local app is the durable copy of record.

SYNC_SECRET is a single value shared by every installation of the app (there is
no per-user boundary on a shared public Space), so it only deters casual
scraping of /events, not a determined reader of the shipped app's own source —
the real privacy boundary is client-side: the syncing app discards any event
whose token it doesn't recognize as its own.
"""

from __future__ import annotations

import base64
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, RedirectResponse, Response
from urllib.parse import urlparse

DB_PATH = Path(__file__).parent / "tracker.sqlite3"
SYNC_SECRET = os.environ.get("SYNC_SECRET", "")

# The standard minimal 1x1 GIF used by countless open-source tracking pixels —
# decoded from a literal already verified to be a well-formed 34-byte GIF89a
# (header + 1x1 logical screen + 2-color table + one image block + trailer).
_PIXEL_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

app = FastAPI()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT,
                occurred_at TEXT NOT NULL
            )
            """
        )


_init_db()


def _log_event(token: str, event_type: str, url: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO events (token, type, url, occurred_at) VALUES (?, ?, ?, ?)",
            (token, event_type, url, _now_iso()),
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/o/{token}.gif")
def open_pixel(token: str) -> Response:
    _log_event(token, "open")
    return Response(content=_PIXEL_GIF, media_type="image/gif", headers=_NO_STORE_HEADERS)


@app.get("/c/{token}")
def click_redirect(token: str, u: str = Query(...)):
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(u) > 2000:
        return JSONResponse({"error": "invalid destination url"}, status_code=400)
    _log_event(token, "click", url=u)
    return RedirectResponse(url=u, status_code=302, headers=_NO_STORE_HEADERS)


@app.get("/events")
def events(since_id: int = 0, secret: str = "", limit: int = 500):
    if not SYNC_SECRET or secret != SYNC_SECRET:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    limit = max(1, min(limit, 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, token, type, url, occurred_at FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
    return {"events": [dict(row) for row in rows]}
