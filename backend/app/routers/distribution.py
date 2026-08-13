import json
import logging
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

log = logging.getLogger(__name__)

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


def _custom_statuses(rows: list[dict], connected: set[str]) -> list[dict]:
    return [
        {
            "channel": row["channel"],
            "label": row["label"],
            "authType": row["auth_type"],
            "pieceName": row["piece_name"],
            "connected": row["channel"] in connected,
            "custom": True,
        }
        for row in rows
    ]


@router.get("/channels")
def list_channels() -> dict:
    custom = db.list_custom_channels()
    try:
        activepieces_client.ensure_flows_imported()
        activepieces_client.ensure_custom_flows_imported(custom)
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
            "customChannels": _custom_statuses(custom, set()),
        }

    return {
        "ready": True,
        "channels": _statuses(_CHANNELS, connected),
        "communityChannels": _statuses(_COMMUNITY_CHANNELS, connected),
        "customChannels": _custom_statuses(custom, connected),
    }


class ConnectionRequest(BaseModel):
    type: str  # "CUSTOM_AUTH" | "SECRET_TEXT" — OAUTH2 channels connect via Activepieces' own screen
    value: dict


# ---------------------------------------------------------------------------
# Credential prefill
#
# Several channels need exactly the credentials the user has already entered
# somewhere in Settings, and before this they had to type them a second time into
# the connect dialog. The two stores this reads are the ones the backend owns:
#
#   bluesky -> BLUESKY_HANDLE / BLUESKY_APP_PASSWORD, held in the process
#              environment by vendor/socialpost's config layer
#   email   -> DATA_DIR/mail_settings.json, via services.mail
#
# Mastodon's credentials live in Electron's encrypted store instead, which the
# backend never sees, so the renderer fills those in itself.
#
# Secrets are NEVER returned. A field the backend can supply comes back as the
# placeholder below, and `connect_channel` swaps the real value in server-side, so
# a saved app password is not round-tripped through the renderer just to be handed
# straight back. Non-secret fields (a handle, an SMTP host) are returned in the
# clear because the point is for the user to see and confirm them.
# ---------------------------------------------------------------------------

SETTINGS_PLACEHOLDER = "__from_settings__"


def _bluesky_prefill() -> dict[str, tuple[str, bool]]:
    """{field: (value, is_secret)} for the fields Settings can supply."""
    import os

    # Importing the vendored config pulls in its db module, which loads the per-user
    # .env into the process. Without this the prefill would disagree with the rest of
    # the app (is_set(), the Settings screen) whenever nothing had touched the
    # Bluesky tooling yet in this process.
    from vendor.socialpost.src import config as spg_config  # noqa: F401

    handle = (os.environ.get("BLUESKY_HANDLE") or "").strip()
    password = (os.environ.get("BLUESKY_APP_PASSWORD") or "").strip()
    out: dict[str, tuple[str, bool]] = {}
    if handle:
        out["identifier"] = (handle, False)
    if password:
        out["password"] = (password, True)
    return out


def _email_prefill() -> dict[str, tuple[str, bool]]:
    from ..services import mail

    cfg = mail.load()
    out: dict[str, tuple[str, bool]] = {}
    if cfg.host:
        out["host"] = (cfg.host, False)
    # The piece's "email" prop is the SMTP login, which is `username` here;
    # from_email is the fallback for providers where the two are the same.
    login = cfg.username or cfg.from_email
    if login:
        out["email"] = (login, False)
    if cfg.password:
        out["password"] = (cfg.password, True)
    if cfg.port:
        out["port"] = (str(cfg.port), False)
    out["TLS"] = ("true" if cfg.use_tls else "false", False)
    return out


_PREFILL_SOURCES = {
    "bluesky": ("Bluesky Post Creator settings", _bluesky_prefill),
    "email": ("your mail settings", _email_prefill),
}


class PrefillField(BaseModel):
    key: str
    # The real value for non-secret fields, SETTINGS_PLACEHOLDER for secrets.
    value: str
    secret: bool


