"""snapshot — capture engagement counts for posts inside each age bucket.

Schedule: hourly (.github/workflows/snapshot.yml).

Buckets are 1h, 24h and 48h after a post's created_at, each with a tolerance:
GitHub Actions cron is best-effort and routinely drifts by 5-30 minutes (worse
during peak minutes — :00 is the most contended slot on the platform, which is
why the workflow runs at :17). Without tolerance, a drifted run would miss the
bucket entirely and the post would never get that snapshot.

Snapshots are append-only. The UNIQUE (post_uri, window_label) constraint is the
backstop: if a drifted run somehow tries to double-capture, the insert fails
rather than silently rewriting history.

Run:
    python -m src.jobs.snapshot --dry-run
    python -m src.jobs.snapshot --backfill-48h    # cold-start bootstrap, see below
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import timedelta

from .. import bluesky
from ..db import JobRun, configure_logging, get_client, insert, iso, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bucket:
    """An age at which we capture engagement, and how much cron drift to allow."""

    label: str
    target: timedelta
    tolerance: timedelta


# The 1h window is tight because early velocity is the most time-sensitive
# signal; 24h/48h get ±2h since engagement has plateaued by then and a couple of
# hours of drift barely moves the number.
BUCKETS = [
    Bucket("1h", timedelta(hours=1), timedelta(minutes=30)),
    Bucket("24h", timedelta(hours=24), timedelta(hours=2)),
    Bucket("48h", timedelta(hours=48), timedelta(hours=2)),
]

# Ceiling on posts fetched per bucket per run, to bound Bluesky calls. getPosts
# takes 25 URIs per call, so 500 posts = 20 calls per bucket, 60 per run.
MAX_POSTS_PER_BUCKET = 500


def engagement_rate(likes: int, reposts: int, replies: int, follower_count: int) -> float:
    """(likes + reposts + replies) / max(follower_count, 1).

    Normalising by followers is what lets a 200-follower account's post out-rank
    a 200k-follower account's post in the exemplar pool. Without it the system
    would just learn "be famous", which is not a transferable style signal.

    max(...,1) guards division by zero for brand-new or zero-follower authors.
    Their raw engagement then reads as the rate, which overstates them — but such
    posts almost never accumulate enough engagement to rank anyway.
    """
    return (likes + reposts + replies) / max(follower_count, 1)


def _posts_due(bucket: Bucket, limit: int) -> list[dict]:
    """Posts whose age is within `bucket` and which lack that bucket's snapshot.

    Two queries rather than a NOT EXISTS join, because PostgREST cannot express
    an anti-join. The candidate set is small (one hour's worth of posts), so the
    second query stays cheap.
    """
    now = utcnow()
    # Age within [target - tol, target + tol]  =>  created_at within
    # [now - target - tol, now - target + tol].
    oldest = now - bucket.target - bucket.tolerance
    newest = now - bucket.target + bucket.tolerance

    client = get_client()
    candidates = (
        client.table("posts")
        .select("uri, author_did, created_at")
        .gte("created_at", iso(oldest))
        .lte("created_at", iso(newest))
        .limit(limit)
        .execute()
        .data
        or []
    )
    if not candidates:
        return []

    uris = [c["uri"] for c in candidates]
    done: set[str] = set()
    for i in range(0, len(uris), 100):
        chunk = uris[i : i + 100]
        rows = (
            client.table("engagement_snapshots")
            .select("post_uri")
            .eq("window_label", bucket.label)
            .in_("post_uri", chunk)
            .execute()
            .data
            or []
        )
        done.update(r["post_uri"] for r in rows)

    return [c for c in candidates if c["uri"] not in done]


def _follower_counts(dids: set[str]) -> dict[str, int]:
    """Stored follower counts for the given authors.

    Read from our authors table rather than re-fetching profiles: ingest refreshes
    these every 6h, and one getProfiles call per snapshot would triple this job's
    API usage for a number that barely moves in an hour.
    """
    if not dids:
        return {}
    client = get_client()
    out: dict[str, int] = {}
    did_list = list(dids)
    for i in range(0, len(did_list), 100):
        rows = (
            client.table("authors")
            .select("did, follower_count")
            .in_("did", did_list[i : i + 100])
            .execute()
            .data
            or []
        )
        for r in rows:
            out[r["did"]] = r["follower_count"] or 0
    return out


# --- cold-start backfill ----------------------------------------------------
#
# A fresh install has no 48h snapshots, so refresh_exemplars has nothing to rank
# and generation falls back to platform norms for ~3 days. Meanwhile the corpus
# already contains posts that are days old and whose engagement has long since
# plateaued.
#
# --backfill-48h captures those posts' CURRENT counts as their 48h snapshot. The
# approximation: for a post aged 2-7 days, engagement is essentially final, so
# current counts are close to what a real 48h capture would have recorded. It is
# an approximation, not a measurement — hence opt-in, never scheduled, and bounded
# to posts young enough for the approximation to hold.
BACKFILL_MIN_AGE = timedelta(hours=50)  # past the 48h bucket's upper edge
BACKFILL_MAX_AGE = timedelta(days=7)  # beyond this, drift and deletions distort


def backfill_48h(job: JobRun, dry_run: bool, limit: int = MAX_POSTS_PER_BUCKET) -> None:
    """Approximate 48h snapshots for posts that already missed the bucket."""
    now = utcnow()
    client = get_client()

    candidates = (
        client.table("posts")
        .select("uri, author_did, created_at")
        .lte("created_at", iso(now - BACKFILL_MIN_AGE))
        .gte("created_at", iso(now - BACKFILL_MAX_AGE))
        .limit(limit)
        .execute()
        .data
        or []
    )
    if not candidates:
        job.note("backfill: no posts aged 50h-7d")
        return

    uris = [c["uri"] for c in candidates]
    done: set[str] = set()
    for i in range(0, len(uris), 100):
        rows = (
            client.table("engagement_snapshots")
            .select("post_uri")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        )
        done.update(r["post_uri"] for r in rows)

    due = [c for c in candidates if c["uri"] not in done]
    if not due:
        job.note("backfill: all eligible posts already have a 48h snapshot")
        return

    job.note(f"backfill: {len(due)} posts aged 50h-7d lack a 48h snapshot")

    live = bluesky.get_posts([p["uri"] for p in due])
    followers = _follower_counts({p["author_did"] for p in due if p["author_did"]})

    rows_out = []
    for candidate in due:
        post = live.get(candidate["uri"])
        if post is None:
            job.count("backfill_unavailable")
            continue
        count = followers.get(candidate["author_did"], 0)
        rows_out.append(
            {
                "post_uri": post.uri,
                "captured_at": iso(now),
                "window_label": "48h",
                "likes": post.likes,
                "reposts": post.reposts,
                "replies": post.replies,
                "engagement_rate": engagement_rate(
                    post.likes, post.reposts, post.replies, count
                ),
            }
        )

    if dry_run:
        job.note(f"backfill: would insert {len(rows_out)} approximate 48h snapshots")
        job.count("would_backfill", len(rows_out))
        return

    job.count("backfilled_48h", insert("engagement_snapshots", rows_out))


def run(dry_run: bool = False) -> None:
    with JobRun("snapshot", dry_run=dry_run) as job:
        now = utcnow()

        for bucket in BUCKETS:
            due = _posts_due(bucket, MAX_POSTS_PER_BUCKET)
            if not due:
                job.note(f"{bucket.label}: nothing due")
                continue

            job.note(f"{bucket.label}: {len(due)} posts due")
            if len(due) == MAX_POSTS_PER_BUCKET:
                # Silently dropping posts would look like normal operation.
                job.note(
                    f"{bucket.label}: hit the {MAX_POSTS_PER_BUCKET}-post cap; "
                    f"some posts will miss this bucket"
                )
                job.partial()

            live = bluesky.get_posts([p["uri"] for p in due])
            followers = _follower_counts({p["author_did"] for p in due if p["author_did"]})

            rows = []
            for candidate in due:
                post = live.get(candidate["uri"])
                if post is None:
                    # Deleted, or by a now-suspended account. Record nothing: a
                    # zeroed row would look like a real post that flopped and
                    # would drag the niche baseline down.
                    job.count(f"{bucket.label}_unavailable")
                    continue

                count = followers.get(candidate["author_did"], 0)
                rows.append(
                    {
                        "post_uri": post.uri,
                        "captured_at": iso(now),
                        "window_label": bucket.label,
                        "likes": post.likes,
                        "reposts": post.reposts,
                        "replies": post.replies,
                        "engagement_rate": engagement_rate(
                            post.likes, post.reposts, post.replies, count
                        ),
                    }
                )

            if dry_run:
                job.note(f"{bucket.label}: would insert {len(rows)} snapshots")
                for row in rows[:3]:
                    log.info(
                        "  would snapshot %s likes=%d reposts=%d replies=%d rate=%.5f",
                        row["post_uri"].rsplit("/", 1)[-1],
                        row["likes"],
                        row["reposts"],
                        row["replies"],
                        row["engagement_rate"],
                    )
                job.count(f"would_write_{bucket.label}", len(rows))
                continue

            job.count(f"inserted_{bucket.label}", insert("engagement_snapshots", rows))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture engagement snapshots for posts in the 1h/24h/48h buckets."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be written without writing anything.",
    )
    parser.add_argument(
        "--backfill-48h",
        action="store_true",
        help=(
            "Cold-start bootstrap: record current counts as an approximate 48h "
            "snapshot for posts aged 50h-7d that missed the real bucket. Opt-in "
            "and approximate — see the docstring. Skips the normal buckets."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.backfill_48h:
        with JobRun("snapshot_backfill", dry_run=args.dry_run) as job:
            backfill_48h(job, dry_run=args.dry_run)
        return

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
