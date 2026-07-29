"""topics — suggest what to post about, per niche. Manual; not scheduled.

    python -m src.jobs.topics                     # every active niche
    python -m src.jobs.topics --niche "indie makers"
    python -m src.jobs.topics --niche "indie makers" --evidence   # show the workings

Suggestions are grounded in two things and nothing else: the recent posts this
system collected for that niche (with the engagement it measured itself), and
live trend signals queried with THAT NICHE'S OWN KEYWORDS. It does not report
what is trending globally — a hot topic the niche does not care about is noise.

Each suggestion cites which evidence made it timely, so a claim you doubt can be
checked against `--evidence` rather than taken on faith.
"""

from __future__ import annotations

import argparse
import logging

from .. import topics
from ..db import configure_logging

log = logging.getLogger(__name__)


def _render(niche: str, report: topics.TopicReport, show_evidence: bool) -> None:
    print(f"\n=== {niche} ===")
    if report.note:
        print(f"  {report.note}")
        return
    if not report.suggestions:
        print("  No suggestions returned.")
        return

    for i, s in enumerate(report.suggestions, 1):
        print(f"\n  {i}. {s.topic}")
        if s.why_now:
            print(f"     why now : {s.why_now}")
        if s.sources:
            print(f"     grounded in: {', '.join(s.sources)}")

    if show_evidence:
        print(f"\n  --- evidence ({report.corpus_posts} recent posts considered) ---")
        for j, c in enumerate(report.clusters, 1):
            print(f"    cluster {j}: {c['size']} posts, best 48h engagement {c['best_engagement']}")
            for sample in c["samples"][:2]:
                print(f"      • {sample[:90]}")
        for name, items in report.overlays.items():
            print(f"    {name}:")
            for item in items:
                print(f"      • {item[:100]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Suggest post topics for your niches, grounded in live evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--niche", help="Just this niche (default: every active one).")
    parser.add_argument(
        "-n", type=int, default=topics.N_SUGGESTIONS, help="Suggestions per niche."
    )
    parser.add_argument(
        "--evidence",
        action="store_true",
        help="Also print the clusters and trend signals behind the suggestions.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.niche:
        try:
            _render(args.niche, topics.suggest_topics(args.niche, n=args.n), args.evidence)
        except ValueError as err:
            raise SystemExit(f"Error: {err}") from None
    else:
        reports = topics.suggest_for_all_niches(n=args.n)
        if not reports:
            raise SystemExit(
                "No active niches. Add one:  python -m src.jobs.niches --add ..."
            )
        for niche, report in reports.items():
            _render(niche, report, args.evidence)
    print()


if __name__ == "__main__":
    main()
