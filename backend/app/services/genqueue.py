"""One queue in front of every generation, so a burst waits instead of failing.

The problem this solves is not the local machine — it is what the generations run on.
Blog Writer, Email Writer and Brand Studio all call free Hugging Face CPU Spaces, which
serve one request at a time; the model-backed tools call inference providers that rate-limit.
Firing twelve requests at either produces twelve slow failures rather than twelve slow
successes. Brand Studio alone generates twelve sections per document.

So work is admitted through lanes, each with a concurrency limit and a bounded waiting room:

  * `space` — the free CPU Spaces. One at a time, because that is genuinely what they are.
  * `model` — inference-provider calls. A few at once is fine and much faster in aggregate.
  * `image` — GPU image generation, which is slow and expensive per call.

Waiting is the normal case and is not an error. What IS an error is a queue so long that
joining it means a pointless wait, so each lane has a maximum depth and anything beyond it
is refused immediately with a "try again shortly" — an honest refusal in a second beats a
timeout in three minutes.

This is deliberately in-process. The app is a single-user desktop program with one backend,
so a shared counter is the whole mechanism; a broker would be machinery without a purpose.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

log = logging.getLogger(__name__)


class QueueFull(RuntimeError):
    """Too many requests already waiting. The caller should ask the user to retry."""


@dataclass
class Lane:
    name: str
    #: How many may run at once.
    limit: int
    #: How many may wait. Beyond this, new arrivals are refused rather than queued.
    max_waiting: int
    #: Human wording for the refusal, since "lane" means nothing to a user.
    what: str
    _sem: threading.Semaphore = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    running: int = field(default=0, init=False)
    waiting: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._sem = threading.Semaphore(self.limit)


LANES: dict[str, Lane] = {
    # A free CPU Space is serial in practice. Letting two through just makes both slow.
    "space": Lane("space", limit=1, max_waiting=8, what="generation"),
    # Inference providers handle concurrency; the cap is about not tripping rate limits.
    "model": Lane("model", limit=3, max_waiting=12, what="generation"),
    # Slow and metered, so a short queue and a low limit.
    "image": Lane("image", limit=1, max_waiting=4, what="image"),
}

# Longest a request will sit in the waiting room before giving up. Past this the caller has
# almost certainly stopped caring, and holding the worker helps nobody.
MAX_WAIT_SECONDS = 240


@contextmanager
def slot(lane_name: str) -> Iterator[None]:
    """Hold a slot in `lane_name` for the duration of the block.

    Raises QueueFull immediately when the waiting room is full, and after MAX_WAIT_SECONDS
    if a slot never comes free.
    """
    lane = LANES.get(lane_name) or LANES["model"]

    with lane._lock:
        if lane.waiting >= lane.max_waiting:
            raise QueueFull(
                f"{lane.waiting} {lane.what} requests are already waiting. "
                f"Give it a minute and try again — nothing has been lost."
            )
        lane.waiting += 1

    started = time.monotonic()
    acquired = lane._sem.acquire(timeout=MAX_WAIT_SECONDS)
    with lane._lock:
        lane.waiting -= 1
        if acquired:
            lane.running += 1

    if not acquired:
        raise QueueFull(
            f"Still waiting for a free slot after {MAX_WAIT_SECONDS // 60} minutes. "
            f"Something upstream is slow — try again shortly."
        )

    waited = time.monotonic() - started
    if waited > 1:
        log.info("[queue] %s waited %.1fs", lane_name, waited)
    try:
        yield
    finally:
        with lane._lock:
            lane.running -= 1
        lane._sem.release()


def status() -> dict:
    """What the UI shows. Cheap enough to poll."""
    lanes = {}
    total_running = total_waiting = 0
    for name, lane in LANES.items():
        with lane._lock:
            running, waiting = lane.running, lane.waiting
        lanes[name] = {
            "running": running,
            "waiting": waiting,
            "limit": lane.limit,
            "maxWaiting": lane.max_waiting,
        }
        total_running += running
        total_waiting += waiting
    return {
        "running": total_running,
        "waiting": total_waiting,
        # Only worth showing a queue indicator when something is actually queued behind
        # something else. One request running on its own is just the app working.
        "busy": total_running > 0,
        "queued": total_waiting > 0,
        "lanes": lanes,
    }


def queue_slot(lane_name: str):
    """FastAPI dependency that holds a lane slot for the whole request.

    A generator dependency rather than a decorator or a `with` inside every handler: FastAPI
    runs the part before `yield` on the way in and the part after on the way out, so the slot
    covers the handler without any of them being restructured. Wiring an endpoint is then one
    line in its decorator, which is the difference between this being applied everywhere and
    being applied to the three that seemed worst.
    """

    def dependency() -> Iterator[None]:
        with slot(lane_name):
            yield

    return dependency
