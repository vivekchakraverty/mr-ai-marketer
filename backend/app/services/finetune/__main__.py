"""CLI for the fine-tune corpus pipeline (P0).

    python -m app.services.finetune scan      --target 20000
    python -m app.services.finetune rerank
    python -m app.services.finetune rehydrate --limit 5000
    python -m app.services.finetune report

Run from backend/ so `app` and `vendor` resolve.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import acquire, briefs, evaluate, mastodon_collect, mastodon_import, pairs, pipeline, rehydrate, store, tiers


def _report() -> dict:
    stats = store.label_stats("bluesky")
    with store.connect() as conn:
        by_niche = [
            dict(r)
            for r in conn.execute(
                """
                select niche, status, count(*) n,
                       round(avg(lifetime_engagement_rate), 6) avg_rate,
                       round(avg(relevance), 4) avg_relevance
                  from ft_posts group by niche, status order by niche, status
                """
            )
        ]
        top = [
            dict(r)
            for r in conn.execute(
                """
                select round(lifetime_engagement_rate, 4) rate, likes, reposts,
                       replies, follower_count, niche, substr(text, 1, 70) preview
                  from ft_posts where status = 'labelled'
                 order by lifetime_engagement_rate desc limit 5
                """
            )
        ]
    return {"counts": store.counts(), "labels": stats, "by_niche": by_niche, "top": top}


def main() -> int:
    parser = argparse.ArgumentParser(prog="finetune", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Stage 1: filter the dump into candidates.")
    p_scan.add_argument("--target", type=int, default=20_000)
    p_scan.add_argument("--stride", type=int, default=7)
    p_scan.add_argument("--dataset-dir", default=str(acquire.DEFAULT_DATASET_DIR))

    p_rr = sub.add_parser("rerank", help="Stage 1b: embedding relevance pass.")
    p_rr.add_argument("--min-relevance", type=float, default=0.0)

    p_rh = sub.add_parser("rehydrate", help="Stage 2: attach live engagement.")
    p_rh.add_argument("--limit", type=int, default=5000)
    p_rh.add_argument("--sleep", type=float, default=0.0)

    p_mi = sub.add_parser("mastodon", help="Stage 2b: import our own Mastodon corpus.")
    p_mi.add_argument("--limit", type=int, default=None)

    sub.add_parser("tiers", help="Stage 3: follower floor + quality tiers.")

    sub.add_parser("mastodon-collect", help="Collect Mastodon training data (trends+tags+public).")

    p_b = sub.add_parser("briefs", help="Stage 4: backtranslate briefs via fal.")
    p_b.add_argument("--limit", type=int, default=None)
    p_b.add_argument("--model", default="", help=f"fal model (default {briefs.DEFAULT_MODEL})")
    p_b.add_argument("--workers", type=int, default=briefs.DEFAULT_WORKERS)
    p_b.add_argument("--dry-run", action="store_true", help="One sample brief, no writes.")
    p_b.add_argument("--provider", choices=["fal", "hf"], default="fal",
                     help="hf uses the app's configured LLM provider (spends your HF budget).")

    p_pipe = sub.add_parser("pipeline", help="Resume every remaining stage in order.")
    p_pipe.add_argument("--workers", type=int, default=24)
    p_pipe.add_argument("--skip-collect", action="store_true")

    sub.add_parser("status", help="Where the pipeline currently stands.")

    sub.add_parser("pairs", help="Stage 5: build train/val/test JSONL.")

    p_ev = sub.add_parser("eval", help="P2: capability gate + divergence suite.")
    p_ev.add_argument("--model", default="", help="HF model id for the baseline.")
    p_ev.add_argument("--space", default="", help="Gradio Space id of the fine-tune.")
    p_ev.add_argument("-n", type=int, default=60)

    sub.add_parser("report", help="Label distribution + sanity gate.")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # Social posts are full of emoji and the Windows console defaults to cp1252,
    # which raises UnicodeEncodeError mid-print and loses an otherwise successful
    # run's output. Force UTF-8 rather than requiring PYTHONIOENCODING everywhere.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    from pathlib import Path

    if args.cmd == "scan":
        out = acquire.scan(
            target=args.target,
            dataset_dir=Path(args.dataset_dir),
            stride=args.stride,
        )
    elif args.cmd == "rerank":
        out = acquire.rerank(min_relevance=args.min_relevance)
    elif args.cmd == "rehydrate":
        out = rehydrate.run(limit=args.limit, sleep_seconds=args.sleep)
    elif args.cmd == "mastodon":
        out = mastodon_import.run(limit=args.limit)
    elif args.cmd == "mastodon-collect":
        out = mastodon_collect.run()
    elif args.cmd == "tiers":
        out = tiers.run()
    elif args.cmd == "briefs":
        out = briefs.run(
            limit=args.limit, model=args.model, workers=args.workers,
            dry_run=args.dry_run, provider=args.provider,
        )
    elif args.cmd == "pipeline":
        out = pipeline.run(workers=args.workers, skip_collect=args.skip_collect)
    elif args.cmd == "pairs":
        out = pairs.build()
    elif args.cmd == "eval":
        out = evaluate.run(model=args.model, space=args.space, n=args.n)
    elif args.cmd == "status":
        out = pipeline.status()
    else:
        out = _report()

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
