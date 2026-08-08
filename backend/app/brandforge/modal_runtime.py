"""App-side half of the bring-your-own-Modal path: provision the user's
workspace, report progress, and run sections on it.

Calls go over the Modal SDK (`Function.from_name(...).remote(...)`) rather than
an HTTP endpoint. That is deliberate: a `@modal.fastapi_endpoint` is public, so
a URL-based design would need a shared auth token and a URL to derive, store and
keep in sync. Invoking by name authenticates with the user's own API token
instead, which removes the endpoint, the token and the URL all at once.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import modal
import modal.exception
from modal.runner import deploy_app

from .client import BrandForgeError
from .modal_backend import APP_NAME, FUNCTION_NAME, MAX_NEW_TOKENS, MODEL_PAGE, build_app
from .modal_image_backend import (
    APP_NAME as IMAGE_APP_NAME,
    BUCKET_PAGE as IMAGE_BUCKET_PAGE,
    FUNCTION_NAME as IMAGE_FUNCTION_NAME,
    build_app as build_image_app,
)
from .sections import SECTION_SPECS, build_student_messages

# The first deploy builds a container image and bakes in 3.4 GB of base weights,
# which is slow enough that the UI has to say so up front.
FIRST_DEPLOY_HINT = (
    "The first setup builds text and image GPU containers and can take 5-15 minutes. "
    "Later runs reuse them and are quick."
)


@dataclass(frozen=True)
class ModalConfig:
    token_id: str
    token_secret: str
    hf_token: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.token_id.strip() and self.token_secret.strip())


# One client per credential pair. Building a client opens a connection, so this
# is cached for the process lifetime rather than rebuilt per request.
_clients: dict[tuple[str, str], object] = {}
_clients_lock = threading.Lock()

# Provisioning runs in a background thread because deploy_app() blocks for the
# whole image build — far longer than any HTTP request should be held open.
_provision_lock = threading.Lock()
_provision: dict = {"status": "idle", "message": "", "startedAt": None, "finishedAt": None, "appPageUrl": "", "logsUrl": ""}
_provision_thread: Optional[threading.Thread] = None


def _get_client(cfg: ModalConfig):
    if not cfg.configured:
        raise BrandForgeError("Add your Modal API token in Settings > Brand Studio GPU.")
    key = (cfg.token_id.strip(), cfg.token_secret.strip())
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            try:
                client = modal.Client.from_credentials(*key)
            except modal.exception.AuthError as exc:
                raise BrandForgeError(
                    f"Modal rejected those credentials. Check the token ID and secret in Settings. ({exc})"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - network, version mismatch, ...
                raise BrandForgeError(f"Couldn't connect to Modal: {exc}") from exc
            _clients[key] = client
        return client


def _explain(exc: Exception) -> str:
    """Modal's own wording is usually the useful part, but the one failure users
    will actually hit is the gated model: the image build downloads the weights
    with their HF token, and that 401s until they have accepted the terms once.
    Raw Modal build output buries that, so name it explicitly."""
    text = str(exc)
    low = text.lower()
    if "401" in text or "gated" in low or "restricted" in low or "access to model" in low:
        return (
            "Couldn't download model weights. Confirm that your Hugging Face token can read "
            f"{MODEL_PAGE} and {IMAGE_BUCKET_PAGE}, then run setup again. ({text})"
        )
    if "model_index.json" in low or "not found" in low or "repository" in low or "bucket" in low:
        return (
            "Modal couldn't load the image model. Put a standard Diffusers export in "
            f"{IMAGE_BUCKET_PAGE}, then run setup again. ({text})"
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
    if started:
        state["elapsedSeconds"] = int((finished or time.time()) - started)
    else:
        state["elapsedSeconds"] = 0
    state["hint"] = FIRST_DEPLOY_HINT
    return state


def _provision_worker(cfg: ModalConfig) -> None:
    try:
        client = _get_client(cfg)
        text_app = build_app(cfg.hf_token)
        text_result = deploy_app(text_app, name=APP_NAME, client=client)
        image_app = build_image_app(cfg.hf_token)
        image_result = deploy_app(image_app, name=IMAGE_APP_NAME, client=client)
        _set_provision(
            status="ready",
            message="Your Modal text and image GPU backends are deployed and ready.",
            finishedAt=time.time(),
            appPageUrl=(
                getattr(image_result, "app_page_url", "")
                or getattr(text_result, "app_page_url", "")
                or ""
            ),
            logsUrl=(
                getattr(image_result, "app_logs_url", "")
                or getattr(text_result, "app_logs_url", "")
                or ""
            ),
        )
    except BrandForgeError as exc:
        _set_provision(status="error", message=str(exc), finishedAt=time.time())
    except Exception as exc:  # noqa: BLE001 - surface Modal's own wording, it is usually the useful part
        _set_provision(status="error", message=_explain(exc), finishedAt=time.time())


def start_provision(cfg: ModalConfig) -> dict:
    """Kick off (or report an already-running) deploy. Returns the status snapshot."""
    global _provision_thread
    if not cfg.configured:
        raise BrandForgeError("Add your Modal token ID and token secret first.")

    with _provision_lock:
        if _provision.get("status") == "running":
            return provision_status()
        _provision.update(
            status="running",
            message="Deploying to your Modal workspace...",
            startedAt=time.time(),
            finishedAt=None,
            appPageUrl="",
            logsUrl="",
        )

    _provision_thread = threading.Thread(target=_provision_worker, args=(cfg,), daemon=True)
    _provision_thread.start()
    return provision_status()


def generate_section(cfg: ModalConfig, intake: dict, section_name: str) -> str:
    """One section on the user's own GPU. Raises BrandForgeError on failure."""
    if section_name not in SECTION_SPECS:
        raise BrandForgeError(f"Unknown section: {section_name}")

    messages = build_student_messages(intake, section_name, SECTION_SPECS[section_name])
    client = _get_client(cfg)

    try:
        fn = modal.Function.from_name(APP_NAME, FUNCTION_NAME, client=client)
        result = fn.remote(messages, MAX_NEW_TOKENS)
    except modal.exception.NotFoundError as exc:
        raise BrandForgeError(
            "Your Modal workspace has no BrandForge backend yet. Open Settings > Brand Studio GPU "
            'and click "Set up my GPU".'
        ) from exc
    except modal.exception.AuthError as exc:
        raise BrandForgeError(f"Modal rejected your API token. Check it in Settings. ({exc})") from exc
    except Exception as exc:  # noqa: BLE001 - out of credit, GPU unavailable, timeout, ...
        raise BrandForgeError(f"Modal generation failed: {exc}") from exc

    text = result if isinstance(result, str) else str(result)
    if not text.strip():
        raise BrandForgeError("The model returned an empty section. Try again.")
    return text


def generate_image(
    cfg: ModalConfig,
    prompt: str,
    width: int,
    height: int,
    *,
    seed: int | None = None,
    steps: int = 4,
) -> bytes:
    """Render an image in the user's Modal workspace and return PNG bytes."""
    client = _get_client(cfg)
    try:
        fn = modal.Function.from_name(IMAGE_APP_NAME, IMAGE_FUNCTION_NAME, client=client)
        result = fn.remote(prompt, width, height, seed, steps)
    except modal.exception.NotFoundError as exc:
        raise BrandForgeError(
            "Your Modal workspace has no image backend yet. Open Settings > Brand Studio GPU "
            'and click "Set up my GPU".'
        ) from exc
    except modal.exception.AuthError as exc:
        raise BrandForgeError(f"Modal rejected your API token. Check it in Settings. ({exc})") from exc
    except Exception as exc:  # noqa: BLE001 - surface upstream GPU failures clearly
        raise BrandForgeError(f"Modal image generation failed: {exc}") from exc

    if not isinstance(result, (bytes, bytearray)) or not result:
        raise BrandForgeError("The Modal image model returned no PNG data. Try again.")
    return bytes(result)
