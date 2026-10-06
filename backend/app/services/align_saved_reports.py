"""Local persistence for Align analyses that previously lived only in renderer memory.

Only derived measurements, user-entered context and source-backed research are saved.
Original audio and images are never sent to this store.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .. import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize() -> None:
    with db._connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS align_saved_reports (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('music','visual_art')),
          title TEXT NOT NULL, document_json TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS align_saved_reports_recent ON align_saved_reports(kind,updated_at DESC);
        """)


def _validate(kind: str, document: dict) -> None:
    if kind == "music":
        if not isinstance(document.get("analysis"), dict) or not isinstance(document.get("audience_context"), dict):
            raise ValueError("A music report needs analysis and audience context.")
        if not isinstance(document.get("destinations"), list):
            raise ValueError("Music destinations must be a list.")
    elif kind == "visual_art":
        if not isinstance(document.get("labels"), dict) or not isinstance(document.get("report"), dict):
            raise ValueError("An art report needs reviewed labels and benchmark results.")
    else:
        raise ValueError("Only music and visual art reports can be saved here.")
    if len(json.dumps(document, ensure_ascii=False)) > 150_000:
        raise ValueError("The derived report is too large to save.")


def save(kind: str, title: str, document: dict, report_id: str = "") -> dict:
    _validate(kind, document)
    title = title.strip()[:180] or "Untitled Align analysis"
    stamp = _now()
    with db._connect() as conn:
        if report_id:
            row = conn.execute("SELECT kind FROM align_saved_reports WHERE id=?", (report_id,)).fetchone()
            if not row or row["kind"] != kind:
                raise LookupError("The saved Align report is no longer available.")
            conn.execute("UPDATE align_saved_reports SET title=?,document_json=?,updated_at=? WHERE id=?",
                         (title, json.dumps(document, ensure_ascii=False), stamp, report_id))
        else:
            report_id = uuid.uuid4().hex
            conn.execute("INSERT INTO align_saved_reports VALUES (?,?,?,?,?,?)",
                         (report_id, kind, title, json.dumps(document, ensure_ascii=False), stamp, stamp))
    return get(report_id)


def get(report_id: str) -> dict:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM align_saved_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        raise LookupError("The saved Align report is no longer available.")
    return {"id": row["id"], "kind": row["kind"], "title": row["title"],
            "document": json.loads(row["document_json"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def list_recent(kind: str) -> list[dict]:
    if kind not in {"music", "visual_art"}:
        raise ValueError("Unknown Align report type.")
    with db._connect() as conn:
        rows = conn.execute("SELECT id,kind,title,created_at,updated_at FROM align_saved_reports WHERE kind=? ORDER BY updated_at DESC LIMIT 30", (kind,)).fetchall()
    return [dict(row) for row in rows]
