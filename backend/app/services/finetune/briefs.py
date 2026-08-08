"""Stage 4 — backtranslate a brief for every training post.

We have posts but no instructions. Training on raw post text would be next-token
completion, which degrades instruction-following — and instruction-following is
what the whole pipeline leans on at inference (respect this instance's character
limit, never invent a URL or statistic, use the supplied source material, keep
the disclosure line). So we synthesise the missing half: for each post, ask a
model to write the brief its author would have typed to get it.

LEAK GUARDS ARE THE POINT. A brief that quotes the post, or names a number or
URL that only appears in the post, teaches the model to expect facts it will not
have at inference time — it would learn to hallucinate specifics. Every brief is
therefore checked for n-gram overlap and for concrete tokens, and rejected rather
than stored if it leaks.

Provider is fal (fal-ai/any-llm), called over plain HTTP with `requests` rather
than adding an SDK — same choice vendor/socialpost/src/llm.py makes and for the
same reason: the payload is ordinary JSON and requests already ships.

Cost note: this is one call per post, the most expensive stage in the pipeline.
It runs AFTER tiering so we never buy a brief for a post that failed the follower
floor, and it is resumable — only rows with a null brief are fetched.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import store

log = logging.getLogger(__name__)

FAL_URL = "https://fal.run/fal-ai/any-llm"

# Backtranslation is an easy extraction task, so the cheapest capable instruct
# model is the right call at this volume — a frontier model here would cost
# orders of magnitude more for no measurable gain. Override with FAL_MODEL.
DEFAULT_MODEL = "google/gemini-flash-1.5"

TIMEOUT = 60
MAX_RETRIES = 3
DEFAULT_WORKERS = 8

# Connection-level failures (DNS, refused, reset) get their own, much more
# patient policy. Measured the hard way: a brief local network drop cost 517 rows
# in one burst, because the old path treated a DNS failure like an HTTP 5xx —
# three retries at 2/4/6s, which all fail instantly and are exhausted inside ~12
# seconds. A machine that just lost its network needs tens of seconds, not
# milliseconds, so these back off further and try more times.
CONNECT_RETRIES = 5
CONNECT_BACKOFF_BASE = 4.0
CONNECT_BACKOFF_MAX = 60.0

# Leak thresholds.
NGRAM_N = 5
MAX_BRIEF_CHARS = 220

# A brief shorter than this is a truncated generation, not a terse one. Measured
# on a real run: 43 of 2,454 came back as fragments ("Write a two", "Share an",
# "Prom") because fal can return a partial response. Those are junk training
# targets — they teach the model to expect an instruction that says nothing.
MIN_BRIEF_CHARS = 25

_SYSTEM = (
    "You reverse-engineer social media post briefs. You output one sentence and "
    "nothing else — no preamble, no quotes, no explanation."
)

_PROMPT = """\
Below is a real social media post. Write the one-sentence request its author \
would have typed into a post composer to get it — the intent and desired tone, \
NOT a summary of what it says.

Rules:
- Never quote or reuse distinctive phrases from the post.
- Never mention specific numbers, URLs, @handles, product names or people from it.
- Describe the *kind* of thing to write, not the content itself.
- One sentence, imperative, under 25 words.

