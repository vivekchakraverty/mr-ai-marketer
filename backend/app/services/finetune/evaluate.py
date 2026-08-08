"""P2 — offline evaluation. Works on any generator, so baseline and fine-tune
are measured by identical code.

Two suites, and they answer different questions:

  DIVERGENCE (S1)  Did the control tokens take? Generate the same briefs under
                   `top` and under `low` and see whether the output actually
                   differs, in the direction the real corpus differs. A model
                   that ignores the token produces two identical distributions,
                   and the whole conditioning design has failed silently.

  CAPABILITY (S2)  Did fine-tuning break instruction-following? This is a GATE,
                   not a score. The pipeline leans on the model obeying rules
                   the training data never demonstrates — respect a character
                   limit, never invent a URL or statistic, emit bare post text.
                   Style gains are worthless if the model starts hallucinating
                   links, so a regression here stops the release regardless of
                   how good the prose looks.

Run the baseline BEFORE training. "Better" is meaningless without the number it
was better than, and after training the stock model is no longer conveniently
at hand in the same configuration.
"""

from __future__ import annotations

import json
import logging
import random
import re
import statistics as st
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import store

log = logging.getLogger(__name__)

# What the real corpus does, measured on 32,582 tiered posts. The divergence
# suite checks whether generations reproduce these gaps — the model has learned
# the token only if its output moves the same way.
CORPUS_REFERENCE = {
    "top": {"median_chars": 165, "mean_hashtags": 0.76, "pct_punct": 33.3},
    "low": {"median_chars": 142, "mean_hashtags": 0.34, "pct_punct": 26.7},
}

_HASHTAG_RE = re.compile(r"#\w+")
_URL_RE = re.compile(r"https?://|www\.\w|\w+\.(?:com|io|ai|org|net|co)\b", re.I)
_HANDLE_RE = re.compile(r"@\w+")
_STAT_RE = re.compile(r"\b\d{2,}(?:[.,]\d+)?\s*(?:%|percent|x|k|m|bn|billion|million)\b", re.I)
_MONEY_RE = re.compile(r"[$£€]\s?\d")
_PREAMBLE_RE = re.compile(
    r"^\s*(?:here(?:'s| is)|sure[,!]|okay[,!]|option \d|draft|post:|tweet:)", re.I
)

# Briefs written to TEMPT invention. Each names something the model does not have
# — a link, a number, a handle — so a model that fabricates will do it here.
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


def _measure(texts: list[str]) -> dict:
    """Mechanical shape of a batch of generations."""
    clean = [t.strip() for t in texts if t and t.strip()]
    if not clean:
        return {"n": 0}
    lengths = [len(t) for t in clean]
    tags = [len(_HASHTAG_RE.findall(t)) for t in clean]
    return {
        "n": len(clean),
        "median_chars": round(st.median(lengths), 1),
        "mean_chars": round(st.fmean(lengths), 1),
        "mean_hashtags": round(st.fmean(tags), 2),
        "pct_punct": round(100 * sum(1 for t in clean if re.search(r"[?!]", t)) / len(clean), 1),
        "pct_over_limit": round(100 * sum(1 for L in lengths if L > 300) / len(clean), 1),
    }


def divergence(generate, n: int = 60, workers: int = 8) -> dict:
    """Same briefs under `top` vs `low`. Does the token change anything?

    Held-out test briefs only — asking a model to reproduce something it trained
    on measures memorisation, not conditioning.
    """
    path = store.FINETUNE_DIR / "test.jsonl"
    if not path.exists():
        return {"error": "no test.jsonl — run `pairs` first"}

    records = [json.loads(line) for line in path.open(encoding="utf-8")]
    random.Random(7).shuffle(records)
    sample = records[:n]

    out: dict = {}
    for tier in ("top", "low"):
        prompts = []
        for rec in sample:
            system = rec["messages"][0]["content"]
            # Swap the tier, hold everything else fixed — the only variable.
            system = re.sub(r"Target performance: \w+", f"Target performance: {tier}", system)
            system = re.sub(r"Media: [\w-]+", "Media: text-only", system)
            prompts.append((system, rec["messages"][1]["content"]))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            texts = list(pool.map(lambda p: _safe(generate, *p), prompts))
        out[tier] = _measure(texts)

    top, low = out.get("top", {}), out.get("low", {})
    if top.get("n") and low.get("n"):
        out["deltas"] = {
            "chars_top_minus_low": round(top["median_chars"] - low["median_chars"], 1),
            "hashtags_top_minus_low": round(top["mean_hashtags"] - low["mean_hashtags"], 2),
            "punct_top_minus_low": round(top["pct_punct"] - low["pct_punct"], 1),
        }
        # The corpus gaps this should be reproducing.
        out["corpus_deltas"] = {
            "chars_top_minus_low": CORPUS_REFERENCE["top"]["median_chars"]
            - CORPUS_REFERENCE["low"]["median_chars"],
            "hashtags_top_minus_low": round(
                CORPUS_REFERENCE["top"]["mean_hashtags"]
                - CORPUS_REFERENCE["low"]["mean_hashtags"], 2
            ),
            "punct_top_minus_low": round(
                CORPUS_REFERENCE["top"]["pct_punct"] - CORPUS_REFERENCE["low"]["pct_punct"], 1
            ),
        }
        # Directionally correct on 2 of 3 is the bar. Requiring all three would
        # fail on noise at n=60; requiring one would pass on it.
        agree = sum(
            1
            for k in out["deltas"]
            if (out["deltas"][k] > 0) == (out["corpus_deltas"][k] > 0)
        )
        out["directions_matching"] = f"{agree}/3"
        out["verdict"] = "LEARNED" if agree >= 2 else "TOKEN IGNORED"
    return out


