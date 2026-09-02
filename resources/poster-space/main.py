"""Posts the queue when it is due.

WHAT IS AND IS NOT EXPOSED HERE. The queue lives in a private dataset (see store.py), never
in a request or a response. So this Space's HTTP surface carries no post content, no
schedule and no credentials, and `/tick` — which does the actual work — needs no auth: a
stranger who calls it causes the owner's own due posts to go out, on time, which is what
they were going to do anyway. `/status` reports queue depth and is therefore gated.

TIMING, HONESTLY. On free hardware a Space sleeps after inactivity and cannot configure
that (huggingface_hub's own set_space_sleep_time warns as much on cpu-basic). Three things
push against it: an internal tick every TICK_SECONDS, a self-ping so traffic through HF's
router keeps the Space marked active, and the desktop app calling /tick when it is open.
None of them is a guarantee. What makes that acceptable is that a pass fires everything
whose time has PASSED rather than what is due this minute — so a Space that wakes at 07:00
still posts the 03:00 job. Late is possible; lost is not.
"""

from __future__ import annotations

import logging
import os
import secrets as secretslib
import threading
import time

import httpx
from fastapi import FastAPI, Header, HTTPException

import networks
import store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("poster")

app = FastAPI(title="Mr AI Marketer Poster", docs_url=None, redoc_url=None)

POSTER_KEY = os.environ.get("POSTER_KEY", "").strip()
SELF_URL = os.environ.get("SELF_URL", "").strip()
MASTODON_HOST = os.environ.get("MASTODON_HOST", "").strip()
MASTODON_TOKEN = os.environ.get("MASTODON_TOKEN", "").strip()
BLUESKY_PDS = os.environ.get("BLUESKY_PDS", "").strip() or "https://bsky.social"
BLUESKY_DID = os.environ.get("BLUESKY_DID", "").strip()
BLUESKY_REFRESH_JWT = os.environ.get("BLUESKY_REFRESH_JWT", "").strip()

TICK_SECONDS = 60
SELF_PING_SECONDS = 300
#: A stranger calling /tick repeatedly costs nothing beyond this.
MIN_SECONDS_BETWEEN_PASSES = 20

_lock = threading.Lock()
_last_pass_at = 0.0
_last_sha = ""
_state = {"lastTickAt": "", "lastError": "", "needsBlueskyReauth": False}


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------


def _due(job: dict) -> bool:
    """Everything whose time has passed, not only what is due right now."""
    from datetime import datetime, timezone

    raw = str(job.get("dueAt") or "").replace("Z", "+00:00")
    if not raw:
        return True
    try:
        return datetime.fromisoformat(raw) <= datetime.now(timezone.utc)
    except ValueError:
        # An unparseable time is a job that would otherwise sit in the queue forever.
        return True


def _media_for(job_id: str, job: dict) -> tuple[tuple[str, bytes] | None, list[str]]:
    name = str(job.get("mediaFilename") or "").strip()
    if not name:
        return None, []
    path = store.media_path(job_id, name)
    if path is None:
        raise networks.PostError("The attachment is no longer in the outbox.")
    with open(path, "rb") as handle:
        return (name, handle.read()), [name]


def _post_bluesky(job: dict, media, alt: str, rkey: str) -> str:
    saved = store.bluesky_session()
    refresh = str(saved.get("refreshJwt") or "") or BLUESKY_REFRESH_JWT
    if not refresh:
        raise networks.PostError("Bluesky is not connected for cloud posting.")
    session = networks.refresh_bluesky(BLUESKY_PDS, refresh)
    # Rotated: persist before posting, or a crash mid-post loses the only usable token.
    store.save_bluesky_session(
        {
            "refreshJwt": session.get("refreshJwt", ""),
            "did": session.get("did", BLUESKY_DID),
            "rotatedAt": networks._now_iso(),
        }
    )
    return networks.post_bluesky(
        BLUESKY_PDS,
        str(session.get("did") or BLUESKY_DID),
        str(session.get("accessJwt") or ""),
        str(job.get("text") or ""),
        media,
        alt,
        job.get("aspectRatio") if isinstance(job.get("aspectRatio"), dict) else None,
        rkey,
    )


