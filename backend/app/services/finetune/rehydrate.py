"""Stage 2 — attach real engagement to dump candidates via the live Bluesky API.

This is the step that turns text into *labelled* text. The dump has no
engagement columns (firehose captures posts at creation), but AT-URIs are stable
and `app.bsky.feed.getPosts` returns current counts — so a post from the dump can
be measured today.

Reuses vendor/socialpost's existing client rather than adding another: it already
handles auth (one cached session — createSession is the most rate-limited
endpoint), 429/5xx backoff with Retry-After, and chunking at the API's 25-URI
ceiling. `snapshot.py::backfill_48h` is the working precedent for this exact
shape: fetch -> engagement_rate -> write.

TWO SEMANTIC POINTS, both deliberate:

  * These are LIFETIME measurements, not 48h ones. Dump posts are ~18 months
    old. `backfill_48h` bounds itself to posts aged 50h-7d precisely because
    "beyond this, drift and deletions distort" — we are far outside that window,
    so the label is stored as `measured_window='lifetime'` and must never be
    compared directly against the live corpus's 48h rates.

  * Deleted posts are marked `gone`, never written as zeroes. A zero row reads
    as a real post that flopped; it would drag the quality tiers down and teach
    the model that good posts get no engagement. Absent beats zeroes.
"""

from __future__ import annotations

import logging
import time

from . import store

log = logging.getLogger(__name__)

# getPosts takes 25 URIs; getProfiles likewise. One "batch" here is one API call
# worth of posts, so progress and checkpointing align with real network work.
BATCH = 25

# Follower counts change slowly and we need one per author, not per post, so they
# are fetched once per run and cached.
_profile_cache: dict[str, int] = {}


def engagement_rate(likes: int, reposts: int, replies: int, followers: int) -> float:
    """Identical definition to the live corpus, so the two stay comparable.

    Follower-normalised: without it the corpus would just teach "be famous",
    which is not a transferable style signal. max(...,1) guards division by zero.
    """
    return round((likes + reposts + replies) / max(followers, 1), 6)


def _followers(dids: list[str]) -> dict[str, int]:
    """Follower counts for authors we have not already looked up this run."""
    from vendor.socialpost.src import bluesky

    missing = [d for d in dict.fromkeys(dids) if d and d not in _profile_cache]
    for start in range(0, len(missing), BATCH):
        chunk = missing[start : start + BATCH]
        try:
            for did, author in bluesky.get_profiles(chunk).items():
                _profile_cache[did] = author.follower_count
        except Exception as err:  # noqa: BLE001 — a bad chunk must not sink the run
            log.warning("getProfiles chunk failed: %s", str(err)[:120])
        # Authors the API would not return (deleted/suspended) get 0 so we do not
        # retry them every batch. engagement_rate's max(...,1) keeps that safe,
        # and such posts are almost always 'gone' anyway.
        for did in chunk:
            _profile_cache.setdefault(did, 0)
    return _profile_cache


def run(limit: int = 5000, sleep_seconds: float = 0.0) -> dict:
    """Re-hydrate up to `limit` pending candidates.

    Resumable by construction: candidates are selected by `status='candidate'`
    and flipped to `labelled`/`gone` as they are processed, so an interrupted run
    simply picks up where it stopped.
    """
    from vendor.socialpost.src import bluesky

    uris = store.pending_uris("bluesky", limit=limit)
    if not uris:
        log.info("nothing pending")
        return {"requested": 0}

    # Chunked: SQLite caps bound parameters per statement (SQLITE_MAX_VARIABLE_
    # NUMBER), so a single `in (...)` over the whole pending set raises
    # "too many SQL variables" once the corpus grows past a few thousand rows.
    # Same chunking the vendored jobs use for exactly this reason.
    authors: dict[str, str] = {}
    with store.connect() as conn:
        for start in range(0, len(uris), 500):
            chunk = uris[start : start + 500]
            for row in conn.execute(
                f"select uri, author_did from ft_posts where uri in "
                f"({','.join('?' * len(chunk))})",
                chunk,
            ):
                authors[row["uri"]] = row["author_did"]

    started = time.time()
    labelled = gone = 0

    for start in range(0, len(uris), BATCH):
        chunk = uris[start : start + BATCH]
        try:
            live = bluesky.get_posts(chunk)
        except Exception as err:  # noqa: BLE001
            log.warning("getPosts chunk failed, leaving pending: %s", str(err)[:120])
            continue

        followers = _followers([authors.get(u, "") for u in chunk])

        rows = []
        for uri in chunk:
            post = live.get(uri)
            if post is None:
                continue  # deleted / suspended / private -> marked below
            count = followers.get(post.author_did, 0)
            rows.append(
                {
                    "uri": uri,
                    "likes": post.likes,
                    "reposts": post.reposts,
                    "replies": post.replies,
                    "follower_count": count,
                    "has_media": int(post.has_media),
                    "hashtags": post.hashtags,
                    "lifetime_engagement_rate": engagement_rate(
                        post.likes, post.reposts, post.replies, count
                    ),
                    "measured_window": store.WINDOW_LIFETIME,
                }
            )

        missing = [u for u in chunk if u not in live]
        labelled += store.write_labels(rows)
        gone += store.mark_gone(missing)

        done = start + len(chunk)
        if (start // BATCH) % 20 == 0:
            rate = done / max(time.time() - started, 1e-9)
            log.info(
                "  %d/%d · %d labelled · %d gone · %.1f posts/s",
                done, len(uris), labelled, gone, rate,
            )
        if sleep_seconds:
            time.sleep(sleep_seconds)

    result = {
        "requested": len(uris),
        "labelled": labelled,
        "gone": gone,
        "gone_pct": round(100 * gone / max(len(uris), 1), 1),
        "elapsed_seconds": round(time.time() - started, 1),
        "authors_looked_up": len(_profile_cache),
    }
    store.update_manifest(
        stage2_rehydration={
            **result,
            # Load-bearing for reproducibility: engagement is time-dependent, so
            # the same URIs re-hydrated later produce different labels.
            "measured_at": store.iso(store.utcnow()),
            "measured_window": store.WINDOW_LIFETIME,
        }
    )
    log.info("re-hydration complete: %s", result)
    return result
