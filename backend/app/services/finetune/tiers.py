"""Stage 3 — follower floor, then quality tiers.

Runs BEFORE brief generation on purpose: briefs cost one LLM call each, and
there is no reason to buy one for a post that will never enter training.

Two rules, both load-bearing:

  * FOLLOWER FLOOR FIRST. engagement_rate is follower-normalised, so a
    1-follower account with 3 likes scores 6.0 and would occupy the entire top
    tier. Measured on the real re-hydrated corpus: without the floor the maximum
    rate is 6.0 (that exact post); with it, 1.014 (210 likes on 212 followers —
    an actual hit). The live corpus already learned this at
    refresh_exemplars._recompute_baseline, where an unfloored niche baseline came
    out at 0.20 instead of 0.009.

  * PERCENTILE WITHIN (platform, niche), never globally. Bluesky rows carry
    lifetime engagement and Mastodon rows carry a 48h measurement; those are not
    comparable numbers. Neither are two niches with different audience sizes. A
    global percentile would mostly rank platforms and niches against each other
    rather than posts against their own peers.

The `low` tier is KEPT, not discarded. Training only on winners throws away the
contrast that teaches what distinguishes them; the quality token lets us ask for
`top` at inference while still learning from the whole distribution.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from . import store

log = logging.getLogger(__name__)

# Matches refresh_exemplars.MIN_FOLLOWERS. Mastodon uses the lower floor its own
# collector uses, since instance populations are far smaller than Bluesky's.
MIN_FOLLOWERS = {"bluesky": 200, "mastodon": 50}

TOP_PERCENTILE = 0.90
MID_PERCENTILE = 0.40

# A niche/platform cell with fewer than this cannot support meaningful
# percentiles — p90 of six posts is just "the best one". Such cells are tiered
# as `mid` wholesale rather than given a fake ranking.
MIN_CELL_SIZE = 20


def run() -> dict:
    """Assign quality_tier to every labelled row. Idempotent."""
    with store.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                select uri, platform, niche, follower_count, lifetime_engagement_rate
                  from ft_posts
                 where status = 'labelled' and lifetime_engagement_rate is not null
                """
            )
        ]

    if not rows:
        log.warning("nothing labelled yet; run rehydrate first")
        return {"labelled": 0}

    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    below_floor = 0

    for row in rows:
        floor = MIN_FOLLOWERS.get(row["platform"], 200)
        if (row["follower_count"] or 0) < floor:
            below_floor += 1
            continue
        cells[(row["platform"], row["niche"] or "(none)")].append(row)

    updates: list[tuple[str, str]] = []
    tier_counts: dict[str, int] = defaultdict(int)
    small_cells = 0

    for (platform, niche), members in cells.items():
        members.sort(key=lambda r: r["lifetime_engagement_rate"])
        n = len(members)

        if n < MIN_CELL_SIZE:
            small_cells += 1
            for row in members:
                updates.append(("mid", row["uri"]))
                tier_counts["mid"] += 1
            continue

        top_cut = members[int(n * TOP_PERCENTILE)]["lifetime_engagement_rate"]
        mid_cut = members[int(n * MID_PERCENTILE)]["lifetime_engagement_rate"]

        for row in members:
            rate = row["lifetime_engagement_rate"]
            tier = "top" if rate >= top_cut else "mid" if rate >= mid_cut else "low"
            updates.append((tier, row["uri"]))
            tier_counts[tier] += 1

    with store.connect() as conn:
        # Clear first, then write. Re-running after a floor or percentile change
        # must not leave a stale tier on a row that no longer qualifies.
        conn.execute("update ft_posts set quality_tier = null where status = 'labelled'")
        conn.executemany("update ft_posts set quality_tier = ? where uri = ?", updates)

    result = {
        "labelled": len(rows),
        "below_follower_floor": below_floor,
        "tiered": len(updates),
        "cells": len(cells),
        "small_cells_forced_mid": small_cells,
        **{f"tier_{k}": v for k, v in tier_counts.items()},
    }
    store.update_manifest(
        stage3_tiers={
            **result,
            "min_followers": MIN_FOLLOWERS,
            "top_percentile": TOP_PERCENTILE,
            "mid_percentile": MID_PERCENTILE,
        }
    )
    log.info("tiering complete: %s", result)
    return result
