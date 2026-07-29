import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..services import activepieces_client
from ..services.activepieces_client import ActivepiecesError

router = APIRouter(prefix="/distribution", tags=["distribution"])

# Broadcast tail — fires immediately (or on schedule), no human gate.
_CHANNELS = ["bluesky", "mastodon", "discord", "linkedin", "facebook", "instagram", "email", "twitter"]

# Community head — every send pauses for a human decision before the real post happens.
# The approval step is the line between automation that grows an audience and automation
# that reads as spam, per the Distribution Layer plan.
_COMMUNITY_CHANNELS = ["reddit", "discord-conversation"]


def _statuses(names: list[str], connected: set[str]) -> list[dict]:
    # discord-conversation reuses the same bot connection as the discord broadcast channel.
    return [
        {"channel": name, "connected": ("discord" if name == "discord-conversation" else name) in connected}
        for name in names
    ]


@router.get("/channels")
def list_channels() -> dict:
    try:
        activepieces_client.ensure_flows_imported()
        # Flows reference their connection as {{connections.<externalId>}}; we create/expect
        # one connection per channel with externalId == channel key.
        connected = {c["externalId"] for c in activepieces_client.list_connections()}
    except ActivepiecesError as err:
        # The engine being down hides connection *state*, not the catalogue — every channel is
        # still listed (as not connected) so the page always shows what this app can post to
        # rather than collapsing to a bare error. `ready` is what callers gate real sends on.
        return {
            "ready": False,
            "detail": str(err),
            "channels": _statuses(_CHANNELS, set()),
            "communityChannels": _statuses(_COMMUNITY_CHANNELS, set()),
        }

    return {
        "ready": True,
        "channels": _statuses(_CHANNELS, connected),
        "communityChannels": _statuses(_COMMUNITY_CHANNELS, connected),
    }


class ConnectionRequest(BaseModel):
    type: str  # "CUSTOM_AUTH" | "SECRET_TEXT" — OAUTH2 channels connect via Activepieces' own screen
    value: dict


@router.post("/connections/{channel}")
def connect_channel(channel: str, body: ConnectionRequest) -> dict:
    if channel not in {**activepieces_client.CUSTOM_AUTH_PIECES, **activepieces_client.SECRET_TEXT_PIECES}:
        raise HTTPException(status_code=404, detail=f"Unknown or OAuth-only channel '{channel}'")
    try:
        activepieces_client.create_connection(channel, body.type, body.value)
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"connected": True}


@router.delete("/connections/{channel}")
def disconnect_channel(channel: str) -> dict:
    if channel not in _CHANNELS and channel not in _COMMUNITY_CHANNELS:
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'")
    # discord-conversation shares its bot connection with the discord broadcast channel —
    # disconnecting either one removes the one underlying connection both rely on.
    external_id = "discord" if channel == "discord-conversation" else channel
    try:
        activepieces_client.delete_connection(external_id)
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"connected": False}


@router.get("/console-url")
def console_url() -> dict:
    try:
        return {"url": activepieces_client.get_console_url()}
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


class SendRequest(BaseModel):
    libraryItemId: str
    channels: list[str]
    text: str
    channelId: Optional[str] = None  # discord, discord-conversation
    pageId: Optional[str] = None  # facebook, instagram
    imageUrl: Optional[str] = None  # instagram
    to: Optional[str] = None  # email
    from_: Optional[str] = Field(default=None, alias="from")  # email
    subject: Optional[str] = None  # email
    subreddit: Optional[str] = None  # reddit
    title: Optional[str] = None  # reddit
    scheduledAt: Optional[str] = None  # ISO 8601; omitted or past -> sends immediately

    class Config:
        populate_by_name = True


def _payload_for(body: SendRequest) -> dict:
    payload = {"text": body.text}
    for field in ("channelId", "pageId", "imageUrl", "to", "subject", "subreddit", "title"):
        value = getattr(body, field)
        if value is not None:
            payload[field] = value
    if body.from_ is not None:
        payload["from"] = body.from_
    return payload


def _record_run_outcome(job_id: str, run: dict) -> None:
    """Updates a job row to match a flow run's outcome. A PAUSED run (the community-head
    channels) gets its approval/disapproval links pulled from the create_links step's
    output and moves into the approval queue instead of a terminal status."""
    status = run["status"]
    if status == "FAILED":
        failed_step = run.get("failedStep") or {}
        db.update_distribution_job(
            job_id, status="failed", activepieces_run_id=run["id"], error=failed_step.get("message", "Flow run failed")
        )
    elif status == "SUCCEEDED":
        db.update_distribution_job(job_id, status="sent", activepieces_run_id=run["id"])
    elif status == "PAUSED":
        steps = run.get("steps")
        if steps is None:
            try:
                run = activepieces_client.get_flow_run(run["id"])
                steps = run.get("steps") or {}
            except ActivepiecesError:
                steps = {}
        links = (steps.get("create_links") or {}).get("output") or {}
        db.update_distribution_job(
            job_id, status="pending_approval", activepieces_run_id=run["id"], resume_url=json.dumps(links)
        )
    elif status in ("RUNNING", "QUEUED"):
        db.update_distribution_job(job_id, status="sending", activepieces_run_id=run["id"])
    else:
        db.update_distribution_job(job_id, status=status.lower(), activepieces_run_id=run["id"])


