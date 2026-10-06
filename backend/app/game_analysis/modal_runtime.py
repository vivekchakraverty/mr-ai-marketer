"""Private Modal invocation with first-use deployment in the user's workspace."""
from __future__ import annotations

import os
import threading

import modal
import modal.exception
from modal.runner import deploy_app

from . import config

_deploy_lock = threading.Lock()


class GameInferenceError(RuntimeError):
    """A safe, actionable message for the local report UI."""


def credentials_configured() -> bool:
    return bool((os.getenv("GAME_ANALYSIS_MODAL_TOKEN_ID") or os.getenv("MODAL_TOKEN_ID")) and
                (os.getenv("GAME_ANALYSIS_MODAL_TOKEN_SECRET") or os.getenv("MODAL_TOKEN_SECRET")))


def _client():
    token_id = os.getenv("GAME_ANALYSIS_MODAL_TOKEN_ID") or os.getenv("MODAL_TOKEN_ID")
    token_secret = os.getenv("GAME_ANALYSIS_MODAL_TOKEN_SECRET") or os.getenv("MODAL_TOKEN_SECRET")
    if not token_id or not token_secret:
        raise GameInferenceError("Modal credentials are missing. Add them in Settings > Brand Studio GPU, then restart the app.")
    try:
        return modal.Client.from_credentials(token_id, token_secret)
    except modal.exception.AuthError as exc:
        raise GameInferenceError("Modal rejected the saved token. Check Settings > Brand Studio GPU.") from exc


def _invoke(client, prompt: str, frames: list[tuple[float, bytes]]) -> str:
    function = modal.Function.from_name(config.MODAL_APP, config.MODAL_FUNCTION, client=client)
    return function.remote(prompt, frames)


def _deploy(client) -> None:
    # Imported here so ordinary API startup never builds a Modal image.
    from . import modal_backend

    deploy_app(modal_backend.app, name=config.MODAL_APP, client=client)


def observe(prompt: str, frames: list[tuple[float, bytes]]) -> str:
    client = _client()
    try:
        try:
            return _invoke(client, prompt, frames)
        except modal.exception.NotFoundError:
            # A concurrent worker may have deployed while this one waited.
            with _deploy_lock:
                try:
                    return _invoke(client, prompt, frames)
                except modal.exception.NotFoundError:
                    try:
                        _deploy(client)
                    except Exception as exc:
                        raise GameInferenceError(
                            "Could not set up the Games GPU in Modal. Check deployment permission, credits and GPU quota."
                        ) from exc
                    return _invoke(client, prompt, frames)
    except GameInferenceError:
        raise
    except modal.exception.AuthError as exc:
        raise GameInferenceError("Modal rejected the saved token. Check Settings > Brand Studio GPU.") from exc
    except modal.exception.NotFoundError as exc:
        raise GameInferenceError("The Games GPU function was not found after setup. Retry the analysis.") from exc
    except TimeoutError as exc:
        raise GameInferenceError("Modal inference timed out. Try a shorter clip or 1 frame per second.") from exc
    except Exception as exc:
        message = str(exc).casefold()
        if "out of memory" in message or "cuda oom" in message:
            public = "The Modal GPU ran out of memory. Try 1 frame per second or a shorter clip."
        elif "resource exhausted" in message or "quota" in message or "credit" in message:
            public = "Modal has insufficient GPU capacity or credits for this analysis. Check your Modal workspace."
        else:
            public = "The Games GPU call failed. Check the mr-ai-marketer-game-analysis logs in Modal, then retry."
        raise GameInferenceError(public) from exc
