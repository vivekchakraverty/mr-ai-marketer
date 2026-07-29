"""niches — manage what the system watches. Manual only; never scheduled.

Niches live in the database, not in config/niches.yaml. The YAML seeds the table
on first run and is a template afterwards, so you can add and remove niches from
here or from the Streamlit UI without editing a file in the repo.

    python -m src.jobs.niches --list
    python -m src.jobs.niches --add "rust gamedev" --keywords "rust gamedev" "bevy engine"
    python -m src.jobs.niches --edit "rust gamedev" --keywords "bevy engine" "wgpu"
    python -m src.jobs.niches --rename "rust gamedev" --to "rust game dev"
    python -m src.jobs.niches --disable "ai tools"
    python -m src.jobs.niches --enable "ai tools"
    python -m src.jobs.niches --remove "ai tools"            # keeps collected data
    python -m src.jobs.niches --remove "ai tools" --purge    # deletes it too
    python -m src.jobs.niches --import-config                # re-read the YAML

A new niche collects nothing until ingest runs:

    python -m src.jobs.ingest --niche "rust gamedev"

and produces no exemplars until its posts reach 48h and refresh_exemplars runs.
See DOCUMENTATION.md -> The cold start.
"""

from __future__ import annotations

import argparse
import logging
import sys

from ..db import (
    NicheError,
    configure_logging,
    delete_niche,
    get_niche,
    list_niches,
    load_niches_yaml,
    niche_data_counts,
    normalise_niche_name,
    rename_niche,
    save_niche,
    set_niche_active,
    weak_keywords,
    validate_niche,
)

log = logging.getLogger(__name__)


def _print_list(show_counts: bool = True) -> None:
    rows = list_niches()
    if not rows:
        print("No niches configured.")
        print('Add one:  python -m src.jobs.niches --add "my niche" --keywords "a phrase"')
        return

    print(f"{len(rows)} niche(s):\n")
    for row in rows:
        state = "" if row["active"] else "  (disabled — not collecting)"
        print(f"  {row['name']}{state}")
        print(f"    keywords: {', '.join(row['keywords'])}")
        if show_counts:
            counts = niche_data_counts(row["name"])
            print(
                f"    data    : {counts['posts']} posts, {counts['exemplars']} exemplars, "
                f"{counts['authors']} authors, {counts['generations']} generations"
            )
        weak = weak_keywords(row["keywords"])
        if weak:
            print(f"    note    : {', '.join(weak)} may be too broad to be useful")
        print()


def _warn_weak(keywords: list[str]) -> None:
    weak = weak_keywords(keywords)
    if weak:
        log.warning(
            "These keywords are short single words and will match a lot of unrelated "
            "posts, which wastes rate limit and dilutes the corpus: %s",
            ", ".join(weak),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage the niches this system watches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="Show every niche and its data.")
    action.add_argument("--add", metavar="NAME", help="Create a niche. Needs --keywords.")
    action.add_argument("--edit", metavar="NAME", help="Replace a niche's keywords. Needs --keywords.")
    action.add_argument("--rename", metavar="NAME", help="Rename a niche. Needs --to.")
    action.add_argument("--enable", metavar="NAME", help="Resume collecting for a niche.")
    action.add_argument("--disable", metavar="NAME", help="Stop collecting. Keeps existing data.")
    action.add_argument("--remove", metavar="NAME", help="Delete the niche. Keeps data unless --purge.")
    action.add_argument(
        "--import-config",
        action="store_true",
        help="Re-import config/niches.yaml, overwriting matching niches.",
    )

    parser.add_argument("--keywords", nargs="+", metavar="KW", help="Search keywords.")
    parser.add_argument("--to", metavar="NEW", help="New name, for --rename.")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="With --remove: also delete the niche's authors, posts, snapshots and exemplars.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt for --purge.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    try:
        if args.list:
            _print_list()

        elif args.add:
            if not args.keywords:
                raise NicheError("--add needs --keywords, e.g. --keywords \"bevy engine\" \"wgpu\"")
            if get_niche(args.add):
                raise NicheError(
                    f"{normalise_niche_name(args.add)!r} already exists. Use --edit to change "
                    f"its keywords."
                )
            name, keywords = validate_niche(args.add, args.keywords)
            _warn_weak(keywords)
            save_niche(name, keywords)
            print(f"Added {name!r} with {len(keywords)} keyword(s).")
            print(f'Collect now:  python -m src.jobs.ingest --niche "{name}"')

        elif args.edit:
            if not args.keywords:
                raise NicheError("--edit needs --keywords.")
            existing = get_niche(args.edit)
            if not existing:
                raise NicheError(f"No niche called {normalise_niche_name(args.edit)!r}.")
            name, keywords = validate_niche(args.edit, args.keywords)
            _warn_weak(keywords)
            save_niche(name, keywords, active=existing["active"])
            print(f"Updated {name!r}: {', '.join(keywords)}")
            print("Existing posts keep their niche; new keywords apply from the next ingest.")

        elif args.rename:
            if not args.to:
                raise NicheError("--rename needs --to, e.g. --rename \"old\" --to \"new\"")
            moved = rename_niche(args.rename, args.to)
            total = sum(moved.values())
            print(f"Renamed to {normalise_niche_name(args.to)!r}, migrating {total} row(s):")
            for table, n in moved.items():
                print(f"  {table:24} {n}")

        elif args.enable:
            set_niche_active(args.enable, True)
            print(f"{normalise_niche_name(args.enable)!r} is collecting again.")

        elif args.disable:
            set_niche_active(args.disable, False)
            print(f"{normalise_niche_name(args.disable)!r} disabled. Existing data is untouched.")

        elif args.remove:
            name = normalise_niche_name(args.remove)
            if get_niche(name) is None:
                raise NicheError(f"No niche called {name!r}.")
            if args.purge:
                counts = niche_data_counts(name)
                print(
                    f"--purge will delete {counts['authors']} authors and cascade to "
                    f"{counts['posts']} posts, their snapshots, and {counts['exemplars']} "
                    f"exemplars. This cannot be undone."
                )
                if not args.yes:
                    # Destructive and irreversible; make the human type it.
                    reply = input(f'Type the niche name to confirm ({name}): ').strip()
                    if reply != name:
                        print("Cancelled.")
                        raise SystemExit(1)
            removed = delete_niche(name, purge_data=args.purge)
            if args.purge:
                print(f"Removed {name!r} and purged {removed.get('authors', 0)} authors.")
            else:
                print(f"Removed {name!r}. Its collected data is still in the database.")
                print("       Re-adding the same name picks that data back up.")

        elif args.import_config:
            seed = load_niches_yaml()
            if not seed:
                raise NicheError("config/niches.yaml defines no niches to import.")
            for name, keywords in seed.items():
                existing = get_niche(name)
                save_niche(name, keywords, active=existing["active"] if existing else True)
                print(f"  {'updated' if existing else 'added  '} {name}")
            print(f"\nImported {len(seed)} niche(s) from config/niches.yaml.")
            print("Niches you added elsewhere were left alone.")

    except NicheError as err:
        # These messages are written for a human; no traceback.
        print(f"Error: {err}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
