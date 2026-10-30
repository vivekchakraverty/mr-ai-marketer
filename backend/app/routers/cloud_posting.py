"""Setting up, and connecting credentials to, the user's own poster Space.

The credentials handed over here are deliberately NOT the ones the rest of the app uses:

  * Mastodon — a second application, scoped to write:statuses and write:media, which cannot
    read the account at all. Verified behaviourally below rather than taken on trust.
  * Bluesky — a refresh session minted from the app password, so the password itself never
    leaves this machine. It rotates on every use and dies with the app password.

Both go straight into the Space's own secrets and are never returned to the renderer, never
written to the app's database, and never put in the outbox.
"""

from __future__ import annotations

import logging

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import cloud_poster, hf_spaces

log = logging.getLogger(__name__)

router = APIRouter(prefix="/cloud-posting", tags=["cloud-posting"])


class ProvisionRequest(BaseModel):
    """`hfToken` creates the repos; `spaceToken` is what the Space itself will hold."""

    hfToken: str = ""
    spaceToken: str = ""
    name: str = "mr-ai-marketer-poster"


class MastodonConnectRequest(BaseModel):
    hfToken: str = ""
    spaceId: str = ""
    instance: str = ""
    accessToken: str = ""


class BlueskyConnectRequest(BaseModel):
    hfToken: str = ""
    spaceId: str = ""
    identifier: str = ""
    appPassword: str = ""
    pdsHost: str = ""


@router.post("/provision")
def provision(body: ProvisionRequest) -> dict:
    try:
        return hf_spaces.start(body.hfToken, body.spaceToken, body.name)
    except hf_spaces.SpaceProvisionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None


@router.get("/provision-status")
def provision_status() -> dict:
    return hf_spaces.status()


@router.post("/connect/mastodon")
def connect_mastodon(body: MastodonConnectRequest) -> dict:
    """Check the token really is write-only, then hand it to the Space.

    Two calls, and the interesting one is the second: /api/v1/apps/verify_credentials needs no
    scope at all, so a 200 there only proves the token is real. It is
    /api/v1/accounts/verify_credentials returning 403 that proves the token CANNOT read the
    account — which is the property being asked for. A 200 means the user left `read` ticked;
    that still posts perfectly well, so it is reported as a caution rather than refused.
    """
    host = body.instance.strip().replace("https://", "").replace("http://", "").split("/")[0].lower()
    token = body.accessToken.strip()
    if not host or not token:
        raise HTTPException(status_code=400, detail="Both the instance and the access token are needed.")

    headers = {"Authorization": f"Bearer {token}", "User-Agent": "MrAIMarketer/1.0"}
    try:
        app_check = requests.get(f"https://{host}/api/v1/apps/verify_credentials", headers=headers, timeout=20)
    except requests.RequestException as err:
        raise HTTPException(status_code=400, detail=f"Could not reach {host}: {err}") from None
    if app_check.status_code != 200:
        raise HTTPException(status_code=400, detail=f"{host} did not accept that token.")

    try:
        read_check = requests.get(
            f"https://{host}/api/v1/accounts/verify_credentials", headers=headers, timeout=20
        )
        can_read = read_check.status_code == 200
    except requests.RequestException:
        can_read = False

    if not body.spaceId.strip():
        raise HTTPException(status_code=400, detail="Set up your poster Space first.")
    try:
        hf_spaces.push_variable(body.hfToken, body.spaceId, "MASTODON_HOST", host)
        hf_spaces.push_secret(body.hfToken, body.spaceId, "MASTODON_TOKEN", token)
    except hf_spaces.SpaceProvisionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None

    return {
        "connected": True,
        "instance": host,
        "writeOnly": not can_read,
        "detail": (
            f"Connected to {host}. This token can only post — it cannot read your account."
            if not can_read
            else f"Connected to {host}. Note this token can also READ your account; you can "
            f"narrow it to write:statuses and write:media and reconnect."
        ),
    }


