"""refresh_exemplars — rebuild the per-niche exemplar pool from 48h engagement.

Schedule: nightly + workflow_dispatch (.github/workflows/refresh_exemplars.yml).
The watchdog dispatches this job when published generations underperform.

Scoring:  score = engagement_rate * 0.5 ** (age_days / 14)

The 14-day half-life keeps the pool responsive to platform shifts without
letting one good week dominate forever. A 14-day-old post needs twice the
engagement rate of a fresh one to hold the same rank.

Selection is greedy with a similarity gate: walk the ranked list, keep a
candidate only if it is less than DEDUPE_THRESHOLD similar to everything already
kept. Without this the pool collapses — the highest-engagement posts in a niche
are often near-identical ("just shipped X!"), and few-shotting the LLM on twenty
paraphrases of one post produces twenty paraphrases back.

Run:
    python -m src.jobs.refresh_exemplars --dry-run
    python -m src.jobs.refresh_exemplars --niche "indie makers"
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

import numpy as np

from .. import embeddings
from ..db import (
    JobRun,
    configure_logging,
    get_client,
    insert,
    iso,
    load_niches,
    rpc,
    upsert,
    utcnow,
)

log = logging.getLogger(__name__)

TARGET_POOL_SIZE = 20
HALF_LIFE_DAYS = 14.0
DEDUPE_THRESHOLD = 0.85

# See the exemplar_candidates RPC: engagement_rate is follower-normalised, so
# tiny accounts produce absurd rates. Posts from accounts below this floor are
# not eligible to be exemplars.
MIN_FOLLOWERS = 200

# Only consider posts this recent. Beyond ~90 days the decay term has crushed the
# score anyway, so this mostly bounds the query.
MAX_CANDIDATE_AGE_DAYS = 90

# Baselines are computed over a shorter, more current window than exemplars.
BASELINE_WINDOW_DAYS = 30


@dataclass
class Candidate:
    post_uri: str
    text: str
    engagement_rate: float
    follower_count: int
    age_days: float
    score: float


def decay(age_days: float, half_life: float = HALF_LIFE_DAYS) -> float:
    """Exponential recency decay in [0, 1]."""
    return float(0.5 ** (max(age_days, 0.0) / half_life))


def _load_candidates(niche: str) -> list[Candidate]:
    rows = rpc(
        "exemplar_candidates",
        {
            "target_niche": niche,
            "max_age_days": MAX_CANDIDATE_AGE_DAYS,
            "min_followers": MIN_FOLLOWERS,
        },
    )
    out: list[Candidate] = []
    for row in rows:
        rate = float(row["engagement_rate"] or 0.0)
        age = float(row["age_days"] or 0.0)
        out.append(
            Candidate(
                post_uri=row["post_uri"],
                text=row["text"],
                engagement_rate=rate,
                follower_count=int(row["follower_count"] or 0),
                age_days=age,
                score=rate * decay(age),
            )
        )
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def select_diverse(
    candidates: list[Candidate],
    vectors: np.ndarray,
    target: int = TARGET_POOL_SIZE,
    threshold: float = DEDUPE_THRESHOLD,
) -> list[int]:
    """Greedily pick indices of up to `target` candidates that are mutually dissimilar.

    `candidates` must be pre-sorted by score descending and row-aligned with
    `vectors`. Returns indices into that list.

    O(target * n) similarity checks — with n in the low thousands and target=20
    this is milliseconds, so the simple version is the right one.
    """
    chosen: list[int] = []
    for i in range(len(candidates)):
        if len(chosen) >= target:
            break
        if any(
            embeddings.cosine_similarity(vectors[i], vectors[j]) > threshold
            for j in chosen
        ):
            continue
        chosen.append(i)
    return chosen


def _refresh_niche(job: JobRun, niche: str, dry_run: bool) -> None:
    candidates = _load_candidates(niche)
    if not candidates:
        job.note(
            f"{niche}: no candidates (needs posts with a 48h snapshot from authors "
            f"with >={MIN_FOLLOWERS} followers)"
        )
        return

    job.note(f"{niche}: {len(candidates)} candidates")

    vectors = embeddings.embed([c.text for c in candidates])
    chosen_idx = select_diverse(candidates, vectors)
    chosen = [candidates[i] for i in chosen_idx]

    job.note(
        f"{niche}: selected {len(chosen)}/{TARGET_POOL_SIZE} after dedupe "
        f"(rejected {len(candidates) - len(chosen)} as duplicates or surplus)"
    )

    now = utcnow()
    new_rows = [
        {
            "post_uri": c.post_uri,
            "niche": niche,
            "score": c.score,
            "embedding": vectors[i].tolist(),
            "active": True,
            "refreshed_at": iso(now),
        }
        for i, c in zip(chosen_idx, chosen)
    ]

    if dry_run:
        job.note(f"{niche}: would activate {len(new_rows)} exemplars")
        for c in chosen[:5]:
            log.info(
                "  score=%.5f rate=%.5f age=%.1fd followers=%d | %s",
                c.score,
                c.engagement_rate,
                c.age_days,
                c.follower_count,
                c.text.replace("\n", " ")[:70],
            )
        job.count("would_activate", len(new_rows))
        return

    client = get_client()

    # Deactivate the whole current pool, then insert the new one. Exemplars are
    # never deleted, so a bad refresh stays auditable — and generation.py only
    # ever reads active rows.
    #
    # Not a transaction: PostgREST has no cross-request transaction. If the insert
    # below fails, the niche is left with an empty active pool until the next run
    # rather than a stale one. Retrieval degrades to KB-only, which is acceptable
    # and preferable to silently serving a pool we meant to replace.
    displaced = (
        client.table("exemplars")
        .update({"active": False})
        .eq("niche", niche)
        .eq("active", True)
        .execute()
        .data
        or []
    )
    job.count("deactivated", len(displaced))
    job.count("activated", insert("exemplars", new_rows))


def _recompute_baseline(job: JobRun, niche: str, dry_run: bool) -> None:
    """Mean 48h engagement_rate for a niche over the last 30 days.

    This is what watchdog.py compares published generations against: it should
    describe the niche's *typical* post, so no recency decay is applied and no
    ranking happens — unlike exemplar selection, which wants the best.

    The MIN_FOLLOWERS floor IS applied, for the same reason it is applied to
    exemplars. Measured on real data: without the floor this niche's baseline came
    out at 0.20, because a 7-follower account with 12 replies scores 1.71 and drags
    the mean up by an order of magnitude. No real account achieves a 0.2 rate, so
    every published generation would read as "below 80% of baseline" forever and
    the watchdog would dispatch a refresh every week regardless of actual quality.
    With the floor the same niche baselines at ~0.009, which real posts can
    straddle in both directions — which is the entire point of a baseline.

    Reuses exemplar_candidates because it already encodes the floor + 48h join;
    the decay that refresh applies afterwards is deliberately not applied here.
    """
    rows = rpc(
        "exemplar_candidates",
        {
            "target_niche": niche,
            "max_age_days": BASELINE_WINDOW_DAYS,
            "min_followers": MIN_FOLLOWERS,
        },
    )
    rates = [float(r["engagement_rate"]) for r in rows if r["engagement_rate"] is not None]

    if not rates:
        job.note(
            f"{niche}: no 48h snapshots from authors with >={MIN_FOLLOWERS} followers "
            f"in {BASELINE_WINDOW_DAYS}d; baseline unchanged"
        )
        return

    # Mean, matching the avg_engagement_rate column. Engagement is heavy-tailed
    # even above the floor, so this is still outlier-sensitive; a median would be
    # more robust if your niche has a few very large accounts in it.
    avg = sum(rates) / len(rates)
    if dry_run:
        job.note(f"{niche}: would set baseline avg_engagement_rate={avg:.6f} (n={len(rates)})")
        return

    upsert(
        "performance_baselines",
        [
            {
                "scope": "niche",
                "scope_key": niche,
                "window_label": "48h",
                "avg_engagement_rate": avg,
                "computed_at": iso(utcnow()),
            }
        ],
        on_conflict="scope,scope_key,window_label",
    )
    job.note(f"{niche}: baseline avg_engagement_rate={avg:.6f} (n={len(rates)})")
    job.count("baselines_written")


def run(dry_run: bool = False, only_niche: str | None = None) -> None:
    niches = load_niches()
    if only_niche:
        if only_niche not in niches:
            raise SystemExit(
                f"No active niche called {only_niche!r}. "
                f"Active: {', '.join(sorted(niches)) or '(none)'}. "
                f"See `python -m src.jobs.niches --list`."
            )
        niches = {only_niche: niches[only_niche]}

    with JobRun("refresh_exemplars", dry_run=dry_run) as job:
        if not niches:
            job.note("no active niches; nothing to refresh")
            return
        for niche in niches:
            try:
                _refresh_niche(job, niche, dry_run)
                _recompute_baseline(job, niche, dry_run)
            except Exception as err:  # noqa: BLE001
                log.exception("Niche %r failed", niche)
                job.note(f"niche {niche!r} failed: {type(err).__name__}: {err}")
                job.partial()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild the per-niche exemplar pool and recompute baselines."
    )
    parser.add_argument("--dry-run", action="store_true", help="Log without writing.")
    parser.add_argument("--niche", help="Only refresh this niche (default: all).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    run(dry_run=args.dry_run, only_niche=args.niche)


if __name__ == "__main__":
    main()
