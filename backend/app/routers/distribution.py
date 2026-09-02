import json
import os
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, db
from ..services import activepieces_client, cloud_poster, generation_link, mastodon_delivery
from ..services.activepieces_client import ActivepiecesError

router = APIRouter(prefix="/distribution", tags=["distribution"])

log = logging.getLogger(__name__)

# Broadcast tail — fires immediately (or on schedule), no human gate.
_CHANNELS = ["bluesky", "mastodon", "discord", "linkedin", "facebook", "instagram", "email", "twitter"]

# Community head — every send pauses for a human decision before the real post happens.
# The approval step is the line between automation that grows an audience and automation
# that reads as spam, per the Distribution Layer plan.
_COMMUNITY_CHANNELS = ["reddit", "discord-conversation"]

# Channels the user's own poster Space can publish for them. A SCHEDULED post on one of these
# is handed to the Space so it goes out whether or not this app is running; an immediate send
# stays local, because the app is by definition open and the local path is already working.
#
# Handing one over is a change of OWNER, not a flag: the job's status becomes
# "scheduled_cloud", and every status-conditional query in db.py keys on the literal
# 'scheduled' — list_due_scheduled_jobs, cancel_scheduled_distribution_job,
# claim_scheduled_distribution_job and fail_scheduled_distribution_job alike. So the local
# scheduler cannot see a cloud job at all, and the two can never both believe one is due.
_CLOUD_CHANNELS = {"bluesky", "mastodon"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        if channel == "mastodon":
            mastodon_delivery.set_credentials(
                str(value.get("base_url") or ""),
                str(value.get("access_token") or ""),
            )
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
    if channel == "mastodon":
        mastodon_delivery.set_credentials()
    return {"connected": False}


class MastodonCredentialsRequest(BaseModel):
    """The active account Electron decrypted from its OS-protected settings store."""

    instance: str = ""
    accessToken: str = ""


@router.post("/mastodon-credentials")
def set_mastodon_credentials(body: MastodonCredentialsRequest) -> dict:
    """Hand Mastodon credentials to the scheduler for this app process only."""
    return {
        "ready": mastodon_delivery.set_credentials(body.instance, body.accessToken)
    }


# The fields a send request can carry (see SendRequest/_payload_for below). A generated
# flow binds a piece's props to these by name, so this list is what the mapping UI offers.
PAYLOAD_FIELDS = [
    "text",
    "title",
    "imageUrl",
    "imageUrls",
    "videoUrl",
    "videoFileAlt",
    "mediaUrl",
    "channelId",
    "pageId",
    "subreddit",
    "to",
    "subject",
    "from",
]

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
    imageUrl: Optional[str] = None  # image-capable social channels
    videoFileUrl: Optional[str] = None  # staged /outputs video for Bluesky/Mastodon
    videoFileAlt: Optional[str] = None  # Bluesky; Mastodon's piece has no alt prop
    to: Optional[str] = None  # email
    from_: Optional[str] = Field(default=None, alias="from")  # email
    subject: Optional[str] = None  # email
    subreddit: Optional[str] = None  # reddit
    title: Optional[str] = None  # reddit
    scheduledAt: Optional[str] = None  # ISO 8601; omitted or past -> sends immediately

    class Config:
        populate_by_name = True


# How the engine addresses this backend. Electron announces the current WSL gateway each
# launch; using it directly avoids a container's static hostname mapping going stale when
# WSL reassigns the adapter. Overridable for a non-Docker deployment.
ENGINE_HOST_BASE = os.environ.get("MRAIM_ENGINE_HOST_BASE", "").strip()
_announced_share_host = os.environ.get("MRAIM_SHARE_HOST", "").strip()


def _shareable_media_url(media_url: str) -> str:
    """Turn the app's own /outputs URL into one the engine can fetch, if it is ours.

    Media generated or staged in the app lives on disk here and is served behind the
    session token, which a container cannot send — so it is re-issued as a signed,
    expiring link on a path that answers without the token (services/share_links.py).

    Anything else is passed through untouched: a user pasting a public URL is naming a
    picture that is already reachable, and rewriting that would be wrong.
    """
    from ..services import share_links, share_server

    path = share_links.path_from_outputs_url(media_url) or share_links.path_from_shared_url(media_url)
    if path is None:
        # An app-owned URL is never meaningful to Activepieces as a relative path.  If
        # its file has disappeared, fail the send explicitly instead of handing the
        # connector ``/outputs/...``.  Activepieces' File processor catches that fetch
        # error and substitutes null, which makes image-capable actions report success
        # after publishing a misleading text-only post.
        if media_url.startswith("/outputs/"):
            raise HTTPException(
                status_code=400,
                detail="The attached media file no longer exists. Attach it again and retry.",
            )
        return media_url
    if not share_server.is_listening() or (not ENGINE_HOST_BASE and not _announced_share_host):
        raise HTTPException(
            status_code=503,
            detail=(
                "Local media sharing is not ready, so the posting engine cannot fetch "
                "this attachment. Restart the Distribution engine and try again."
            ),
        )

    port = int(os.environ.get("MRAIM_SHARE_PORT", "8756"))
    host = f"[{_announced_share_host}]" if ":" in _announced_share_host else _announced_share_host
    base = ENGINE_HOST_BASE or f"http://{host}:{port}"
    return share_links.url_for(path, base)


class ShareHostRequest(BaseModel):
    #: The WSL adapter address the container reaches this machine on. Empty stops the
    #: listener, which is what should happen when the engine is shut down.
    host: str


@router.post("/share-host")
def set_share_host(body: ShareHostRequest) -> dict:
    """Open (or close) the share listener on the address the engine can reach us at.

    An endpoint rather than an environment variable because the address is only knowable by
    asking WSL, and asking costs a `wsl.exe` round trip that also *starts* the VM. Doing that
    at backend boot would make every launch slower and spin up WSL for people who never open
    Distribute. The app already knows the address by the time it starts the engine, so it
    hands it over then.

    Behind the session token like everything else here, so only this app can ask for a socket
    to be opened.
    """
    global _announced_share_host
    from ..services import share_server

    host = body.host.strip()
    if not host:
        share_server.stop()
        _announced_share_host = ""
        return {"listening": False, "host": ""}

    ok = share_server.start(host, int(os.environ.get("MRAIM_SHARE_PORT", "8756")))
    _announced_share_host = host if ok else ""
    return {"listening": ok, "host": host if ok else ""}


def _payload_for(body: SendRequest) -> dict:
    if body.imageUrl and body.videoFileUrl:
        raise HTTPException(
            status_code=400,
            detail="Attach either an image or a video, not both; social posts carry one media embed.",
        )

    payload = {"text": body.text}
    if body.imageUrl:
        payload["imageUrl"] = body.imageUrl
        payload["mediaUrl"] = body.imageUrl
        if "bluesky" in body.channels:
            # Bluesky's createPost action takes an ARRAY and enforces a strict decimal
            # 1MB cap per image. Keep the original scalar for other selected channels,
            # but give Bluesky its correctly typed, platform-prepared derivative.
            from ..services import image_prompt

            try:
                prepared = image_prompt.prepare_bluesky_image(body.imageUrl)
            except image_prompt.ImageRenderError as err:
                raise HTTPException(status_code=400, detail=str(err)) from None
            payload["imageUrls"] = [prepared]
    if body.videoFileUrl:
        supported = {"bluesky", "mastodon"}.intersection(body.channels)
        if not supported:
            raise HTTPException(
                status_code=400,
                detail="Uploaded video attachments are supported on Bluesky and Mastodon.",
            )
        from ..services import video_attach

        max_bytes = (
            video_attach.MASTODON_DEFAULT_MAX_BYTES
            if "mastodon" in supported
            else video_attach.BLUESKY_MAX_BYTES
        )
        network = "Mastodon" if "mastodon" in supported else "Bluesky"
        try:
            video_attach.attachment_path(body.videoFileUrl, max_bytes, network)
        except video_attach.VideoUnusable as err:
            raise HTTPException(status_code=400, detail=str(err)) from None
        payload["videoUrl"] = body.videoFileUrl
        payload["mediaUrl"] = body.videoFileUrl
        if body.videoFileAlt is not None:
            payload["videoFileAlt"] = body.videoFileAlt.strip()
    # Activepieces evaluates a missing webhook property as an empty string.  Bluesky's
    # action strictly requires an array even when the post carries video instead of images,
    # so make the empty shape explicit for every Bluesky send.
    if "bluesky" in body.channels:
        payload.setdefault("imageUrls", [])
    for field in ("channelId", "pageId", "to", "subject", "subreddit", "title"):
        value = getattr(body, field)
        if value is not None:
            payload[field] = value
    if body.from_ is not None:
        payload["from"] = body.from_
    return payload


def _materialize_media_payload(payload: dict) -> dict:
    """Mint fetchable links immediately before a flow runs.

    Scheduled jobs keep canonical ``/outputs`` URLs in SQLite and come through here at
    execution time. That avoids expiring a signed link while a post waits in the queue
    (share links are deliberately capped at fourteen days).
    """
    materialized = dict(payload)
    for field in ("imageUrl", "videoUrl", "mediaUrl"):
        value = materialized.get(field)
        if isinstance(value, str) and value:
            materialized[field] = _shareable_media_url(value)
    image_urls = materialized.get("imageUrls")
    if isinstance(image_urls, list):
        materialized["imageUrls"] = [
            _shareable_media_url(value)
            for value in image_urls
            if isinstance(value, str) and value
        ]
    return materialized


def _upgrade_legacy_media_payload(payload: dict, channel: str) -> dict:
    """Add the media fields introduced after older scheduled jobs were persisted."""
    if channel == "bluesky" and "imageUrls" not in payload:
        payload = {**payload, "imageUrls": []}
    image_url = payload.get("imageUrl")
    if not isinstance(image_url, str) or not image_url:
        return payload
    if channel not in {"bluesky", "mastodon"}:
        return payload

    from ..services import image_prompt, share_links

    upgraded = dict(payload)
    path = share_links.path_from_outputs_url(image_url) or share_links.path_from_shared_url(image_url)
    canonical = image_prompt.outputs_url(path) if path is not None else image_url
    upgraded["imageUrl"] = canonical
    upgraded.setdefault("mediaUrl", canonical)
    if channel == "bluesky" and not upgraded.get("imageUrls"):
        try:
            upgraded["imageUrls"] = [image_prompt.prepare_bluesky_image(canonical)]
        except image_prompt.ImageRenderError as err:
            raise HTTPException(status_code=400, detail=str(err)) from None
    return upgraded


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


_DELIVERY_JOB_FIELD = "_mraimDeliveryJobId"


def _matching_flow_run(runs: list[dict], job_id: str) -> dict | None:
    """Return the run whose webhook body carries this delivery's unique marker."""
    for summary in runs:
        try:
            run = (
                summary
                if summary.get("steps") is not None
                else activepieces_client.get_flow_run(summary["id"])
            )
        except (ActivepiecesError, KeyError):
            continue
        trigger = ((run.get("steps") or {}).get("trigger") or {}).get("output") or {}
        body = trigger.get("body") if isinstance(trigger, dict) else None
        if isinstance(body, dict) and body.get(_DELIVERY_JOB_FIELD) == job_id:
            return run
    return None


def fire_job(job_id: str, channel: str, payload: dict) -> None:
    """Triggers a channel's webhook and records the resulting run on the job row.
    The webhook call itself is fire-and-forget (Activepieces runs the flow async and
    returns an empty body), so we poll flow-runs briefly afterward to pick up the run
    id and its outcome so far — community channels reach PAUSED well within this window
    since there's no external API call before the approval gate."""
    if channel == "mastodon" and mastodon_delivery.carries_media(payload):
        try:
            created = mastodon_delivery.publish(payload, idempotency_key=job_id)
        except Exception as err:  # noqa: BLE001 - converted to a durable job failure here
            db.update_distribution_job(job_id, status="failed", error=str(err))
            return
        status_id = str(created.get("id") or "")
        db.update_distribution_job(
            job_id,
            status="sent",
            activepieces_run_id=f"mastodon:{status_id}" if status_id else None,
        )
        return

    # A generous buffer against clock skew between this process and the Activepieces
    # container — their clocks can drift after a WSL2 VM suspend/resume, and missing the
    # just-fired run here is worse than the rare case of it picking up an older one too.
    since = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    try:
        # This is the last boundary before Activepieces.  Keep media materialization here
        # even though current send/scheduler callers prepare it earlier: the live failure
        # that prompted this guard reached the webhook with an existing ``/outputs`` file,
        # and Activepieces silently converted that unfetchable relative File URL to null.
        # Re-materializing an already-signed URL is safe and refreshes its short expiry.
        delivery_payload = _materialize_media_payload(payload)
        delivery_payload[_DELIVERY_JOB_FIELD] = job_id
        activepieces_client.trigger_webhook(channel, delivery_payload)

        flow_id = activepieces_client.get_flow_id(channel)
        run = None
        for _ in range(5):
            time.sleep(1)
            runs = activepieces_client.list_flow_runs_since(flow_id, since) if flow_id else []
            if runs:
                run = _matching_flow_run(runs, job_id)
            if run:
                if run["status"] not in ("QUEUED", "RUNNING"):
                    break
    except HTTPException as err:
        db.update_distribution_job(job_id, status="failed", error=str(err.detail))
        return
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
    known_custom = {row["channel"] for row in db.list_custom_channels()}
    for channel in body.channels:
        if channel not in _CHANNELS and channel not in _COMMUNITY_CHANNELS and channel not in known_custom:
            raise HTTPException(status_code=400, detail=f"Unknown channel '{channel}'")

    scheduled_at = _normalize_scheduled_at(body.scheduledAt)
    canonical_payload = _payload_for(body)
    jobs = []
    for channel in body.channels:
        status = "scheduled" if scheduled_at else "sending"
        job = db.add_distribution_job(
            body.libraryItemId,
            channel,
            status,
            scheduled_at=scheduled_at,
            # History keeps durable app-local URLs. The separately materialized payload
            # is what the flow receives; its signed fetch links are intentionally short-
            # lived and would otherwise turn an old history entry into a dead URL.
            payload=json.dumps(canonical_payload),
        )
        if not scheduled_at:
            payload = (
                canonical_payload
                if channel == "mastodon" and mastodon_delivery.carries_media(canonical_payload)
                else _materialize_media_payload(canonical_payload)
            )
            fire_job(job["id"], channel, payload)
            job = db.get_distribution_job(job["id"])
        elif channel in _CLOUD_CHANNELS and cloud_poster.is_configured():
            # The upload can take minutes for a video. It happens here, while the user is
            # watching the send dialog, rather than at posting time when nobody is.
            try:
                cloud_poster.enqueue(job["id"], channel, canonical_payload, scheduled_at)
                job = db.update_distribution_job(
                    job["id"], status="scheduled_cloud", cloud_enqueued_at=_now_iso()
                )
            except cloud_poster.CloudPosterError as err:
                # Never lose the post over this. It stays on the local scheduler exactly as
                # it would have before cloud posting existed — it just needs the app open.
                log.warning("[distribution] cloud enqueue failed for %s: %s", job["id"], err)
                job = db.update_distribution_job(
                    job["id"],
                    error=f"Could not hand this to your poster Space, so it will go out from this app instead: {err}",
                )
        jobs.append(job)
    return {"jobs": jobs}


@router.get("/jobs")
def list_jobs(status: Optional[str] = None) -> dict:
    return {"jobs": db.list_distribution_jobs(status=status)}


@router.post("/jobs/{job_id}/cancel")
def cancel_scheduled_job(job_id: str) -> dict:
    existing = db.get_distribution_job(job_id)
    if existing and existing["status"] == "scheduled_cloud":
        # Out of the outbox first. A Space that has already claimed the job is posting it
        # right now, and reporting success here would leave the user believing a post they
        # are about to see was stopped.
        if not cloud_poster.cancel(job_id):
            raise HTTPException(
                status_code=409,
                detail="Your poster Space is already sending this one, so it cannot be cancelled.",
            )
        cancelled_cloud = db.cancel_cloud_scheduled_distribution_job(job_id)
        if cancelled_cloud:
            return cancelled_cloud

    cancelled = db.cancel_scheduled_distribution_job(job_id)
    if cancelled:
        return cancelled

    # Treat a retry after a lost response as success.  This keeps the action idempotent
    # without suggesting that a job already claimed by the scheduler can still be stopped.
    job = db.get_distribution_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No distribution job with that id")
    if job["status"] == "cancelled":
        return job
    raise HTTPException(
        status_code=409,
        detail="This post is no longer scheduled, so it cannot be cancelled.",
    )


@router.post("/jobs/{job_id}/retry")
def retry_failed_job(job_id: str) -> dict:
    """Send a failed post again, on the same row.

    History had no way to act on a failure: the detail view showed the engine's error and
    left re-creating the whole post by hand as the only way forward. That was tolerable
    while failures meant the content was wrong, and stopped being tolerable once one of
    them turned out to be a timeout — a post that would have gone out untouched on a
    second attempt.

    Re-fired in place rather than queued as a new job, for two reasons. The history stays
    one row per intent instead of accumulating a copy per attempt; and `fire_job` keys
    Mastodon's Idempotency-Key off the job id, so re-using the row is what lets the server
    recognise a repeat. That matters for the failure this was written for: an upload can
    fail in a way that leaves the status genuinely unsent, but a lost *response* looks
    identical from here, and re-firing under the original key is what stops the second
    attempt becoming a second post.

    Only a failed job. A scheduled one has `cancel`, a sent one must not be sent twice, and
    anything mid-flight belongs to the scheduler.
    """
    job = db.get_distribution_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No distribution job with that id")
    if job["status"] != "failed":
        raise HTTPException(
            status_code=409,
            detail="Only a post that failed can be sent again.",
        )

    # A cloud job that failed may have failed only on the way BACK. The outcome file is the
    # authority, and re-firing through Activepieces — which has no idempotency key of its own
    # on the Bluesky path — is exactly how one lost response becomes two posts.
    if job["cloud_enqueued_at"] and cloud_poster.is_configured():
        recorded = cloud_poster.outcome(job_id)
        if recorded and recorded.get("status") == "sent":
            db.update_distribution_job(
                job_id, status="sent", error=None, cloud_ref=str(recorded.get("ref") or "")
            )
            raise HTTPException(
                status_code=409,
                detail="This one did go out — your poster Space sent it. History has been corrected.",
            )

    stored_payload = json.loads(job["payload"] or "{}")
    canonical_payload = _upgrade_legacy_media_payload(stored_payload, job["channel"])
    native_mastodon = (
        job["channel"] == "mastodon" and mastodon_delivery.carries_media(canonical_payload)
    )
    if native_mastodon and not mastodon_delivery.has_credentials():
        raise HTTPException(
            status_code=503,
            detail="Mastodon is not connected. Reconnect it in Distribute and retry.",
        )
    # Raises 400 for media the payload still names but the disk no longer has, which is a
    # better answer than firing and failing on it a second time.
    payload = canonical_payload if native_mastodon else _materialize_media_payload(canonical_payload)

    # The old error has to go with the old attempt. Leaving it set would put a stale
    # explanation next to a post that has just succeeded.
    db.update_distribution_job(job_id, status="sending", error=None)
    fire_job(job_id, job["channel"], payload)
    return db.get_distribution_job(job_id)


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


def _fire_due_scheduled_jobs() -> None:
    jobs = db.list_due_scheduled_jobs()
    if not jobs:
        return
    needs_engine = any(
        not (
            job["channel"] == "mastodon"
            and mastodon_delivery.carries_media(json.loads(job["payload"] or "{}"))
        )
        for job in jobs
    )
    engine_ready = True
    try:
        # The backend scheduler starts before Electron's deliberately backgrounded
        # container startup. Preflight once so every due job — including text-only and
        # public-media posts — stays scheduled during that normal cold-start window.
        if needs_engine:
            activepieces_client.list_flows()
    except ActivepiecesError as err:
        log.info("[distribution] posting engine not ready for scheduled jobs: %s", err)
        engine_ready = False

    for job in jobs:
        try:
            stored_payload = json.loads(job["payload"] or "{}")
            canonical_payload = _upgrade_legacy_media_payload(stored_payload, job["channel"])
            native_mastodon = (
                job["channel"] == "mastodon"
                and mastodon_delivery.carries_media(canonical_payload)
            )
            if native_mastodon and not mastodon_delivery.has_credentials():
                # Electron hands the OS-decrypted token over just after backend startup.
                # A due post must wait through that small launch window, not be claimed and
                # permanently failed before the credential arrives.
                continue
            if not native_mastodon and not engine_ready:
                continue
            payload = canonical_payload if native_mastodon else _materialize_media_payload(canonical_payload)
        except HTTPException as err:
            if err.status_code == 503:
                # Electron announces the WSL-reachable listener just after backend
                # startup. A post that became due while the app was closed must wait for
                # that transient readiness window, not become permanently failed on the
                # scheduler's first tick.
                log.info("[distribution] media listener not ready for scheduled job %s", job["id"])
                continue
            db.fail_scheduled_distribution_job(job["id"], str(err.detail))
            continue
        stored_upgrade = (
            json.dumps(canonical_payload)
            if canonical_payload != stored_payload
            else None
        )
        claimed = db.claim_scheduled_distribution_job(
            job["id"], payload=stored_upgrade
        )
        if not claimed:
            # The due-job list is a snapshot.  Cancellation may have won while media was
            # being prepared; in that case the conditional claim deliberately does nothing.
            continue
        fire_job(claimed["id"], claimed["channel"], payload)


def _reconcile_cloud_jobs() -> None:
    """Read back what the poster Space did, for jobs it owns.

    The outbox is the boundary in both directions, so this works whether or not the Space is
    awake — the outcome file is already committed by the time it matters.

    The reference is written as "mastodon:<id>" because that is the exact form
    generation_link already parses (see _mastodon_status_id), so posts sent from the cloud
    keep feeding the Social Post learning loop with no change there.
    """
    if not cloud_poster.is_configured():
        return
    for job in db.list_cloud_pending_jobs():
        outcome = cloud_poster.outcome(job["id"])
        if not outcome:
            continue
        ref = str(outcome.get("ref") or "")
        if outcome.get("status") == "sent":
            db.update_distribution_job(
                job["id"], status="sent", error=None, cloud_ref=ref, activepieces_run_id=ref or None
            )
        else:
            db.update_distribution_job(
                job["id"],
                status="failed",
                error=str(outcome.get("error") or "Your poster Space could not send this one."),
            )


#: The outbox is swept about once a day. More often would be pointless — nothing shrinks
#: between posts — and each sweep reads every outcome file, so it is not free.
_PRUNE_INTERVAL_SECONDS = 24 * 3600
_last_prune_at = 0.0


def _prune_outbox_occasionally() -> None:
    """Keep the user's outbox from growing without bound.

    Deliberately here rather than in the Space: squashing rewrites the branch the Space's
    compare-and-swap claims against, so it must not run from the process that might be
    mid-post. This one is, by definition, not.
    """
    global _last_prune_at
    if not cloud_poster.is_configured():
        return
    if time.monotonic() - _last_prune_at < _PRUNE_INTERVAL_SECONDS:
        return
    _last_prune_at = time.monotonic()
    result = cloud_poster.prune()
    if result.get("pruned") or result.get("squashed"):
        log.info("[distribution] outbox swept: %s", result)


def _wake_cloud_if_due() -> None:
    """Nudge the Space when something it owns is due soon.

    Free hardware sleeps and cannot be told not to, so the Space's own timer only runs while
    it happens to be awake. This costs one HTTP call and is the difference between "posted at
    3am" and "posted whenever something next touched the Space" — but only while this app is
    running, which is why it is insurance rather than the mechanism.
    """
    if not cloud_poster.is_configured() or not config.CLOUD_POSTER_URL:
        return
    soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    if any((job["scheduled_at"] or "") <= soon for job in db.list_cloud_pending_jobs()):
        cloud_poster.wake()


def _scheduler_loop() -> None:
    while True:
        try:
            _fire_due_scheduled_jobs()
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
            # Trace posts that have gone out back to the drafts that wrote them, which is
            # what the Social Post learning loop measures. Runs here rather than at send
            # time so a slow or failed lookup is never reported as a publishing problem,
            # and costs nothing — not even an import — when there is nothing to link.
            generation_link.link_sent_bluesky_posts()
            generation_link.link_sent_mastodon_posts()
            # Posts the user's own Space owns: read back its outcomes, and wake it if one of
            # them is nearly due. Both are no-ops when cloud posting is not set up.
            _reconcile_cloud_jobs()
            _wake_cloud_if_due()
            _prune_outbox_occasionally()
        except Exception:  # noqa: BLE001 — a bad tick must not kill the scheduler thread
            pass
        time.sleep(30)


def start_scheduler() -> None:
    """Started once at app startup. Polling rather than precise timers is fine here —
    scheduled sends aren't latency-sensitive, and this avoids a whole separate task-queue
    dependency for what's effectively a handful of posts a day."""
    threading.Thread(target=_scheduler_loop, daemon=True).start()
