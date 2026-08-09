"""LLM wrapper — the only module that talks to a generation provider.

Every LLM call in the system goes through summarize_kb() or generate_post().
Nothing else imports a provider SDK. That containment is what makes the provider
switchable at all.

Two providers, selected by LLM_PROVIDER:

  gemini  (default)  Google Gemini free tier. Zero setup beyond an API key.

  hf                 Hugging Face Inference Providers. The token IS the billing
                     identity: every call authenticated with a user's hf_... token
                     spends THEIR credits and bills THEIR account. That is what
                     lets one deployment serve several people, each paying for
                     their own generations, and it opens up open-weight models
                     (Llama, Qwen, DeepSeek, gpt-oss) instead of one vendor's
                     free tier.

Adding a third provider means implementing _call_<name>() and _probe_<name>(),
and adding it to the dispatch tables at the bottom of the provider section.
"""

from __future__ import annotations

import functools
import logging
import os
import random
import time
from typing import TYPE_CHECKING, Sequence

import requests

from .db import require_env

if TYPE_CHECKING:
    from .sources import FetchedSource

log = logging.getLogger(__name__)

PROVIDER_GEMINI = "gemini"
PROVIDER_HF = "hf"
VALID_PROVIDERS = (PROVIDER_GEMINI, PROVIDER_HF)

# --- Gemini ---------------------------------------------------------------
#
# Two properties matter here, both learned the hard way against a real free-tier
# key:
#
# 1. NOT a thinking model. Newer Gemini flash/pro models spend output tokens on
#    internal reasoning before emitting text. At our budgets (300 tokens for KB
#    triage) that reasoning eats the whole allowance and the response comes back
#    finish_reason=MAX_TOKENS with truncated or absent text. The -lite models do
#    not think and finish cleanly with STOP.
#
# 2. An alias, not a pinned version. This project was first written against
#    `gemini-2.0-flash`; by the time it ran, that model returned HTTP 429 (no free
#    quota) and `gemini-2.5-flash` returned 404 ("no longer available to new
#    users"). Pinned model names rot out of the free tier. The alias tracks
#    whatever the current free lite model is.
#
# The tradeoff of an alias is that Google can change what it points at underneath
# you. That is the better failure mode: a behaviour shift you can see in the
# output beats a hard 429 that silently stops the KB updating.
#
# Override per-environment with GEMINI_MODEL. To check what your key can actually
# reach:  python -m src.llm --probe
DEFAULT_GEMINI_MODEL = "gemini-flash-lite-latest"

# Back-compat: this name was the Gemini model before there were two providers.
DEFAULT_MODEL = DEFAULT_GEMINI_MODEL

# --- Hugging Face ---------------------------------------------------------
#
# OpenAI-compatible router in front of a dozen serving providers (Together, Groq,
# Fireworks, Cerebras, Novita, ...). Plain HTTP with `requests` rather than a new
# SDK dependency: the payload is ordinary JSON and we already ship requests.
HF_ROUTER_URL = "https://router.huggingface.co/v1"

# Chosen by measuring candidates against this project's ACTUAL prompts and token
# budgets, not by reputation:
#
#   * Not a thinking model. Qwen3-8B — which is NOT labelled "Thinking" — burned
#     all 300 tokens of a KB-triage budget and returned EMPTY text, because Qwen3
#     has hybrid thinking enabled by default. The name does not warn you. The
#     `-Instruct-2507` releases have it off and finish cleanly.
#   * Precise. Given an Instagram-only article, Llama-3.3-70B tagged it
#     "Instagram, Facebook, Twitter, TikTok" — exactly the over-tagging that makes
#     KB retrieval inject irrelevant platform advice. The Qwen Instruct models
#     tagged it "Instagram".
#   * Obedient. Asked for bare post text, DeepSeek-V3 and Llama-3.3 both wrapped
#     the output in quotation marks anyway. Qwen3 did not.
#   * Cheap. A 80B MoE with ~3B active parameters, so it prices and runs like a
#     small model.
#
# Override with HF_MODEL; `python -m src.llm --probe` reports what your token can
# actually reach and which models finish cleanly at our budgets.
DEFAULT_HF_MODEL = "Qwen/Qwen3-Next-80B-A3B-Instruct"

