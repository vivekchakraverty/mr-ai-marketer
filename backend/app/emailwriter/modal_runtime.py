"""App-side half of the Email Writer's bring-your-own-Modal path.

Calls go over the Modal SDK (`Function.from_name(...).remote(...)`) rather than an HTTP
endpoint, for the reason the Brand Studio runtime gives: a `@modal.fastapi_endpoint` is
public, so a URL-based design needs a shared auth token and a URL to derive, store and keep
in sync. Invoking by name authenticates with the user's own API token instead, which removes
the endpoint, the token and the URL all at once.

The Space stays the default and the fallback. Modal is faster and unshared, but it spends
credits that can run out; the Space is slow and contended and free forever. A user with no
Modal token configured should notice nothing about any of this.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import modal
import modal.exception
from modal.runner import deploy_app

from .modal_backend import APP_NAME, FUNCTION_NAME, MAX_NEW_TOKENS, MODEL_PAGE, build_app

# The first deploy builds a CUDA image and bakes ~4.5 GB of weights into it. Minutes, not
# seconds, and the UI has to say so rather than leave a progress bar looking stuck.
FIRST_DEPLOY_HINT = (
    "The first setup builds a GPU container and downloads about 4.5 GB of model weights — "
    "usually a few minutes. Later runs reuse it and start in seconds."
)


class EmailWriterModalError(RuntimeError):
    """Something went wrong on the Modal path. The message is written for a person."""


@dataclass(frozen=True)
class ModalConfig:
    token_id: str
    token_secret: str
    hf_token: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.token_id.strip() and self.token_secret.strip())


# One client per credential pair: building one opens a connection, so it is cached for the
# process lifetime rather than rebuilt per request.
_clients: dict[tuple[str, str], object] = {}
_clients_lock = threading.Lock()

# Provisioning runs on a background thread because deploy_app() blocks for the whole image
# build — far longer than any HTTP request should be held open.
_provision_lock = threading.Lock()
_provision: dict = {
    "status": "idle",
    "message": "",
    "startedAt": None,
    "finishedAt": None,
    "appPageUrl": "",
}
_provision_thread: Optional[threading.Thread] = None


def _get_client(cfg: ModalConfig):
    if not cfg.configured:
        raise EmailWriterModalError("Add your Modal API token in Settings.")
    key = (cfg.token_id.strip(), cfg.token_secret.strip())
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            try:
                client = modal.Client.from_credentials(*key)
            except modal.exception.AuthError as exc:
                raise EmailWriterModalError(
                    f"Modal rejected those credentials. Check the token ID and secret in "
                    f"Settings. ({exc})"
                ) from exc
            except Exception as exc:  # noqa: BLE001 — network, version mismatch, ...
                raise EmailWriterModalError(f"Couldn't connect to Modal: {exc}") from exc
            _clients[key] = client
        return client


def _explain(exc: Exception) -> str:
    """Modal's own wording is usually the useful part, except for the one failure a user
    will actually hit: the weights repo is private, so the image build 401s unless their
    Hugging Face token can read it. Raw build output buries that."""
    text = str(exc)
    low = text.lower()
    if "401" in text or "403" in text or "gated" in low or "not found" in low:
        return (
            "Couldn't download the model weights. Confirm your Hugging Face token can read "
            f"{MODEL_PAGE}, then run setup again. ({text})"
        )
    return f"Modal setup failed: {text}"


def _set_provision(**fields) -> None:
    with _provision_lock:
        _provision.update(fields)


def provision_status() -> dict:
    """Snapshot for the Settings screen to poll."""
    with _provision_lock:
        state = dict(_provision)
    started, finished = state.get("startedAt"), state.get("finishedAt")
    state["elapsedSeconds"] = int((finished or time.time()) - started) if started else 0
    state["hint"] = FIRST_DEPLOY_HINT
    return state


def _provision_worker(cfg: ModalConfig) -> None:
    try:
        client = _get_client(cfg)
        result = deploy_app(build_app(cfg.hf_token), name=APP_NAME, client=client)
        _set_provision(
            status="ready",
            message="Your Modal GPU backend is deployed and ready.",
            finishedAt=time.time(),
            appPageUrl=getattr(result, "app_page_url", "") or "",
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the user as a status message
        _set_provision(status="error", message=_explain(exc), finishedAt=time.time())


def provision(cfg: ModalConfig) -> dict:
    """Start a deploy into the user's workspace. Returns immediately; poll provision_status."""
    global _provision_thread

    if not cfg.configured:
        raise EmailWriterModalError("Add your Modal API token in Settings.")
    if _provision_thread is not None and _provision_thread.is_alive():
        return provision_status()

    _set_provision(
        status="running",
        message="Deploying to your Modal workspace...",
        startedAt=time.time(),
        finishedAt=None,
        appPageUrl="",
    )
    _provision_thread = threading.Thread(target=_provision_worker, args=(cfg,), daemon=True)
    _provision_thread.start()
    return provision_status()


def generate_email(cfg: ModalConfig, instruction: str) -> str:
    """One email on the user's own GPU. Raises EmailWriterModalError on failure."""
    client = _get_client(cfg)
    try:
        fn = modal.Function.from_name(APP_NAME, FUNCTION_NAME, client=client)
        result = fn.remote(instruction, MAX_NEW_TOKENS)
    except modal.exception.NotFoundError as exc:
        raise EmailWriterModalError(
            "Your Modal workspace has no Email Writer backend yet. Open Settings and run "
            "the Email Writer GPU setup."
        ) from exc
    except modal.exception.AuthError as exc:
        raise EmailWriterModalError(
            f"Modal rejected your API token. Check it in Settings. ({exc})"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — out of credit, GPU unavailable, timeout, ...
        raise EmailWriterModalError(f"Modal generation failed: {exc}") from exc

    text = result if isinstance(result, str) else str(result)
    if not text.strip():
        raise EmailWriterModalError("The model returned an empty email — try again.")
    return text
