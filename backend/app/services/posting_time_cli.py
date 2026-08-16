"""Refresh a platform's posting-time curve over the last month.

    python -m app.services.posting_time_cli bluesky
    python -m app.services.posting_time_cli mastodon                     # every accepted instance
    python -m app.services.posting_time_cli mastodon --instance toot.garden

Mastodon curves are per-instance: an instance is a community with its own daily
rhythm, so each gets its own curve and its own sufficiency verdict.

DATA_DIR must point at the app's userData directory, or this reads and writes a
different (near-empty) store than the app does — the same trap the fine-tune CLI
documents.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import posting_time_corpus as ptc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=["bluesky", "mastodon"])
    parser.add_argument("--days", type=int, default=31, help="window length (default 31)")
    parser.add_argument(
        "--settle-hours",
        type=int,
        default=None,
        help="how long a post must have been live before its counts are read",
    )
    parser.add_argument(
        "--target-authors", type=int, default=1500, help="bluesky only: how many accounts to sample"
    )
    parser.add_argument(
        "--instance",
        default="",
        help="mastodon only: one instance host. Omitted means every accepted instance.",
    )
    parser.add_argument(
        "--token",
        default="",
        help=(
            "mastodon only: an access token. The larger instances (mastodon.social) "
            "refuse anonymous reads of the public local timeline, which is the only "
            "sample this learns from. Defaults to MASTODON_ACCESS_TOKEN."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the result without storing it"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )

    kwargs: dict = {"days": args.days}
    if args.settle_hours is not None:
        kwargs["settle_hours"] = args.settle_hours
    if args.platform == "mastodon":
        # Env fallback so the token need not appear in shell history.
        kwargs["token"] = args.token or os.environ.get("MASTODON_ACCESS_TOKEN", "")

    if args.platform == "bluesky":
        kwargs["target_authors"] = args.target_authors
        curves = [ptc.collect_bluesky(**kwargs)]
    elif args.instance:
        curves = [ptc.collect_mastodon(args.instance, **kwargs)]
    else:
        curves = ptc.collect_mastodon_all(**kwargs)
        if not curves:
            print(
                "No Mastodon instance has had its rules accepted yet — open the Mastodon "
                "Post Creator and read a server's rules first."
            )
            return 1

    any_usable = False
    for curve in curves:
        print(ptc.summarise(curve))
        for note in curve.notes:
            print(f"  note: {note}")

        if args.dry_run:
            print("  (dry run — not stored)\n")
            continue

        wrote = ptc.save_curve(curve)
        if curve.usable:
            any_usable = True
            print(f"  stored -> {ptc.CURVES_PATH}\n")
            continue

        print(
            f"  INSUFFICIENT DATA: reliability {curve.reliability:+.3f} is below the "
            f"{ptc.MIN_RELIABILITY:+.2f} floor, so the app will say so rather than show a curve."
        )
        print(
            "  "
            + (
                "Recorded so the screen can explain why."
                if wrote
                else "Kept the existing usable curve instead."
            )
            + "\n"
        )

    return 0 if (args.dry_run or any_usable) else 1


if __name__ == "__main__":
    raise SystemExit(main())