# Config fingerprint for pooled telemetry and A/B analysis. Bump whenever the
# generation prompt or platform norms change materially, so pooled outcomes can be
# segmented by which prompt produced them. This is the whole reason a prompt tweak
# is measurable rather than a shot in the dark.
PROMPT_VERSION = "v1"

# The sentinel summarize_kb() returns for items that carry no actionable change.
# Callers must treat this as "drop the item", not as summary text.
SKIP = "SKIP"

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 4.0

HF_TIMEOUT_SECONDS = 120

# Feed items can be enormous (some blogs put the full post in the RSS body).
# Truncate before spending tokens: platform announcements front-load the news,
# and the free tier's throughput is the real constraint.
MAX_KB_INPUT_CHARS = 6000


class LLMError(Exception):
    """Raised when the provider fails after retries, or returns nothing usable."""


def provider() -> str:
    """Which LLM provider is configured. Defaults to gemini."""
    name = (os.environ.get("LLM_PROVIDER") or PROVIDER_GEMINI).strip().lower()
    if name not in VALID_PROVIDERS:
        raise LLMError(
            f"LLM_PROVIDER={name!r} is not valid. Use one of: {', '.join(VALID_PROVIDERS)}."
        )
    return name


def model_name() -> str:
    """The model id in use, for logs and for telemetry's config fingerprint."""
    if provider() == PROVIDER_HF:
        return os.environ.get("HF_MODEL") or DEFAULT_HF_MODEL
    return os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def hf_token() -> str:
    """The HF token to bill generations against.

    Falls back to the `hf auth login` cache so a user who is already logged in
    does not have to copy their token into .env. HF_TOKEN wins if set, matching
    huggingface_hub's own precedence.
    """
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    from pathlib import Path

    cached = Path.home() / ".cache" / "huggingface" / "token"
    if cached.exists() and cached.read_text().strip():
        return cached.read_text().strip()
    raise LLMError(
        "LLM_PROVIDER=hf needs a Hugging Face token. Set HF_TOKEN in .env, or run "
        "`hf auth login`. The token is the billing identity — generations are "
        "charged to whoever it belongs to."
    )


@functools.lru_cache(maxsize=1)
def _model():
    """Configure and cache the Gemini client."""
    import google.generativeai as genai

    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    name = model_name()
    log.info("Using LLM %s", name)
    return genai.GenerativeModel(name)


def _thinking_model_hint(env_var: str, suggestion: str) -> str:
    """The same diagnosis both providers need, worded for whichever is in play.

    Every provider eventually ships reasoning models that spend the output budget
    before emitting a token, and every one of them reports it as an ordinary
    length stop. Measured here: Gemini's flash/pro models and Qwen3-8B (whose name
    does not say "thinking" — Qwen3 enables it by default) both return an empty
    response at a 300-token budget.
    """
    return (
        f"Model produced no text and stopped at the token limit. This usually means "
        f"{model_name()!r} is a thinking model that spent the whole budget on "
        f"internal reasoning. Set {env_var} to {suggestion}, or run "
        f"`python -m src.llm --probe` to see which models finish cleanly."
    )


def _extract_text(response) -> str:
    """Pull text out of a Gemini response, with errors that say what happened.

    `response.text` is a convenience accessor that raises an opaque ValueError
    whenever there is no text part — which happens for safety blocks, for empty
    candidate lists, and (most confusingly) when a thinking model spends its
    entire token budget reasoning. Read the candidate directly so the caller gets
    a diagnosis instead of a stack trace.
    """
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        raise LLMError(f"Model returned no candidates (prompt_feedback={feedback})")

    candidate = candidates[0]
    reason = getattr(candidate, "finish_reason", None)
    reason_name = getattr(reason, "name", str(reason))

    parts = getattr(getattr(candidate, "content", None), "parts", None) or []
    text = "".join(getattr(p, "text", "") or "" for p in parts).strip()

    if text:
        if reason_name == "MAX_TOKENS":
            # Truncated mid-sentence. Usable for KB bullets, bad for a post.
            log.warning(
                "Model hit max_output_tokens; output is truncated. If this is "
                "frequent, GEMINI_MODEL may be a thinking model — prefer a -lite "
                "alias, or raise the token budget."
            )
        return text

    if reason_name == "MAX_TOKENS":
        raise LLMError(
            _thinking_model_hint("GEMINI_MODEL", "a -lite alias (e.g. gemini-flash-lite-latest)")
        )
    if reason_name in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
        raise LLMError(f"Model blocked the response (finish_reason={reason_name})")
    raise LLMError(f"Model returned no text (finish_reason={reason_name})")