class PrefillResponse(BaseModel):
    channel: str
    # False when this channel has no backend-held credentials at all — the dialog
    # then behaves exactly as it did before.
    available: bool
    source: str | None
    fields: list[PrefillField]


@router.get("/connections/{channel}/prefill", response_model=PrefillResponse)
def connection_prefill(channel: str) -> PrefillResponse:
    entry = _PREFILL_SOURCES.get(channel)
    if not entry:
        return PrefillResponse(channel=channel, available=False, source=None, fields=[])
    source, loader = entry
    try:
        found = loader()
    except Exception as err:  # noqa: BLE001 — a missing/corrupt store is "nothing to prefill"
        log.warning("[distribution] prefill for %s failed: %s", channel, err)
        return PrefillResponse(channel=channel, available=False, source=None, fields=[])

    # A lone non-secret default (email's TLS flag) is not worth announcing as
    # "we filled this in from Settings" — it carries no credential.
    meaningful = [k for k, (_, secret) in found.items() if secret or k != "TLS"]
    return PrefillResponse(
        channel=channel,
        available=bool(meaningful),
        source=source if meaningful else None,
        fields=[
            PrefillField(key=key, value=SETTINGS_PLACEHOLDER if secret else value, secret=secret)
            for key, (value, secret) in found.items()
        ],
    )


class VerifyConnectionResponse(BaseModel):
    ok: bool
    detail: str


@router.post("/connections/{channel}/verify-settings", response_model=VerifyConnectionResponse)
def verify_connection_settings(channel: str) -> VerifyConnectionResponse:
    """Check the stored credentials actually work, before they become a connection.

    This is a live login/handshake, so it is only ever run when the user asks for
    it — never on opening the dialog.
    """
    if channel == "bluesky":
        from vendor.socialpost.src import config as spg_config

        ok, detail = spg_config.verify_bluesky()
        return VerifyConnectionResponse(ok=ok, detail=detail)
    if channel == "email":
        from ..services import mail

        ok, detail = mail.verify()
        return VerifyConnectionResponse(ok=ok, detail=detail)
    raise HTTPException(status_code=404, detail=f"No stored credentials to verify for '{channel}'")


def _resolve_placeholders(channel: str, value: dict) -> dict:
    """Swap SETTINGS_PLACEHOLDER for the real secret the backend holds."""
    entry = _PREFILL_SOURCES.get(channel)
    if not entry:
        return value
    _, loader = entry
    try:
        found = loader()
    except Exception:  # noqa: BLE001
        return value

    resolved = dict(value)
    for key, raw in value.items():
        if raw != SETTINGS_PLACEHOLDER:
            continue
        if key in found:
            resolved[key] = found[key][0]
        else:
            # Nothing stored to substitute — drop it rather than sending the
            # literal placeholder to Activepieces as if it were a password.
            resolved.pop(key, None)
    return resolved


@router.post("/connections/{channel}")
def connect_channel(channel: str, body: ConnectionRequest) -> dict:
    piece = None
    if channel not in {**activepieces_client.CUSTOM_AUTH_PIECES, **activepieces_client.SECRET_TEXT_PIECES}:
        # A user-added channel carries its own piece coordinates; anything else really is
        # unknown here (or is OAuth-only, which connects through Activepieces' own screen).
        custom = db.get_custom_channel(channel)
        if not custom:
            raise HTTPException(status_code=404, detail=f"Unknown or OAuth-only channel '{channel}'")
        piece = (custom["piece_name"], custom["piece_version"])
    try:
        value = _resolve_placeholders(channel, body.value)
        activepieces_client.create_connection(channel, body.type, value, piece=piece)
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"connected": True}


