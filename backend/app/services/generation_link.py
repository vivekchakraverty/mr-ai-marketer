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

BLUESKY AND MASTODON. Both publish through Distribute, so the app knows the published post
without being told — a Bluesky at:// URI from the automation run's output, a Mastodon status
id straight off the delivery record when the native uploader sent it. Tumblr is not a
Distribute channel at all, so there is no publish event here to hook; its loop still closes
through the composer's own "I posted this" step.
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


def _mastodon_status_id(run_id: str) -> str:
    """The status id behind a sent Mastodon job, from whichever path published it.

    Media posts go out through the app's own uploader, which records `mastodon:<id>` as the
    delivery reference — the id is simply there. A text-only post goes through the
    automation engine instead, and its id has to be read out of the run.
    """
    if run_id.startswith("mastodon:"):
        return run_id.split(":", 1)[1]

    from . import activepieces_client

    try:
        run = activepieces_client.get_flow_run(run_id)
    except activepieces_client.ActivepiecesError as err:
        log.info("[generation-link] run %s unreadable: %s", run_id, err)
        return ""
    output = ((run.get("steps") or {}).get("post_to_mastodon") or {}).get("output") or {}
    status_id = ((output.get("body") or {}).get("id")) or ""
    return str(status_id) if status_id else ""


def link_sent_mastodon_posts(limit: int = _BATCH) -> int:
    """Attach published toots to the drafts that produced them.

    The manual path asks for the post's link and an access token, because a URL is not
    addressable — engagement has to be re-read by the id the instance assigned, which only
    an authenticated search can resolve. None of that applies here: the app published the
    post, so it has the id, and a public status reads back without a token.
    """
    pending = db.unlinked_generation_links("mastodon", limit)
    if not pending:
        return 0

    from . import mastodon as masto
    from ..routers import mastodon_post

    linked = 0
    for row in pending:
        run_id = row.get("activepieces_run_id") or ""
        host = (row.get("instance") or "").strip()
        if not run_id or not host:
            continue
        status_id = _mastodon_status_id(run_id)
        if not status_id:
            continue
        try:
            status = masto.get_status(host, status_id)
            if status is None:
                continue
            result = mastodon_post.link_published_status(
                status, host, row["niche"], int(row["generation_id"])
            )
        except Exception as err:  # noqa: BLE001 — retried on the next tick
            log.info("[generation-link] could not link mastodon %s: %s", status_id, err)
            continue
        db.mark_generation_linked(row["library_item_id"], result["postedUri"])
        linked += 1

    if linked:
        log.info("[generation-link] linked %d published toot(s) to their drafts", linked)
    return linked
