"""ingest — collect a niche from Bluesky, upsert posts and authors.

Two sources per niche: keyword search (broad, a sample of the niche) and the
feeds of any tracked authors (narrow, a census of accounts that perform in it).
See src/jobs/authors.py.

Schedule: every 6 hours (.github/workflows/ingest.yml).

Posts already in the table are skipped: their text never changes, and their
engagement is snapshot.py's job. Authors are always upserted so follower_count
stays reasonably fresh.

Run:
    python -m src.jobs.ingest --dry-run
    python -m src.jobs.ingest --niche "ai tools" --limit 50
    python -m src.jobs.ingest --max-age-hours 168      # cold-start bootstrap
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta

from .. import bluesky
from ..db import (
    JobRun,
    configure_logging,
    existing_post_uris,
    iso,
    list_tracked_authors,
    load_niches,
    upsert,
    utcnow,
)

log = logging.getLogger(__name__)

# Posts per keyword per run. 50 x 6 keywords x 2 niches x 4 runs/day is ~2400
# posts/day upper bound, comfortably inside both Bluesky's rate limit and
# Supabase's 500MB free-tier disk.
DEFAULT_LIMIT_PER_KEYWORD = 50

# Reject records claiming to be from the future (clock skew / spoofed createdAt).
# Snapshots key off created_at, so a bogus value would make a post permanently
# ineligible for every bucket, or eligible forever.
MAX_CLOCK_SKEW = timedelta(hours=1)

# Reject posts too old to still earn a 48h snapshot.
#
# snapshot.py's 48h bucket accepts posts aged 46-50h. A post ingested at 60h can
# never be snapshotted, so it can never become an exemplar (exemplar_candidates
# requires a 48h snapshot) and never influences anything — it is just bytes in a
# 500MB budget. Ingest runs every 6h, so a ceiling of 44h guarantees every stored
# post gets at least one shot at the 48h bucket, with margin for cron drift.
#
# For scheduled runs, raising this only adds unusable rows.
#
# The exception is the cold-start bootstrap, and it is why --max-age-hours exists.
# `snapshot.py --backfill-48h` deliberately works on posts aged 50h-7d, which this
# 44h ceiling would otherwise make it impossible to ever collect: on a fresh
# install every post would be younger than 44h and the backfill would match
# nothing. A bootstrap run passes --max-age-hours 168 to reach into that window on
# purpose. Keep the two in step: BACKFILL_MAX_AGE in snapshot.py is 7 days = 168h.
MAX_POST_AGE = timedelta(hours=44)

# Matches snapshot.BACKFILL_MAX_AGE. Suggested in --help so the bootstrap recipe
# is discoverable from the CLI rather than only from the docs.
BOOTSTRAP_MAX_AGE_HOURS = 168

# Posts pulled per tracked author per run. Their feed is newest-first and ingest
# runs every 6h, so this only needs to cover what one account plausibly posts in
# that window — with plenty of headroom.
AUTHOR_FEED_LIMIT = 50


def run(
    dry_run: bool = False,
    only_niche: str | None = None,
    limit: int = DEFAULT_LIMIT_PER_KEYWORD,
    max_age: timedelta = MAX_POST_AGE,
    until: "datetime | None" = None,
) -> None:
    niches = load_niches()
    if only_niche:
        if only_niche not in niches:
            raise SystemExit(
                f"No active niche called {only_niche!r}. "
                f"Active: {', '.join(sorted(niches)) or '(none)'}. "
                f"See `python -m src.jobs.niches --list`."
            )
        niches = {only_niche: niches[only_niche]}

    with JobRun("ingest", dry_run=dry_run) as job:
        if not niches:
            # Every niche deactivated is a legitimate user choice, not a failure.
            job.note("no active niches; nothing to collect")
            return
        if max_age != MAX_POST_AGE:
            job.note(f"max post age overridden to {max_age.total_seconds() / 3600:.0f}h")
        for niche, keywords in niches.items():
            try:
                _ingest_niche(job, niche, keywords, limit, dry_run, max_age, until)
            except Exception as err:  # noqa: BLE001 — one bad niche must not sink the rest
                log.exception("Niche %r failed", niche)
                job.note(f"niche {niche!r} failed: {type(err).__name__}: {err}")
                job.partial()


def _ingest_niche(
    job: JobRun,
    niche: str,
    keywords: list[str],
    limit: int,
    dry_run: bool,
    max_age: timedelta = MAX_POST_AGE,
    until: "datetime | None" = None,
) -> None:
    """Collect one niche from both sources, then write posts + authors.

    Two sources feed the same dict, deduped by URI:
      * keyword search  — broad but a SAMPLE; Bluesky returns what its index
        feels like returning, so coverage of any one account is arbitrary.
      * tracked author feeds — narrow but a CENSUS of accounts already shown to
        perform in this niche, which is what the exemplar pool wants.
    """
    # Dedupe across keywords: "ai tools" and "ai agents" overlap heavily, and we
    # want one row per URI, not one per keyword that matched it.
    found: dict[str, bluesky.BskyPost] = {}
    for keyword in keywords:
        for post in bluesky.search_posts(keyword, limit=limit, until=until):
            found.setdefault(post.uri, post)

    from_search = len(found)
    job.note(f"{niche}: {from_search} unique posts across {len(keywords)} keywords")

    tracked = list_tracked_authors(niche)
    for author in tracked:
        # Prefer the DID: handles change, DIDs do not.
        for post in bluesky.get_author_feed(author["did"], limit=AUTHOR_FEED_LIMIT):
            found.setdefault(post.uri, post)
    if tracked:
        job.note(
            f"{niche}: +{len(found) - from_search} more from {len(tracked)} tracked "
            f"author feed(s)"
        )
        job.count("from_author_feeds", len(found) - from_search)

    if not found:
        return

    # --- filter: implausible timestamps -------------------------------------
    now = utcnow()
    plausible: dict[str, bluesky.BskyPost] = {}
    for uri, post in found.items():
        if post.created_at is None:
            job.count("skipped_no_timestamp")
            continue
        if post.created_at > now + MAX_CLOCK_SKEW:
            job.count("skipped_future_timestamp")
            continue
        if post.created_at < now - max_age:
            job.count("skipped_too_old")
            continue
        plausible[uri] = post

    # --- filter: authors requesting no indexing -----------------------------
    # Fetched before anything is written, so a no-index author's post never
    # touches the database even transiently.
    profiles = bluesky.get_profiles({p.author_did for p in plausible.values()})
    no_index = {did for did, author in profiles.items() if author.no_index}
    if no_index:
        job.note(f"{niche}: skipping {len(no_index)} authors labelled no-unauthenticated")

    keep = {
        uri: post for uri, post in plausible.items() if post.author_did not in no_index
    }
    job.count("skipped_no_index", len(plausible) - len(keep))

    # --- filter: already stored ---------------------------------------------
    if dry_run:
        # Reads are fine in a dry run, but if the table does not exist yet this
        # would be a confusing failure. Report the pre-skip number instead.
        already = set()
        job.note(f"{niche}: dry run — not checking for existing posts")
    else:
        already = existing_post_uris(keep)
    new_posts = {uri: post for uri, post in keep.items() if uri not in already}
    job.count("skipped_already_present", len(already))

    # --- authors ------------------------------------------------------------
    # Upsert authors for every kept post, not just new ones: refreshing
    # follower_count on known authors is the point of the 6-hourly cadence.
    author_rows = []
    for did in {p.author_did for p in keep.values()}:
        profile = profiles.get(did)
        if profile is None:
            continue  # getProfiles chunk failed; try again next run
        author_rows.append(
            {
                "did": profile.did,
                "handle": profile.handle,
                "follower_count": profile.follower_count,
                "niche": niche,
                "last_seen_at": iso(now),
            }
        )

    post_rows = [
        {
            "uri": post.uri,
            "platform": "bluesky",
            "author_did": post.author_did,
            "text": post.text,
            "hashtags": post.hashtags,
            "has_media": post.has_media,
            "created_at": iso(post.created_at),
            "niche": niche,
            "ingested_at": iso(now),
        }
        for post in new_posts.values()
        # Guard the FK: a post whose author's profile we could not fetch has no
        # authors row to point at.
        if post.author_did in profiles
    ]

    if dry_run:
        job.note(
            f"{niche}: would write {len(author_rows)} authors, {len(post_rows)} posts"
        )
        for post in list(new_posts.values())[:3]:
            preview = post.text.replace("\n", " ")[:90]
            log.info("  would insert @%s: %s", post.author_handle, preview)
        job.count("would_write_posts", len(post_rows))
        job.count("would_write_authors", len(author_rows))
        return

    # Authors first — posts.author_did references authors.did.
    job.count("authors_upserted", upsert("authors", author_rows, on_conflict="did"))
    job.count("posts_upserted", upsert("posts", post_rows, on_conflict="uri"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Bluesky posts by niche keywords.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be written without writing anything.",
    )
    parser.add_argument("--niche", help="Only ingest this niche (default: all).")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT_PER_KEYWORD,
        help=f"Posts per keyword (default: {DEFAULT_LIMIT_PER_KEYWORD}).",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=MAX_POST_AGE.total_seconds() / 3600,
        help=(
            f"Ignore posts older than this "
            f"(default: {MAX_POST_AGE.total_seconds() / 3600:.0f}h — the newest age "
            f"that can still earn a 48h snapshot). For a cold-start bootstrap use "
            f"{BOOTSTRAP_MAX_AGE_HOURS}, then run "
            f"`snapshot --backfill-48h`; the default is too tight to collect posts "
            f"the backfill can use."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    run(
        dry_run=args.dry_run,
        only_niche=args.niche,
        limit=args.limit,
        max_age=timedelta(hours=args.max_age_hours),
    )


if __name__ == "__main__":
    main()
