"""Stage 2b — copy our own Mastodon corpus into staging.

The Mastodon half of the training corpus needs no dump and no re-hydration: the
live corpus is already engagement-scored (hashtag timelines return favourite /
boost / reply counts at collection time) and already consent-filtered by
`services/mastodon.should_learn_from` — public only, no bots, honouring
`discoverable=false` and #nobot / #noindex / #noarchive.

That is exactly why no Mastodon dump is imported here. The fediverse has a
documented history of non-consensual research scraping (a Harvard Dataverse
dataset was retracted after a community open letter), and this app enforces
instance policies at every other layer — a bulk import would contradict its own
gate. Our own collection is both the ethical source and the better one.

READ-ONLY against the live corpus. Rows are copied out; nothing is written back.

Note the measurement asymmetry this creates, which stage 3 must respect:
Mastodon rows carry `measured_window='48h'` while re-hydrated Bluesky rows carry
'lifetime'. The two are NOT comparable, so quality tiers are percentile-ranked
WITHIN (platform, niche), never globally.
"""

from __future__ import annotations

import json
import logging

from . import store

log = logging.getLogger(__name__)

# Matches the namespace the Mastodon post creator writes under.
NAMESPACE_SUFFIX = " · mastodon"


def run(limit: int | None = None) -> dict:
    """Copy scored Mastodon posts from the live corpus into staging."""
    from vendor.socialpost.src import db as spg_db

    client = spg_db.get_client()

    posts = (
        client.table("posts")
        .select("uri, text, hashtags, author_did, created_at, has_media, niche")
        .eq("platform", "mastodon")
        .execute()
        .data
        or []
    )
    posts = [p for p in posts if (p.get("text") or "").strip()]
    if limit:
        posts = posts[:limit]
    if not posts:
        log.info("no mastodon posts in the live corpus yet")
        return {"found": 0, "imported": 0}

    uris = [p["uri"] for p in posts]

    # 48h engagement, measured by our own collector.
    rates: dict[str, dict] = {}
    for start in range(0, len(uris), 100):
        for row in (
            client.table("engagement_snapshots")
            .select("post_uri, likes, reposts, replies, engagement_rate")
            .eq("window_label", "48h")
            .in_("post_uri", uris[start : start + 100])
            .execute()
            .data
            or []
        ):
            if row.get("engagement_rate") is not None:
                rates[row["post_uri"]] = row

    followers: dict[str, int] = {}
    dids = [p["author_did"] for p in posts if p.get("author_did")]
    for start in range(0, len(dids), 100):
        for row in (
            client.table("authors")
            .select("did, follower_count")
            .in_("did", dids[start : start + 100])
            .execute()
            .data
            or []
        ):
            followers[row["did"]] = row.get("follower_count") or 0

    candidates: list[dict] = []
    labels: list[dict] = []
    skipped_unmeasured = 0

    for post in posts:
        snap = rates.get(post["uri"])
        if snap is None:
            # No measurement means no label. Importing it unlabelled would only
            # create a row that stage 3 has to drop anyway.
            skipped_unmeasured += 1
            continue

        # The corpus stores hashtags as a JSON array; sqlite backend may hand it
        # back either decoded or raw depending on the backend in use.
        tags = post.get("hashtags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except ValueError:
                tags = []

        niche = (post.get("niche") or "").replace(NAMESPACE_SUFFIX, "").strip()

        candidates.append(
            {
                "uri": post["uri"],
                "platform": "mastodon",
                "text": post["text"],
                "hashtags": tags,
                "author_did": post.get("author_did"),
                "author_handle": "",
                "created_at": post.get("created_at"),
                "niche": niche or None,
            }
        )
        labels.append(
            {
                "uri": post["uri"],
                "likes": snap.get("likes") or 0,
                "reposts": snap.get("reposts") or 0,
                "replies": snap.get("replies") or 0,
                "follower_count": followers.get(post.get("author_did") or "", 0),
                "has_media": int(bool(post.get("has_media"))),
                "hashtags": tags,
                "lifetime_engagement_rate": float(snap["engagement_rate"]),
                # Deliberately NOT 'lifetime' — these were measured at 48h by our
                # own collector, and conflating the two would be a lie about the
                # data. Stage 3 tiers within (platform, niche) because of this.
                "measured_window": store.WINDOW_48H,
            }
        )

    added = store.add_candidates(candidates)
    labelled = store.write_labels(labels)

    result = {
        "found": len(posts),
        "added": added,
        "labelled": labelled,
        "skipped_unmeasured": skipped_unmeasured,
    }
    store.update_manifest(mastodon_import=result)
    log.info("mastodon import: %s", result)
    return result