@router.delete("/connections/{channel}")
def disconnect_channel(channel: str) -> dict:
    if channel not in _CHANNELS and channel not in _COMMUNITY_CHANNELS and not db.get_custom_channel(channel):
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'")
    # discord-conversation shares its bot connection with the discord broadcast channel —
    # disconnecting either one removes the one underlying connection both rely on.
    external_id = "discord" if channel == "discord-conversation" else channel
    try:
        activepieces_client.delete_connection(external_id)
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"connected": False}


# The fields a send request can carry (see SendRequest/_payload_for below). A generated
# flow binds a piece's props to these by name, so this list is what the mapping UI offers.
PAYLOAD_FIELDS = ["text", "title", "imageUrl", "channelId", "pageId", "subreddit", "to", "subject", "from"]

# Prop names that conventionally carry the post body, best-guess first. Used only to
# pre-select a mapping the user can change — a wrong guess costs a dropdown change, and
# reading the piece's own prop names beats asking a model to infer them.
_TEXT_PROP_HINTS = ["text", "message", "content", "status", "body", "caption", "description", "comment"]


def _auth_block(piece: dict) -> dict:
    """A piece's auth descriptor, normalised to a dict.

    Not every piece in the catalogue reports one the same way — some carry no auth at all,
    and a few report it as a list — so this collapses those to an empty dict rather than
    letting one odd piece break the whole listing.
    """
    auth = piece.get("auth")
    return auth if isinstance(auth, dict) else {}


def _normalize_props(props: Optional[dict]) -> list[dict]:
    """Flattens Activepieces' prop map into the ordered, typed list the forms render from."""
    out = []
    for key, spec in (props or {}).items():
        spec = spec or {}
        options = ((spec.get("options") or {}).get("options")) or []
        out.append(
            {
                "key": key,
                "label": spec.get("displayName") or key,
                "description": spec.get("description") or "",
                "type": spec.get("type") or "SHORT_TEXT",
                "required": bool(spec.get("required")),
                "defaultValue": spec.get("defaultValue"),
                "options": [{"label": o.get("label"), "value": o.get("value")} for o in options],
            }
        )
    return out


def _suggest_binding(prop_key: str) -> Optional[str]:
    """The payload field a prop most likely wants, or None to leave it for the user."""
    key = prop_key.lower()
    if key in _TEXT_PROP_HINTS:
        return "text"
    for field in PAYLOAD_FIELDS:
        if key == field.lower():
            return field
    if "title" in key:
        return "title"
    if "image" in key or "photo" in key or "media" in key:
        return "imageUrl"
    return None


@router.get("/catalogue")
def catalogue(q: str = "", limit: int = 60) -> dict:
    """Every piece the engine can post through, for the Add-a-channel browser.

    Served from the engine rather than a list of our own: the catalogue is whatever this
    Activepieces version actually ships, and a hardcoded copy would silently rot the first
    time it updated. Pieces with no actions are dropped — there would be nothing to send.
    """
    try:
        pieces = activepieces_client.list_pieces()
    except ActivepiecesError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err

    added = {row["piece_name"] for row in db.list_custom_channels()}
    builtin = {name for name, _ in {**activepieces_client.CUSTOM_AUTH_PIECES, **activepieces_client.SECRET_TEXT_PIECES}.values()}
    builtin |= {name for name, _ in activepieces_client.OAUTH_PIECES.values()}

    needle = q.strip().lower()
    rows = []
    for piece in pieces:
        if not piece.get("actions"):
            continue
        name, display = piece.get("name", ""), piece.get("displayName", "")
        if needle and needle not in display.lower() and needle not in name.lower():
            continue
        rows.append(
            {
                "name": name,
                "displayName": display,
                "description": (piece.get("description") or "")[:200],
                "logoUrl": piece.get("logoUrl"),
                "version": piece.get("version"),
                "authType": _auth_block(piece).get("type"),
                "actionCount": piece.get("actions"),
                "categories": piece.get("categories") or [],
                "alreadyAdded": name in added,
                "builtIn": name in builtin,
            }
        )
    rows.sort(key=lambda r: r["displayName"].lower())
    return {"total": len(rows), "pieces": rows[:limit]}


