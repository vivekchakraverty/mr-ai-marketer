"""The app's half of the cloud outbox.

The desktop app writes jobs and media into a private dataset the user owns; the poster Space
reads them, posts, and writes outcomes back. This module is the writing and the reading-back.

WHY MEDIA IS PREPARED HERE AND NOT IN THE SPACE. `_payload_for` in routers/distribution.py
already applies each network's rules — Bluesky's 1MB image ceiling via
image_prompt.prepare_bluesky_image, the aspect ratio via video_attach.probe_aspect_ratio, the
per-network byte caps in video_attach. If the Space re-derived any of that there would be two
implementations of the same rule, and the same draft would come out differently depending on
whether it was sent now or scheduled. So the bytes uploaded here are the finished bytes.

This also means the cloud path never touches share_links/share_server. That machinery exists
because the Activepieces container cannot reach 127.0.0.1; a Space reading a dataset has no
such problem — and it sidesteps the 30-minute signed-link TTL, which a post scheduled a week
out would have outlived.

huggingface_hub is imported inside functions. CI installs a minimal dependency set without
it, so a module-scope import would fail collection for everything that reaches this router.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from .. import config

log = logging.getLogger(__name__)

# Where the outbox settings actually come from at runtime.
#
# config.CLOUD_POSTER_* are read from the environment ONCE, at import, and Electron builds
# that environment when it spawns the backend. So a Space provisioned during a session — which
# is exactly what the setup walkthrough does — would be invisible until the app was restarted,
# and every scheduled post in between would quietly go to the local scheduler instead. The
# walkthrough would have appeared to work and changed nothing.
#
# Electron therefore hands these over as well as setting them at spawn, the same shape as
# mastodon_delivery: in memory only, per launch, and re-sent whenever they change. The
# environment stays the fallback so a dev run without Electron behaves as before.
_lock = RLock()
_runtime: dict[str, str] = {}


def set_credentials(
    space_id: str = "", url: str = "", key: str = "", outbox: str = "", token: str = ""
) -> bool:
    """Replace the active poster Space. Blank outbox or token clears it."""
    global _runtime
    with _lock:
        if not outbox.strip() or not token.strip():
            _runtime = {}
            return False
        _runtime = {
            "CLOUD_POSTER_SPACE": space_id.strip(),
            "CLOUD_POSTER_URL": url.strip().rstrip("/"),
            "CLOUD_POSTER_KEY": key.strip(),
            "CLOUD_POSTER_OUTBOX": outbox.strip(),
            "CLOUD_POSTER_TOKEN": token.strip(),
        }
        return True


def _setting(name: str) -> str:
    with _lock:
        return _runtime.get(name) or str(getattr(config, name, ""))


class CloudPosterError(RuntimeError):
    """Written to be shown to a user."""


def is_configured() -> bool:
    return bool(_setting("CLOUD_POSTER_OUTBOX") and _setting("CLOUD_POSTER_TOKEN"))


def _api():
    from huggingface_hub import HfApi

    return HfApi(token=_setting("CLOUD_POSTER_TOKEN"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attachment(payload: dict, channel: str) -> tuple[str, bytes] | None:
    """The finished bytes for this post, or None when it carries no media."""
    from . import image_prompt, video_attach

    video_url = str(payload.get("videoUrl") or "").strip()
    if video_url:
        ceiling = (
            video_attach.BLUESKY_MAX_BYTES
            if channel == "bluesky"
            else video_attach.MASTODON_DEFAULT_MAX_BYTES
        )
        return video_attach.attachment_bytes(video_url, ceiling, channel.title())

    # Bluesky receives its prepared, size-safe derivative; every other channel the original.
    image_urls = payload.get("imageUrls")
    if channel == "bluesky" and isinstance(image_urls, list) and image_urls:
        return image_prompt.attachment_bytes(str(image_urls[0]))
    image_url = str(payload.get("imageUrl") or "").strip()
    if image_url:
        return image_prompt.attachment_bytes(image_url)
    return None


def enqueue(job_id: str, channel: str, payload: dict, due_at: str) -> None:
    """Put one post in the outbox for the Space to pick up.

    Media first, then the job record: the Space only ever sees a job whose attachment is
    already there, so a pass that starts mid-upload finds nothing rather than a job it cannot
    complete.
    """
    if not is_configured():
        raise CloudPosterError("Cloud posting is not set up yet.")

    from huggingface_hub.utils import HfHubHTTPError

    api = _api()
    repo = _setting("CLOUD_POSTER_OUTBOX")
    record: dict[str, Any] = {
        "job": job_id,
        "channel": channel,
        "text": str(payload.get("text") or ""),
        "dueAt": due_at,
        "queuedAt": _now(),
    }

    try:
        media = _attachment(payload, channel)
    except Exception as err:  # noqa: BLE001 - video_attach/image_prompt raise their own types
        raise CloudPosterError(str(err)) from None

    try:
        if media is not None:
            filename, content = media
            api.upload_file(
                repo_id=repo,
                repo_type="dataset",
                path_in_repo=f"media/{job_id}/{filename}",
                path_or_fileobj=content,
                commit_message=f"media for {job_id}",
            )
            record["mediaFilename"] = filename
            record["mediaAlt"] = str(payload.get("videoFileAlt") or payload.get("imageAlt") or "")
            ratio = payload.get("aspectRatio")
            if isinstance(ratio, dict):
                record["aspectRatio"] = ratio

        api.upload_file(
            repo_id=repo,
            repo_type="dataset",
            path_in_repo=f"queue/{job_id}.json",
            path_or_fileobj=json.dumps(record, indent=1).encode("utf-8"),
            commit_message=f"queue {job_id}",
        )
    except HfHubHTTPError as err:
        raise CloudPosterError(f"Could not reach your outbox: {err}") from None


def outcome(job_id: str) -> dict | None:
    """What the Space recorded, or None while the job is still in flight."""
    if not is_configured():
        return None
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError, HfHubHTTPError

    try:
        path = hf_hub_download(
            repo_id=_setting("CLOUD_POSTER_OUTBOX"),
            repo_type="dataset",
            filename=f"outcomes/{job_id}.json",
            token=_setting("CLOUD_POSTER_TOKEN"),
        )
    except (EntryNotFoundError, HfHubHTTPError, OSError):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return None


def cancel(job_id: str) -> bool:
    """Take a job back out of the outbox.

    False means it could not be removed — almost always because the Space has already claimed
    it and is posting right now, which is exactly when cancelling must not appear to succeed.
    """
    if not is_configured():
        return False
    from huggingface_hub import HfApi  # noqa: F401 - keeps the lazy-import rule obvious
    from huggingface_hub.utils import HfHubHTTPError

    api = _api()
    try:
        files = api.list_repo_files(_setting("CLOUD_POSTER_OUTBOX"), repo_type="dataset")
    except HfHubHTTPError:
        return False
    if f"claims/{job_id}.json" in files:
        return False
    if f"queue/{job_id}.json" not in files:
        # Never queued, or already finished. Either way there is nothing to stop.
        return True
    try:
        api.delete_file(
            path_in_repo=f"queue/{job_id}.json",
            repo_id=_setting("CLOUD_POSTER_OUTBOX"),
            repo_type="dataset",
            commit_message=f"cancel {job_id}",
        )
        return True
    except HfHubHTTPError:
        return False


#: Outcomes are the app's read-back channel, not an archive. A month is long past the point
#: where the local job row has been reconciled and is the only copy that still matters.
OUTCOME_RETENTION_DAYS = 30


def prune() -> dict:
    """Drop finished outcomes, and collapse the outbox's history when it is safe to.

    WHY HISTORY NEEDS COLLAPSING AT ALL. `delete_file` removes a blob from the tree, not from
    the repo's history — so every video ever posted is still stored, forever, in a dataset the
    user pays no attention to. Deleting the queue entry makes the outbox *look* empty while it
    keeps growing by up to 50MB a post.

    WHY ONLY WHEN NOTHING IS PENDING. `super_squash_history` rewrites the branch, and the Space
    claims jobs with a `parent_commit` compare-and-swap against the head it just read. Squashing
    underneath a pass in flight invalidates that head — at best the claim fails and the job
    retries, at worst the squash lands between the post and the outcome commit and the job looks
    unsent. So this runs only when the queue and the claims are both empty, which is most of the
    time and is the only moment it costs nothing to be wrong about.
    """
    if not is_configured():
        return {"pruned": 0, "squashed": False}

    from huggingface_hub.utils import HfHubHTTPError

    api = _api()
    repo = _setting("CLOUD_POSTER_OUTBOX")
    try:
        files = api.list_repo_files(repo, repo_type="dataset")
    except HfHubHTTPError as err:
        log.info("[cloud-posting] outbox unreadable while pruning: %s", err)
        return {"pruned": 0, "squashed": False}

    pending = [f for f in files if f.startswith(("queue/", "claims/", "media/"))]
    outcomes = [f for f in files if f.startswith("outcomes/") and f.endswith(".json")]

    cutoff = datetime.now(timezone.utc) - timedelta(days=OUTCOME_RETENTION_DAYS)
    stale = []
    for path in outcomes:
        recorded = outcome(path[len("outcomes/") : -len(".json")])
        at = str((recorded or {}).get("at") or "")
        try:
            if at and datetime.fromisoformat(at.replace("Z", "+00:00")) < cutoff:
                stale.append(path)
        except ValueError:
            continue

    if stale:
        from huggingface_hub import CommitOperationDelete

        try:
            api.create_commit(
                repo_id=repo,
                repo_type="dataset",
                commit_message=f"prune {len(stale)} finished outcomes",
                operations=[CommitOperationDelete(path_in_repo=path) for path in stale],
            )
        except HfHubHTTPError as err:
            log.info("[cloud-posting] could not prune outcomes: %s", err)
            return {"pruned": 0, "squashed": False}

    squashed = False
    if not pending:
        try:
            api.super_squash_history(repo_id=repo, repo_type="dataset")
            squashed = True
        except HfHubHTTPError as err:
            # Not worth surfacing: the outbox works perfectly well fat, and this will be
            # retried on the next sweep.
            log.info("[cloud-posting] could not squash the outbox history: %s", err)

    return {"pruned": len(stale), "squashed": squashed}


def wake() -> bool:
    """Nudge the Space into running a pass. Best effort — a failure just means it will run on
    its own schedule instead."""
    if not _setting("CLOUD_POSTER_URL"):
        return False
    import requests

    base = _setting("CLOUD_POSTER_URL").rstrip("/")
    try:
        resp = requests.get(f"{base}/tick", timeout=20)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def space_status() -> dict:
    """What the Space says about itself, for the Distribute status row."""
    if not _setting("CLOUD_POSTER_URL") or not _setting("CLOUD_POSTER_KEY"):
        return {"reachable": False, "detail": "Cloud posting is not set up yet."}
    import requests

    try:
        resp = requests.get(
            f"{_setting('CLOUD_POSTER_URL').rstrip('/')}/status",
            headers={"X-Poster-Key": _setting("CLOUD_POSTER_KEY")},
            timeout=25,
        )
    except requests.RequestException as err:
        # A sleeping free Space answers slowly or not at all. That is not a fault, and the
        # queue is safe either way — say so rather than showing an error.
        return {"reachable": False, "detail": f"Your Space is asleep or unreachable: {err}"}
    if resp.status_code == 401:
        return {"reachable": False, "detail": "Your Space did not recognise this app's key."}
    if resp.status_code >= 400:
        return {"reachable": False, "detail": f"Your Space answered HTTP {resp.status_code}."}
    return {"reachable": True, **(resp.json() or {})}