def fire_job(job_id: str, channel: str, payload: dict) -> None:
    """Triggers a channel's webhook and records the resulting run on the job row.
    The webhook call itself is fire-and-forget (Activepieces runs the flow async and
    returns an empty body), so we poll flow-runs briefly afterward to pick up the run
    id and its outcome so far — community channels reach PAUSED well within this window
    since there's no external API call before the approval gate."""
    # A generous buffer against clock skew between this process and the Activepieces
    # container — their clocks can drift after a WSL2 VM suspend/resume, and missing the
    # just-fired run here is worse than the rare case of it picking up an older one too.
    since = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    try:
        activepieces_client.trigger_webhook(channel, payload)

        flow_id = activepieces_client.get_flow_id(channel)
        run = None
        for _ in range(5):
            time.sleep(1)
            runs = activepieces_client.list_flow_runs_since(flow_id, since) if flow_id else []
            if runs:
                run = runs[0]  # newest first
                if run["status"] not in ("QUEUED", "RUNNING"):
                    break
    except ActivepiecesError as err:
        db.update_distribution_job(job_id, status="failed", error=str(err))
        return

    if not run:
        db.update_distribution_job(job_id, status="sent")
    else:
        _record_run_outcome(job_id, run)


def _normalize_scheduled_at(raw: Optional[str]) -> Optional[str]:
    """Validates scheduledAt and re-serializes it in the exact same UTC isoformat
    the scheduler's `scheduled_at <= now` string comparison uses (see
    db.list_due_scheduled_jobs) — the frontend sends `...Z`, Python emits `...+00:00`,
    and any other client could send an arbitrary offset, none of which compare
    correctly as raw strings. Returns None for a time that isn't in the future
    (documented contract: omitted or past -> sends immediately)."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=f"Invalid scheduledAt '{raw}': {err}") from err
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    normalized = parsed.astimezone(timezone.utc)
    if normalized <= datetime.now(timezone.utc):
        return None
    return normalized.isoformat()


@router.post("/send")
def send(body: SendRequest) -> dict:
    payload = _payload_for(body)
    scheduled_at = _normalize_scheduled_at(body.scheduledAt)
    jobs = []
    for channel in body.channels:
        if channel not in _CHANNELS and channel not in _COMMUNITY_CHANNELS:
            raise HTTPException(status_code=400, detail=f"Unknown channel '{channel}'")
        status = "scheduled" if scheduled_at else "sending"
        job = db.add_distribution_job(
            body.libraryItemId, channel, status, scheduled_at=scheduled_at, payload=json.dumps(payload)
        )
        if not scheduled_at:
            fire_job(job["id"], channel, payload)
            job = db.get_distribution_job(job["id"])
        jobs.append(job)
    return {"jobs": jobs}


@router.get("/jobs")
def list_jobs(status: Optional[str] = None) -> dict:
    return {"jobs": db.list_distribution_jobs(status=status)}


@router.get("/queue")
def list_queue() -> dict:
    return {"jobs": db.list_distribution_jobs(status="pending_approval")}


def _resolve_queue_item(job_id: str, action: str) -> dict:
    job = db.get_distribution_job(job_id)
    if not job or job["status"] != "pending_approval":
        raise HTTPException(status_code=404, detail="No pending approval item with that id")
    links = json.loads(job["resume_url"] or "{}")
    link = links.get("approvalLink" if action == "approve" else "disapprovalLink")
    if not link:
        raise HTTPException(status_code=500, detail="No resume link recorded for this item")
    try:
        activepieces_client.resume_waitpoint(link)
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    new_status = "approved" if action == "approve" else "rejected"
    return db.update_distribution_job(job_id, status=new_status)


@router.post("/queue/{job_id}/approve")
def approve_queue_item(job_id: str) -> dict:
    return _resolve_queue_item(job_id, "approve")


@router.post("/queue/{job_id}/reject")
def reject_queue_item(job_id: str) -> dict:
    return _resolve_queue_item(job_id, "reject")


def _reconcile_job(job: dict) -> None:
    """Picks up wherever the last short poll left off, using the run id it already
    recorded — covers runs that took longer than fire_job's initial window (still
    'sending') and approved/rejected runs whose real post hasn't resolved yet."""
    if not job["activepieces_run_id"]:
        return
    try:
        run = activepieces_client.get_flow_run(job["activepieces_run_id"])
    except ActivepiecesError:
        return
    if run["status"] == "PAUSED" and job["status"] in ("approved", "rejected"):
        # The waitpoint call already recorded the decision — Activepieces just hasn't
        # finished processing the resume yet. Reproduced live: checking right after
        # approve/reject can still see PAUSED for a moment, and treating that as a new
        # pause would wrongly regress an already-decided job back into the queue.
        return
    if run["status"] in ("FAILED", "SUCCEEDED", "PAUSED"):
        _record_run_outcome(job["id"], run)


def _scheduler_loop() -> None:
    while True:
        try:
            for job in db.list_due_scheduled_jobs():
                db.update_distribution_job(job["id"], status="sending")
                fire_job(job["id"], job["channel"], json.loads(job["payload"] or "{}"))
            # pending_approval is included so queue items self-heal if their run was resolved
            # outside our approve/reject endpoints (or we crashed between resuming the
            # waitpoint and recording the decision) — re-recording a still-PAUSED run's
            # outcome is an idempotent no-op, so the common case costs one GET per item.
            for job in (
                db.list_distribution_jobs(status="sending")
                + db.list_distribution_jobs(status="approved")
                + db.list_distribution_jobs(status="pending_approval")
            ):
                _reconcile_job(job)
        except Exception:  # noqa: BLE001 — a bad tick must not kill the scheduler thread
            pass
        time.sleep(30)


def start_scheduler() -> None:
    """Started once at app startup. Polling rather than precise timers is fine here —
    scheduled sends aren't latency-sensitive, and this avoids a whole separate task-queue
    dependency for what's effectively a handful of posts a day."""
    threading.Thread(target=_scheduler_loop, daemon=True).start()