def _call_hf(prompt: str, temperature: float, max_output_tokens: int) -> str:
    """One completion via the HF router. Raises LLMError for permanent failures.

    Transport only — retry lives in _call(), shared with Gemini.
    """
    response = requests.post(
        f"{HF_ROUTER_URL}/chat/completions",
        headers={"Authorization": f"Bearer {hf_token()}"},
        json={
            "model": model_name(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        },
        timeout=HF_TIMEOUT_SECONDS,
    )

    if response.status_code == 401:
        raise LLMError(
            "Hugging Face rejected the token. It needs the 'Make calls to Inference "
            "Providers' permission — a fine-grained token without it authenticates "
            "fine everywhere else and still fails here."
        )
    if response.status_code == 402:
        raise LLMError(
            "Hugging Face says this account is out of inference credits. Enable "
            "pay-as-you-go, or point HF_MODEL at a cheaper model."
        )
    if response.status_code == 404:
        raise LLMError(
            f"No provider currently serves {model_name()!r}. Model availability on "
            f"the router changes; run `python -m src.llm --probe` for what works now."
        )
    if response.status_code >= 400:
        # Let _call() decide whether this is worth retrying.
        raise requests.HTTPError(
            f"HTTP {response.status_code}: {response.text[:200]}", response=response
        )

    choice = (response.json().get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    finish = choice.get("finish_reason")

    if text:
        if finish == "length":
            log.warning(
                "Model hit max_tokens; output is truncated. If frequent, HF_MODEL may "
                "be a thinking model — prefer an -Instruct variant."
            )
        return text

    if finish == "length":
        raise LLMError(
            _thinking_model_hint("HF_MODEL", "an -Instruct variant (e.g. " + DEFAULT_HF_MODEL + ")")
        )
    if finish == "content_filter":
        raise LLMError("The provider blocked the response (content filter)")
    raise LLMError(f"Model returned no text (finish_reason={finish})")


def _call_gemini(prompt: str, temperature: float, max_output_tokens: int) -> str:
    """One completion via Gemini. Transport only; retry lives in _call()."""
    import google.generativeai as genai

    response = _model().generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        ),
    )
    return _extract_text(response)


def _call(prompt: str, temperature: float, max_output_tokens: int) -> str:
    """Single completion with retry on rate limit / transient server errors.

    Provider-agnostic: dispatches to the configured backend and applies the same
    backoff to both. Free-tier 429s are common and expected under a burst; back
    off generously rather than failing the job.
    """
    fn = _call_hf if provider() == PROVIDER_HF else _call_gemini

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn(prompt, temperature, max_output_tokens)
        except LLMError:
            # Our own diagnosis (safety block, no text, thinking-model budget, bad
            # token, no credits). Retrying cannot change any of these.
            raise
        except Exception as err:  # noqa: BLE001 — providers raise a wide range
            last_err = err
            message = str(err).lower()
            if "429" in message or "quota" in message:
                # Distinguish "slow down" from "this model has no free quota".
                # The latter retries four times and still fails, which reads as a
                # transient blip in the logs when it is actually a config problem.
                log.warning(
                    "Quota/rate error from %s. If every call fails this way, the "
                    "model likely has no free-tier quota — run "
                    "`python -m src.llm --probe` to see what your key can reach.",
                    model_name(),
                )
            retryable = any(
                token in message
                for token in ("429", "rate", "quota", "500", "503", "timeout", "unavailable")
            )
            if not retryable or attempt == MAX_RETRIES - 1:
                break
            delay = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1)
            log.warning("LLM call failed (%s); retrying in %.1fs", str(err)[:80], delay)
            time.sleep(delay)

    raise LLMError(f"LLM call failed after {MAX_RETRIES} attempts: {last_err}") from last_err


# ---------------------------------------------------------------------------
# Knowledge-base summarisation
# ---------------------------------------------------------------------------

