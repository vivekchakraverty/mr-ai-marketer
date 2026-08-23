"""Reliable Mastodon media delivery for the Distribution scheduler.

Activepieces' Mastodon piece currently uploads a File with a hand-written
``multipart/form-data`` content type.  That header has no boundary, so image uploads are
rejected; for videos the File input can instead resolve to null and the piece publishes a
text-only status.  Distribution cannot accept either outcome.

The app already has a native Mastodon transport used by Engage.  This module reuses that
transport and keeps the active account credential in memory only.  Electron owns the
credential at rest in ``safeStorage`` and hands it over on every launch, matching the
existing Tumblr watcher pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from . import image_prompt, mastodon, video_attach


@dataclass(frozen=True)
class Credentials:
    host: str
    access_token: str


_lock = RLock()
_credentials: Credentials | None = None


def set_credentials(instance: str = "", access_token: str = "") -> bool:
    """Replace the in-memory active account, or clear it when either value is blank."""
    global _credentials

    instance = instance.strip()
    access_token = access_token.strip()
    with _lock:
        if not instance or not access_token:
            _credentials = None
            return False
        _credentials = Credentials(
            host=mastodon.normalise_host(instance),
            access_token=access_token,
        )
        return True


def get_credentials() -> Credentials | None:
    with _lock:
        return _credentials


def has_credentials() -> bool:
    return get_credentials() is not None


def carries_media(payload: dict) -> bool:
    """Whether a distribution payload names a local image or video attachment."""
    return any(
        isinstance(payload.get(field), str) and bool(payload[field].strip())
        for field in ("imageUrl", "videoUrl")
    )


def publish(payload: dict, *, idempotency_key: str) -> dict[str, Any]:
    """Upload and publish one media status, refusing a text-only success.

    ``payload`` must still contain its canonical ``/outputs`` URL.  Reading the local file
    avoids the container/network seam entirely and retains the project's containment rule:
    a compose request cannot name an arbitrary file elsewhere on the machine.
    """
    credentials = get_credentials()
    if credentials is None:
        raise mastodon.MastodonError(
            "Mastodon media posting is not ready. Reconnect Mastodon in Distribute and retry."
        )

    video_url = str(payload.get("videoUrl") or "").strip()
    image_url = str(payload.get("imageUrl") or "").strip()
    if video_url:
        filename, content = video_attach.attachment_bytes(
            video_url,
            video_attach.MASTODON_DEFAULT_MAX_BYTES,
            "Mastodon",
        )
        description = str(payload.get("videoFileAlt") or "").strip()
    elif image_url:
        filename, content = image_prompt.attachment_bytes(image_url)
        description = ""
    else:
        raise mastodon.MastodonError("That Mastodon post has no media attachment to publish.")

    media_id = mastodon.upload_media(
        credentials.host,
        credentials.access_token,
        filename,
        content,
        description=description,
    )
    created = mastodon.api_post(
        credentials.host,
        "/api/v1/statuses",
        credentials.access_token,
        {
            "status": str(payload.get("text") or ""),
            "media_ids": [media_id],
        },
        idempotency_key=idempotency_key,
    ) or {}

    # The API response is the final delivery boundary.  Never turn a connector/server
    # anomaly into another apparently-successful text-only status.  If an id was created,
    # remove that incomplete post before reporting the failure.
    if not created.get("media_attachments"):
        status_id = str(created.get("id") or "").strip()
        if status_id:
            try:
                mastodon.api_delete(
                    credentials.host,
                    f"/api/v1/statuses/{status_id}",
                    credentials.access_token,
                )
            except mastodon.MastodonError:
                pass
        raise mastodon.MastodonError(
            "Mastodon created the status without its attachment; the incomplete post was removed."
        )
    return created
