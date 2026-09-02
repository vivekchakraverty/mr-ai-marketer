"""The outbox: a private dataset repo used as a queue.

WHY A DATASET AND NOT THIS SPACE'S DISK. A Space's container disk does not survive a restart
or a redeploy, and a post scheduled for next Thursday has to. A dataset repo is durable,
private, versioned, and — the part that matters most — it is the boundary rather than this
Space's HTTP surface. The app writes jobs into it directly with the user's own token and
reads outcomes back the same way, so nothing confidential is ever exposed over HTTP here.
That is what lets /tick be unauthenticated.

Layout:

    queue/<job_id>.json           waiting to fire
    claims/<job_id>.json          written immediately before posting
    outcomes/<job_id>.json        terminal result; the app reads these back
    media/<job_id>/<filename>     the attachment
    state/bluesky_session.json    the rotated refresh token
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, hf_hub_download
from huggingface_hub.utils import EntryNotFoundError, HfHubHTTPError

log = logging.getLogger(__name__)

OUTBOX_REPO = os.environ.get("OUTBOX_REPO", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()

_api = HfApi(token=HF_TOKEN or None)


def configured() -> bool:
    return bool(OUTBOX_REPO and HF_TOKEN)


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        local = hf_hub_download(
            repo_id=OUTBOX_REPO, repo_type="dataset", filename=path, token=HF_TOKEN
        )
    except (EntryNotFoundError, HfHubHTTPError, OSError):
        return None
    try:
        with open(local, encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return None


def head_sha() -> str:
    """The outbox's current commit, or '' if it cannot be read.

    Used to skip a pass entirely when nothing has changed since the last one: an idle tick
    then costs a single repo_info call rather than a listing plus a download per job.
    """
    try:
        return _api.repo_info(OUTBOX_REPO, repo_type="dataset").sha or ""
    except HfHubHTTPError:
        return ""


def list_queue() -> list[str]:
    """Job ids waiting to fire, oldest commit first."""
    try:
        files = _api.list_repo_files(OUTBOX_REPO, repo_type="dataset")
    except HfHubHTTPError as err:
        log.warning("outbox unreadable: %s", err)
        return []
    return sorted(
        f[len("queue/") : -len(".json")]
        for f in files
        if f.startswith("queue/") and f.endswith(".json")
    )


def job(job_id: str) -> dict[str, Any] | None:
    return _read_json(f"queue/{job_id}.json")


def claimed(job_id: str) -> bool:
    return _read_json(f"claims/{job_id}.json") is not None


def claim(job_id: str) -> bool:
    """Mark a job as being posted right now. False means someone else got there first.

    The compare-and-swap is `parent_commit`: if the outbox moved between reading the head and
    writing the claim — the app cancelling the job is the case that matters — the commit is
    rejected and this returns False.

    NOTE the CAS is repo-wide, not per-file, so any concurrent write to the outbox loses this
    race, not only a conflicting one. That is deliberate and fine at a handful of posts a day:
    the loser simply retries on the next tick. Do not "fix" it into an unconditional commit,
    which is how a cancelled post gets published anyway.
    """
    sha = head_sha()
    if not sha:
        return False
    try:
        _api.create_commit(
            repo_id=OUTBOX_REPO,
            repo_type="dataset",
            parent_commit=sha,
            commit_message=f"claim {job_id}",
            operations=[
                CommitOperationAdd(
                    path_in_repo=f"claims/{job_id}.json",
                    path_or_fileobj=json.dumps({"job": job_id}).encode("utf-8"),
                )
            ],
        )
        return True
    except HfHubHTTPError as err:
        log.info("could not claim %s (someone else moved the outbox): %s", job_id, err)
        return False


def media_path(job_id: str, filename: str) -> str | None:
    """Download this job's attachment and return the local path, or None."""
    try:
        return hf_hub_download(
            repo_id=OUTBOX_REPO,
            repo_type="dataset",
            filename=f"media/{job_id}/{filename}",
            token=HF_TOKEN,
        )
    except (EntryNotFoundError, HfHubHTTPError, OSError) as err:
        log.warning("media for %s unreadable: %s", job_id, err)
        return None


def finish(job_id: str, outcome: dict[str, Any], media_files: list[str]) -> None:
    """Record a terminal result and remove everything the job still occupies.

    One commit rather than three, so an outcome can never exist alongside a live queue entry.
    A crash between two separate commits would leave a job that has already been posted still
    sitting in the queue, and the next tick would post it again.
    """
    ops: list[Any] = [
        CommitOperationAdd(
            path_in_repo=f"outcomes/{job_id}.json",
            path_or_fileobj=json.dumps(outcome, indent=1).encode("utf-8"),
        ),
        CommitOperationDelete(path_in_repo=f"queue/{job_id}.json"),
        CommitOperationDelete(path_in_repo=f"claims/{job_id}.json", is_folder=False),
    ]
    for name in media_files:
        ops.append(CommitOperationDelete(path_in_repo=f"media/{job_id}/{name}"))
    try:
        _api.create_commit(
            repo_id=OUTBOX_REPO,
            repo_type="dataset",
            commit_message=f"{outcome.get('status', 'done')} {job_id}",
            operations=ops,
        )
    except HfHubHTTPError as err:
        # The post itself already happened. Losing this commit means the app will not see the
        # outcome yet and the next tick will try again — which the idempotency key makes safe.
        log.error("could not record outcome for %s: %s", job_id, err)


def bluesky_session() -> dict[str, Any]:
    return _read_json("state/bluesky_session.json") or {}


def save_bluesky_session(data: dict[str, Any]) -> None:
    """Persist the rotated refresh token.

    Into the outbox rather than back into a Space secret: writing a secret restarts the Space,
    and doing that in the middle of a tick would abandon whatever else was due.
    """
    try:
        _api.upload_file(
            repo_id=OUTBOX_REPO,
            repo_type="dataset",
            path_in_repo="state/bluesky_session.json",
            path_or_fileobj=json.dumps(data, indent=1).encode("utf-8"),
            commit_message="rotate bluesky session",
        )
    except HfHubHTTPError as err:
        log.error("could not persist the rotated Bluesky session: %s", err)
