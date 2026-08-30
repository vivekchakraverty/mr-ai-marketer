"""Trace a published post back to the draft that wrote it, without anyone pressing a button.

WHY THIS EXISTS. The Social Post generator learns only from generations that carry a
`posted_uri`: `jobs/snapshot.py` picks those up at the 1h/24h/48h buckets and `watchdog.py`
judges the system against them. The only way to set one was `POST /social-post/published`,
which nothing ever called — 94 generations recorded, zero linked, so an hourly snapshot job
had been running against an empty set the whole time.

Nothing joined the two halves. A generation lives in the vendored corpus database and knows
nothing about Library entries; a distribution job knows its Library entry and, once it has
gone out, the real post — but not which draft produced the words. `db.generation_links` is
that missing edge, written when the draft is generated, and this module walks it afterwards.

WHY A SWEEP RATHER THAN A CALL AT SEND TIME. Linking needs the published URI, which for
Bluesky is inside the automation run's output, and `attach_posted_uri` then fetches the post
from the network to confirm it exists. Doing that inline would put a network round trip in
the middle of publishing, where a slow or failed call would be reported as a send problem —
which it is not. A sweep retries on the next tick instead, and a post whose run has been
purged simply never links rather than failing anything.

DELIBERATELY BLUESKY ONLY. Mastodon and Tumblr have their own loops with their own
measurement paths; this is the one that was never connected.
"""

from __future__ import annotations

import logging

from .. import db

log = logging.getLogger(__name__)

#: Enough to catch up a backlog in a few ticks without turning one sweep into a long series
#: of network calls. Newest sends are linked first, so a stale unlinkable row cannot starve
#: a fresh one.
_BATCH = 25


def _published_uri(run_id: str) -> str:
    """The at:// URI the Bluesky action reported, or '' if this run did not produce one."""
    from . import activepieces_client

    try:
        run = activepieces_client.get_flow_run(run_id)
    except activepieces_client.ActivepiecesError as err:
        log.info("[generation-link] run %s unreadable: %s", run_id, err)
        return ""

    output = ((run.get("steps") or {}).get("post_to_bluesky") or {}).get("output") or {}
    uri = ((output.get("mainPost") or {}).get("uri")) or ""
    return uri if isinstance(uri, str) else ""


def link_sent_bluesky_posts(limit: int = _BATCH) -> int:
    """Attach published URIs to the drafts that produced them. Returns how many were linked.

    Safe to call on every scheduler tick: it does nothing at all — and imports nothing
    expensive — when there is no draft waiting to be linked.
    """
    pending = db.unlinked_generation_links("bluesky", limit)
    if not pending:
        return 0

    # Imported here, not at module scope: the vendored package pulls in torch through
    # sentence-transformers. Reaching this line means the generator has already been used,
    # so the cost is one the process has paid anyway — but a user who never opens the tool
    # must not pay it just because the distribution scheduler ticks.
    from vendor.socialpost.src import generation

    linked = 0
    for row in pending:
        run_id = row.get("activepieces_run_id") or ""
        if not run_id:
            continue
        uri = _published_uri(run_id)
        if not uri:
            continue
        try:
            resolved = generation.attach_posted_uri(
                generation_id=int(row["generation_id"]),
                posted_uri=uri,
                niche=row["niche"],
            )
        except Exception as err:  # noqa: BLE001 — a link that fails is retried next tick
            log.info("[generation-link] could not link %s: %s", uri, err)
            continue
        db.mark_generation_linked(row["library_item_id"], resolved)
        linked += 1

    if linked:
        log.info("[generation-link] linked %d published post(s) to their drafts", linked)
    return linked
