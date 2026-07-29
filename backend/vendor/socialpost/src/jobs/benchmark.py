"""benchmark — does grounded generation actually beat baseline? Manual analysis.

Not scheduled; a maintainer/analyst runs it. Two sources, one analysis:

    python -m src.jobs.benchmark                     # this instance's own DB
    python -m src.jobs.benchmark --source pool       # the pooled HF dataset
    python -m src.jobs.benchmark --out report.md     # also write markdown
    python -m src.jobs.benchmark --source pool --push # commit to the dataset's reports/

The core question is testable because of a natural experiment already in the data:
every generation records how many exemplars it used. n_exemplars = 0 means the draft
fell back to platform norms only — an UNGROUNDED control. n_exemplars > 0 is the
GROUNDED treatment. Comparing their lift-over-baseline directly tests the premise.

Honest about its own limits (printed in every report):
  * Selection bias — users publish the drafts they like, so outcomes are a sample
    of the winners, not all drafts. Fine for comparing groups against each other;
    not for absolute "beats baseline by X%" claims.
  * Cold-start confound — ungrounded generations cluster early in a niche's life,
    so "grounded vs ungrounded" is partly "mature niche vs new niche".
  * Small samples — early on, every number here is directional at best.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from dataclasses import dataclass
from datetime import timezone

from .. import telemetry
from ..db import configure_logging, get_client

log = logging.getLogger(__name__)


@dataclass
class Pair:
    """One generation joined with its outcome. Source-agnostic."""

    niche: str
    n_exemplars: int
    engagement_rate_48h: float | None
    baseline: float | None
    edit_distance_ratio: float | None
    model_id: str | None = None
    prompt_version: str | None = None

    @property
    def lift(self) -> float | None:
        """engagement / baseline. None when baseline is missing or zero."""
        if self.engagement_rate_48h is None or not self.baseline:
            return None
        return self.engagement_rate_48h / self.baseline

    @property
    def grounded(self) -> bool:
        return self.n_exemplars > 0


# ---------------------------------------------------------------------------
# Analysis — pure, so it can be tested on synthetic pairs
# ---------------------------------------------------------------------------


def _stats(values: list[float]) -> dict:
    """mean/median/n for a list, tolerant of emptiness."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "median": None}
    return {
        "n": len(clean),
        "mean": round(statistics.fmean(clean), 4),
        "median": round(statistics.median(clean), 4),
    }


def _group(pairs: list[Pair], key) -> dict[str, dict]:
    """Bucket pairs by a key function, reporting lift + edit stats per bucket."""
    buckets: dict[str, list[Pair]] = {}
    for p in pairs:
        label = key(p)
        if label is None:
            continue
        buckets.setdefault(str(label), []).append(p)
    out = {}
    for label, group in sorted(buckets.items()):
        out[label] = {
            "n": len(group),
            "lift": _stats([p.lift for p in group]),
            "edit_distance": _stats([p.edit_distance_ratio for p in group]),
        }
    return out