@router.post("/connect/bluesky")
def connect_bluesky(body: BlueskyConnectRequest) -> dict:
    """Mint a refresh session from the app password, and send only the session onward.

    createSession is heavily rate limited (30 per 5 minutes per account), which is why this
    runs once at connect time rather than per post — the Space refreshes from here on.
    """
    pds = (body.pdsHost.strip() or "https://bsky.social").rstrip("/")
    identifier = body.identifier.strip()
    password = body.appPassword.strip()

    # The walkthrough hands over whatever the connect form above holds, and that form is
    # prefilled from Settings — where a SECRET arrives as SETTINGS_PLACEHOLDER, never as
    # itself, so the renderer cannot leak a stored app password. Sent on unresolved, that
    # literal string would be offered to Bluesky as the password and rejected, which would
    # read as "your app password is wrong" for a password the user never mistyped.
    from .distribution import SETTINGS_PLACEHOLDER, _resolve_placeholders

    if password == SETTINGS_PLACEHOLDER or identifier == SETTINGS_PLACEHOLDER:
        resolved = _resolve_placeholders(
            "bluesky", {"identifier": identifier, "password": password}
        )
        identifier = str(resolved.get("identifier") or "").strip()
        password = str(resolved.get("password") or "").strip()
        if not password:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No Bluesky app password is stored yet. Type one into the form above, "
                    "then connect."
                ),
            )
    if not identifier or not password:
        raise HTTPException(status_code=400, detail="Both the handle and the app password are needed.")
    if not body.spaceId.strip():
        raise HTTPException(status_code=400, detail="Set up your poster Space first.")

    try:
        resp = requests.post(
            f"{pds}/xrpc/com.atproto.server.createSession",
            json={"identifier": identifier, "password": password},
            timeout=30,
        )
    except requests.RequestException as err:
        raise HTTPException(status_code=400, detail=f"Could not reach {pds}: {err}") from None
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail="Bluesky did not accept that handle and app password.")

    session = resp.json() or {}
    did, refresh = str(session.get("did") or ""), str(session.get("refreshJwt") or "")
    if not did or not refresh:
        raise HTTPException(status_code=400, detail="Bluesky did not return a usable session.")

    try:
        hf_spaces.push_variable(body.hfToken, body.spaceId, "BLUESKY_PDS", pds)
        hf_spaces.push_variable(body.hfToken, body.spaceId, "BLUESKY_DID", did)
        hf_spaces.push_secret(body.hfToken, body.spaceId, "BLUESKY_REFRESH_JWT", refresh)
    except hf_spaces.SpaceProvisionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None

    # The app password itself is not returned, not stored here, and not sent to the Space.
    return {
        "connected": True,
        "did": did,
        "detail": (
            "Connected. Your Space holds a session that renews itself, not your app password — "
            "revoking the app password on bsky.app ends it."
        ),
    }


class CredentialsRequest(BaseModel):
    spaceId: str = ""
    spaceUrl: str = ""
    posterKey: str = ""
    outboxRepo: str = ""
    spaceToken: str = ""


@router.post("/credentials")
def set_credentials(body: CredentialsRequest) -> dict:
    """Give this process the active poster Space, without a restart.

    Electron sets these in the backend's environment at spawn, which is enough for a machine
    that was already set up — and useless for the launch where the walkthrough creates the
    Space, because that happens after spawn. Without this the wizard would appear to succeed
    and every scheduled post until the next restart would quietly go to the local scheduler.

    Same shape as /distribution/mastodon-credentials: in memory only, per launch, re-sent on
    every change. The token is never written to the database or returned.
    """
    ready = cloud_poster.set_credentials(
        space_id=body.spaceId,
        url=body.spaceUrl,
        key=body.posterKey,
        outbox=body.outboxRepo,
        token=body.spaceToken,
    )
    return {"ready": ready}


@router.post("/wake")
def wake() -> dict:
    return {"woken": cloud_poster.wake()}


@router.get("/space-status")
def space_status() -> dict:
    return cloud_poster.space_status()


class RestartRequest(BaseModel):
    """`hfToken` must be able to write to the account. `spaceId` is optional — the active
    Space is used when it is omitted, which is what the Settings panel does."""

    hfToken: str = ""
    spaceId: str = ""


@router.post("/restart")
def restart(body: RestartRequest) -> dict:
    """Bring a paused or crashed poster Space back up.

    The app could not do this at all until now, which is the gap that let a paused Space stop
    scheduled posting silently: /wake pings a URL, and a paused Space does not answer pings.
    Only healthmon's tray could restart one, and it was not watching the poster.

    The queue needs no attention afterwards. The Space fires everything whose time has passed
    on its first pass, and both networks are idempotent per job id, so a post stranded by the
    pause goes out exactly once however many times this is called.
    """
    target = body.spaceId.strip() or cloud_poster.space_id()
    if not target:
        raise HTTPException(status_code=400, detail="There is no poster Space to restart yet.")
    try:
        hf_spaces.restart(body.hfToken, target)
    except hf_spaces.SpaceProvisionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    return {
        "restarted": True,
        "spaceId": target,
        "detail": (
            "Restarting. It takes a minute or two to come up, and then everything that was "
            "due while it was down goes out."
        ),
    }
