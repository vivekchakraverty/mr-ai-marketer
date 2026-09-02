"""Give a user their own poster Space, built from the source shipped with this app.

WHY A SPACE PER USER RATHER THAN ONE WE RUN. To post while the desktop app is closed,
something in the cloud has to hold a credential that can post. A single shared Space would
mean holding everyone's, under one master key, on infrastructure with no key management —
and one compromise would be everyone's accounts at once. The mail-tracker Space already
says this about itself: "there is no per-user boundary on a shared public Space".

WHY BUILT FROM SOURCE RATHER THAN DUPLICATED. The obvious approach is to publish one
template Space and have every install duplicate it. That works, and it means every user's
poster is a copy of whatever the publisher's account happens to contain at that moment —
including, if that account were ever compromised, code that has just been handed their
Mastodon token. Creating the Space from `resources/poster-space/`, which ships inside the
app and is readable in the repo, removes the trusted third party entirely: the code that
gets the credentials is the code the user already has. It also means there is no template
to keep alive, and no `CLOUD_POSTER_TEMPLATE_SPACE` to configure.

The Space is created PUBLIC and the outbox dataset PRIVATE, which is the right way round:
the Space is open-source code holding no data (its secrets stay private whatever the repo's
visibility), while the queue and the media are the confidential part.

huggingface_hub is imported inside the functions, never at module scope, matching the
lazy-import rule the Email Writer's import-weight regression established.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()
_provision: dict = {"status": "idle", "message": "", "startedAt": None, "finishedAt": None}


class SpaceProvisionError(RuntimeError):
    """Written to be shown to a user."""


def poster_source_dir() -> Path:
    """Where the Space's source lives, in a checkout and once packaged.

    Same shape as activepieces_client._resolve_flows_dir, and for the same reason: PyInstaller
    rewrites __file__ to sit under _MEIPASS, so a fixed walk up from here is right in a source
    checkout and wrong in the shipped app. Candidates plus a loud error rather than one clever
    path expression — an empty directory would otherwise produce a Space that builds into
    nothing and fails much later, looking like a Hugging Face problem.
    """
    override = os.environ.get("CLOUD_POSTER_SOURCE_DIR", "").strip()
    if override:
        return Path(override)

    here = Path(__file__).resolve()
    # Source checkout: backend/app/services/x.py -> <repo>/resources/poster-space
    candidates = [here.parent.parent.parent.parent / "resources" / "poster-space"]
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", here.parent))
        candidates += [
            # Packaged: <app>/resources/backend/_internal -> <app>/resources/poster-space
            base.parent.parent / "poster-space",
            base / "resources" / "poster-space",
        ]
    for candidate in candidates:
        if (candidate / "main.py").is_file():
            return candidate

    log.error(
        "[cloud-posting] poster Space source not found; looked in: %s",
        ", ".join(str(c) for c in candidates),
    )
    return candidates[0]


def _api(token: str):
    from huggingface_hub import HfApi

    return HfApi(token=token)


def space_url(space_id: str) -> str:
    """The https URL a Space id resolves to.

    Hugging Face lowercases the slug and replaces dots and underscores with hyphens, so this
    cannot be built by string-joining the id as typed.
    """
    owner, _, name = space_id.partition("/")
    slug = f"{owner}-{name}".lower().replace(".", "-").replace("_", "-")
    return f"https://{slug}.hf.space"


def status() -> dict:
    """Snapshot for the wizard to poll, shaped like brandforge's provision_status."""
    with _lock:
        state = dict(_provision)
    started, finished = state.get("startedAt"), state.get("finishedAt")
    state["elapsedSeconds"] = int((finished or time.time()) - started) if started else 0
    return state


def _set(**fields) -> None:
    with _lock:
        _provision.update(fields)


