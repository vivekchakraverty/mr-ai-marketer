"""telemetry — collect outcomes and drain the send queue. Best-effort.

Schedule: hourly-ish (local scheduler) / every few hours (Actions). It is cheap
and idempotent, so cadence barely matters — a missed run just delivers next time.

Two steps each run:
  1. collect_outcomes() — for published generations that reached 48h, queue an
     outcome record (derived numbers only).
  2. flush() — POST undelivered outbox rows to the ingest Space.

Entirely inert unless TELEMETRY_ENDPOINT is set. Nothing here can fail a user's
generation; the outbox decouples the two.

Run:
    python -m src.jobs.telemetry --dry-run
    python -m src.jobs.telemetry
"""

from __future__ import annotations

import argparse
import logging

from .. import telemetry
from ..db import JobRun, configure_logging

log = logging.getLogger(__name__)


def run(dry_run: bool = False) -> None:
    with JobRun("telemetry", dry_run=dry_run) as job:
        if not telemetry.is_enabled():
            job.note("telemetry disabled (TELEMETRY_ENDPOINT not set); nothing to do")
            return

        if telemetry.needs_consent():
            # Records are only ever enqueued after consent, so this mainly guards
            # the case where terms were bumped and re-consent is pending.
            job.note("awaiting consent to current terms; not sending")
            return

        if dry_run:
            # collect_outcomes writes (enqueues + marks reported), so in a dry run
            # only report what is already queued and waiting.
            pending = _pending_count()
            job.note(f"would collect outcomes and flush; {pending} record(s) already queued")
            return

        collected = telemetry.collect_outcomes()
        job.count("outcomes_queued", collected)

        summary = telemetry.flush()
        for key, value in summary.items():
            job.count(key, value)
        job.note(f"flush: {summary}")


def _pending_count() -> int:
    from ..db import get_client

    return (
        get_client()
        .table("telemetry_outbox")
        .select("id", count="exact")
        .is_("delivered_at", "null")
        .limit(0)
        .execute()
        .count
        or 0
    )


def _accept_consent(content_opt_in: bool, assume_yes: bool) -> None:
    """Headless consent, for users who run without the Streamlit UI."""
    import json

    if not telemetry.is_enabled():
        print("Telemetry is off (TELEMETRY_ENDPOINT not set); no consent needed.")
        return

    preview = telemetry.preview_payloads(content_opt_in)
    print("This tool pools anonymous performance metrics to improve results for")
    print("everyone. Participating is part of using the generator.\n")
    print("Exactly what gets sent (metrics tier — never post/prompt text unless you")
    print("opt into content sharing):\n")
    print(json.dumps(preview["generation"], indent=2))
    print("\nOutcome record (after a published post reaches 48h):\n")
    print(json.dumps(preview["outcome"], indent=2))
    print(f"\ncontent tier (raw prompt + draft text): {'ON' if content_opt_in else 'off'}")

    if not assume_yes:
        reply = input("\nAccept these terms? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Not accepted. The generator stays gated until you accept.")
            raise SystemExit(1)

    telemetry.record_consent(content_opt_in=content_opt_in)
    print("Consent recorded. You can withdraw pooled data later with --forget-pool.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect outcomes and drain the telemetry outbox.")
    parser.add_argument("--dry-run", action="store_true", help="Report without sending or marking.")
    parser.add_argument(
        "--consent",
        action="store_true",
        help="Review the telemetry terms and accept them (headless equivalent of the UI gate).",
    )
    parser.add_argument(
        "--share-content",
        action="store_true",
        help="With --consent: also opt into sharing raw prompt/draft text (off by default).",
    )
    parser.add_argument("--yes", action="store_true", help="With --consent: skip the prompt.")
    parser.add_argument(
        "--forget-pool",
        action="store_true",
        help="Queue a request to erase this instance's contributions from the pool.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.consent:
        _accept_consent(content_opt_in=args.share_content, assume_yes=args.yes)
        return
    if args.forget_pool:
        telemetry.request_pool_deletion(note="requested via CLI")
        print("Queued a pool-deletion request; it sends on the next telemetry run.")
        return

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
