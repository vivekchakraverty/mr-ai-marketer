"""Fill a niche the moment it is created, instead of leaving it empty until a timer.

Adding a niche used to only write a row. Both generators then read an empty corpus
until either the user found the Collect button or a scheduled job came round — six
hours for the Bluesky ingest, twelve for the Mastodon one. In between, generation
still worked, but silently ungrounded: the tool answered from platform norms rather
than from anything that had actually performed in the new niche, and the panel showed
"0 posts · 0 exemplars" with nothing to say whether that was a problem or a wait.

So creation now queues a first fill across both tools. Three things shape it:

WHY BOTH TOOLS, FROM ONE PLACE. A niche is a single shared object — the backend keeps
one list and the Bluesky and Mastodon composers both read it — so which screen it was
typed on says nothing about where it should be filled from. Filling only the tool the
user happened to be looking at would leave the other one's pool empty for a niche that,
as far as it is concerned, exists.

WHY A WORKER THREAD, NOT THE REQUEST. A cold-start Bluesky bootstrap is three jobs
(deep ingest, 48h backfill, exemplar refresh) and the Mastodon half is one pass per
accepted instance — measured at 14-23s each against mastodon.social. Holding the POST
open for minutes would time out the client and make adding a niche feel broken. One
worker rather than a thread per niche, because adding three niches in a row should
queue three fills, not open three concurrent crawls of someone else's server.

WHY EACH HALF MAY NO-OP. Bluesky collection needs credentials; Mastodon collection
needs an instance whose rules the user has accepted, and must respect that gate here
exactly as the button does. Neither is an error — an install with only one configured
should fill that one and say plainly that it skipped the other, which is what `status()`
reports back to the panel.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# How many finished fills to keep describable. The panel only ever asks about niches
# the user just added, so this is a display buffer, not a history.
MAX_REMEMBERED = 12

_work: queue.Queue[str] = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()
_state: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set(name: str, **fields) -> None:
    with _lock:
        _state.setdefault(name, {"niche": name}).update(fields)


def _forget_old() -> None:
    """Drop the oldest finished entries once there are more than MAX_REMEMBERED."""
    with _lock:
        done = [
            (v.get("finishedAt") or "", k)
            for k, v in _state.items()
            if v.get("state") in {"done", "failed"}
        ]
        for _, name in sorted(done)[: max(0, len(done) - MAX_REMEMBERED)]:
            _state.pop(name, None)


def _fill_bluesky(name: str) -> dict:
    """Cold-start bootstrap for the Bluesky corpus.

    Calls the router's own endpoint function rather than reassembling the recipe here.
    That recipe is subtle — a deep ingest, then backfill_48h, then a refresh, in that
    order and for documented reasons — and a second copy of it would drift.
    """
    from ..routers import social_post

    missing = [m for m in social_post._missing_credentials() if m.startswith("BLUESKY")]
    if missing:
        return {"skipped": f"not connected ({', '.join(missing)})"}

    try:
        counts = social_post.collect_niche(name)
    except Exception as err:  # noqa: BLE001 — one half must not cost the other
        log.warning("[first-fill] %s: Bluesky fill failed: %s", name, str(err)[:200])
        return {"error": str(err)[:200]}
    return {"posts": counts.get("posts", 0), "exemplars": counts.get("exemplars", 0)}


def _fill_mastodon(name: str) -> dict:
    """One collection pass per instance whose rules are still accepted.

    `_accepted_hosts()` re-checks each instance's rule fingerprint over the network, so
    an instance that edited its rules since the user read them drops out here — a new
    niche must not become a way to collect from a server whose gate has re-closed.
    """
    from ..routers import mastodon_post

    hosts = mastodon_post._accepted_hosts()
    if not hosts:
        return {"skipped": "no instance rules accepted"}

    per_host: dict[str, dict] = {}
    for host in hosts:
        try:
            got = mastodon_post._collect_niche(host, name)
        except Exception as err:  # noqa: BLE001 — one instance must not stop the rest
            log.warning("[first-fill] %s on %s failed: %s", name, host, str(err)[:200])
            per_host[host] = {"error": str(err)[:200]}
            continue
        per_host[host] = {"stored": got.stored, "exemplars": got.exemplars}
    return {"instances": per_host}


def _run_one(name: str) -> None:
    _set(name, state="running", startedAt=_now())
    log.info("[first-fill] filling %r", name)

    bluesky = _fill_bluesky(name)
    _set(name, bluesky=bluesky)

    mastodon = _fill_mastodon(name)
    _set(name, mastodon=mastodon)

    _set(name, state="done", finishedAt=_now())
    log.info("[first-fill] %r done: bluesky=%s mastodon=%s", name, bluesky, mastodon)
    _forget_old()


def _loop() -> None:
    while True:
        name = _work.get()
        try:
            _run_one(name)
        except Exception as err:  # noqa: BLE001 — the worker must outlive one bad niche
            log.exception("[first-fill] %r failed", name)
            _set(name, state="failed", finishedAt=_now(), error=str(err)[:200])
        finally:
            _work.task_done()


def enqueue(name: str) -> dict:
    """Queue a first fill for a newly created niche. Idempotent while one is pending.

    Returns what the caller can tell the user right away — the queue position — rather
    than anything about the outcome, which is minutes away and arrives via `status()`.
    """
    global _worker

    with _lock:
        current = _state.get(name, {}).get("state")
        already = current in {"queued", "running"}
        if not already:
            _state[name] = {"niche": name, "state": "queued", "queuedAt": _now()}

    if already:
        return {"niche": name, "state": current, "queued": False}

    if _worker is None:
        _worker = threading.Thread(target=_loop, name="niche-first-fill", daemon=True)
        _worker.start()
        log.info("[first-fill] worker started")

    _work.put(name)
    return {"niche": name, "state": "queued", "queued": True, "ahead": _work.qsize() - 1}


def status(name: str = "") -> list[dict]:
    """What every remembered fill is doing, newest activity last.

    The panel polls this while anything is unfinished and stops once nothing is, so it
    deliberately keeps reporting finished fills for a while — a poll that arrives just
    after the worker finished should see the result, not an empty list.
    """
    with _lock:
        rows = [dict(v) for v in _state.values() if not name or v.get("niche") == name]
    return sorted(rows, key=lambda r: r.get("queuedAt") or "")


def pending() -> bool:
    """True while any fill is queued or running."""
    with _lock:
        return any(v.get("state") in {"queued", "running"} for v in _state.values())
