from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from ..services.genqueue import queue_slot
from pydantic import BaseModel

from .. import config, db
from vendor.docmaker.src import docx_export
from vendor.docmaker.src import frames as doc_frames
from vendor.docmaker.src import guide as doc_guide
from vendor.docmaker.src import llm as doc_llm
from vendor.docmaker.src import transcribe as doc_transcribe
from vendor.docmaker.src import video as doc_video

router = APIRouter(prefix="/docu-maker", tags=["docu-maker"])


class GuideStepOut(BaseModel):
    heading: str
    text: str
    timestamp: float | None = None
    imageUrl: str | None = None
    caption: str | None = None


class GenerateDocuResponse(BaseModel):
    title: str
    intro: str
    prerequisites: list[str]
    steps: list[GuideStepOut]
    docxPath: str | None
    docxUrl: str | None
    libraryId: str


def _outputs_url(path: Path) -> str:
    return "/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()


@router.post("/generate", response_model=GenerateDocuResponse, dependencies=[Depends(queue_slot("model"))])
async def generate_docu(
    video: UploadFile,
    product: str = Form(""),
    hfToken: str = Form(""),
) -> GenerateDocuResponse:
    if not hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account.")

    run_id = str(uuid.uuid4())
    run_dir = config.OUTPUTS_DIR / "docu" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    video_ext = Path(video.filename or "video.mp4").suffix or ".mp4"
    video_path = run_dir / f"source{video_ext}"
    contents = await video.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded video is empty.")
    video_path.write_bytes(contents)

    try:
        wav_path = run_dir / "audio.wav"
        doc_video.extract_audio(video_path, wav_path)
        transcript = doc_transcribe.transcribe(wav_path)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transcription failed: {err}") from err

    try:
        draft = doc_llm.build_guide_draft(transcript, token=hfToken)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Guide drafting failed: {err}") from err

    step_timestamps = [s.approx_timestamp for s in draft.steps if s.approx_timestamp is not None]
    spoken_intervals = [(s.start, s.end) for s in transcript.segments] or None
    spoken_range = (
        (transcript.segments[0].start, transcript.segments[-1].end) if transcript.segments else None
    )

    # Frame extraction/matching is best-effort — a failure here should still produce a
    # text-only guide rather than fail the whole request.
    pool = []
    try:
        pool = doc_frames.extract_auto_frames(
            video_path, run_dir, spoken_intervals=spoken_intervals, step_timestamps=step_timestamps or None
        )
    except Exception as err:  # noqa: BLE001
        print(f"[docu-maker] frame extraction failed, continuing without images: {err}")

    guide_obj = doc_guide.assemble_guide(
        draft, pool, video_path=video_path, session_dir=run_dir, do_caption=True, token=hfToken, spoken_range=spoken_range
    )

    slug = (product or guide_obj.title or "guide").strip().lower().replace(" ", "-")[:60] or "guide"
    docx_out_path = run_dir / f"{slug}.docx"
    docx_path: str | None = None
    docx_url: str | None = None
    try:
        docx_export.export_docx(guide_obj, docx_out_path)
        docx_path = str(docx_out_path)
        docx_url = _outputs_url(docx_out_path)
    except Exception as err:  # noqa: BLE001
        print(f"[docu-maker] docx export failed: {err}")

    steps_out = [
        GuideStepOut(
            heading=s.heading,
            text=s.text,
            timestamp=s.timestamp,
            imageUrl=_outputs_url(Path(s.image_path)) if s.image_path else None,
            caption=s.caption,
        )
        for s in guide_obj.steps
    ]

    title = f"{product} — {guide_obj.title}" if product.strip() else guide_obj.title
    item = db.add_item(tool="Docs", title=title, subtitle="Documentation", content=guide_obj.intro, output_path=docx_path)
    return GenerateDocuResponse(
        title=title,
        intro=guide_obj.intro,
        prerequisites=guide_obj.prerequisites,
        steps=steps_out,
        docxPath=docx_path,
        docxUrl=docx_url,
        libraryId=item["id"],
    )
