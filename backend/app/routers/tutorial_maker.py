from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db
from vendor.tutorialmaker.pipeline import captions, docx_builder, frames, search, sentiment, transcribe, tutorial

router = APIRouter(prefix="/tutorial-maker", tags=["tutorial-maker"])


class GenerateTutorialRequest(BaseModel):
    topic: str
    primaryKeyword: str = ""
    secondaryKeyword: str = ""
    contentBrief: str = ""
    maxScreenshots: int = 6
    hfToken: str = ""
    youtubeApiKey: str = ""


class TutorialStep(BaseModel):
    heading: str
    body: str
    timestamp: float | None = None
    imageUrl: str | None = None
    caption: str | None = None


class GenerateTutorialResponse(BaseModel):
    title: str
    intro: str
    answer: str
    steps: list[TutorialStep]
    faqs: list[dict]
    sourceUrl: str | None
    docxPath: str | None
    docxUrl: str | None
    libraryId: str


def _outputs_url(path) -> str:
    return "/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()


@router.post("/generate", response_model=GenerateTutorialResponse)
def generate_tutorial_endpoint(body: GenerateTutorialRequest) -> GenerateTutorialResponse:
    if not body.topic.strip():
        raise HTTPException(status_code=400, detail="Topic is required.")
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account.")

    run_id = str(uuid.uuid4())
    run_dir = config.OUTPUTS_DIR / "tutorial" / run_id
    frames_dir = run_dir / "frames"
    run_dir.mkdir(parents=True, exist_ok=True)

    api_key = body.youtubeApiKey.strip() or None
    # No proxy here: the desktop backend already runs on the user's own machine, so the
    # tier-1 scrape exits from their residential IP natively. The API key still enables
    # the tier-2 fallback and the videos.list enrichment below.
    try:
        videos = search.search_top5(body.topic, api_key=api_key)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(err)) from err
    if not videos:
        raise HTTPException(status_code=502, detail=f"No videos found for '{body.topic}' — try a broader topic.")

    # Drop Shorts and caption-less videos before spending sentiment quota and a transcript
    # fetch on them. Best-effort: without a key there are no stats to filter on.
    try:
        videos, notes = search.enrich_and_filter(videos, api_key)
        for note in notes:
            print(f"[tutorial-maker] {note}")
    except Exception as err:  # noqa: BLE001
        print(f"[tutorial-maker] candidate filtering skipped: {err}")

    # Sentiment ranking is an optional tier — only runs if the user configured a
    # YouTube Data API key in Settings; otherwise fall back to the top search result,
    # exactly like the source pipeline does when no key is configured.
    best = videos[0]
    if api_key:
        try:
            best, _scored = sentiment.rank_by_sentiment(videos, api_key)
        except Exception as err:  # noqa: BLE001
            print(f"[tutorial-maker] sentiment ranking failed, falling back to top result: {err}")

    try:
        segs = transcribe.get_segments(best["video_id"])
        transcript = transcribe.transcript_text(segs)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(err)) from err

    keywords = {
        "primary": body.primaryKeyword,
        "secondary": [body.secondaryKeyword] if body.secondaryKeyword.strip() else [],
    }
    try:
        tut = tutorial.generate_tutorial(
            transcript, body.hfToken, keywords=keywords, brief=body.contentBrief,
            comments=best.get("comments") or [],
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(err)) from err

    # Screenshots are best-effort — a failure here (blocked download, no ffmpeg, etc.)
    # should still produce a text-only tutorial rather than fail the whole request.
    selected: dict[int, dict] = {}
    try:
        times = frames.compute_shot_times(tut["steps"], segs, max_shots=body.maxScreenshots)
        if times:
            selected = frames.capture_shots(times, best["video_id"], str(frames_dir))
    except Exception as err:  # noqa: BLE001
        print(f"[tutorial-maker] screenshot capture failed, continuing without images: {err}")

    caption_map: dict[int, str] = {}
    if selected:
        try:
            caption_map = captions.caption_frames(selected, tut["steps"], body.hfToken)
        except Exception as err:  # noqa: BLE001
            print(f"[tutorial-maker] captioning failed, continuing without captions: {err}")

    slug = tut.get("slug") or "tutorial"
    docx_out_path = run_dir / f"{slug}.docx"
    docx_path: str | None = None
    docx_url: str | None = None
    try:
        docx_builder.build_docx(tut, selected, caption_map, str(docx_out_path), source_url=best["url"])
        docx_path = str(docx_out_path)
        docx_url = _outputs_url(docx_out_path)
    except Exception as err:  # noqa: BLE001
        print(f"[tutorial-maker] docx build failed: {err}")

    steps: list[TutorialStep] = []
    for i, step in enumerate(tut["steps"]):
        sel = selected.get(i)
        image_url = None
        if sel and sel.get("path"):
            image_url = _outputs_url(Path(sel["path"]))
        steps.append(
            TutorialStep(
                heading=step["heading"],
                body=step["body"],
                timestamp=sel["time"] if sel else step.get("t_llm"),
                imageUrl=image_url,
                caption=caption_map.get(i),
            )
        )

    item = db.add_item(tool="Tutorial", title=tut["title"], subtitle="Tutorial", content=tut.get("intro"), output_path=docx_path)
    return GenerateTutorialResponse(
        title=tut["title"],
        intro=tut.get("intro", ""),
        answer=tut.get("answer", ""),
        steps=steps,
        faqs=tut.get("faqs", []),
        sourceUrl=best.get("url"),
        docxPath=docx_path,
        docxUrl=docx_url,
        libraryId=item["id"],
    )
