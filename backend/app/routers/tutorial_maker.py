from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db
from ..services import piped, ytsearch
from vendor.tutorialmaker.pipeline import captions, docx_builder, frames, search, sentiment, transcribe, tutorial

router = APIRouter(prefix="/tutorial-maker", tags=["tutorial-maker"])

# Comment sentiment a video must clear before we spend a download on it. `positive_share`
# is the fraction of fetched comments the classifier scores positive, and neutral comments
# (questions, "first", timestamps) count against that share — so this sits well below half
# deliberately. It is a floor against documenting a badly-received video, not a quality bar.
DEFAULT_MIN_SENTIMENT = 0.35


class GenerateTutorialRequest(BaseModel):
    topic: str
    primaryKeyword: str = ""
    secondaryKeyword: str = ""
    contentBrief: str = ""
    maxScreenshots: int = 6
    hfToken: str = ""
    youtubeApiKey: str = ""
    minSentiment: float = DEFAULT_MIN_SENTIMENT


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
    # Why there are no screenshots, when the sentiment floor is what stopped them.
    sentimentNote: str | None = None


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

    # Search tiers, in order of how much has to be working for them to answer:
    #
    # 1. yt-dlp's own search — no third-party instance, no API quota, and it exits from
    #    this machine, the same egress the download stage uses.
    # 2. Piped, instance chosen automatically (services/piped.py). Kept because it takes
    #    the request off this IP entirely, but demoted: a live probe of all fifteen
    #    documented instances found one still serving its API, so it cannot be the tier
    #    everything depends on.
    # 3. The vendored tiers — direct scrape, then Data API search.list, then the Space.
    videos: list[dict] = []
    for label, fetch in (
        ("yt-dlp", lambda: ytsearch.search(body.topic)),
        ("Piped", lambda: piped.search(body.topic)[0]),
    ):
        try:
            videos = fetch()
            print(f"[tutorial-maker] search via {label} ({len(videos)} results)")
            break
        except Exception as err:  # noqa: BLE001
            print(f"[tutorial-maker] {label} search unavailable, falling back: {err}")

    # No proxy on the vendored tiers either: the desktop backend already runs on the
    # user's own machine, so the direct scrape exits from their residential IP natively.
    # The API key still enables the Data API fallback and the videos.list enrichment below.
    if not videos:
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
    # None means "never scored" (no key, or the ranking failed) — which must not be
    # confused with "scored badly": the download gate below only fires on a real score.
    sentiment_score: float | None = None
    if api_key:
        try:
            best, _scored = sentiment.rank_by_sentiment(videos, api_key)
            sentiment_score = best.get("positive_share")
            print(
                f"[tutorial-maker] sentiment winner {best['video_id']}: "
                f"{sentiment_score:.0%} positive over {best.get('n_comments', 0)} comments"
            )
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

    # Screenshots need the video itself, so this is the one stage that downloads anything.
    # Two things gate it:
    #
    # 1. Sentiment. A video whose comments came back below `minSentiment` is not worth the
    #    bandwidth or the request to YouTube, so it yields a text-only tutorial. Skipped
    #    when nothing scored it — an absent score is not a bad one.
    # 2. Where the download comes from. capture_shots is called with no proxy, so yt-dlp
    #    resolves and fetches from this machine over the user's own connection. That is
    #    deliberate: a shared datacenter egress is what actually collects rate limits and
    #    blocks, and the Space-era YT_PROXY/YT_MEDIA_PROXY hops do not apply on desktop.
    selected: dict[int, dict] = {}
    sentiment_note: str | None = None
    if sentiment_score is not None and sentiment_score < body.minSentiment:
        sentiment_note = (
            f"No screenshots: viewers rated the best match {sentiment_score:.0%} positive "
            f"across {best.get('n_comments', 0)} comments, below the {body.minSentiment:.0%} "
            f"floor, so the video wasn't downloaded."
        )
        print(f"[tutorial-maker] {sentiment_note}")
    else:
        # Best-effort — a failure here (blocked download, no ffmpeg, etc.) should still
        # produce a text-only tutorial rather than fail the whole request.
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
        sentimentNote=sentiment_note,
    )
