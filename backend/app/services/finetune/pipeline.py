"""Run every remaining stage, in order, skipping work already done.

Each stage is individually resumable because each selects only unfinished rows:

    scan        insert-or-ignore on the URI primary key
    rerank      rows where relevance is null
    rehydrate   rows with status='candidate'
    tiers       idempotent — clears and recomputes from scratch
    briefs      rows where brief is null

So this is safe to run repeatedly, and safe to interrupt: re-running picks up
from the database rather than from any in-memory position. Nothing here tracks
"where we got to" in a file, deliberately — the corpus tables ARE the progress
state, and a second source of truth would only drift from them.

Stages that need a credential the environment does not have are SKIPPED with a
reason rather than failing the run, so a partial setup still makes progress.
"""

from __future__ import annotations

import logging
import time

from . import briefs, mastodon_collect, rehydrate, store, tiers

log = logging.getLogger(__name__)


def status() -> dict:
    """Where the pipeline currently stands. Cheap, read-only."""
    with store.connect() as conn:
        def n(where: str, params: tuple = ()) -> int:
            return conn.execute(f"select count(*) from ft_posts where {where}", params).fetchone()[0]

        return {
            "candidates_awaiting_rehydration": n("status = 'candidate'"),
            "labelled": n("status = 'labelled'"),
            "gone": n("status = 'gone'"),
            "rejected_offtopic": n("status = 'rejected'"),
            "unscored_relevance": n("relevance is null and platform = 'bluesky'"),
            "tiered": n("quality_tier is not null"),
            "briefs_done": n("brief is not null"),
            "briefs_outstanding": n("brief is null and quality_tier is not null"),
            "by_platform": {
                row[0]: row[1]
                for row in conn.execute(
                    "select platform, count(*) from ft_posts group by platform"
                )
            },
        }


def run(workers: int = 24, skip_collect: bool = False) -> dict:
    """Advance the corpus as far as it can go from its current state."""
    started = time.time()
    results: dict[str, object] = {"start_state": status()}

    # --- Mastodon collection (optional, gated on accepted instance rules) ----
    if not skip_collect:
        try:
            results["mastodon_collect"] = mastodon_collect.run()
        except Exception as err:  # noqa: BLE001 — never block the Bluesky path
            results["mastodon_collect"] = {"skipped": str(err)[:160]}

    # --- Re-hydration --------------------------------------------------------
    pending = status()["candidates_awaiting_rehydration"]
    if pending:
        try:
            results["rehydrate"] = rehydrate.run(limit=pending)
        except Exception as err:  # noqa: BLE001
            results["rehydrate"] = {"skipped": str(err)[:160]}
            log.warning("rehydrate unavailable: %s", str(err)[:160])
    else:
        results["rehydrate"] = {"skipped": "nothing pending"}

    # --- Tiering (local, always safe) ---------------------------------------
    results["tiers"] = tiers.run()

    # --- Briefs --------------------------------------------------------------
    outstanding = status()["briefs_outstanding"]
    if outstanding:
        try:
            results["briefs"] = briefs.run(workers=workers)
        except Exception as err:  # noqa: BLE001
            results["briefs"] = {"skipped": str(err)[:160]}
            log.warning("briefs unavailable: %s", str(err)[:160])
    else:
        results["briefs"] = {"skipped": "all briefs written"}

    results["end_state"] = status()
    results["elapsed_seconds"] = round(time.time() - started, 1)
    store.update_manifest(pipeline_last_run=results)
    return results
