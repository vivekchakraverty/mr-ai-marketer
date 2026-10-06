"""Align > Games upload, progress and derived report endpoints."""
from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config as app_config
from ..game_analysis import config, igdb, jobs, modal_runtime

router = APIRouter(prefix="/align/games", tags=["align-games"])
EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


@router.get("/status")
def provider_status() -> dict:
    ready = modal_runtime.credentials_configured()
    return {"modal_credentials_configured": ready, "igdb_configured": igdb.configured(),
            "message": ("A Modal token is configured. The first analysis will check it and can set up the Games GPU in your workspace."
                        if ready else "Add a Modal token in Settings > Brand Studio GPU and restart the app before analyzing gameplay.")}


@router.post("/analyze", status_code=202)
async def analyze(file: UploadFile = File(...), sampling_fps: float = Form(1.0)) -> dict:
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "gameplay").name)[:120]
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSIONS:
        raise HTTPException(400, "Upload an MP4, MOV, WebM, MKV, or M4V video.")
    if file.content_type and file.content_type not in ("application/octet-stream", "video/mp4", "video/quicktime",
                                                       "video/webm", "video/x-matroska", "video/x-m4v"):
        raise HTTPException(400, "The selected file is not a supported video.")
    if not .25 <= sampling_fps <= 4:
        raise HTTPException(400, "Sampling FPS must be between 0.25 and 4.")
    path: Path | None = None
    try:
        upload_dir = app_config.DATA_DIR / "private-game-analysis-tmp"
        upload_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="game-analysis-", suffix=suffix, dir=upload_dir, delete=False) as output:
            path = Path(output.name)
            digest = hashlib.sha256()
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_BYTES:
                    raise HTTPException(413, f"Videos must be at most {config.MAX_BYTES // 1024 // 1024} MB.")
                digest.update(chunk)
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "The selected video is empty.")
        job_id, reused = jobs.submit(path, filename, digest.hexdigest(), sampling_fps)
        path = None
        return {"analysis_id": job_id, "status": jobs.get(job_id)["status"] if reused else "queued", "reused": reused}
    except RuntimeError as exc:
        raise HTTPException(429, str(exc)) from None
    finally:
        await file.close()
        if path:
            path.unlink(missing_ok=True)


@router.get("")
def recent() -> dict:
    return {"analyses": jobs.list_recent()}


@router.post("/{analysis_id}/refresh-comparables")
def refresh_comparables(analysis_id: str) -> dict:
    try:
        return jobs.refresh_comparables(analysis_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except igdb.IGDBError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/{analysis_id}")
def analysis(analysis_id: str) -> dict:
    result = jobs.get(analysis_id)
    if result is None:
        raise HTTPException(404, "Game analysis not found.")
    return result


def _section(analysis_id: str, section: str):
    result = analysis(analysis_id)
    return result["report"][section] if result["report"] else []


@router.get("/{analysis_id}/events")
def events(analysis_id: str):
    result = analysis(analysis_id)
    return result["report"]["profile"]["events"] if result["report"] else []


@router.get("/{analysis_id}/comparables")
def comparables(analysis_id: str):
    return _section(analysis_id, "comparables")


@router.get("/{analysis_id}/audience")
def audience(analysis_id: str):
    return _section(analysis_id, "audience")


@router.get("/{analysis_id}/platforms")
def platforms(analysis_id: str):
    return _section(analysis_id, "platforms")


@router.delete("/{analysis_id}", status_code=204)
def delete(analysis_id: str) -> None:
    if not jobs.delete(analysis_id):
        raise HTTPException(404, "Game analysis not found.")
