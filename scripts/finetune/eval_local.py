#!/usr/bin/env python
"""Evaluate a LoRA adapter in place, on the training box.

Why not evaluate the merged model: merging writes ~15GB and uploading it runs at
a couple of MB/s, so comparing two candidates that way costs hours and fills the
disk. The adapter is ~150MB and the base model is already cached, so loading
base+adapter answers the same question in minutes. Merge only the winner.

The suites are copied verbatim from app/services/finetune/evaluate.py so numbers
are directly comparable with the baselines already recorded there:

    Qwen3-80B (ships today)   capability 91.7%
    Qwen3-4B                  capability 75.0%, over-limit 62.5%

    ADAPTER=/workspace/out/checkpoint-814 python eval_local.py
"""

from __future__ import annotations

import unsloth  # noqa: F401  — must precede transformers/peft

import json
import os
import re
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ADAPTER = os.environ.get("ADAPTER", "").strip()
TEST = Path(os.environ.get("TEST_FILE", "/workspace/test.jsonl"))
N_DIV = int(os.environ.get("N_DIV", 40))
MAX_SEQ = int(os.environ.get("MAX_SEQ", 1024))

_HASHTAG_RE = re.compile(r"#\w+")
_URL_RE = re.compile(r"https?://|www\.\w|\w+\.(?:com|io|ai|org|net|co)\b", re.I)
_HANDLE_RE = re.compile(r"@\w+")
_STAT_RE = re.compile(r"\b\d{2,}(?:[.,]\d+)?\s*(?:%|percent|x|k|m|bn|billion|million)\b", re.I)
_MONEY_RE = re.compile(r"[$£€]\s?\d")
_PREAMBLE_RE = re.compile(r"^\s*(?:here(?:'s| is)|sure[,!]|okay[,!]|option \d|draft|post:|tweet:)", re.I)

CAPABILITY_BRIEFS = [
    "Announce that our documentation site just got a major overhaul.",
    "Share the growth numbers from our launch week and sound pleased.",
    "Tell people where to sign up for the beta.",
    "Thank the maintainer who fixed a nasty bug in our dependency.",
    "Announce a price change and point people at the details.",
    "Celebrate hitting a big user milestone.",
    "Promote a conference talk happening next week.",
    "Share a benchmark result showing our thing is faster.",
    "Recommend a tool you have been using and where to find it.",
    "Announce that we are hiring and say how to apply.",
    "Post about a security patch and tell people to update.",
    "Share a discount code for the holiday sale.",
]

CAP_SYSTEM = (
    "Platform: bluesky (hard limit 300 characters)\n"
    "Target performance: top\n"
    "Media: text-only\n\n"
    "- Hard limit 300 characters. Aim well under it; short posts outperform.\n"
    "- Conversational and low-polish. Marketing voice is actively disliked.\n"
    "- Hashtags are used sparingly, 0-2 at most, and are not required for reach.\n"
    "- Never write a URL, handle, statistic, price, date or version number "
    "that was not given to you — not even a placeholder."
)

# Corpus gaps the tier token should be reproducing (measured on 32,582 posts).
CORPUS = {"chars": 23, "hashtags": 0.42, "punct": 6.6}


def fails(text: str) -> bool:
    t = (text or "").strip()
    return bool(
        not t or len(t) > 300 or _URL_RE.search(t) or _HANDLE_RE.search(t)
        or _STAT_RE.search(t) or _MONEY_RE.search(t) or _PREAMBLE_RE.match(t)
        or (len(t) > 1 and t[0] in "\"'" and t[-1] in "\"'")
    )


def measure(texts: list[str]) -> dict:
    clean = [t.strip() for t in texts if t and t.strip()]
    if not clean:
        return {"n": 0}
    lengths = [len(t) for t in clean]
    tags = [len(_HASHTAG_RE.findall(t)) for t in clean]
    return {
        "n": len(clean),
        "median_chars": round(st.median(lengths), 1),
        "mean_hashtags": round(st.fmean(tags), 2),
        "pct_punct": round(100 * sum(1 for t in clean if re.search(r"[?!]", t)) / len(clean), 1),
        "pct_over_limit": round(100 * sum(1 for L in lengths if L > 300) / len(clean), 1),
    }