def analyze(pairs: list[Pair]) -> dict:
    """Turn paired records into a structured report."""
    with_lift = [p for p in pairs if p.lift is not None]
    lifts = [p.lift for p in with_lift]

    grounded = [p for p in with_lift if p.grounded]
    ungrounded = [p for p in with_lift if not p.grounded]

    edit_ratios = [p.edit_distance_ratio for p in pairs if p.edit_distance_ratio is not None]

    return {
        "totals": {
            "pairs": len(pairs),
            "with_lift": len(with_lift),
            "with_edit_distance": len(edit_ratios),
        },
        "overall_lift": _stats(lifts),
        "pct_beat_baseline": (
            round(100 * sum(1 for lift in lifts if lift > 1) / len(lifts), 1)
            if lifts
            else None
        ),
        # The headline comparison.
        "grounded_vs_ungrounded": {
            "grounded": _stats([p.lift for p in grounded]),
            "ungrounded": _stats([p.lift for p in ungrounded]),
        },
        "by_model": _group(with_lift, lambda p: p.model_id),
        "by_prompt_version": _group(with_lift, lambda p: p.prompt_version),
        "by_niche": _group(with_lift, lambda p: p.niche),
        "edit_distance": {
            **_stats(edit_ratios),
            # Fraction the user published close to verbatim — a strong "the draft
            # was good" signal, and one that carries no selection bias about
            # engagement.
            "pct_near_verbatim": (
                round(100 * sum(1 for e in edit_ratios if e < 0.1) / len(edit_ratios), 1)
                if edit_ratios
                else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_CAVEATS = (
    "Selection bias: outcomes are only for drafts you chose to publish, so they "
    "over-represent the ones you liked. Use these to compare groups, not for "
    "absolute claims.",
    "Cold-start confound: ungrounded drafts (0 exemplars) happen early in a "
    "niche's life, so grounded-vs-ungrounded is partly mature-vs-new niche.",
    "Small samples are directional only. Treat anything under ~30 pairs as a hint.",
)


def _fmt(stat: dict) -> str:
    if stat["n"] == 0:
        return "—"
    return f"mean {stat['mean']}  median {stat['median']}  (n={stat['n']})"


def render(report: dict, source: str) -> str:
    t = report["totals"]
    lines = [
        f"# Benchmark — source: {source}",
        "",
        f"{t['pairs']} generation+outcome pairs "
        f"({t['with_lift']} with a baseline to measure lift against, "
        f"{t['with_edit_distance']} with an edit-distance signal).",
        "",
    ]

    if t["with_lift"] == 0 and t["with_edit_distance"] == 0:
        lines += [
            "No measurable outcomes yet. This needs generations that were published "
            "(their link pasted back) and have since reached a 48h snapshot. Come "
            "back after a few posts have run their course.",
        ]
        return "\n".join(lines)

    lines += [
        "## Does grounding help? (the core test)",
        "",
        f"- **Grounded** (used exemplars): {_fmt(report['grounded_vs_ungrounded']['grounded'])}",
        f"- **Ungrounded** (platform norms only): {_fmt(report['grounded_vs_ungrounded']['ungrounded'])}",
        "",
        "Lift is engagement ÷ the niche baseline at the time. >1 means the post beat "
        "a typical post in its niche.",
        "",
        f"Overall lift: {_fmt(report['overall_lift'])}"
        + (
            f" · {report['pct_beat_baseline']}% of published posts beat baseline"
            if report["pct_beat_baseline"] is not None
            else ""
        ),
        "",
    ]

    def _table(title: str, grouped: dict) -> list[str]:
        if not grouped:
            return []
        rows = [f"## {title}", "", "| segment | lift (mean / median) | n | edit dist (mean) |", "|---|---|---|---|"]
        for label, g in grouped.items():
            lift = g["lift"]
            liftcell = "—" if lift["n"] == 0 else f"{lift['mean']} / {lift['median']}"
            ed = g["edit_distance"]["mean"]
            rows.append(f"| {label} | {liftcell} | {g['n']} | {ed if ed is not None else '—'} |")
        rows.append("")
        return rows

    lines += _table("By model", report["by_model"])
    lines += _table("By prompt version", report["by_prompt_version"])
    lines += _table("By niche", report["by_niche"])

    ed = report["edit_distance"]
    lines += [
        "## How close did you publish to the draft?",
        "",
        f"Edit distance draft→published: {_fmt(ed)}."
        + (
            f" {ed['pct_near_verbatim']}% published near-verbatim (<0.1)."
            if ed.get("pct_near_verbatim") is not None
            else ""
        ),
        "Lower means the draft landed as written — a quality signal free of "
        "engagement selection bias.",
        "",
        "## Read this before trusting the numbers",
        "",
        *[f"- {c}" for c in _CAVEATS],
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_local_pairs() -> list[Pair]:
    """Pairs from THIS instance's database. No HF, no pooling required.

    A generation qualifies when it was published (posted_uri) and that post has a
    48h engagement snapshot. model_id/prompt_version are not stored per-generation
    locally, so the per-model tables are empty for this source — the grounded-vs-
    ungrounded and per-niche cuts, which are the load-bearing ones, are fully
    available because n_exemplars and niche are.
    """
    client = get_client()

    gens = [
        g
        for g in (
            client.table("generations")
            .select("niche, output_text, posted_uri, exemplar_ids")
            .execute()
            .data
            or []
        )
        if g.get("posted_uri")
    ]
    if not gens:
        return []

    baselines = {
        b["scope_key"]: float(b["avg_engagement_rate"])
        for b in (
            client.table("performance_baselines")
            .select("scope_key, avg_engagement_rate")
            .eq("scope", "niche")
            .eq("window_label", "48h")
            .execute()
            .data
            or []
        )
        if b["avg_engagement_rate"] is not None
    }

    pairs: list[Pair] = []
    for g in gens:
        uri = g["posted_uri"]
        snaps = (
            client.table("engagement_snapshots")
            .select("engagement_rate")
            .eq("post_uri", uri)
            .eq("window_label", "48h")
            .limit(1)
            .execute()
            .data
            or []
        )
        if not snaps:
            continue
        post = (
            client.table("posts").select("text").eq("uri", uri).limit(1).execute().data or []
        )
        published = post[0]["text"] if post else ""
        pairs.append(
            Pair(
                niche=g["niche"] or "(none)",
                n_exemplars=len(g.get("exemplar_ids") or []),
                engagement_rate_48h=float(snaps[0]["engagement_rate"])
                if snaps[0]["engagement_rate"] is not None
                else None,
                baseline=baselines.get(g["niche"]),
                edit_distance_ratio=(
                    telemetry.edit_distance_ratio(g.get("output_text") or "", published)
                    if published
                    else None
                ),
            )
        )
    return pairs


def load_pool_pairs(dataset: str, token: str) -> list[Pair]:
    """Pairs from the pooled HF dataset. Maintainer view across all instances.

    Joins generation and outcome records by generation_uid. These records DO carry
    model_id and prompt_version (captured at generation time), so the pooled report
    gets the full per-model / per-prompt breakdown that the local one cannot.
    """
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    files = [
        f
        for f in api.list_repo_files(dataset, repo_type="dataset")
        if f.endswith(".jsonl")
    ]
    generations: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    for fname in files:
        path = api.hf_hub_download(dataset, fname, repo_type="dataset")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                uid = rec.get("generation_uid")
                if rec.get("record_type") == "generation" and uid:
                    generations[uid] = rec  # last write wins on dupes
                elif rec.get("record_type") == "outcome" and uid:
                    outcomes[uid] = rec

    pairs: list[Pair] = []
    for uid, gen in generations.items():
        out = outcomes.get(uid)
        retrieval = gen.get("retrieval") or {}
        pairs.append(
            Pair(
                niche=gen.get("niche") or "(none)",
                n_exemplars=int(retrieval.get("n_exemplars") or 0),
                engagement_rate_48h=(out or {}).get("engagement_rate_48h"),
                baseline=(out or {}).get("baseline_at_measure"),
                edit_distance_ratio=(out or {}).get("edit_distance_ratio"),
                model_id=gen.get("model_id"),
                prompt_version=gen.get("prompt_version"),
            )
        )
    return pairs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _push_report(dataset: str, token: str, markdown: str) -> str:
    """Commit the report to the dataset's reports/ folder. Returns the path."""
    from datetime import datetime

    from huggingface_hub import HfApi

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    path_in_repo = f"reports/benchmark-{stamp}.md"
    HfApi(token=token).upload_file(
        path_or_fileobj=markdown.encode("utf-8"),
        path_in_repo=path_in_repo,
        repo_id=dataset,
        repo_type="dataset",
        commit_message=f"Benchmark report {stamp}",
    )
    return path_in_repo


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="Benchmark grounded generation against baseline.")
    parser.add_argument(
        "--source",
        choices=["local", "pool"],
        default="local",
        help="local = this instance's DB (default); pool = the shared HF dataset.",
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("TELEMETRY_DATASET", ""),
        help="HF dataset id for --source pool (or set TELEMETRY_DATASET).",
    )
    parser.add_argument("--out", help="Also write the report to this markdown file.")
    parser.add_argument(
        "--push",
        action="store_true",
        help="With --source pool: commit the report to the dataset's reports/ folder.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.source == "pool":
        from .. import llm  # for the token resolver

        if not args.dataset:
            raise SystemExit("--source pool needs --dataset or TELEMETRY_DATASET.")
        token = llm.hf_token()
        pairs = load_pool_pairs(args.dataset, token)
    else:
        pairs = load_local_pairs()

    report = analyze(pairs)
    markdown = render(report, source=args.source)
    print(markdown)

    if args.out:
        from pathlib import Path

        Path(args.out).write_text(markdown, encoding="utf-8")
        log.info("Wrote %s", args.out)

    if args.push:
        if args.source != "pool":
            raise SystemExit("--push only makes sense with --source pool.")
        from .. import llm

        where = _push_report(args.dataset, llm.hf_token(), markdown)
        log.info("Pushed report to %s in %s", where, args.dataset)


if __name__ == "__main__":
    main()