def run_pass() -> int:
    """Fire everything due. Returns how many jobs reached a terminal state."""
    global _last_sha

    if not store.configured():
        _state["lastError"] = "outbox not configured"
        return 0

    sha = store.head_sha()
    # An idle tick costs one repo_info call. Only skip when a previous pass has run against
    # this exact commit — otherwise a job that became due without the repo changing would
    # never fire.
    if sha and sha == _last_sha and _state["lastTickAt"]:
        return 0

    done = 0
    for job_id in store.list_queue():
        job = store.job(job_id)
        if not job or not _due(job):
            continue
        if store.claimed(job_id):
            # A previous pass died mid-post. The idempotency key makes a retry safe, so let
            # it through rather than stranding the job forever.
            log.info("%s was already claimed; retrying under the same key", job_id)
        elif not store.claim(job_id):
            continue

        channel = str(job.get("channel") or "")
        alt = str(job.get("mediaAlt") or "")
        try:
            media, files = _media_for(job_id, job)
            if channel == "mastodon":
                ref = f"mastodon:{networks.post_mastodon(MASTODON_HOST, MASTODON_TOKEN, str(job.get('text') or ''), media, alt, job_id)}"
            elif channel == "bluesky":
                ref = f"bluesky:{_post_bluesky(job, media, alt, job_id.replace('-', '')[:32])}"
            else:
                raise networks.PostError(f"This Space does not post to {channel or 'that'}.")
            store.finish(job_id, {"status": "sent", "ref": ref, "at": networks._now_iso()}, files)
            log.info("sent %s (%s)", job_id, ref)
        except networks.PostError as err:
            if channel == "bluesky" and "renew the session" in str(err):
                _state["needsBlueskyReauth"] = True
            store.finish(
                job_id,
                {"status": "failed", "error": str(err), "at": networks._now_iso()},
                [],
            )
            log.warning("failed %s: %s", job_id, err)
        except Exception as err:  # noqa: BLE001 - one bad job must not end the pass
            store.finish(
                job_id,
                {"status": "failed", "error": f"Unexpected: {err}", "at": networks._now_iso()},
                [],
            )
            log.exception("unexpected failure on %s", job_id)
        done += 1

    _last_sha = store.head_sha()
    _state["lastTickAt"] = networks._now_iso()
    _state["lastError"] = ""
    return done


def _guarded_pass() -> int:
    """One pass at a time, and not more often than MIN_SECONDS_BETWEEN_PASSES."""
    global _last_pass_at
    with _lock:
        if time.monotonic() - _last_pass_at < MIN_SECONDS_BETWEEN_PASSES:
            return 0
        _last_pass_at = time.monotonic()
        try:
            return run_pass()
        except Exception as err:  # noqa: BLE001 - a bad pass must not kill the thread
            _state["lastError"] = str(err)
            log.exception("pass failed")
            return 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/tick")
def tick() -> dict:
    """Run a pass. Open on purpose — see the module docstring.

    Answers the same thing whether it did work or not, so it cannot be used to probe whether
    anything is queued.
    """
    _guarded_pass()
    return {"ok": True}


@app.get("/status")
def status(x_poster_key: str = Header(default="")) -> dict:
    if not POSTER_KEY or not secretslib.compare_digest(x_poster_key, POSTER_KEY):
        raise HTTPException(status_code=401, detail="This Space only answers its own app.")
    return {
        "queued": len(store.list_queue()) if store.configured() else 0,
        "lastTickAt": _state["lastTickAt"],
        "lastError": _state["lastError"],
        "needsBlueskyReauth": _state["needsBlueskyReauth"],
        "mastodonConfigured": bool(MASTODON_HOST and MASTODON_TOKEN),
        "blueskyConfigured": bool(BLUESKY_DID and (BLUESKY_REFRESH_JWT or store.bluesky_session())),
    }


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------


def _ticker() -> None:
    while True:
        time.sleep(TICK_SECONDS)
        _guarded_pass()


def _self_ping() -> None:
    """Keep the Space marked active by giving HF's router something to route.

    A background thread alone does not count as activity. This is a nudge, not a guarantee —
    see the timing note in the module docstring and the README.
    """
    if not SELF_URL:
        return
    while True:
        time.sleep(SELF_PING_SECONDS)
        try:
            with httpx.Client(timeout=20) as client:
                client.get(f"{SELF_URL.rstrip('/')}/health")
        except httpx.HTTPError:
            pass


@app.on_event("startup")
def _start() -> None:
    threading.Thread(target=_ticker, daemon=True).start()
    threading.Thread(target=_self_ping, daemon=True).start()
    # A Space that has just woken may have posts that came due while it slept.
    threading.Thread(target=_guarded_pass, daemon=True).start()