def _worker(hf_token: str, space_token: str, name: str) -> None:
    try:
        from huggingface_hub.utils import HfHubHTTPError

        source = poster_source_dir()
        if not (source / "main.py").is_file():
            raise SpaceProvisionError(
                "The poster Space source is missing from this installation, so it cannot be created."
            )

        api = _api(hf_token)
        try:
            owner = str(api.whoami().get("name") or "")
        except Exception as err:  # noqa: BLE001 - the hub's own wording is the useful part
            raise SpaceProvisionError(f"That Hugging Face token could not be used: {err}") from None
        if not owner:
            raise SpaceProvisionError("Could not tell which Hugging Face account that token belongs to.")

        space_id = f"{owner}/{name}"
        outbox_repo = f"{owner}/{name}-outbox"
        poster_key = secrets.token_urlsafe(32)

        _set(status="running", message="Creating your private outbox…")
        try:
            api.create_repo(repo_id=outbox_repo, repo_type="dataset", private=True, exist_ok=True)
        except HfHubHTTPError as err:
            raise SpaceProvisionError(f"Could not create your outbox dataset: {err}") from None

        _set(message="Creating your poster Space…")
        try:
            api.create_repo(
                repo_id=space_id,
                repo_type="space",
                space_sdk="docker",
                private=False,
                exist_ok=True,
            )
        except HfHubHTTPError as err:
            raise SpaceProvisionError(f"Could not create the Space in your account: {err}") from None

        # Secrets and variables are pushed EXPLICITLY, never through create_repo's
        # space_secrets argument.
        #
        # That argument only takes effect when the repo is actually created. With
        # exist_ok=True a second run returns the existing Space and silently ignores it — so
        # re-running setup generated a fresh POSTER_KEY, saved it on this machine, and left
        # the Space holding the old one. Everything then looked configured and /status
        # answered 401 forever, reported to the user as "your Space did not recognise this
        # app's key". Pushing every time makes a re-run self-healing, which is what the
        # Settings copy already promises ("safe to re-run").
        _set(message="Setting up your Space's credentials…")
        for key, value in (("HF_TOKEN", space_token), ("POSTER_KEY", poster_key)):
            push_secret(hf_token, space_id, key, value)
        for key, value in (
            ("OUTBOX_REPO", outbox_repo),
            ("SELF_URL", space_url(space_id)),
        ):
            push_variable(hf_token, space_id, key, value)

        _set(message="Uploading the poster…")
        try:
            api.upload_folder(
                repo_id=space_id,
                repo_type="space",
                folder_path=str(source),
                commit_message="Mr AI Marketer poster",
                # Nothing else should ever be in that directory, but a stray __pycache__ from
                # a local import check would otherwise be published too.
                ignore_patterns=["__pycache__/*", "*.pyc"],
            )
        except HfHubHTTPError as err:
            raise SpaceProvisionError(f"Could not upload the poster to your Space: {err}") from None

        _set(
            status="ready",
            message="Your poster Space is building. It will be ready in a minute or two.",
            finishedAt=time.time(),
            spaceId=space_id,
            spaceUrl=space_url(space_id),
            outboxRepo=outbox_repo,
            posterKey=poster_key,
        )
    except SpaceProvisionError as err:
        _set(status="error", message=str(err), finishedAt=time.time())
    except Exception as err:  # noqa: BLE001
        log.exception("poster Space provisioning failed")
        _set(status="error", message=f"Setup failed: {err}", finishedAt=time.time())


def start(hf_token: str, space_token: str, name: str = "mr-ai-marketer-poster") -> dict:
    """Create the Space in the background, or report the run already in flight.

    `hf_token` creates the repos and must be able to write to the account. `space_token` is
    what the Space itself will hold and should be fine-grained and scoped to the outbox alone —
    they are separate arguments precisely so the broad one never becomes a Space secret.
    """
    if not hf_token.strip():
        raise SpaceProvisionError("Add your Hugging Face token first.")
    if not space_token.strip():
        raise SpaceProvisionError(
            "The Space needs its own fine-grained token, scoped to your own repos."
        )

    with _lock:
        if _provision.get("status") == "running":
            return status()
        _provision.update(status="running", message="Starting…", startedAt=time.time(), finishedAt=None)

    threading.Thread(
        target=_worker, args=(hf_token.strip(), space_token.strip(), name), daemon=True
    ).start()
    return status()


def push_secret(hf_token: str, space_id: str, key: str, value: str) -> None:
    """Set one Space secret. This restarts the Space, which is why credentials are pushed at
    the end of a connect step rather than per keystroke."""
    from huggingface_hub.utils import HfHubHTTPError

    try:
        _api(hf_token).add_space_secret(repo_id=space_id, key=key, value=value)
    except HfHubHTTPError as err:
        raise SpaceProvisionError(f"Could not set {key} on your Space: {err}") from None


def push_variable(hf_token: str, space_id: str, key: str, value: str) -> None:
    from huggingface_hub.utils import HfHubHTTPError

    try:
        _api(hf_token).add_space_variable(repo_id=space_id, key=key, value=value)
    except HfHubHTTPError as err:
        raise SpaceProvisionError(f"Could not set {key} on your Space: {err}") from None
