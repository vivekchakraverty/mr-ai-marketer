"""authors — choose whose feeds to collect. Manual only; never scheduled.

Keyword search finds a niche's posts, but only the ones Bluesky's index happens to
return — a sample, and a biased one. Tracking an account collects its whole feed
on every ingest: a census of somebody who reliably performs in that niche. That is
what turns a thin exemplar pool into one worth learning from.

    python -m src.jobs.authors --list
    python -m src.jobs.authors --suggest "indie makers"
    python -m src.jobs.authors --add pfrazee.com --niche "indie makers"
    python -m src.jobs.authors --remove pfrazee.com --niche "indie makers"

Tracked feeds are collected by the normal ingest run — nothing extra to schedule:

    python -m src.jobs.ingest --niche "indie makers"

Tracking is not an exemption from anything: a tracked author still passes the
no-index consent check, the follower floor, and the 44h age ceiling like any other.
"""

from __future__ import annotations

import argparse
import logging
import sys

from ..db import (
    NicheError,
    configure_logging,
    list_tracked_authors,
    suggest_authors,
    track_author,
    untrack_author,
)

log = logging.getLogger(__name__)


def _resolve(actor: str) -> tuple[str, str]:
    """handle-or-DID -> (did, handle). DIDs are what we store; handles change."""
    from .. import bluesky

    profile = bluesky.get_client().app.bsky.actor.get_profile({"actor": actor})
    return profile.did, profile.handle


def _print_list() -> None:
    rows = list_tracked_authors(active_only=False)
    if not rows:
        print("No tracked authors.")
        print('Find some:  python -m src.jobs.authors --suggest "your niche"')
        return

    by_niche: dict[str, list[dict]] = {}
    for row in rows:
        by_niche.setdefault(row["niche"], []).append(row)

    for niche, authors in sorted(by_niche.items()):
        print(f"\n[{niche}] {len(authors)} tracked")
        for a in authors:
            state = "" if a["active"] else "  (paused)"
            print(f"  {a['handle'] or '(unknown handle)':34} {a['did']}{state}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Track the accounts whose feeds are worth collecting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="Show tracked authors by niche.")
    action.add_argument(
        "--suggest",
        metavar="NICHE",
        help="Rank authors worth tracking, from engagement already measured in this niche.",
    )
    action.add_argument("--add", metavar="HANDLE_OR_DID", help="Track an author. Needs --niche.")
    action.add_argument("--remove", metavar="HANDLE_OR_DID", help="Stop tracking an author.")

    parser.add_argument("--niche", help="Which niche the author belongs to.")
    parser.add_argument(
        "--limit", type=int, default=10, help="How many suggestions to show (default 10)."
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    try:
        if args.list:
            _print_list()

        elif args.suggest:
            rows = suggest_authors(args.suggest, limit=args.limit)
            if not rows:
                print(f"Nothing to suggest for {args.suggest!r} yet.")
                print(
                    "This ranks authors using 48h engagement already measured in that\n"
                    "niche, so it needs a few days of collection first. Check progress:\n"
                    "  python -m src.jobs.niches --list"
                )
                return
            print(f"Authors worth tracking in {args.suggest!r}, best first:\n")
            print(f"  {'handle':34} {'followers':>10} {'posts':>6} {'mean rate':>11}")
            for r in rows:
                print(
                    f"  {(r['handle'] or r['did'])[:34]:34} {r['follower_count'] or 0:>10,} "
                    f"{r['measured_posts']:>6} {r['mean_engagement_rate']:>11.5f}"
                )
            print(
                f"\nTrack one:  python -m src.jobs.authors --add {rows[0]['handle']} "
                f'--niche "{args.suggest}"'
            )

        elif args.add:
            if not args.niche:
                raise NicheError("--add needs --niche.")
            did, handle = _resolve(args.add)
            track_author(did, handle, args.niche)
            print(f"Tracking @{handle} ({did}) for {args.niche!r}.")
            print(f'Their feed is collected from the next ingest:\n  python -m src.jobs.ingest --niche "{args.niche}"')

        elif args.remove:
            did, handle = _resolve(args.remove)
            removed = untrack_author(did, args.niche)
            if removed:
                where = f" for {args.niche!r}" if args.niche else " everywhere"
                print(f"Stopped tracking @{handle}{where}. Their collected posts are untouched.")
                print("To erase those too:  python -m src.jobs.forget --handle " + handle)
            else:
                print(f"@{handle} was not being tracked.")

    except NicheError as err:
        print(f"Error: {err}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