@router.get("/catalogue/{piece_name:path}")
def catalogue_piece(piece_name: str) -> dict:
    """One piece's auth schema and actions, with a suggested payload binding per prop."""
    try:
        piece = activepieces_client.get_piece(piece_name)
    except ActivepiecesError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    auth = _auth_block(piece)
    actions = []
    for name, spec in (piece.get("actions") or {}).items():
        props = _normalize_props(spec.get("props"))
        for prop in props:
            prop["suggestedBinding"] = _suggest_binding(prop["key"])
        actions.append(
            {
                "name": name,
                "label": spec.get("displayName") or name,
                "description": spec.get("description") or "",
                "props": props,
            }
        )
    actions.sort(key=lambda a: a["label"].lower())

    return {
        "name": piece.get("name"),
        "displayName": piece.get("displayName"),
        "version": piece.get("version"),
        "logoUrl": piece.get("logoUrl"),
        "auth": {
            "type": auth.get("type"),
            "label": auth.get("displayName") or "",
            "description": auth.get("description") or "",
            "props": _normalize_props(auth.get("props")),
        },
        "actions": actions,
        "payloadFields": PAYLOAD_FIELDS,
    }


class CustomChannelRequest(BaseModel):
    channel: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,38}$")
    label: str
    pieceName: str
    pieceVersion: str
    actionName: str
    authType: str
    # {prop key: {"field": "<payload field>"} | {"value": "<literal>"}}
    inputMap: dict[str, dict]


def _resolve_input_map(raw: dict[str, dict]) -> dict:
    """Turns the UI's per-prop choice into the Activepieces bindings a flow carries.

    A payload binding becomes {{trigger.body.<field>}} — the same template the ten bundled
    flows use — and anything else is passed through as the literal the user typed.
    """
    resolved = {}
    for key, choice in raw.items():
        field = (choice or {}).get("field")
        if field:
            if field not in PAYLOAD_FIELDS:
                raise HTTPException(status_code=400, detail=f"Unknown payload field '{field}' for prop '{key}'")
            resolved[key] = f"{{{{trigger.body.{field}}}}}"
        elif (choice or {}).get("value") not in (None, ""):
            resolved[key] = choice["value"]
    return resolved


@router.post("/custom-channels")
def create_custom_channel(body: CustomChannelRequest) -> dict:
    if body.channel in _CHANNELS or body.channel in _COMMUNITY_CHANNELS:
        raise HTTPException(status_code=400, detail=f"'{body.channel}' is a built-in channel name")

    input_map = _resolve_input_map(body.inputMap)
    try:
        spec = activepieces_client.build_flow_spec(
            body.channel, body.label, body.pieceName, body.pieceVersion, body.actionName, input_map
        )
        activepieces_client.import_custom_flow(body.channel, spec)
    except ActivepiecesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    # Written only once the flow is live, so a failed import can't leave a channel row
    # whose send button points at a webhook that was never published.
    row = db.add_custom_channel(
        body.channel, body.label, body.pieceName, body.pieceVersion, body.actionName, body.authType, input_map
    )
    return {"channel": row["channel"], "label": row["label"]}


@router.delete("/custom-channels/{channel}")
def delete_custom_channel(channel: str) -> dict:
    row = db.get_custom_channel(channel)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'")
    try:
        activepieces_client.delete_flow_by_display_name(row["label"])
        activepieces_client.delete_connection(channel)
    except ActivepiecesError:
        # The row goes regardless: leaving it behind would show the user a channel they
        # already deleted, and an orphaned flow is re-created on the next add anyway.
        pass
    db.delete_custom_channel(channel)
    return {"deleted": True}


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
    known_custom = {row["channel"] for row in db.list_custom_channels()}
    for channel in body.channels:
        if channel not in _CHANNELS and channel not in _COMMUNITY_CHANNELS and channel not in known_custom:
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