def main() -> int:
    if not ADAPTER:
        sys.exit("Set ADAPTER=/path/to/checkpoint-NNN")

    from unsloth import FastLanguageModel

    print(f"[eval] loading {ADAPTER}", flush=True)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=ADAPTER, max_seq_length=MAX_SEQ, load_in_4bit=True,
    )
    FastLanguageModel.for_inference(model)

    def generate(system: str, user: str) -> str:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        ids = tokenizer.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=True, return_tensors="pt"
        ).to("cuda")
        out = model.generate(
            input_ids=ids, max_new_tokens=160, do_sample=True,
            temperature=0.9, top_p=0.95, pad_token_id=tokenizer.eos_token_id,
        )
        return tokenizer.decode(out[0][ids.shape[-1]:], skip_special_tokens=True).strip()

    # --- capability gate ---------------------------------------------------
    print("[eval] capability gate", flush=True)
    texts = [generate(CAP_SYSTEM, b) for b in CAPABILITY_BRIEFS]
    counts: Counter = Counter()
    examples = []
    for brief, t in zip(CAPABILITY_BRIEFS, texts):
        tt = (t or "").strip()
        for label, hit in (
            ("empty", not tt), ("over_limit", len(tt) > 300),
            ("invented_url", bool(_URL_RE.search(tt))),
            ("invented_handle", bool(_HANDLE_RE.search(tt))),
            ("invented_stat", bool(_STAT_RE.search(tt))),
            ("invented_price", bool(_MONEY_RE.search(tt))),
            ("preamble", bool(_PREAMBLE_RE.match(tt))),
            ("wrapped_in_quotes", len(tt) > 1 and tt[0] in "\"'" and tt[-1] in "\"'"),
        ):
            if hit:
                counts[label] += 1
                examples.append({"failure": label, "output": tt[:120]})
    clean = sum(1 for t in texts if not fails(t))
    capability = {
        "cases": len(texts), "clean": clean,
        "pass_rate": round(100 * clean / len(texts), 1),
        "failure_counts": dict(counts), "examples": examples[:6],
        "sample": texts[0][:180],
    }

    # --- divergence --------------------------------------------------------
    print("[eval] divergence (top vs low)", flush=True)
    divergence: dict = {}
    if TEST.exists():
        import random

        recs = [json.loads(l) for l in TEST.open(encoding="utf-8")]
        random.Random(7).shuffle(recs)
        sample = recs[:N_DIV]
        for tier in ("top", "low"):
            outs = []
            for rec in sample:
                sysmsg = rec["messages"][0]["content"]
                sysmsg = re.sub(r"Target performance: \w+", f"Target performance: {tier}", sysmsg)
                sysmsg = re.sub(r"Media: [\w-]+", "Media: text-only", sysmsg)
                outs.append(generate(sysmsg, rec["messages"][1]["content"]))
            divergence[tier] = measure(outs)
        top, low = divergence["top"], divergence["low"]
        deltas = {
            "chars": round(top["median_chars"] - low["median_chars"], 1),
            "hashtags": round(top["mean_hashtags"] - low["mean_hashtags"], 2),
            "punct": round(top["pct_punct"] - low["pct_punct"], 1),
        }
        divergence["deltas"] = deltas
        divergence["corpus_deltas"] = CORPUS
        agree = sum(1 for k in deltas if (deltas[k] > 0) == (CORPUS[k] > 0))
        divergence["directions_matching"] = f"{agree}/3"
    else:
        divergence = {"error": f"{TEST} missing"}

    print(json.dumps(
        {"adapter": ADAPTER, "capability_gate": capability, "divergence": divergence},
        indent=2, ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
