"""Background scheduler — starts the daemon in a thread, inert until configured.

Mirrors the pattern the Social Post Generator already uses in this app
(social_post.start_scheduler / _scheduler_loop): a single daemon thread starts with the
backend, does nothing while there is no active campaign or no reasoning-LLM credential, and
never lets one failing tick kill the loop. This is the packaged-backend-safe alternative to
a standalone scheduler process (there is no interpreter to shell out to at runtime).
"""

from __future__ import annotations

import logging
import os
import threading
import time

from . import config, daemon, db
from .email import tracking, writer

log = logging.getLogger(__name__)

_TICK_SECONDS = 60
_scheduler_thread: threading.Thread | None = None


def _ready() -> bool:
    """Whether there's anything to do and the reasoning LLM can run."""
    if not db.active_campaigns():
        return False
    if config.current("LLM_BACKEND") == "ollama":
        return True
    return bool((os.environ.get("HF_TOKEN") or "").strip())


def _loop() -> None:
    while True:
        try:
            if _ready():
                daemon.tick()
        except Exception:  # noqa: BLE001 — a bad tick must never stop the scheduler
            log.exception("[leadgen] scheduler tick failed")
        time.sleep(_TICK_SECONDS)


def start_scheduler(
    draft_writer: writer.DraftWriter | None = None,
    tracking_prepare: tracking.PrepareHook | None = None,
    tracking_finalize: tracking.FinalizeHook | None = None,
    tracking_bounce: tracking.BounceHook | None = None,
) -> None:
    """Start the daemon loop in the background. Safe to call once at startup.

    `draft_writer` is the app's Email Writer callable; the `tracking_*` callables
    are the app's mail-tracking service. Injecting all of these here keeps the
    vendored package decoupled from app.* (tests inject fakes, or nothing).
    """
    global _scheduler_thread
    if draft_writer is not None:
        writer.set_draft_writer(draft_writer)
    if tracking_prepare is not None or tracking_finalize is not None or tracking_bounce is not None:
        tracking.set_tracking_hooks(prepare=tracking_prepare, finalize=tracking_finalize, bounce=tracking_bounce)
    db.init_db()
    if _scheduler_thread is not None:
        return
    _scheduler_thread = threading.Thread(target=_loop, name="leadgen-scheduler", daemon=True)
    _scheduler_thread.start()
    log.info("[leadgen] scheduler started")
