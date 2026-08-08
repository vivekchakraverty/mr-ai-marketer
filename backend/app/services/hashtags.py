"""Hashtag Suggester — ranks hashtags for a draft across three axes.

The three axes the user asked for, and where each honestly comes from:

  * suitability   — does this tag fit THIS post? Embedding cosine between the
                    draft and the tag's words, plus the fact that the LLM (which
                    read the draft) proposed it at all.
  * trend         — is it moving right now? Mastodon's trends surface and each
                    tag's week-over-week usage, plus Google Trends search
                    direction for the strongest candidates.
  * hashtag data  — real usage numbers: weekly `uses`/`accounts` from Mastodon
                    (reach vs. competition, favouring the mid-tail), and whether
                    the tag actually appeared on high-engagement posts in the
                    user's own niche corpus.

Native to the app, not vendored: it orchestrates across the shared socialpost
corpus, app/services/mastodon.py, Google Trends (pytrends) and one LLM call.

Every external source is a sidecar. Any of them can be down, keyless or empty and
a result still comes back — degraded to whatever answered, with the gaps reported
rather than papered over. The one thing it never does is invent a usage number: a
candidate with no data reads as "unknown", never as zero. Same honesty rule that
keeps the topic suggester (vendor/socialpost/src/topics.py, the pattern this
follows) working off the corpus alone when every trend feed is down.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field

from . import mastodon

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 15

# How many on-topic candidates to pay a network round-trip to enrich. Trending
# tags already arrive with their numbers; these are the requests spent on tags
# that are relevant but not currently trending.
MAX_TAG_LOOKUPS = 8
MAX_GOOGLE_TERMS = 3
MAX_RESULTS = 24

# Weekly-use thresholds for the reach label. Deliberately fediverse-sized (a busy
# Mastodon tag is tens-to-thousands, not the millions a Twitter tag would be), and
# kept as named constants because they are heuristics, not measurements.
REACH_BROAD = 150
REACH_NICHE = 15

# Blend weights. Suitability leads because the feature is "hashtags for THIS post"
# first and a trend radar second — a hot tag that does not fit the draft is noise.
W_SUITABILITY = 0.50
W_TREND = 0.28
W_DATA = 0.22

# A tag pulled from the trends firehose that is this far off the draft's topic and
# has no niche history is dropped — it is someone else's trend, not this post's.
MIN_SUITABILITY_KEEP = 0.12

# Suggested number of tags to actually use, by platform norm.
_RECOMMENDED = {"mastodon": 4, "bluesky": 2, "x": 2, "linkedin": 4}

_ALNUM_RE = re.compile(r"[^0-9a-z]+")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Suggestion:
    tag: str  # display form, no '#'
    score: float  # 0..1 blended
    suitability: float  # 0..1
    trend: str  # hot | rising | steady | cooling | unknown
    reach: str  # broad | balanced | niche | unknown
    volume: int | None  # weekly uses, when a data source knew
    accounts: int | None
    bucket: str  # proven | trending | onTopic
    sources: list[str]


@dataclass
class Report:
    platform: str
    niche: str
    recommendedCount: int
    suggestions: list[Suggestion]
    sourcesUsed: list[str]
    sourcesUnavailable: list[str]
    note: str = ""


# ---------------------------------------------------------------------------
# Candidate bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _Cand:
    key: str  # normalised alnum-lowercase — dedup key and the Mastodon tag path
    display: str
    words: str  # space-separated words, for embedding and trend lookups
    sources: set[str] = field(default_factory=set)
    uses: int | None = None
    accounts: int | None = None
    momentum: float = 0.0
    trending: bool = False
    corpus_weight: float = 0.0
    suitability: float = 0.0
    google: str = ""  # rising | falling | flat | ""


def _normalise(tag: str) -> str:
    return _ALNUM_RE.sub("", (tag or "").lower())


def _to_words(raw: str) -> str:
    """A tag or phrase -> space-separated words, for a meaningful embedding.

    "#RustGamedev" and "rustGamedev2024" both need splitting or they embed as one
    opaque token that sits near nothing. Multi-word input is already fine.
    """
    s = (raw or "").lstrip("#").strip()
    if " " in s:
        return s.lower()
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", s)
    s = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", s)
    return s.lower()


def _display_form(raw: str) -> str:
    """Readable display: CamelCase a multi-word phrase, keep a single token as-is.

    CamelCase is the accessibility convention on Mastodon (screen readers announce
    the word boundaries) and reads fine everywhere else.
    """
    s = (raw or "").lstrip("#").strip()
    if " " in s:
        return "".join(w[:1].upper() + w[1:] for w in s.split() if w)
    return s


def _add(cands: dict[str, _Cand], raw: str, source: str) -> _Cand | None:
    key = _normalise(raw)
    if not key:
        return None
    cand = cands.get(key)
    if cand is None:
        cand = _Cand(key=key, display=_display_form(raw), words=_to_words(raw))
        cands[key] = cand
    else:
        # Prefer the more readable display if a later source has a cased one.
        if any(c.isupper() for c in raw) and not any(c.isupper() for c in cand.display):
            cand.display = _display_form(raw)
    cand.sources.add(source)
    return cand


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _corpus_hashtags(niche: str) -> dict[str, tuple[str, float]]:
    """key -> (display, weight in 0..1) mined from the niche's own posts.

    Weight is engagement-aware: a tag on a post we measured doing numbers counts
    for more than one on a post that sank. This is the app's unique signal — no
    external feed is niche-scoped, engagement-weighted and consent-filtered at
    once — so it is also what buckets a tag as "proven in your niche".
    """
    from vendor.socialpost.src.db import get_client

    client = get_client()
    posts = (
        client.table("posts").select("uri, hashtags").eq("niche", niche).execute().data or []
    )
    if not posts:
        return {}

    uris = [p["uri"] for p in posts]
    rates: dict[str, float] = {}
    for i in range(0, len(uris), 100):
        for row in (
            client.table("engagement_snapshots")
            .select("post_uri, engagement_rate")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        ):
            if row.get("engagement_rate") is not None:
                rates[row["post_uri"]] = float(row["engagement_rate"])

    agg: dict[str, list] = {}  # key -> [display, weight]
    for p in posts:
        weight = 1.0 + 10.0 * (rates.get(p["uri"]) or 0.0)
        for h in p.get("hashtags") or []:
            key = _normalise(str(h))
            if not key:
                continue
            entry = agg.setdefault(key, [str(h).lstrip("#"), 0.0])
            entry[1] += weight

    if not agg:
        return {}
    hi = max(w for _, w in agg.values()) or 1.0
    return {k: (d, round(w / hi, 4)) for k, (d, w) in agg.items()}


_LLM_PROMPT = """\
You suggest hashtags for a social media post.

