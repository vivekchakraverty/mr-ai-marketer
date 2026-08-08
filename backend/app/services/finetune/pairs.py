"""Stage 5 — assemble control-tagged training pairs and split them.

Emits chat-format JSONL (system / user / assistant), the shape almost every SFT
trainer accepts.

THE FORMAT MUST MATCH INFERENCE. The control signal lives in the system prompt,
in natural language, alongside the *same* platform-norms text the router will
send at generation time — pulled from the vendored llm.platform_norms() rather
than retyped, so the two cannot drift. A model conditioned on wording it never
sees again has learned nothing transferable.

v1 IS BLUESKY-ONLY. The `Platform:` line is still emitted even though every row
says bluesky: it carries no learning signal today (no contrast), but keeping the
slot means a v2 Mastodon corpus drops in without changing the prompt format or
invalidating this run's weights. The `Target performance:` line DOES carry
signal — top/mid/low are all well represented — and is the dial we turn at
inference by always asking for `top`.

THE SPLIT IS AUTHOR-DISJOINT, and that is not negotiable. Posts by one author are
near-duplicates in voice; a random split would put a person's posts on both sides
and every eval metric would be inflated by memorisation. Where author-disjointness
and the temporal holdout conflict, author wins — leakage invalidates results,
whereas imperfect recency only makes the test slightly easier.
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter, defaultdict

from . import store

log = logging.getLogger(__name__)

TRAIN, VAL, TEST = 0.80, 0.10, 0.10
SEED = 20260731

# Bluesky's hard ceiling. Stated in the system prompt so the limit is part of
# what the model is conditioned on, not just a filter we applied upstream.
CHAR_LIMIT = {"bluesky": 300, "mastodon": 0}  # 0 = per-instance, set at inference


def _norms(platform: str) -> str:
    """Platform guidance, taken from the same source the router uses."""
    try:
        from vendor.socialpost.src import llm

        return llm.platform_norms(platform)
    except Exception:  # noqa: BLE001 — training data build must not need the app configured
        return "- Keep it concise and lead with the most interesting idea.\n- Write like a person, not a brand."


def _system(platform: str, tier: str, has_media: bool) -> str:
    """The conditioning block. Must match what the router sends at inference.

    The `Media:` line deconfounds the quality token, and it is not optional.
    Measured on this corpus: 55.2% of `top` posts carry an image against 23.5% of
    `low` — media presence is the STRONGEST correlate of engagement, and it is
    the one thing a text model cannot reproduce. Without this line, "write a top
    post" would train the model to imitate captions written to accompany a
    picture (shorter, deictic — "look at this") and call that high performance.

    With it, the model learns "high-performing text-only post" as a category of
    its own, and inference always asks for text-only — which is the only thing
    the composer actually produces.
    """
    limit = CHAR_LIMIT.get(platform, 0)
    lines = [f"Platform: {platform}" + (f" (hard limit {limit} characters)" if limit else "")]
    lines.append(f"Target performance: {tier}")
    lines.append(f"Media: {'with-image' if has_media else 'text-only'}")
    lines.append("")
    lines.append(_norms(platform))
    return "\n".join(lines)


def _user(brief: str, niche: str) -> str:
    text = brief.strip()
    if niche and niche != "general":
        text += f"\nNiche: {niche}"
    return text


def _split_by_author(rows: list[dict]) -> dict[str, str]:
    """author_did -> split. Author-disjoint, biased so newer authors land in test.

    Ordering authors by their most recent post and taking the newest for `test`
    satisfies both constraints at once wherever they are compatible: the test set
    is both unseen-author AND later-in-time, which is the honest way to ask
    "does this generalise?" rather than "did it memorise?".
    """
    latest: dict[str, str] = {}
    post_count: dict[str, int] = {}
    for row in rows:
        did = row["author_did"] or ""
        stamp = row["created_at"] or ""
        if stamp > latest.get(did, ""):
            latest[did] = stamp
        post_count[did] = post_count.get(did, 0) + 1

    authors = sorted(latest, key=lambda d: latest[d], reverse=True)  # newest first

    # Quotas are in POSTS, not authors. Splitting on author count alone produced
    # 62/15/22 instead of 80/10/10 on this corpus, because posts-per-author is
    # uneven and the newest authors happen to be the most prolific — which parked
    # ~5k pairs in an oversized test set instead of using them for training.
    total = len(rows)
    quota = {"test": int(total * TEST), "val": int(total * VAL)}
    filled = {"test": 0, "val": 0}

    assignment: dict[str, str] = {}
    for did in authors:
        n = post_count[did]
        # Whole authors only — an author must never straddle two splits.
        if filled["test"] < quota["test"]:
            assignment[did] = "test"
            filled["test"] += n
        elif filled["val"] < quota["val"]:
            assignment[did] = "val"
            filled["val"] += n
        else:
            assignment[did] = "train"
    return assignment


def build(out_dir=None) -> dict:
    """Write train/val/test JSONL from every tiered row that has a brief."""
    out_dir = out_dir or store.FINETUNE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with store.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                select uri, platform, niche, quality_tier, brief, text,
                       author_did, created_at, lifetime_engagement_rate, has_media
                  from ft_posts
                 where brief is not null and quality_tier is not null
                   and status = 'labelled'
                """
            )
        ]

    if not rows:
        return {"pairs": 0, "note": "nothing with both a tier and a brief yet"}

    assignment = _split_by_author(rows)
    random.Random(SEED).shuffle(rows)

    counts: Counter = Counter()
    cells: dict[str, Counter] = defaultdict(Counter)
    media: dict[str, Counter] = defaultdict(Counter)
    handles = {
        name: (out_dir / f"{name}.jsonl").open("w", encoding="utf-8")
        for name in ("train", "val", "test")
    }

    try:
        for row in rows:
            split = assignment.get(row["author_did"] or "", "train")
            record = {
                "messages": [
                    {"role": "system", "content": _system(row["platform"], row["quality_tier"], bool(row["has_media"]))},
                    {"role": "user", "content": _user(row["brief"], row["niche"] or "")},
                    {"role": "assistant", "content": row["text"]},
                ],
                # Carried for analysis, ignored by trainers.
                "meta": {
                    "uri": row["uri"],
                    "platform": row["platform"],
                    "niche": row["niche"],
                    "tier": row["quality_tier"],
                    "has_media": bool(row["has_media"]),
                    "engagement_rate": row["lifetime_engagement_rate"],
                },
            }
            handles[split].write(json.dumps(record, ensure_ascii=False) + "\n")
            counts[split] += 1
            cells[split][row["quality_tier"]] += 1
            media[split][("with-image" if row["has_media"] else "text-only")] += 1
    finally:
        for fh in handles.values():
            fh.close()

    with store.connect() as conn:
        conn.executemany(
            "update ft_posts set split = ? where uri = ?",
            [(assignment.get(r["author_did"] or "", "train"), r["uri"]) for r in rows],
        )

    result = {
        "pairs": len(rows),
        "authors": len(assignment),
        "splits": dict(counts),
        "tier_by_split": {k: dict(v) for k, v in cells.items()},
        "media_by_split": {k: dict(v) for k, v in media.items()},
        "out_dir": str(out_dir),
        "seed": SEED,
    }
    store.update_manifest(stage5_pairs=result)
    log.info("pairs built: %s", result)
    return result
