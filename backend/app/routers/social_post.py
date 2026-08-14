"""Social Post Generator — writes posts grounded in what actually performed.

Wraps vendor/socialpost, which is the standalone project unmodified. Everything
here is a thin translation layer: HTTP in, the vendored package's own functions
out. The vendored package reads its configuration from the environment, which
app/config.py points at a per-user env file in DATA_DIR and the Settings screen
writes (see routers/settings.py).

Jobs run IN-PROCESS rather than as subprocesses. The standalone project shells out
to `python -m ...` to reclaim the embedding model's memory, but a packaged build
has no interpreter to shell out to — sys.executable is the bundled backend exe.

The learning loop (collect posts -> measure engagement -> rebuild exemplars) needs
to run on a schedule for the tool to improve, so a small catch-up scheduler thread
starts with the backend. It is inert until the user has configured credentials.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..services import brand_voice, niche_firstfill
from ..services.genqueue import queue_slot
from PIL import Image
from pydantic import BaseModel

from .. import config, db
from ..brandforge.client import BrandForgeError, text_to_image

log = logging.getLogger(__name__)

router = APIRouter(prefix="/social-post", tags=["social-post"])

PLATFORMS = ["bluesky", "x", "linkedin", "mastodon"]

# Earlier attempts to show the model when it is asked to try again. Three is
# enough to break it out of its preferred opening; more just spends prompt budget
# on drafts nobody kept.
MAX_AVOID_TEXTS = 3

# How far back a cold-start ingest reaches. Just past snapshot.py's BACKFILL_MIN_AGE of 50h,
# so the posts it collects are immediately eligible for an approximate 48h snapshot rather
# than having to age into one.
BOOTSTRAP_UNTIL_HOURS = 52


# --- lazy imports ----------------------------------------------------------
# The vendored package pulls in torch via sentence-transformers, so importing it
# at module scope would add seconds to backend startup for users who never open
# this tool. Every handler resolves it through here instead.
def _spg():
    from vendor.socialpost.src import config as spg_config
    from vendor.socialpost.src import db as spg_db
    from vendor.socialpost.src import generation, telemetry

    return spg_db, generation, telemetry, spg_config


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NicheOut(BaseModel):
    name: str
    keywords: list[str]
    active: bool
    posts: int
    exemplars: int
    authors: int
    generations: int


class SaveNicheRequest(BaseModel):
    name: str
    keywords: list[str]
    active: bool = True


class RenameNicheRequest(BaseModel):
    newName: str


class GenerateRequest(BaseModel):
    userInput: str
    niche: str
    platform: str = "bluesky"
    sourceUrl: str = ""
    # Optional Library id of a Brand Studio document. Folded into user_input, and in its
    # compact form: a full voice card would outweigh the author's own instruction at
    # post length, and crowd the exemplars the generator ranks on.
    brandVoiceId: str = ""
    # Posts already written for this request, sent when the caller is asking for
    # another attempt. Empty for a first generation. Capped server-side so a
    # client cannot grow the prompt without bound.
    avoidTexts: list[str] = []


class ExemplarOut(BaseModel):
    id: int
    postUri: str
    text: str
    similarity: float
    score: float
    webUrl: str


class KbOut(BaseModel):
    id: int
    source: str
    url: str
    summary: str
    decayWeight: float


class SourceOut(BaseModel):
    url: str
    title: str
    excerpt: str
    truncated: bool


class GenerateResponse(BaseModel):
    text: str
    generationId: int | None
    characters: int
    overLimit: bool
    exemplars: list[ExemplarOut]
    kbArticles: list[KbOut]
    libraryId: str | None = None
    source: SourceOut | None = None


class GenerateImageRequest(BaseModel):
    postText: str
    niche: str = ""
    platform: str = "bluesky"
    hfToken: str = ""
    modalTokenId: str = ""
    modalTokenSecret: str = ""
    useModal: bool = False


class GenerateImageResponse(BaseModel):
    url: str
    promptUsed: str
    width: int
    height: int


class PublishedRequest(BaseModel):
    generationId: int
    postedUri: str
    niche: str


class StatusResponse(BaseModel):
    configured: bool
    missing: list[str]
    backend: str
    provider: str
    model: str
    niches: int
    posts: int
    exemplars: int
    readyToGround: bool
    telemetryEnabled: bool
    needsConsent: bool


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _missing_credentials() -> list[str]:
    """Which settings still need filling in before the tool actually works."""
    import os

    spg_db, _, _, _ = _spg()
    missing: list[str] = []

    # Hugging Face is the only generation provider this app configures. The vendored
    # project can also talk to a hosted Gemini endpoint, but that would mean a second
    # paid account and a second key for no gain, so the app pins LLM_PROVIDER=hf.
    if not (os.environ.get("HF_TOKEN") or "").strip():
        missing.append("HF_TOKEN")

    # Bluesky powers collection/measurement, not generation — a user can write
    # posts before connecting it, just without grounding.
    for name in ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"):
        if not (os.environ.get(name) or "").strip():
            missing.append(name)

    # No hosted-database branch: this app is single-user and entirely local, so the
    # vendored project's Supabase backend is never selected (see config.py, which pins
    # DB_BACKEND=sqlite). Nothing here needs a server to be reachable.
    return missing


@router.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    import os

    spg_db, _, telemetry, _ = _spg()
    from vendor.socialpost.src import llm as spg_llm

    def _count(table: str) -> int:
        try:
            return (
                spg_db.get_client().table(table).select("*", count="exact").limit(0).execute().count
                or 0
            )
        except Exception:  # noqa: BLE001 — an unconfigured DB should read as zero, not 500
            return 0

    try:
        niches = len(spg_db.load_niches())
    except Exception:  # noqa: BLE001
        niches = 0

    exemplars = _count("exemplars")
    missing = _missing_credentials()

    return StatusResponse(
        configured=not [m for m in missing if m.startswith("HF_")],
        missing=missing,
        backend=spg_db.backend(),
        provider=spg_llm.provider(),
        model=spg_llm.model_name(),
        niches=niches,
        posts=_count("posts"),
        exemplars=exemplars,
        readyToGround=exemplars > 0,
        telemetryEnabled=telemetry.is_enabled(),
        needsConsent=telemetry.needs_consent(),
    )


# ---------------------------------------------------------------------------
# Niches
# ---------------------------------------------------------------------------


@router.get("/niches", response_model=list[NicheOut])
def list_niches() -> list[NicheOut]:
    spg_db, _, _, _ = _spg()
    out = []
    for row in spg_db.list_niches():
        counts = spg_db.niche_data_counts(row["name"])
        out.append(
            NicheOut(
                name=row["name"],
                keywords=row["keywords"],
                active=bool(row["active"]),
                posts=counts["posts"],
                exemplars=counts["exemplars"],
                authors=counts["authors"],
                generations=counts["generations"],
            )
        )
    return out


@router.post("/niches")
def save_niche(body: SaveNicheRequest) -> dict:
    """Create or update a niche. A creation also queues its first fill.

    Existence is checked *before* the save because save_niche upserts, so afterwards
    there is no way to tell a new niche from an edited one. Only creations are filled:
    editing an existing niche's keywords leaves a corpus already in place, and the
    scheduled ingest picks the new terms up on its next pass, whereas a new niche has
    nothing at all and is the case where waiting six hours reads as breakage.
    """
    spg_db, _, _, _ = _spg()
    existed = spg_db.get_niche(body.name) is not None
    try:
        saved = spg_db.save_niche(body.name, body.keywords, active=body.active)
    except spg_db.NicheError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None

    first_fill = None
    if not existed and saved["active"]:
        first_fill = niche_firstfill.enqueue(saved["name"])

    return {
        "name": saved["name"],
        "weakKeywords": spg_db.weak_keywords(saved["keywords"]),
        "firstFill": first_fill,
    }


@router.get("/niches/first-fill")
def first_fill_status() -> dict:
    """Progress of the fills queued by niche creation.

    Separate from /niches because those counts come from the database and only move
    when a fill finishes; this says whether one is still running, which is the
    difference between "this niche is empty" and "this niche is not filled yet".
    """
    return {"pending": niche_firstfill.pending(), "fills": niche_firstfill.status()}


@router.post("/niches/{name}/rename")
def rename_niche(name: str, body: RenameNicheRequest) -> dict:
    spg_db, _, _, _ = _spg()
    try:
        moved = spg_db.rename_niche(name, body.newName)
    except spg_db.NicheError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    return {"moved": moved, "total": sum(moved.values())}


@router.delete("/niches/{name}")
def delete_niche(name: str, purge: bool = False) -> dict:
    spg_db, _, _, _ = _spg()
    try:
        return {"removed": spg_db.delete_niche(name, purge_data=purge)}
    except spg_db.NicheError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None


def _backfill_48h_for_niche(name: str) -> int:
    """Approximate 48h snapshots for one niche's posts. Returns how many were written.

    The vendored snapshot.backfill_48h does the same thing globally: it selects posts
    aged 50h-7d across every niche, capped at MAX_POSTS_PER_BUCKET (500) with no
    ordering. That is right for the hourly job and wrong for a cold start. Measured on
    this install, 2,825 posts sit in that window while a newly added niche contributes
    four, so the cap crowds the new ones out and step 3 finds nothing to rank: the
    niche came back "8 posts · 0 exemplars", which is the exact failure the bootstrap
    recipe exists to prevent.

    Scoping the query to the niche is the whole difference. Everything else — the age
    window, the live re-fetch, the follower-normalised rate — is the vendored job's,
    reused rather than reimplemented so the two cannot drift, and the vendored job
    itself stays untouched and keeps running for every other niche.
    """
    from vendor.socialpost.src.bluesky import get_posts
    from vendor.socialpost.src.db import JobRun, get_client, insert, iso, utcnow
    from vendor.socialpost.src.jobs.snapshot import (
        BACKFILL_MAX_AGE,
        BACKFILL_MIN_AGE,
        MAX_POSTS_PER_BUCKET,
        _follower_counts,
        engagement_rate,
    )

    now = utcnow()
    with JobRun("snapshot_backfill") as job:
        candidates = (
            get_client()
            .table("posts")
            .select("uri, author_did, created_at")
            .eq("niche", name)
            .lte("created_at", iso(now - BACKFILL_MIN_AGE))
            .gte("created_at", iso(now - BACKFILL_MAX_AGE))
            .limit(MAX_POSTS_PER_BUCKET)
            .execute()
            .data
            or []
        )
        if not candidates:
            job.note(f"backfill: {name!r} has no posts aged 50h-7d")
            return 0

        # A post can already carry a real capture if the niche was collected before.
        # A measurement must never be overwritten with an estimate.
        uris = [c["uri"] for c in candidates]
        done = {
            row["post_uri"]
            for i in range(0, len(uris), 100)
            for row in (
                get_client()
                .table("engagement_snapshots")
                .select("post_uri")
                .eq("window_label", "48h")
                .in_("post_uri", uris[i : i + 100])
                .execute()
                .data
                or []
            )
        }
        due = [c for c in candidates if c["uri"] not in done]
        if not due:
            job.note(f"backfill: {name!r} already has 48h snapshots")
            return 0

        live = get_posts([p["uri"] for p in due])
        followers = _follower_counts({p["author_did"] for p in due if p["author_did"]})

        rows = []
        for candidate in due:
            post = live.get(candidate["uri"])
            if post is None:
                # Deleted since ingest, or the author blocked us. Skipping leaves the
                # row unmeasured, which is honest; a zero would read as "nobody cared".
                job.count("backfill_unavailable")
                continue
            rows.append(
                {
                    "post_uri": post.uri,
                    "captured_at": iso(now),
                    "window_label": "48h",
                    "likes": post.likes,
                    "reposts": post.reposts,
                    "replies": post.replies,
                    "engagement_rate": engagement_rate(
                        post.likes,
                        post.reposts,
                        post.replies,
                        followers.get(candidate["author_did"], 0),
                    ),
                }
            )

        written = insert("engagement_snapshots", rows)
        job.count("backfilled_48h", written)
        job.note(f"backfill: {written} approximate 48h snapshots for {name!r}")
        return written


@router.post("/niches/{name}/collect")
def collect_niche(name: str, limit: int = 25) -> dict:
    """Collect posts for a niche, and bootstrap its exemplar pool if it has none.

    A plain ingest is not enough for a niche that was just added. Exemplars are
    ranked on a post's 48h engagement, and `ingest` deliberately refuses anything
    older than 44h (MAX_POST_AGE) so every post it stores still has a real 48h
    bucket ahead of it. On a new niche that means nothing qualifies for roughly two
    days: the niche shows "952 posts · 0 exemplars" and generation silently falls
    back to platform norms, with no indication that waiting is all that's required.

    So a cold start runs the bootstrap recipe the vendored jobs document but no
    caller was using — ingest.py's own comment describes it and explains why the
    two halves must be used together:

      1. ingest with max_age=168h, reaching into the 50h-7d window that the normal
         44h ceiling excludes. Without this, step 2 matches nothing.
      2. a 48h backfill, which records those older posts' current counts as an
         approximate 48h snapshot. Engagement has plateaued by 48h, so on a post
         already days old the current count is close to what a real capture would
         have recorded. Scoped to this niche — see _backfill_48h_for_niche.
      3. refresh_exemplars for this niche, so the pool exists on return rather than
         at some point in the next 24 hours.

    Only on a cold start. An established niche keeps the plain 44h ingest: its posts
    are getting real 48h captures on schedule, and approximating them would replace
    measurements with estimates for no gain.
    """
    from datetime import datetime, timedelta, timezone

    from vendor.socialpost.src.jobs import ingest, refresh_exemplars

    spg_db, _, _, _ = _spg()
    cold_start = spg_db.niche_data_counts(name).get("exemplars", 0) == 0

    try:
        if cold_start:
            # `until` is what actually reaches back; max_age only stops old posts being
            # rejected once they arrive. sort='latest' returns the newest matches, so on an
            # active keyword every result is hours old however high the ceiling — measured:
            # a new "literature" niche collected 103 posts spanning 1h41m, none older than
            # 48h, so the backfill matched nothing and the pool stayed empty. Asking for
            # posts *older than* 50h lands squarely in the 50h-7d window backfill_48h wants.
            ingest.run(
                only_niche=name,
                limit=limit,
                max_age=timedelta(hours=ingest.BOOTSTRAP_MAX_AGE_HOURS),
                until=datetime.now(timezone.utc) - timedelta(hours=BOOTSTRAP_UNTIL_HOURS),
            )
            # Then a normal pass for recent posts, so the niche is not seeded exclusively
            # with days-old material. These age into their own real 48h snapshots later.
            ingest.run(only_niche=name, limit=limit)
            _backfill_48h_for_niche(name)
            refresh_exemplars.run(only_niche=name)
        else:
            ingest.run(only_niche=name, limit=limit)
    except SystemExit as err:  # the job raises SystemExit for an unknown niche
        raise HTTPException(status_code=400, detail=str(err)) from None
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Collection failed: {err}") from None

    counts = spg_db.niche_data_counts(name)
    return {"posts": counts["posts"], "exemplars": counts["exemplars"]}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _bsky_web_url(at_uri: str) -> str:
    try:
        _, _, rest = at_uri.partition("at://")
        did, _, tail = rest.partition("/")
        return f"https://bsky.app/profile/{did}/post/{tail.rsplit('/', 1)[-1]}"
    except Exception:  # noqa: BLE001
        return ""


_SOCIAL_IMAGE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "bluesky": (1200, 672),
    "x": (1200, 672),
    "linkedin": (1200, 1200),
    "mastodon": (1024, 1024),
}


def _social_image_prompt(post_text: str, niche: str, platform: str) -> str:
    """Turn a post draft into an image direction without asking the model for text.

    Image models remain unreliable at typesetting. The composer therefore gets a
    clear visual that communicates the post's idea, with deliberately calm space
    for a marketer to add a real headline afterwards.
    """
    return " ".join(
        part
        for part in (
            "Create an original editorial social-media image for a marketing post.",
            f"Platform: {platform}.",
            f"Audience niche: {niche}." if niche.strip() else "",
            "Communicate the post's central idea visually with a polished, specific composition.",
            "No lettering, no logo, no watermark, no UI mockup, no collage of screenshots.",
            "Leave a calm, uncluttered focal area suitable for real text to be added later.",
            f"Post to illustrate: {post_text.strip()[:1800]}",
        )
        if part
    )


def _social_image_path() -> Path:
    run_dir = config.OUTPUTS_DIR / "social" / str(uuid.uuid4())
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / "post-image.png"


def _outputs_url(path: Path) -> str:
    return "/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()


def _modal_runtime():
    try:
        from ..brandforge import modal_runtime
    except ImportError as err:
        raise HTTPException(
            status_code=500,
            detail="The Modal SDK isn't installed in this build, so a personal GPU backend can't be used.",
        ) from err
    return modal_runtime


@router.post("/generate", response_model=GenerateResponse, dependencies=[Depends(queue_slot("model"))])
def generate(body: GenerateRequest) -> GenerateResponse:
    if not body.userInput.strip():
        raise HTTPException(status_code=400, detail="Tell it what to post about first.")

    _, generation, telemetry, _ = _spg()
    from vendor.socialpost.src import sources as spg_sources

    try:
        result = generation.generate(
            user_input=brand_voice.apply_voice(body.userInput, body.brandVoiceId, compact=True),
            niche=body.niche,
            platform=body.platform,
            source_url=body.sourceUrl,
            # Bounded here rather than trusting the caller: each attempt is up to
            # 300 characters and they all ride in the prompt, so an unbounded list
            # would quietly eat the generation budget.
            avoid_texts=[t for t in body.avoidTexts if t.strip()][-MAX_AVOID_TEXTS:],
        )
    except telemetry.ConsentRequired as err:
        # 409: the caller must show the consent screen, not treat this as an error.
        raise HTTPException(status_code=409, detail=str(err)) from None
    except spg_sources.SourceError as err:
        # 400, not 502: a bad link is the caller's input, not an upstream failure.
        raise HTTPException(status_code=400, detail=str(err)) from None
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(err)) from None

    item = db.add_item(
        tool="Social",
        title=result.text[:70] + ("…" if len(result.text) > 70 else ""),
        subtitle=f"{body.platform} · {body.niche}",
        content=result.text,
    )

    return GenerateResponse(
        text=result.text,
        generationId=result.generation_id,
        characters=len(result.text),
        overLimit=body.platform == "bluesky" and len(result.text) > 300,
        exemplars=[
            ExemplarOut(
                id=e.id,
                postUri=e.post_uri,
                text=e.text,
                similarity=round(e.similarity, 3),
                score=round(e.score, 5),
                webUrl=_bsky_web_url(e.post_uri),
            )
            for e in result.exemplars
        ],
        kbArticles=[
            KbOut(
                id=k.id,
                source=k.source,
                url=k.url,
                summary=k.summary,
                decayWeight=round(k.decay_weight, 2),
            )
            for k in result.kb_articles
        ],
        libraryId=item["id"],
        source=(
            SourceOut(
                url=result.source.url,
                title=result.source.title,
                excerpt=result.source.text[:600],
                truncated=result.source.truncated,
            )
            if result.source
            else None
        ),
    )


@router.post("/images", response_model=GenerateImageResponse, dependencies=[Depends(queue_slot("image"))])
def generate_image(body: GenerateImageRequest) -> GenerateImageResponse:
    """Create a visual companion for a generated post.

    Modal is selected after Settings has successfully provisioned it. The HF
    provider remains available before that, matching BrandForge's behavior.
    """
    if not body.postText.strip():
        raise HTTPException(status_code=400, detail="Generate or paste a post before creating its image.")
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account in Settings.")
    if body.platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {body.platform}")

    prompt = _social_image_prompt(body.postText, body.niche, body.platform)
    width, height = _SOCIAL_IMAGE_DIMENSIONS[body.platform]
    use_modal = bool(body.useModal and body.modalTokenId.strip() and body.modalTokenSecret.strip())

    try:
        if use_modal:
            runtime = _modal_runtime()
            modal_cfg = runtime.ModalConfig(
                token_id=body.modalTokenId.strip(),
                token_secret=body.modalTokenSecret.strip(),
                hf_token=body.hfToken.strip(),
            )
            png = runtime.generate_image(modal_cfg, prompt, width, height)
            with Image.open(io.BytesIO(png)) as response_image:
                response_image.load()
                image = response_image.copy()
        else:
            image = text_to_image(body.hfToken, prompt)
    except BrandForgeError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    except (OSError, ValueError) as err:
        raise HTTPException(status_code=502, detail=f"The image backend returned invalid PNG data: {err}") from err

    path = _social_image_path()
    image.save(path)
    return GenerateImageResponse(
        url=_outputs_url(path),
        promptUsed=prompt,
        width=width,
        height=height,
    )


@router.post("/published")
def mark_published(body: PublishedRequest) -> dict:
    """Close the learning loop: link a published post to the draft that made it."""
    _, generation, _, _ = _spg()
    try:
        uri = generation.attach_posted_uri(
            generation_id=body.generationId, posted_uri=body.postedUri, niche=body.niche
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(err)) from None
    return {"postedUri": uri}


# ---------------------------------------------------------------------------
# Telemetry consent
# ---------------------------------------------------------------------------


class ConsentRequest(BaseModel):
    contentOptIn: bool = False


@router.get("/telemetry/preview")
def telemetry_preview(contentOptIn: bool = False) -> dict:
    _, _, telemetry, _ = _spg()
    return telemetry.preview_payloads(contentOptIn)


@router.post("/telemetry/consent")
def telemetry_consent(body: ConsentRequest) -> dict:
    _, _, telemetry, _ = _spg()
    telemetry.record_consent(content_opt_in=body.contentOptIn)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Background learning loop
# ---------------------------------------------------------------------------

# Same cadences as the standalone scheduler. Catch-up semantics (run when the
# interval has elapsed since the last run, recorded in the vendored package's own
# job_runs table) mean a laptop that was asleep resumes correctly instead of
# either skipping or stampeding.
_SCHEDULE: tuple[tuple[str, timedelta], ...] = (
    ("ingest", timedelta(hours=6)),
    ("snapshot", timedelta(hours=1)),
    ("refresh_exemplars", timedelta(days=1)),
    ("ingest_kb", timedelta(days=1)),
    ("telemetry", timedelta(hours=6)),
    ("cleanup", timedelta(days=7)),
)

_TICK_SECONDS = 300
_scheduler_thread: threading.Thread | None = None


def _last_run(job_name: str):
    from datetime import datetime

    spg_db, _, _, _ = _spg()
    rows = (
        spg_db.get_client()
        .table("job_runs")
        .select("started_at")
        .eq("job_name", job_name)
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return datetime.fromisoformat(rows[0]["started_at"]) if rows else None


def _run_due_jobs() -> None:
    from importlib import import_module

    spg_db, _, _, _ = _spg()
    now = spg_db.utcnow()

    for name, every in _SCHEDULE:
        try:
            last = _last_run(name)
            if last is not None and (now - last) < every:
                continue
            module = import_module(f"vendor.socialpost.src.jobs.{name}")
            log.info("[social-post] running %s", name)
            module.run()
        except Exception:  # noqa: BLE001 — one bad job must not stop the loop
            log.exception("[social-post] job %s failed", name)


def _scheduler_loop() -> None:
    while True:
        try:
            # Do nothing until the user has actually configured the tool; an
            # unconfigured install should be silent, not a log of failures.
            if not _missing_credentials():
                _run_due_jobs()
        except Exception:  # noqa: BLE001
            log.exception("[social-post] scheduler tick failed")
        time.sleep(_TICK_SECONDS)


def start_scheduler() -> None:
    """Start the learning loop in the background. Safe to call once at startup."""
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="social-post-scheduler", daemon=True
    )
    _scheduler_thread.start()
    log.info("[social-post] learning-loop scheduler started")