def capability(generate, workers: int = 6) -> dict:
    """The gate. Any failure here blocks release, however good the prose is."""
    system = (
        "Platform: bluesky (hard limit 300 characters)\n"
        "Target performance: top\n"
        "Media: text-only\n\n"
        "- Hard limit 300 characters. Aim well under it; short posts outperform.\n"
        "- Conversational and low-polish. Marketing voice is actively disliked.\n"
        "- Hashtags are used sparingly, 0-2 at most, and are not required for reach.\n"
        "- Never write a URL, handle, statistic, price, date or version number "
        "that was not given to you — not even a placeholder."
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        texts = list(pool.map(lambda b: _safe(generate, system, b), CAPABILITY_BRIEFS))

    failures: list[dict] = []
    counts: Counter = Counter()
    for brief, text in zip(CAPABILITY_BRIEFS, texts):
        t = (text or "").strip()
        for label, hit in (
            ("empty", not t),
            ("over_limit", len(t) > 300),
            ("invented_url", bool(_URL_RE.search(t))),
            ("invented_handle", bool(_HANDLE_RE.search(t))),
            ("invented_stat", bool(_STAT_RE.search(t))),
            ("invented_price", bool(_MONEY_RE.search(t))),
            ("preamble", bool(_PREAMBLE_RE.match(t))),
            ("wrapped_in_quotes", len(t) > 1 and t[0] in "\"'" and t[-1] in "\"'"),
        ):
            if hit:
                counts[label] += 1
                failures.append({"brief": brief, "failure": label, "output": t[:130]})

    total = len(CAPABILITY_BRIEFS)
    clean = sum(1 for t in texts if t and not _fails(t))
    return {
        "cases": total,
        "clean": clean,
        "pass_rate": round(100 * clean / total, 1),
        "failure_counts": dict(counts),
        "failures": failures[:10],
        "verdict": "PASS" if clean == total else "FAIL — blocks release",
    }


def _fails(text: str) -> bool:
    t = (text or "").strip()
    return bool(
        not t
        or len(t) > 300
        or _URL_RE.search(t)
        or _HANDLE_RE.search(t)
        or _STAT_RE.search(t)
        or _MONEY_RE.search(t)
        or _PREAMBLE_RE.match(t)
        or (len(t) > 1 and t[0] in "\"'" and t[-1] in "\"'")
    )


def _safe(generate, system: str, user: str) -> str:
    try:
        return generate(system, user) or ""
    except Exception as err:  # noqa: BLE001 — one bad call must not sink the suite
        log.warning("generate failed: %s", str(err)[:110])
        return ""


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def hf_generator(model: str = ""):
    """Baseline: the app's own LLM provider, i.e. what ships today."""
    import os

    from vendor.socialpost.src import llm

    if model:
        os.environ["HF_MODEL"] = model

    def generate(system: str, user: str) -> str:
        return llm._call(f"{system}\n\n{user}", temperature=0.9, max_output_tokens=200).strip()

    return generate


def space_generator(space_id: str):
    """The fine-tuned model once it is serving, via the BrandForge Space shape."""
    from gradio_client import Client

    client = Client(space_id)

    def generate(system: str, user: str) -> str:
        return str(client.predict(system, user, api_name="/generate_post")).strip()

    return generate


def run(model: str = "", space: str = "", n: int = 60) -> dict:
    generate = space_generator(space) if space else hf_generator(model)
    label = space or (model or "app default")
    log.info("evaluating: %s", label)
    return {
        "target": label,
        "capability_gate": capability(generate),
        "divergence": divergence(generate, n=n),
    }
