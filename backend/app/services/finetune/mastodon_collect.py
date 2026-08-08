"""Build a Mastodon training corpus from three complementary sources.

The two-mode fine-tune needs Mastodon data, and the app's own niche collector had
produced 7 usable posts — nowhere near enough to teach a register (upsampling 7
examples to 25% of training tokens teaches recitation, not style).

WHY THIS WRITES TO STAGING, NOT THE LIVE CORPUS. The app's Mastodon exemplars are
namespaced per niche and are what ground the user's actual drafts. Trending and
public-timeline posts are general, not niche-scoped; dumping thousands of them
into `posts` would silently change what the Mastodon Post Creator writes like.
So this collector writes straight into ft_posts, exactly as the Bluesky dump path
does, and the live corpus is left alone.

THREE SOURCES, because no single one is representative:

  trends   /api/v1/trends/statuses — the `top` tier. Measured on hachyderm:
           96% pass the consent filter (vs 13.5% for tag timelines), because
           trending accounts are discoverable by definition. But median author
           followers was 3,272, so this is survivorship-biased and cannot stand
           alone — a corpus of only these has no `low` tier to contrast against.

  tags     hashtag timelines across the topic vocabulary — topical breadth.

  public   the federated public timeline — ordinary posts by ordinary accounts,
           which is what the mid/low tiers need. Not served on every instance.

Consent and the rules gate are unchanged and non-negotiable: only instances whose
published rules the user has accepted (and whose fingerprint still matches) are
touched, and every status passes should_learn_from before it is stored.
"""

from __future__ import annotations

import logging
import time
from collections import Counter

from ... import db
from .. import mastodon as masto
from .. import mastodon_gate as gate
from . import acquire, store

log = logging.getLogger(__name__)

# A status needs time on the timeline before its counts mean anything — below
# this a zero is indistinguishable from "nobody has seen it yet". Matches
# routers/mastodon_post.MIN_SETTLE_HOURS.
MIN_SETTLE_HOURS = 24

# Same floor the Mastodon collector uses; below it engagement_rate is a
# small-denominator artefact.
MIN_FOLLOWERS = 50

POLITE_DELAY = 0.4
TRENDS_PAGES = 6  # 40 per page; hachyderm still returned results at offset 160


def _accepted_hosts() -> list[str]:
    """Instances whose accepted rules still match what they publish right now.

    A timer must not be a way around the gate, and neither must a training-data
    job. An instance that edited its rules drops out until the user re-reads them.
    """
    hosts: list[str] = []
    for ack in db.list_mastodon_acks():
        try:
            policy = gate.require_accepted(ack["instance"])
        except gate.PolicyNotAccepted:
            log.info("[ft-mastodon] %s: rules changed, skipping", ack["instance"])
            continue
        except masto.MastodonError as err:
            log.warning("[ft-mastodon] %s unreachable: %s", ack["instance"], str(err)[:90])
            continue
        hosts.append(policy.info.host)
    return hosts


def _usable(status: masto.Status, now, stats: Counter) -> bool:
    """Consent, settle time, and follower floor — in that order."""
    ok, why = masto.should_learn_from(status)
    if not ok:
        stats[f"skip_{why}"] += 1
        return False
    if status.created_at is None:
        stats["skip_no_timestamp"] += 1
        return False
    if (now - status.created_at).total_seconds() < MIN_SETTLE_HOURS * 3600:
        stats["skip_too_recent"] += 1
        return False
    if status.account.followers < MIN_FOLLOWERS:
        stats["skip_below_follower_floor"] += 1
        return False
    return True


def _harvest(host: str, token: str, stats: Counter) -> dict[str, masto.Status]:
    """Every source on one instance, deduped by ActivityPub URI.

    Federation means the same viral post trends on many instances at once, so
    dedupe is by the globally-unique status URI rather than per-instance id.
    """
    found: dict[str, masto.Status] = {}

    # --- trends: the top tier ---------------------------------------------
    for page in range(TRENDS_PAGES):
        try:
            batch = masto.trending_statuses(host, limit=40, offset=page * 40, token=token)
        except masto.MastodonError as err:
            log.info("[ft-mastodon] %s trends unavailable: %s", host, str(err)[:80])
            stats["source_trends_failed"] += 1
            break
        if not batch:
            break
        for status in batch:
            found.setdefault(status.uri, status)
        stats["source_trends"] += len(batch)
        time.sleep(POLITE_DELAY)

    # --- tags: topical breadth --------------------------------------------
    for topic in acquire.PREFILTER:
        tag = topic.replace(" ", "")
        try:
            batch = masto.tag_timeline(host, tag, limit=40, token=token)
        except masto.MastodonError:
            stats["source_tags_failed"] += 1
            continue
        for status in batch:
            found.setdefault(status.uri, status)
        stats["source_tags"] += len(batch)

    # --- public: ordinary posts, the mid/low tiers ------------------------
    max_id = ""
    for _ in range(8):
        try:
            batch = masto.public_timeline(host, limit=40, max_id=max_id, token=token)
        except masto.MastodonError as err:
            log.info("[ft-mastodon] %s public timeline unavailable: %s", host, str(err)[:80])
            stats["source_public_failed"] += 1
            break
        if not batch:
            break
        for status in batch:
            found.setdefault(status.uri, status)
        stats["source_public"] += len(batch)
        max_id = batch[-1].id
        time.sleep(POLITE_DELAY)

    return found


def run(token: str = "") -> dict:
    """One collection pass across every accepted instance."""
    hosts = _accepted_hosts()
    if not hosts:
        log.warning(
            "no instances with accepted rules; accept an instance's rules in the "
            "Mastodon Post Creator first"
        )
        return {"instances": 0}

    now = masto.datetime.now(masto.timezone.utc)
    stats: Counter = Counter()
    candidates: list[dict] = []
    labels: list[dict] = []
    seen: set[str] = set()

    for host in hosts:
        found = _harvest(host, token, stats)
        stats["scanned"] += len(found)

        for status in found.values():
            if status.uri in seen:
                continue
            if not _usable(status, now, stats):
                continue
            seen.add(status.uri)

            niche = acquire._match_niche(status.text) or "general"
            uri = masto.corpus_uri(host, status.id)

            candidates.append(
                {
                    "uri": uri,
                    "platform": "mastodon",
                    "text": status.text,
                    "hashtags": status.hashtags,
                    "author_did": status.account.url or status.account.acct,
                    "author_handle": status.account.acct,
                    "created_at": status.created_at.isoformat() if status.created_at else None,
                    "niche": niche,
                }
            )
            labels.append(
                {
                    "uri": uri,
                    "likes": status.favourites,
                    "reposts": status.reblogs,
                    "replies": status.replies,
                    "follower_count": status.account.followers,
                    "has_media": int(status.has_media),
                    "hashtags": status.hashtags,
                    "lifetime_engagement_rate": masto.engagement_rate(
                        status.favourites, status.reblogs, status.replies,
                        status.account.followers,
                    ),
                    # Engagement arrives with the status and the post has settled,
                    # so this is a real measurement — but of a different window
                    # than the Bluesky rows. Stage 3 tiers within (platform,
                    # niche) precisely so the two are never compared.
                    "measured_window": store.WINDOW_48H,
                }
            )
            stats[f"kept_{niche}"] += 1

    added = store.add_candidates(candidates)
    labelled = store.write_labels(labels)

    result = {
        "instances": len(hosts),
        "added": added,
        "labelled": labelled,
        **dict(stats),
    }
    store.update_manifest(mastodon_collect=result)
    log.info("[ft-mastodon] %s", result)
    return result
