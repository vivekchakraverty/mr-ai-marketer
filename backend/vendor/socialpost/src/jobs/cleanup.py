"""cleanup — age out stale knowledge and prune old data.

Schedule: weekly (.github/workflows/cleanup.yml).

Three jobs in one:
  1. Decay the KB. Platform advice rots; multiply every active article's
     decay_weight by 0.9 weekly and deactivate below 0.3. That is ~11 weeks from
     1.0 to retirement, and because generation.py orders by decay_weight, older
     guidance quietly loses to newer guidance before it disappears.
  2. Delete engagement snapshots older than 90 days. They have already been
     folded into baselines and exemplar scores.
  3. Deactivate exemplars not refreshed in 45 days — i.e. a niche whose refresh
     job has been silently failing. Better to generate from platform norms than
     from a stale pool that no longer reflects the platform.
  4. Reap job_runs rows abandoned with a NULL status by a killed process, so
     "is anything stuck?" stays an answerable question.

Side benefit, and a real one: Supabase pauses free-tier projects after a week of
inactivity. These weekly writes keep the project awake.

Run:
    python -m src.jobs.cleanup --dry-run
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta

from ..db import JobRun, configure_logging, get_client, iso, reap_stuck_job_runs, utcnow

log = logging.getLogger(__name__)

KB_DECAY_FACTOR = 0.9
KB_DEACTIVATE_BELOW = 0.3
SNAPSHOT_RETENTION_DAYS = 90
EXEMPLAR_STALE_DAYS = 45


def _decay_kb(job: JobRun, dry_run: bool) -> None:
    client = get_client()
    rows = (
        client.table("kb_articles")
        .select("id, decay_weight")
        .eq("active", True)
        .execute()
        .data
        or []
    )
    if not rows:
        job.note("kb: no active articles")
        return

    decayed = 0
    retired = 0
    for row in rows:
        new_weight = float(row["decay_weight"] or 0.0) * KB_DECAY_FACTOR
        # Deactivating and decaying in one update: an article that falls below the
        # floor this week should not linger at full weight until next week.
        payload: dict = {"decay_weight": new_weight}
        if new_weight < KB_DEACTIVATE_BELOW:
            payload["active"] = False
            retired += 1
        if not dry_run:
            client.table("kb_articles").update(payload).eq("id", row["id"]).execute()
        decayed += 1

    verb = "would decay" if dry_run else "decayed"
    job.note(f"kb: {verb} {decayed} articles, retired {retired} below {KB_DEACTIVATE_BELOW}")
    job.count("kb_decayed", decayed)
    job.count("kb_retired", retired)


def _prune_snapshots(job: JobRun, dry_run: bool) -> None:
    cutoff = iso(utcnow() - timedelta(days=SNAPSHOT_RETENTION_DAYS))
    client = get_client()

    stale = (
        client.table("engagement_snapshots")
        .select("id", count="exact")
        .lt("captured_at", cutoff)
        .limit(0)
        .execute()
    )
    n = stale.count or 0
    if not n:
        job.note("snapshots: nothing older than 90d")
        return

    if dry_run:
        job.note(f"snapshots: would delete {n} older than {SNAPSHOT_RETENTION_DAYS}d")
        job.count("would_delete_snapshots", n)
        return

    client.table("engagement_snapshots").delete().lt("captured_at", cutoff).execute()
    job.note(f"snapshots: deleted {n} older than {SNAPSHOT_RETENTION_DAYS}d")
    job.count("snapshots_deleted", n)


def _deactivate_stale_exemplars(job: JobRun, dry_run: bool) -> None:
    cutoff = iso(utcnow() - timedelta(days=EXEMPLAR_STALE_DAYS))
    client = get_client()

    stale = (
        client.table("exemplars")
        .select("id", count="exact")
        .eq("active", True)
        .lt("refreshed_at", cutoff)
        .limit(0)
        .execute()
    )
    n = stale.count or 0
    if not n:
        job.note("exemplars: none stale")
        return

    if dry_run:
        job.note(f"exemplars: would deactivate {n} unrefreshed for {EXEMPLAR_STALE_DAYS}d")
        job.count("would_deactivate_exemplars", n)
        return

    client.table("exemplars").update({"active": False}).eq("active", True).lt(
        "refreshed_at", cutoff
    ).execute()
    # If this ever fires, refresh_exemplars has been failing for six weeks.
    job.note(
        f"exemplars: deactivated {n} unrefreshed for {EXEMPLAR_STALE_DAYS}d — "
        f"check that refresh_exemplars is succeeding"
    )
    job.count("exemplars_deactivated", n)


def _reap_job_runs(job: JobRun, dry_run: bool) -> None:
    if dry_run:
        # Counting without the update needs the same query; not worth duplicating
        # for a dry run of a self-healing bookkeeping step.
        job.note("job_runs: would reap any NULL-status runs older than 6h")
        return
    reaped = reap_stuck_job_runs()
    if reaped:
        job.note(f"job_runs: reaped {reaped} abandoned run(s) as 'failure'")
        job.count("job_runs_reaped", reaped)
    else:
        job.note("job_runs: none stuck")


def run(dry_run: bool = False) -> None:
    with JobRun("cleanup", dry_run=dry_run) as job:
        _decay_kb(job, dry_run)
        _prune_snapshots(job, dry_run)
        _deactivate_stale_exemplars(job, dry_run)
        _reap_job_runs(job, dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decay the KB and prune stale rows.")
    parser.add_argument("--dry-run", action="store_true", help="Log without writing.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