Post:
\"\"\"{text}\"\"\"
"""

_WORD_RE = re.compile(r"[a-z0-9']+")
_URL_RE = re.compile(r"https?://|www\.")
_HANDLE_RE = re.compile(r"@\w")
_DIGITS_RE = re.compile(r"\d{3,}")

_lock = threading.Lock()


def fal_key() -> str:
    """The fal credential: environment first, then the pipeline's own env file.

    The file is DATA_DIR/finetune/finetune.env, holding `FAL_KEY=...`. Same shape
    as the app's social-post.env, and it exists because `setx` does not reach an
    already-running process — so a shell export is not always a workable channel.

    This code READS the key; it never writes it. Populate the file yourself.
    """
    key = (os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or "").strip()
    if key:
        return key

    env_file = store.FINETUNE_DIR / "finetune.env"
    if env_file.exists():
        # utf-8-sig, not utf-8: PowerShell 5.1's `Set-Content -Encoding utf8`
        # writes a BOM, which would otherwise end up glued to the first key name
        # (or to the key itself) and silently fail to match.
        text = env_file.read_text(encoding="utf-8-sig")
        loose: list[str] = []

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if sep and name.strip() in ("FAL_KEY", "FAL_API_KEY"):
                value = value.strip().strip("'\"")
                if value:
                    return value
            elif not sep:
                loose.append(line)

        # Tolerate a file holding just the bare key with no NAME= prefix — that
        # is what you get from pasting the value straight into the file, and
        # rejecting it would be pedantry.
        if len(loose) == 1:
            return loose[0].strip().strip("'\"")

    raise RuntimeError(
        f"No fal key found. Either set FAL_KEY in the environment, or put a line "
        f"`FAL_KEY=...` in {env_file}. This code will not write the key for you."
    )


def _ngrams(text: str, n: int = NGRAM_N) -> set[tuple[str, ...]]:
    words = _WORD_RE.findall((text or "").lower())
    return {tuple(words[i : i + n]) for i in range(max(len(words) - n + 1, 0))}


def leak_reason(brief: str, post: str) -> str:
    """Why this brief must be rejected, or '' if it is clean."""
    b = (brief or "").strip()
    if not b:
        return "empty"
    if len(b) < MIN_BRIEF_CHARS:
        return "truncated"
    if len(b) > MAX_BRIEF_CHARS:
        return "too long"
    if _URL_RE.search(b):
        return "contains a URL"
    if _HANDLE_RE.search(b):
        return "contains a handle"
    if _DIGITS_RE.search(b):
        return "contains a long number"
    if _ngrams(b) & _ngrams(post):
        return f"shares a {NGRAM_N}-gram with the post"
    return ""


def _clean(raw: str) -> str:
    """Strip the wrappers models add despite being told not to."""
    text = (raw or "").strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    # Leading "Brief:" / "Request:" labels, and surrounding quotes.
    text = re.sub(r"^(brief|request|prompt)\s*:\s*", "", text, flags=re.I).strip()
    if len(text) > 1 and text[0] in "\"'" and text[-1] in "\"'":
        text = text[1:-1].strip()
    return " ".join(text.split())


def _call_fal(prompt: str, model: str, key: str) -> str:
    """One any-llm completion. Retries transient failures only.

    Two independent retry budgets: `attempt` counts HTTP-level retries, and
    `connect_attempt` counts connection-level ones. They are separate because a
    500 from fal and a DNS failure on this machine are different problems with
    different recovery times, and sharing one budget lets a network blip consume
    the retries an actual server error would have needed.
    """
    last: Exception | None = None
    attempt = 0
    connect_attempt = 0

    while attempt < MAX_RETRIES:
        try:
            resp = requests.post(
                FAL_URL,
                headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
                json={"model": model, "prompt": prompt, "system_prompt": _SYSTEM},
                timeout=TIMEOUT,
            )
        except (requests.ConnectionError, requests.Timeout) as err:
            # The network, not the service. Back off hard and do NOT spend an
            # HTTP retry on it.
            last = err
            connect_attempt += 1
            if connect_attempt >= CONNECT_RETRIES:
                break
            delay = min(
                CONNECT_BACKOFF_BASE * (2 ** (connect_attempt - 1)), CONNECT_BACKOFF_MAX
            )
            # Jitter matters at 24 workers: without it every thread wakes at the
            # same instant and stampedes the moment the network returns.
            time.sleep(delay + random.uniform(0, delay * 0.25))
            continue
        except requests.RequestException as err:
            last = err
            attempt += 1
            time.sleep(2 * attempt)
            continue

        attempt += 1

        if resp.status_code == 401:
            raise RuntimeError("fal rejected FAL_KEY (401). Check the key is current.")
        if resp.status_code == 403:
            raise RuntimeError("fal refused the request (403) — key may lack access.")
        if resp.status_code in (429, 500, 502, 503, 504):
            last = RuntimeError(f"fal HTTP {resp.status_code}")
            time.sleep(2 ** attempt + 1)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"fal HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()

        # any-llm can answer 200 with a PARTIAL generation, which arrives looking
        # like a normal short response. Accepting those is what produced 43
        # truncated briefs on the first run ("Write a two"). Treat as retryable.
        if data.get("partial"):
            last = RuntimeError("fal returned a partial generation")
            time.sleep(1 + attempt)
            continue
        if data.get("error"):
            raise RuntimeError(f"fal error: {str(data['error'])[:160]}")

        # any-llm returns {"output": ...}; be tolerant of shape drift.
        for field in ("output", "text", "response", "completion"):
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                return value
        raise RuntimeError(f"fal returned no text: {str(data)[:160]}")

    raise RuntimeError(
        f"fal failed after {attempt} http attempt(s) and {connect_attempt} "
        f"connection attempt(s): {last}"
    )


# Backtranslation is extraction, not writing, so this deliberately does NOT use
# the app's default generation model (Qwen3-Next-80B). A 4B instruct model does
# the job at a fraction of the cost, and vendor/socialpost/src/llm.py already
# establishes that the `-Instruct-2507` releases have hybrid thinking disabled —
# which matters here, because a thinking model spends the whole token budget
# reasoning and returns empty text at these small budgets.
CHEAP_HF_MODEL = "Qwen/Qwen3-4B-Instruct-2507"


def _call_hf(prompt: str) -> str:
    """Backtranslate via the app's existing LLM provider instead of fal.

    An escape hatch, added when fal locked the account mid-run ("User is locked.
    Reason: Exhausted balance") with 13k briefs outstanding. The vendored wrapper
    is already retry-hardened and already billed to the user's own HF token, so
    this needs no new credential — but it DOES spend their inference budget,
    which is why it is opt-in via --provider rather than an automatic fallback.
    """
    from vendor.socialpost.src import llm

    # The vendored caller takes a single prompt, so the system instruction is
    # folded in rather than passed separately.
    return llm._call(f"{_SYSTEM}\n\n{prompt}", temperature=0.3, max_output_tokens=120)


def _generate(prompt: str, model: str, key: str, provider: str) -> str:
    if provider == "hf":
        return _call_hf(prompt)
    return _call_fal(prompt, model, key)


def _pending(limit: int | None) -> list[dict]:
    """Tiered posts still lacking a clean brief."""
    sql = """
        select uri, text from ft_posts
         where brief is null and quality_tier is not null and status = 'labelled'
         order by platform, niche
    """
    if limit:
        sql += f" limit {int(limit)}"
    with store.connect() as conn:
        return [dict(r) for r in conn.execute(sql)]


def run(
    limit: int | None = None,
    model: str = "",
    workers: int = DEFAULT_WORKERS,
    dry_run: bool = False,
    provider: str = "fal",
) -> dict:
    """Generate briefs for tiered posts. Resumable and safe to re-run."""
    key = "" if provider == "hf" else fal_key()
    if provider == "hf":
        # llm.model_name() resolves HF_MODEL from the environment on every call,
        # so setting it once here points the whole run at the cheap model without
        # touching the app's own generation default. Set before any worker starts;
        # read-only thereafter, so it is safe across threads.
        model = model or os.environ.get("FINETUNE_HF_MODEL") or CHEAP_HF_MODEL
        os.environ["HF_MODEL"] = model
    else:
        model = model or os.environ.get("FAL_MODEL") or DEFAULT_MODEL

    rows = _pending(limit)
    if not rows:
        log.info("no posts awaiting a brief (run tiers first?)")
        return {"pending": 0}

    if dry_run:
        sample = rows[0]
        brief = _clean(_generate(_PROMPT.format(text=sample["text"][:1200]), model, key, provider))
        return {
            "pending": len(rows),
            "provider": provider,
            "model": model,
            "sample_post": sample["text"][:160],
            "sample_brief": brief,
            "leak": leak_reason(brief, sample["text"]) or "clean",
        }

    stats = {"ok": 0, "leaked": 0, "failed": 0}
    started = time.time()
    done = 0

    def work(row: dict) -> tuple[str, str, str]:
        brief = _clean(_generate(_PROMPT.format(text=row["text"][:1200]), model, key, provider))
        return row["uri"], brief, leak_reason(brief, row["text"])

    def flush(buffer: list[tuple[str, str]]) -> None:
        if not buffer:
            return
        with _lock, store.connect() as conn:
            conn.executemany("update ft_posts set brief = ? where uri = ?", buffer)

    # Work in bounded chunks rather than submitting all ~30k futures at once.
    # Two reasons, both learned here: a crash or Ctrl-C mid-run used to discard
    # up to 200 completed calls that had already been paid for, and queueing
    # every future up front pins the whole pending set in memory for no benefit.
    # Each chunk is committed before the next starts, so an interrupted run loses
    # at most one chunk's worth of in-flight calls and resumes from the database.
    chunk_size = max(workers * 10, 50)

    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        buffer: list[tuple[str, str]] = []

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, r): r for r in chunk}
            for future in as_completed(futures):
                done += 1
                try:
                    uri, brief, leak = future.result()
                except Exception as err:  # noqa: BLE001 — one post must not sink the run
                    stats["failed"] += 1
                    if stats["failed"] <= 3:
                        log.warning("brief failed: %s", str(err)[:140])
                    continue

                if leak:
                    # Left null so a later run retries it; the post is not lost.
                    stats["leaked"] += 1
                    continue

                buffer.append((brief, uri))
                stats["ok"] += 1

        flush(buffer)

        rate = done / max(time.time() - started, 1e-9)
        log.info(
            "  %d/%d · ok=%d leaked=%d failed=%d · %.1f/s (committed)",
            done, len(rows), stats["ok"], stats["leaked"], stats["failed"], rate,
        )

    result = {
        "pending": len(rows),
        "provider": provider,
        "model": model,
        "elapsed_seconds": round(time.time() - started, 1),
        **stats,
        "leak_pct": round(100 * stats["leaked"] / max(len(rows), 1), 1),
    }
    store.update_manifest(stage4_briefs=result)
    log.info("briefs complete: %s", result)
    return result