# Platforms the KB can tag against. "general" means the advice is mechanism-free
# enough to apply anywhere (hooks, formatting, cadence).
KNOWN_PLATFORMS = ("bluesky", "x", "linkedin", "instagram", "tiktok", "facebook", "general")

_KB_PROMPT = """\
You are triaging blog posts for a knowledge base that helps people write better \
social media posts. The knowledge base must contain ONLY concrete, actionable \
facts about how a platform or its algorithm actually works.

Read the article below and decide:

Respond with exactly the single word SKIP if the article is any of:
- speculation, prediction, or opinion about what a platform *might* do
- general marketing advice with no platform-specific mechanism
- company news, funding, hiring, personnel, or partnership announcements
- a product roundup, listicle, or tool comparison
- an article that only restates that a change happened, without saying what it is

Otherwise respond in EXACTLY this format:

PLATFORMS: <comma-separated list>
* <bullet>
* <bullet>

For PLATFORMS, list only the platforms the facts ACTUALLY apply to, chosen from:
{platforms}
Judge this from the content, not from who published it. An article about TikTok \
published by a general marketing blog is tiktok, NOT every platform. Use "general" \
only if the advice has no platform-specific mechanism and would hold true anywhere.

Then give 1-3 short bullet points stating ONLY what changed and what a poster \
should do differently as a result. Requirements:
- Each bullet must be a fact from the article, not your inference.
- No preamble, no title, no closing summary. Bullets only.
- If the article gives specifics (limits, dates, ranking factors, formats), keep them.
- Maximum 60 words total across the bullets.

Article source: {source}
Article title: {title}

Article body:
{body}
"""


def _parse_kb_response(raw: str, fallback_tags: Sequence[str]) -> tuple[str, list[str]]:
    """Split a PLATFORMS: header off the bullets. Returns (summary, tags).

    Falls back to the source's configured tags if the model omits or mangles the
    header — a usable summary with coarse tags beats dropping the article.
    """
    lines = raw.strip().splitlines()
    tags: list[str] = []
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip().strip("*").strip()
        if not stripped:
            continue
        if stripped.upper().startswith("PLATFORMS:"):
            _, _, value = stripped.partition(":")
            tags = [
                t.strip().lower()
                for t in value.split(",")
                if t.strip().lower() in KNOWN_PLATFORMS
            ]
            body_start = i + 1
        break

    summary = "\n".join(lines[body_start:]).strip()
    if not summary:
        # Header present but no bullets — treat the whole thing as the summary
        # rather than storing an empty row.
        summary = raw.strip()
    if not tags:
        tags = [t for t in fallback_tags]
    # Dedupe, preserve order.
    return summary, list(dict.fromkeys(tags))


