"""Posting to Mastodon and Bluesky.

Deliberately a small, dependency-light port of what the desktop app already does, not an
import of it — this runs in a Space with four packages installed, and the app's transport
modules pull in the whole backend.

Two things carried over on purpose, because both were learned the hard way:

  * The upload timeout is sized from the payload. A socket timeout is not a deadline for the
    call, it is the deadline for each socket operation, and writing a 29MB body is one of
    them. A flat 20 seconds needs a sustained 12Mbit/s uplink, and when it does not get one
    the failure arrives as SSLWantWriteError rather than as a timeout.
  * Mastodon's v2 media endpoint answers 202 while it transcodes video, and a status that
    attaches an id before processing finishes is refused with "Cannot attach files that have
    not finished processing."

Idempotency is not decoration here. This Space and the desktop app can both believe a job is
due (they should not — see the scheduled_cloud handoff — but a bug in that handoff must not
cost a double post), and a lost response looks exactly like a failure from this side.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "MrAIMarketer-Poster/1.0"

#: Enough for connect, TLS handshake and a small body on a poor line.
UPLOAD_BASE_SECONDS = 60
#: A ceiling, so a stalled transfer cannot hold a tick open forever.
UPLOAD_MAX_SECONDS = 900
#: ~1Mbit/s. Pessimistic on purpose: this is the speed below which waiting is abandoned,
#: not the speed anyone expects. Assuming a good uplink is exactly what broke before.
UPLOAD_FLOOR_BYTES_PER_SECOND = 128 * 1024

MEDIA_PROCESSING_TIMEOUT = 120
MEDIA_POLL_SECONDS = 3


class PostError(RuntimeError):
    """Reported back to the app in the outcome file; written to be read by a person."""


def upload_timeout(size_bytes: int) -> float:
    return min(
        UPLOAD_BASE_SECONDS + size_bytes / UPLOAD_FLOOR_BYTES_PER_SECOND,
        float(UPLOAD_MAX_SECONDS),
    )


def _mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


# ---------------------------------------------------------------------------
# Mastodon
# ---------------------------------------------------------------------------


def post_mastodon(
    host: str,
    token: str,
    text: str,
    media: tuple[str, bytes] | None,
    alt: str,
    idempotency_key: str,
) -> str:
    """Publish one status and return its id."""
    if not host or not token:
        raise PostError("Mastodon is not connected for cloud posting.")

    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
    media_ids: list[str] = []

    if media is not None:
        filename, content = media
        budget = upload_timeout(len(content))
        try:
            with httpx.Client(timeout=budget) as client:
                resp = client.post(
                    f"https://{host}/api/v2/media",
                    headers=headers,
                    files={"file": (filename, content, _mime(filename))},
                    data={"description": alt[:1500]} if alt.strip() else None,
                )
        except httpx.HTTPError as err:
            raise PostError(
                f"Could not reach {host} to upload {filename} "
                f"({len(content) / (1024 * 1024):.1f}MB, gave up after {budget:.0f}s): {err}"
            ) from None
        if resp.status_code not in (200, 202):
            raise PostError(f"{host} refused the attachment: {_detail(resp)}")
        media_id = str((resp.json() or {}).get("id") or "")
        if not media_id:
            raise PostError(f"{host} accepted the attachment but returned no id for it.")
        if resp.status_code == 202:
            _wait_for_media(host, headers, media_id)
        media_ids.append(media_id)

    body: dict[str, Any] = {"status": text}
    if media_ids:
        body["media_ids"] = media_ids
    try:
        with httpx.Client(timeout=60) as client:
            created = client.post(
                f"https://{host}/api/v1/statuses",
                headers={**headers, "Idempotency-Key": idempotency_key},
                json=body,
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {host} to publish: {err}") from None
    if created.status_code >= 400:
        raise PostError(f"{host} refused the post: {_detail(created)}")

    payload = created.json() or {}
    # The same refusal the app's own delivery path makes: never let a connector anomaly turn
    # into an apparently-successful text-only post when media was meant to be attached.
    if media_ids and not payload.get("media_attachments"):
        raise PostError(f"{host} created the status without its attachment.")
    return str(payload.get("id") or "")


def _wait_for_media(host: str, headers: dict[str, str], media_id: str) -> None:
    """Block until the server has finished transcoding. 206 means still working, 200 ready."""
    import time

    deadline = time.monotonic() + MEDIA_PROCESSING_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"https://{host}/api/v1/media/{media_id}", headers=headers)
        except httpx.HTTPError:
            time.sleep(MEDIA_POLL_SECONDS)
            continue
        if resp.status_code == 200:
            return
        if resp.status_code not in (206, 404):
            raise PostError(f"{host} refused the attachment: {_detail(resp)}")
        time.sleep(MEDIA_POLL_SECONDS)
    raise PostError(
        f"{host} is still processing that video after {MEDIA_PROCESSING_TIMEOUT} seconds."
    )


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("error") or body.get("error_description") or resp.status_code)
    except ValueError:
        pass
    return f"HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------


def refresh_bluesky(pds: str, refresh_jwt: str) -> dict[str, Any]:
    """Exchange a refresh token for an access token, and for a new refresh token.

    refreshSession ROTATES: the response carries a fresh refreshJwt and the one just used
    stops working. The caller must persist the new one or the next tick cannot authenticate.
    """
    try:
        with httpx.Client(timeout=45) as client:
            resp = client.post(
                f"{pds}/xrpc/com.atproto.server.refreshSession",
                headers={"Authorization": f"Bearer {refresh_jwt}", "User-Agent": USER_AGENT},
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {pds}: {err}") from None
    if resp.status_code >= 400:
        raise PostError(f"Bluesky would not renew the session: {_detail(resp)}")
    return resp.json() or {}


def post_bluesky(
    pds: str,
    did: str,
    access_jwt: str,
    text: str,
    media: tuple[str, bytes] | None,
    alt: str,
    aspect_ratio: dict[str, int] | None,
    rkey: str,
) -> str:
    """Create one post at a known record key and return its at:// URI.

    putRecord with swapRecord: null rather than createRecord, because the record key is then
    ours to choose and the write is create-only-if-absent. A repeat of the same job lands on
    the same rkey and is refused by the server rather than becoming a second post — which is
    a guarantee the Activepieces path cannot make.
    """
    if not did or not access_jwt:
        raise PostError("Bluesky is not connected for cloud posting.")

    headers = {"Authorization": f"Bearer {access_jwt}", "User-Agent": USER_AGENT}
    record: dict[str, Any] = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": _now_iso(),
    }

    if media is not None:
        filename, content = media
        budget = upload_timeout(len(content))
        try:
            with httpx.Client(timeout=budget) as client:
                blob = client.post(
                    f"{pds}/xrpc/com.atproto.repo.uploadBlob",
                    headers={**headers, "Content-Type": _mime(filename)},
                    content=content,
                )
        except httpx.HTTPError as err:
            raise PostError(
                f"Could not upload {filename} to Bluesky "
                f"({len(content) / (1024 * 1024):.1f}MB, gave up after {budget:.0f}s): {err}"
            ) from None
        if blob.status_code >= 400:
            raise PostError(f"Bluesky refused the attachment: {_detail(blob)}")
        ref = (blob.json() or {}).get("blob")
        if not ref:
            raise PostError("Bluesky accepted the attachment but returned no reference.")
        if _mime(filename).startswith("video/"):
            embed: dict[str, Any] = {"$type": "app.bsky.embed.video", "video": ref}
            if alt.strip():
                embed["alt"] = alt.strip()[:1000]
            if aspect_ratio:
                embed["aspectRatio"] = aspect_ratio
        else:
            image: dict[str, Any] = {"image": ref, "alt": alt.strip()[:1000]}
            if aspect_ratio:
                image["aspectRatio"] = aspect_ratio
            embed = {"$type": "app.bsky.embed.images", "images": [image]}
        record["embed"] = embed

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{pds}/xrpc/com.atproto.repo.putRecord",
                headers=headers,
                json={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "rkey": rkey,
                    "record": record,
                    # Create-only-if-absent. A retry of the same job hits this and is refused.
                    "swapRecord": None,
                },
            )
    except httpx.HTTPError as err:
        raise PostError(f"Could not reach {pds} to publish: {err}") from None

    if resp.status_code >= 400:
        detail = _detail(resp)
        if "InvalidSwap" in detail or "swap" in detail.lower():
            # The record is already there, so this job has already been posted. Success.
            return f"at://{did}/app.bsky.feed.post/{rkey}"
        raise PostError(f"Bluesky refused the post: {detail}")
    return str((resp.json() or {}).get("uri") or f"at://{did}/app.bsky.feed.post/{rkey}")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
