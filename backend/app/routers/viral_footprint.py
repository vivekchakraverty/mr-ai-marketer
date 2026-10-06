"""Upload and inspect a public-video comparison job from Align > Videos."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..services.viral_footprint import jobs, public_search

router = APIRouter(prefix="/align/videos", tags=["align-videos"])
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


@router.get("/source")
def source_status() -> dict:
    return public_search.status()


@router.post("/analyze", status_code=202)
async def analyze_video(file: UploadFile = File(...), search_terms: str = Form(""), reference_url: str = Form("")) -> dict:
    reference_url = reference_url.strip()
    if reference_url:
        try:
            video_id = public_search.reference_id(reference_url)
            reference_url = f"https://www.youtube.com/watch?v={video_id}"
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(file.filename or "video").name)[:120]
    if Path(filename).suffix.lower() not in EXTENSIONS:
        raise HTTPException(400, "Upload an MP4, MOV, WebM, MKV, or M4V video.")
    if file.content_type and not (file.content_type.startswith("video/") or file.content_type == "application/octet-stream"):
        raise HTTPException(400, "The selected file is not a video.")
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="viral-footprint-upload-", suffix=Path(filename).suffix, delete=False) as output:
            path = Path(output.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Videos must be at most 200 MB.")
                output.write(chunk)
        if not size:
            raise HTTPException(400, "That video is empty.")
        job_id = jobs.submit(path, filename, search_terms.strip()[:160], reference_url)
        path = None  # worker owns the temporary file and deletes it
        return {"analysis_id": job_id, "status": "queued"}
    except RuntimeError as exc:
        raise HTTPException(429, str(exc)) from None
    finally:
        await file.close()
        if path:
            path.unlink(missing_ok=True)


@router.get("/{analysis_id}")
def analysis(analysis_id: str) -> dict:
    result = jobs.get(analysis_id)
    if result is None:
        raise HTTPException(404, "Video analysis not found.")
    return result
