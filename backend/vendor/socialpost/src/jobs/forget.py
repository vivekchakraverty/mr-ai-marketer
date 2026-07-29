"""forget — erase an author or a post on request. Manual only; never scheduled.

This is the deletion path for the data-ethics commitments in the README. Someone
who does not want their public posts used as training material for a generator
must have a way to get out, and that way must be one command.

    python -m src.jobs.forget --author did:plc:abc123 --dry-run
    python -m src.jobs.forget --author did:plc:abc123
    python -m src.jobs.forget --post at://did:plc:abc/app.bsky.feed.post/xyz
    python -m src.jobs.forget --handle someone.bsky.social

What it removes:
  * the posts themselves
  * every engagement snapshot for those posts (FK cascade)
  * any exemplar derived from them (FK cascade)
  * the authors row, for --author/--handle

Exemplars normally get deactivated rather than deleted, for auditability. Here
they are deleted: "keep a copy but mark it inactive" is not forgetting.

generations rows are kept but their posted_uri is nulled by the FK's ON DELETE
SET NULL — the user's own draft history is not the forgotten party's data, and
destroying it would be its own kind of data loss. The link to the deleted post is
severed, which is what matters.

Deliberately NOT added to a blocklist: that would mean storing the DID of someone
who asked to be forgotten, in order to remember to forget them. Re-ingest is
possible if they still match a niche keyword; see the README.
"""

from __future__ import annotations

import argparse
import logging

from ..db import JobRun, configure_logging, get_client

log = logging.getLogger(__name__)


def _resolve_handle(handle: str) -> str:
    """handle -> DID, via Bluesky."""
    from .. import bluesky

    profile = bluesky.get_client().app.bsky.actor.get_profile({"actor": handle})
    log.info("Resolved %s -> %s", handle, profile.did)
    return profile.did


def forget_author(job: JobRun, did: str, dry_run: bool) -> None:
    client = get_client()

    posts = client.table("posts").select("uri").eq("author_did", did).execute().data or []
    uris = [p["uri"] for p in posts]

    snapshots = 0
    exemplars = 0
    for i in range(0, len(uris), 100):
        chunk = uris[i : i + 100]
        snapshots += (
            client.table("engagement_snapshots")
            .select("id", count="exact")
            .in_("post_uri", chunk)
            .limit(0)
            .execute()
            .count
            or 0
        )
        exemplars += (
            client.table("exemplars")
            .select("id", count="exact")
            .in_("post_uri", chunk)
            .limit(0)
            .execute()
            .count
            or 0
        )

    job.note(
        f"author {did}: {len(uris)} posts, {snapshots} snapshots, {exemplars} exemplars"
    )

    tracked = (
        client.table("tracked_authors")
        .select("id", count="exact")
        .eq("did", did)
        .limit(0)
        .execute()
        .count
        or 0
    )
    if tracked:
        job.note(f"author {did}: also tracked in {tracked} niche(s)")

    if dry_run:
        job.note("dry run — nothing deleted")
        return

    # Untrack FIRST. tracked_authors has no FK to authors (an author is tracked
    # before their first post exists), so nothing would cascade — and leaving the
    # row behind would have the next ingest pull their whole feed again, quietly
    # undoing the erasure this job exists to perform.
    if tracked:
        client.table("tracked_authors").delete().eq("did", did).execute()
        job.count("untracked", tracked)

    # Deleting the author cascades to posts (posts.author_did ON DELETE CASCADE),
    # which cascades to snapshots and exemplars. One delete, but the counts above
    # are what gets reported.
    client.table("authors").delete().eq("did", did).execute()

    # An author with no authors row (e.g. ingest wrote posts before a profile
    # fetch failed) leaves orphan posts the cascade cannot reach.
    if uris:
        for i in range(0, len(uris), 100):
            client.table("posts").delete().in_("uri", uris[i : i + 100]).execute()

    remaining = (
        client.table("posts")
        .select("uri", count="exact")
        .eq("author_did", did)
        .limit(0)
        .execute()
        .count
        or 0
    )
    if remaining:
        raise RuntimeError(f"{remaining} posts for {did} survived deletion")

    job.note(f"author {did}: erased")
    job.count("authors_erased")
    job.count("posts_erased", len(uris))


def forget_post(job: JobRun, uri: str, dry_run: bool) -> None:
    client = get_client()

    existing = client.table("posts").select("uri").eq("uri", uri).execute().data
    if not existing:
        job.note(f"post {uri}: not present")
        return

    snapshots = (
        client.table("engagement_snapshots")
        .select("id", count="exact")
        .eq("post_uri", uri)
        .limit(0)
        .execute()
        .count
        or 0
    )
    exemplars = (
        client.table("exemplars")
        .select("id", count="exact")
        .eq("post_uri", uri)
        .limit(0)
        .execute()
        .count
        or 0
    )
    job.note(f"post {uri}: {snapshots} snapshots, {exemplars} exemplars")

    if dry_run:
        job.note("dry run — nothing deleted")
        return

    client.table("posts").delete().eq("uri", uri).execute()
    job.note(f"post {uri}: erased")
    job.count("posts_erased")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Erase an author's or a post's data on request."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--author", help="Author DID, e.g. did:plc:abc123")
    target.add_argument("--handle", help="Author handle; resolved to a DID via Bluesky.")
    target.add_argument("--post", help="Post AT-URI, e.g. at://did:plc:.../app.bsky.feed.post/...")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be deleted.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    with JobRun("forget", dry_run=args.dry_run) as job:
        if args.handle:
            forget_author(job, _resolve_handle(args.handle), args.dry_run)
        elif args.author:
            forget_author(job, args.author, args.dry_run)
        else:
            forget_post(job, args.post, args.dry_run)


if __name__ == "__main__":
    main()