Platform: {platform}
Niche / community: {niche}
Niche keywords: {keywords}

The post draft:
\"\"\"
{draft}
\"\"\"

List up to 14 hashtags that fit THIS specific post and would help the right people
find it on {platform}. Rules:
- Relevant to the draft's actual subject. No generic filler like #love or #viral.
- Mix a couple of broad tags with several specific, niche ones.
- No spaces or punctuation inside a tag, and do NOT include the # sign.
- Use readable CamelCase for multi-word tags (e.g. RustGamedev, BuildInPublic).

Respond with ONLY a JSON array of strings, nothing else.
"""


def _llm_candidates(draft: str, niche: str, platform: str, keywords: list[str]) -> list[str]:
    """Ask the model for on-topic tags. The suitability-first candidate source."""
    from vendor.socialpost.src import llm

    prompt = _LLM_PROMPT.format(
        platform=platform,
        niche=niche or "general",
        keywords=", ".join(keywords) or "(none given)",
        draft=draft[:1200],
    )
    raw = llm._call(prompt, temperature=0.3, max_output_tokens=300)
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    data = json.loads(text.strip())
    return [str(t) for t in data if str(t).strip()]


def _google_directions(terms: list[str]) -> dict[str, str]:
    """term -> 'rising'|'falling'|'flat' from Google search interest, best-effort.

    Only interest_over_time is used: measured against the live service, pytrends'
    related_queries reliably 429s while interest_over_time succeeds, so this uses
    the half that works (same finding the topic suggester relies on). Every call
    is wrapped — Google Trends being slow or rate-limiting must never fail the
    suggestion.
    """
    try:
        from pytrends.request import TrendReq
    except Exception:  # noqa: BLE001 — optional dependency / import-time failures
        return {}
    try:
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(HTTP_TIMEOUT, HTTP_TIMEOUT))
    except Exception:  # noqa: BLE001
        return {}

    out: dict[str, str] = {}
    for term in terms[:MAX_GOOGLE_TERMS]:
        try:
            pytrends.build_payload([term], timeframe="now 7-d")
            frame = pytrends.interest_over_time()
            if frame.empty or term not in frame:
                continue
            series = [v for v in frame[term].tolist() if isinstance(v, (int, float))]
            if len(series) < 8:
                continue
            window = max(2, len(series) // 4)
            recent = sum(series[-window:]) / window
            earlier = sum(series[:window]) / window
            out[term.lower()] = (
                "rising" if recent > earlier * 1.1 else "falling" if recent < earlier * 0.9 else "flat"
            )
        except Exception as err:  # noqa: BLE001 — one term failing is not fatal
            log.debug("google trends term %r unavailable: %s", term, str(err)[:80])
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _reach_label(uses: int | None) -> str:
    if uses is None:
        return "unknown"
    if uses >= REACH_BROAD:
        return "broad"
    if uses >= REACH_NICHE:
        return "balanced"
    return "niche"


def _data_score(cand: _Cand) -> float:
    """Reach-vs-competition, favouring the mid-tail, plus niche performance."""
    label = _reach_label(cand.uses)
    reach = {"broad": 0.55, "balanced": 0.85, "niche": 0.55, "unknown": 0.4}[label]
    return round(min(1.0, 0.6 * reach + 0.4 * cand.corpus_weight), 4)


def _trend_score(cand: _Cand) -> float:
    """0..1 from the trend signals we actually have; low-neutral when we have none."""
    have_signal = cand.trending or cand.momentum > 0 or bool(cand.google)
    t = 0.3 if have_signal else 0.28
    if cand.trending:
        t = max(t, 0.9)
    if cand.momentum >= 1.5:
        t = max(t, 0.85)
    elif cand.momentum >= 1.1:
        t = max(t, 0.7)
    elif 0 < cand.momentum < 0.9:
        t = min(t, 0.35)
    if cand.google == "rising":
        t = max(t, 0.75)
    elif cand.google == "falling":
        t = min(t, 0.35)
    return round(max(0.0, min(1.0, t)), 4)


def _trend_label(cand: _Cand) -> str:
    if cand.trending:
        return "hot"
    if cand.momentum >= 1.1 or cand.google == "rising":
        return "rising"
    if (cand.momentum and cand.momentum < 0.9) or cand.google == "falling":
        return "cooling"
    if cand.uses is not None or cand.momentum:
        return "steady"
    return "unknown"


def _bucket(cand: _Cand) -> str:
    if cand.corpus_weight >= 0.15:
        return "proven"
    if cand.trending or cand.google == "rising" or cand.momentum >= 1.2:
        return "trending"
    return "onTopic"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def suggest(
    draft: str,
    niche: str,
    platform: str,
    keywords: list[str],
    mastodon_host: str,
) -> Report:
    """Rank hashtags for a draft. Never raises for a missing source — degrades."""
    platform = (platform or "bluesky").lower()
    draft = (draft or "").strip()
    keywords = [k for k in (keywords or []) if k.strip()]

    used: list[str] = []
    unavailable: list[str] = []
    cands: dict[str, _Cand] = {}

    # --- candidate generation ------------------------------------------------
    # Niche keywords: guaranteed on-topic, and the floor a result can never fall
    # below even with every network source down.
    for kw in keywords:
        _add(cands, kw, "niche")

    # Corpus.
    try:
        for key, (display, weight) in _corpus_hashtags(niche).items():
            cand = _add(cands, display, "corpus")
            if cand:
                cand.corpus_weight = max(cand.corpus_weight, weight)
        used.append("corpus")
    except Exception as err:  # noqa: BLE001
        log.warning("corpus hashtags unavailable: %s", str(err)[:120])
        unavailable.append("corpus")

    # Mastodon trends — the trending set arrives with its usage numbers attached.
    if mastodon_host.strip():
        try:
            trend_tags = mastodon.trending_tags(mastodon_host)
            for stats in trend_tags:
                cand = _add(cands, stats.name, "mastodon_trends")
                if cand:
                    cand.uses = stats.uses
                    cand.accounts = stats.accounts
                    cand.momentum = stats.momentum
                    cand.trending = True
            used.append("mastodon_trends")
        except mastodon.MastodonError as err:
            log.info("mastodon trends unavailable on %s: %s", mastodon_host, str(err)[:120])
            unavailable.append("mastodon_trends")
        except Exception as err:  # noqa: BLE001
            log.warning("mastodon trends failed: %s", str(err)[:120])
            unavailable.append("mastodon_trends")
    else:
        unavailable.append("mastodon_trends")

    # LLM — the model reads the draft and proposes fitting tags.
    if draft:
        try:
            for tag in _llm_candidates(draft, niche, platform, keywords):
                _add(cands, tag, "llm")
            used.append("llm")
        except Exception as err:  # noqa: BLE001 — unconfigured provider, bad JSON, rate limit
            log.info("llm hashtag candidates unavailable: %s", str(err)[:120])
            unavailable.append("llm")

    if not cands:
        return Report(
            platform=platform,
            niche=niche,
            recommendedCount=_RECOMMENDED.get(platform, 3),
            suggestions=[],
            sourcesUsed=used,
            sourcesUnavailable=unavailable,
            note="Nothing to suggest yet — add a draft or some niche keywords first.",
        )

    # --- suitability (embeddings) -------------------------------------------
    ordered = list(cands.values())
    if draft:
        try:
            from vendor.socialpost.src import embeddings

            vectors = embeddings.embed([draft] + [c.words for c in ordered])
            draft_vec = vectors[0]
            for cand, vec in zip(ordered, vectors[1:]):
                cand.suitability = round(max(0.0, embeddings.cosine_similarity(draft_vec, vec)), 4)
            if "embeddings" not in used:
                used.append("embeddings")
        except Exception as err:  # noqa: BLE001 — torch/model load can fail on a thin box
            log.warning("embedding suitability unavailable: %s", str(err)[:120])
            unavailable.append("embeddings")
            _prior_suitability(ordered)
    else:
        _prior_suitability(ordered)

    # --- Google Trends direction for the strongest on-topic candidates -------
    google_pool = sorted(
        (c for c in ordered if "mastodon_trends" not in c.sources),
        key=lambda c: c.suitability,
        reverse=True,
    )
    google_terms: list[str] = []
    for cand in google_pool:
        if cand.words not in google_terms:
            google_terms.append(cand.words)
        if len(google_terms) >= MAX_GOOGLE_TERMS:
            break
    if google_terms:
        directions = _google_directions(google_terms)
        if directions:
            for cand in ordered:
                if cand.words in directions:
                    cand.google = directions[cand.words]
            used.append("google_trends")
        else:
            unavailable.append("google_trends")

    # --- per-tag Mastodon usage for the top on-topic candidates --------------
    # Trending tags already have numbers; spend a bounded budget of lookups on the
    # most suitable tags that do not, so their reach/momentum is real too. Stop at
    # the first auth/permission wall so a gated instance costs one request, not ten.
    if mastodon_host.strip() and "mastodon_trends" not in unavailable:
        lookup_pool = sorted(
            (c for c in ordered if c.uses is None and c.suitability >= 0.2),
            key=lambda c: c.suitability,
            reverse=True,
        )[:MAX_TAG_LOOKUPS]
        for cand in lookup_pool:
            try:
                stats = mastodon.tag_info(mastodon_host, cand.key)
            except mastodon.MastodonError as err:
                log.info("tag lookups halted (%s)", str(err)[:80])
                break
            except Exception as err:  # noqa: BLE001
                log.debug("tag_info %s failed: %s", cand.key, str(err)[:80])
                continue
            if stats:
                cand.uses = stats.uses
                cand.accounts = stats.accounts
                cand.momentum = max(cand.momentum, stats.momentum)

    # --- blend, filter, rank -------------------------------------------------
    suggestions: list[Suggestion] = []
    for cand in ordered:
        # Drop the trends-firehose noise: off-topic AND no niche history of its own.
        if (
            cand.suitability < MIN_SUITABILITY_KEEP
            and cand.corpus_weight == 0.0
            and "llm" not in cand.sources
            and "niche" not in cand.sources
        ):
            continue
        trend_s = _trend_score(cand)
        data_s = _data_score(cand)
        blended = round(
            W_SUITABILITY * cand.suitability + W_TREND * trend_s + W_DATA * data_s, 4
        )
        suggestions.append(
            Suggestion(
                tag=cand.display,
                score=blended,
                suitability=cand.suitability,
                trend=_trend_label(cand),
                reach=_reach_label(cand.uses),
                volume=cand.uses,
                accounts=cand.accounts,
                bucket=_bucket(cand),
                sources=sorted(cand.sources),
            )
        )

    suggestions.sort(key=lambda s: s.score, reverse=True)
    suggestions = suggestions[:MAX_RESULTS]

    note = ""
    if platform in ("x", "linkedin", "bluesky"):
        note = (
            f"{platform.capitalize()} has no free hashtag-stats API, so the volume and "
            f"trend numbers here come from the fediverse (Mastodon) as a cross-network "
            f"proxy. Fit and your niche history are platform-independent."
        )

    return Report(
        platform=platform,
        niche=niche,
        recommendedCount=_RECOMMENDED.get(platform, 3),
        suggestions=suggestions,
        sourcesUsed=used,
        sourcesUnavailable=sorted(set(unavailable)),
        note=note,
    )


def _prior_suitability(cands: list[_Cand]) -> None:
    """Rank by source confidence when embeddings can't be computed.

    A draft-less request (suggesting from keywords alone) or a box too thin to
    load the embedding model still needs a sensible order; the model's own tags
    and the niche keywords are the most trustworthy without a similarity read.
    """
    prior = {"llm": 0.6, "niche": 0.55, "corpus": 0.5, "mastodon_trends": 0.25}
    for cand in cands:
        cand.suitability = max((prior.get(s, 0.3) for s in cand.sources), default=0.3)