def summarize_kb(
    title: str,
    body: str,
    source: str = "",
    fallback_tags: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """Extract actionable platform changes and the platforms they apply to.

    Returns (SKIP, []) when the item carries no actionable change; ingest_kb.py
    drops those without writing a row. Otherwise returns (summary, tags).

    Tags come from the article's content rather than its source feed. This matters
    for retrieval precision: source-level tagging put a TikTok-emoji article under
    `bluesky` (because the publishing blog covers every platform) while an article
    entirely about Bluesky missed the `bluesky` tag altogether. Since platform_tags
    is the filter generation.py retrieves on, coarse tags mean TikTok trivia gets
    injected into Bluesky posts as "current platform guidance".

    Deliberately low temperature: this is extraction, not writing. Any creativity
    here becomes a hallucinated "platform change" that later steers generation.
    """
    body = (body or "").strip()
    if not body and not title:
        return SKIP, []

    prompt = _KB_PROMPT.format(
        platforms=", ".join(KNOWN_PLATFORMS),
        source=source or "unknown",
        title=title or "(untitled)",
        body=body[:MAX_KB_INPUT_CHARS],
    )
    result = _call(prompt, temperature=0.1, max_output_tokens=300)

    # The model sometimes wraps the sentinel ("SKIP.", "**SKIP**"). Normalise
    # before comparing, or junk rows leak into the KB.
    normalised = result.strip().strip("*.").strip().upper()
    if normalised == SKIP or normalised.startswith("SKIP"):
        return SKIP, []

    return _parse_kb_response(result, fallback_tags)


# ---------------------------------------------------------------------------
# Post generation
# ---------------------------------------------------------------------------

_GENERATION_PROMPT = """\
You write social media posts for the "{niche}" community on {platform}.

{kb_section}

{exemplar_section}

The exemplars above are real posts that performed well with this audience. Use \
them ONLY to infer tone, structure, rhythm, and length. You must NOT:
- reuse any distinctive phrase, sentence, or opening line from them
- reference their specific products, names, numbers, or events
- imitate any one of them closely enough that its author would recognise it

Treat them as evidence of what this audience responds to, not as text to remix.

Platform norms for {platform}:
{platform_norms}

Do not invent facts. Specifically, never write a URL, link, handle, statistic, \
price, date, or version number that was not given to you in the request or in the \
source material — not even a placeholder like "example.com" or "[link]". If the post \
would benefit from a link the author has not supplied, simply end without one.
{source_section}
Write ONE post about the following. Output only the post text — no preamble, no \
options, no hashtag suggestions unless they belong in the post, no quotation \
marks around the whole thing.

The request: {user_input}
{avoid_section}"""

# Rendered only when the caller is asking for another attempt at a request it has
# already generated for.
#
# This exists because temperature alone does not do the job. With an identical
# prompt, this model reproduces its own opening sentence almost verbatim across
# attempts — measured at temperature 0.9, four regenerations of one request all
# began "I used to stress about posting at optimal times." They differed in
# length and in the back half, so nothing looked broken from the API's side, but
# a person clicking "try again" was handed the same post in different words.
#
# Two things were needed to actually shift it, and the first attempt at this had
# only the second:
#
#  1. Position. This renders LAST, after the request. An earlier version sat
#     above "Write ONE post about the following" and changed nothing — the model
#     followed the instruction nearest the end and reproduced its opening anyway.
#  2. Naming the banned words outright. "Write something different" is too vague
#     to overcome the pull of the exemplars; quoting the exact opening it must
#     not use is not.
_AVOID_TEMPLATE = """
IMPORTANT — this is a retry. You have already written the following for this
exact request and the author rejected it. They want a different post, not a
reworded one:

{previous}

Hard constraints for this attempt:
- Your first sentence must NOT begin with any of these openings: {banned}
- Do not reuse the sentence pattern of any opening above, even with different
  words. If a previous attempt opened by recalling a past habit, do not open by
  recalling a past habit.
- Change the way in: if the last attempt was a personal anecdote, try a direct
  claim, a question, a concrete detail, or an observation about the reader.
- Do not merely shorten, lengthen, or paraphrase what is above.
- Every other instruction in this brief still applies.
"""

# How much of a previous opening to quote back as banned. Long enough to pin the
# actual phrasing, short enough that the model is not just told to avoid one long
# string it was never going to reproduce exactly.
_BANNED_OPENING_WORDS = 8

# Only rendered when the author supplied a link. The rules are explicit because
# this section is the one place facts may legitimately come from: without saying
# so, the anti-invention rule above reads as forbidding the very details that
# make a link-grounded post worth posting.
_SOURCE_TEMPLATE = """
Source material — the author gave you this link and its contents to write about:

URL: {url}
{title_line}\
---
{text}
---
{truncation_note}
Rules for the source material:
- Facts, names, numbers and dates that appear above ARE given to you; using them \
is correct and expected.
- The URL above is the only link you may include, and only if it belongs in the post.
- Write the author's own post about this. Do not quote the source at length, and do \
not copy its headline as your opening line.
- If the request below asks for a specific angle, that angle wins over the source's \
own framing.
"""

_PLATFORM_NORMS = {
    "bluesky": (
        "- Hard limit 300 characters. Aim well under it; short posts outperform.\n"
        "- Conversational and low-polish. Marketing voice is actively disliked.\n"
        "- Hashtags are used sparingly, 0-2 at most, and are not required for reach.\n"
        "- No algorithmic penalty for links, unlike some other platforms."
    ),
    "x": (
        "- 280 characters for standard accounts.\n"
        "- Hook in the first line; it is what shows in the timeline.\n"
        "- Links can suppress reach; consider putting them in a reply.\n"
        "- 1-2 hashtags maximum."
    ),
    "linkedin": (
        "- First 2 lines show before the 'see more' fold. Earn the click.\n"
        "- Short paragraphs with line breaks. Walls of text die.\n"
        "- Professional but personal; first-person stories outperform announcements.\n"
        "- 3-5 hashtags at the end is the norm."
    ),
}

DEFAULT_PLATFORM_NORMS = (
    "- Keep it concise and lead with the most interesting idea.\n"
    "- Write like a person, not a brand."
)


def platform_norms(platform: str) -> str:
    return _PLATFORM_NORMS.get(platform.lower(), DEFAULT_PLATFORM_NORMS)


def generate_post(
    user_input: str,
    niche: str,
    platform: str,
    exemplar_texts: Sequence[str],
    kb_summaries: Sequence[str],
    source: FetchedSource | None = None,
    avoid_texts: Sequence[str] = (),
) -> str:
    """Generate one post grounded in exemplars (style) and KB entries (facts).

    `source`, when given, is a page the author asked to write about (see
    src/sources.py). It is the only sanctioned origin for facts the request did
    not itself contain.

    `avoid_texts` are posts already generated for this same request — pass them
    when the author is asking for another attempt. Temperature alone does not
    produce a different post here (see _AVOID_TEMPLATE for the measurement); the
    model has to be told what it already wrote.

    Temperature is high-ish: this is the one creative call in the system, and the
    anti-copying instruction needs room to actually diverge from the exemplars.
    """
    if not user_input.strip():
        raise ValueError("generate_post() needs a non-empty user_input")

    if kb_summaries:
        kb_section = (
            "Current platform guidance (recent, verified changes — follow these):\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(kb_summaries, 1))
        )
    else:
        kb_section = "No platform-specific guidance is available; use general good practice."

    if exemplar_texts:
        joined = "\n\n".join(
            f"--- Exemplar {i} ---\n{t}" for i, t in enumerate(exemplar_texts, 1)
        )
        exemplar_section = (
            f"High-performing recent posts from the {niche} community:\n\n{joined}"
        )
    else:
        exemplar_section = (
            "No exemplars are available for this niche yet; rely on the platform "
            "norms below."
        )

    if source is not None:
        source_section = _SOURCE_TEMPLATE.format(
            url=source.url,
            title_line=f"Title: {source.title}\n" if source.title else "",
            text=source.text,
            truncation_note=(
                "(The source was longer than this; you are seeing the beginning.)\n"
                if source.truncated
                else ""
            ),
        )
    else:
        source_section = ""

    kept = [t.strip() for t in avoid_texts if t and t.strip()]
    if kept:
        banned = []
        for text in kept:
            words = text.split()[:_BANNED_OPENING_WORDS]
            if words:
                opening = " ".join(words)
                if opening not in banned:
                    banned.append(opening)
        avoid_section = _AVOID_TEMPLATE.format(
            previous="\n\n".join(
                f"--- Attempt {i} ---\n{t}" for i, t in enumerate(kept, 1)
            ),
            banned="; ".join(f'"{b}…"' for b in banned) or "(none)",
        )
    else:
        avoid_section = ""

    prompt = _GENERATION_PROMPT.format(
        niche=niche,
        platform=platform,
        kb_section=kb_section,
        exemplar_section=exemplar_section,
        platform_norms=platform_norms(platform),
        source_section=source_section,
        avoid_section=avoid_section,
        user_input=user_input.strip(),
    )
    return _call(prompt, temperature=0.9, max_output_tokens=600)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def probe() -> None:
    """Report which models this key/token can actually generate with.

        python -m src.llm --probe

    Not optional polish — this is the survival kit, and it has already earned its
    keep twice. A credential that lists models happily can still fail every
    generate call, because listing and generating are separately gated. And model
    availability churns underneath you: `gemini-2.0-flash` went to 429,
    `gemini-2.5-flash` went to 404 "no longer available to new users", and on HF
    a model that is not labelled "thinking" can still eat the whole budget.
    """
    if provider() == PROVIDER_HF:
        _probe_hf()
    else:
        _probe_gemini()


def _probe_hf() -> None:
    """Probe HF router models with a real, budget-sized call."""
    token = hf_token()
    headers = {"Authorization": f"Bearer {token}"}

    print(f"Provider: hf\nConfigured HF_MODEL: {model_name()}\n")

    try:
        resp = requests.get(f"{HF_ROUTER_URL}/models", headers=headers, timeout=60)
        resp.raise_for_status()
        listed = [m.get("id", "") for m in resp.json().get("data", [])]
    except Exception as err:  # noqa: BLE001
        print(f"Could not list models — the token may be invalid: {err}")
        raise SystemExit(1) from err

    print(f"{len(listed)} models on the router.\n")

    # Probing all 121 would spend real money and take minutes. Probe the
    # configured model plus a shortlist of text models this project would
    # plausibly use.
    shortlist = [
        DEFAULT_HF_MODEL,
        "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "Qwen/Qwen3-4B-Instruct-2507",
        "meta-llama/Llama-3.3-70B-Instruct",
        "deepseek-ai/DeepSeek-V3-0324",
    ]
    targets = [model_name()] + [m for m in shortlist if m != model_name()]
    targets = [m for m in targets if m in listed or m == model_name()]

    print("Probing with a real KB-triage-sized call (300 tokens) — the budget that")
    print("exposes thinking models. WORKS = finishes cleanly AND returns text.\n")

    working: list[str] = []
    for name in targets:
        try:
            r = requests.post(
                f"{HF_ROUTER_URL}/chat/completions",
                headers=headers,
                json={
                    "model": name,
                    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                    "max_tokens": 300,
                    "temperature": 0.0,
                },
                timeout=HF_TIMEOUT_SECONDS,
            )
            if r.status_code != 200:
                print(f"  no     {name:38} HTTP {r.status_code} {r.text[:44]}")
                continue
            choice = (r.json().get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            finish = choice.get("finish_reason")
            if not text and finish == "length":
                print(f"  no     {name:38} thinking model (burned 300 tok, no text)")
                continue
            if not text:
                print(f"  no     {name:38} empty (finish={finish})")
                continue
            print(f"  WORKS  {name:38} -> {text[:18]!r}")
            working.append(name)
        except Exception as err:  # noqa: BLE001
            print(f"  no     {name:38} {str(err)[:44]}")

    print()
    if working:
        print(f"Usable: {', '.join(working)}")
        if model_name() not in working:
            print(
                f"\nWARNING: your configured model {model_name()!r} is NOT usable. "
                f"Set HF_MODEL to one of the above."
            )
    else:
        print("No model worked. Check the token has inference permission and credits.")
        raise SystemExit(1)


def _probe_gemini() -> None:
    import google.generativeai as genai

    genai.configure(api_key=require_env("GEMINI_API_KEY"))

    print(f"Provider: gemini\nConfigured GEMINI_MODEL: {model_name()}\n")
    print("Probing each model that supports generateContent (1 cheap call each).")
    print("WORKS = has free quota and returns clean text.\n")

    try:
        available = [
            m.name.removeprefix("models/")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ]
    except Exception as err:  # noqa: BLE001
        print(f"Could not list models — the key itself may be invalid: {err}")
        raise SystemExit(1) from err

    # Skip the obviously irrelevant: image/tts/embedding variants.
    skip = ("image", "tts", "embedding", "aqa", "gemma", "nano-banana")
    working: list[str] = []

    for name in available:
        if any(s in name for s in skip):
            continue
        try:
            model = genai.GenerativeModel(name)
            response = model.generate_content(
                "Reply with exactly: OK",
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=200, temperature=0.0
                ),
            )
            text = _extract_text(response)
            print(f"  WORKS  {name:34} -> {text[:20]!r}")
            working.append(name)
        except LLMError as err:
            print(f"  no     {name:34} -> {str(err)[:70]}")
        except Exception as err:  # noqa: BLE001
            first = str(err).replace("\n", " ")[:70]
            print(f"  no     {name:34} -> {first}")

    print()
    if working:
        print(f"Usable: {', '.join(working)}")
        if model_name() not in working:
            print(
                f"\nWARNING: your configured model {model_name()!r} is NOT usable. "
                f"Set GEMINI_MODEL to one of the above."
            )
    else:
        print("No model worked. The key has no free-tier quota, or billing is required.")
        raise SystemExit(1)


def main() -> None:
    import argparse

    from .db import configure_logging

    parser = argparse.ArgumentParser(description="LLM provider diagnostics.")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Report which models this GEMINI_API_KEY can actually generate with.",
    )
    args = parser.parse_args()
    configure_logging()

    if args.probe:
        probe()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
