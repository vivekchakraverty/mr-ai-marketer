"""Discovery, clustering, ranking, and measurement.

The pipeline, in order:

1. **Discover.** Every selected source runs concurrently and returns ``Evidence``.
   Editorial feeds and TrendScope's social feeds are peers here — same shape, same
   downstream treatment.
2. **Prepare.** Apply the date window, drop items irrelevant to the niche, collapse
   syndicated near-duplicates, and cap how much any one source can contribute.
3. **Cluster.** Count repeated 1–3 word phrases across surviving titles and keep the
   ones that more than one item supports. This is the discovery score: it says a
   phrase is being repeated, not that it is accelerating.
4. **Measure.** For the surviving candidates, compare the current window with the
   preceding equal window on each selected metric, and rescore from that. Discovery
   ranking only survives when no metric returned a usable baseline.
5. **Read the tone.** Sentiment over each ranked topic's evidence, as a separate
   axis — it never touches the momentum score.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlparse

from .models import FAMILY_LABELS, Evidence, Topic
from .sentiment import apply_sentiment
from .signals import measure_topics
from .social import SNAPSHOT_SOURCES, SOCIAL_FETCHERS
from .sources import EDITORIAL_FETCHERS

STOPWORDS = {
    "about", "after", "again", "against", "also", "among", "and", "are", "been",
    "before", "being", "between", "both", "but", "can", "could", "does", "for",
    "from", "gains", "gaining", "have", "into", "just", "momentum", "more", "most",
    "new", "news", "not", "now", "over", "says", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "through", "trending",
    "under", "using", "was", "were", "what", "when", "where", "which", "while",
    "will", "with", "would", "your",
}

GROUPS = {
    "AI and developer platforms": [
        "Foundation models and open weights", "AI coding copilots",
        "AI agents and workflow orchestration", "Vector databases and RAG",
        "MLOps, evals, and observability", "Synthetic media and creative AI",
    ],
    "Cybersecurity and digital trust": [
        "Identity, passkeys, and authentication", "API and application security",
        "Cloud security and CNAPP", "AI security and model governance",
        "Privacy-enhancing technologies", "Fraud, anti-bot, and risk intelligence",
    ],
    "Enterprise software and future of work": [
        "Workflow automation and RPA", "Collaboration and meeting intelligence",
        "Knowledge management and enterprise search", "Vertical SaaS",
        "RevOps and CRM automation", "Customer support and CCaaS",
    ],
    "Consumer internet and creator economy": [
        "Creator monetization", "Short-form video tooling",
        "Social commerce and live shopping", "Community platforms and chat",
        "UGC gaming and virtual economies", "Streaming and fan engagement",
    ],
    "Fintech and commerce": [
        "Embedded finance", "Payments and fraud", "Personal finance and tax automation",
        "B2B fintech and treasury", "Commerce enablement and SMB tooling",
        "Retail media and marketplaces",
    ],
    "Health, biotech, and longevity": [
        "Digital therapeutics and mental health", "Women's health and fertility tech",
        "Obesity, GLP-1, and metabolic health", "Diagnostics and remote monitoring",
        "AI drug discovery and lab automation", "Longevity and preventive care",
    ],
    "Climate, energy, and mobility": [
        "Solar, batteries, and storage", "EV charging and battery supply chain",
        "Heat pumps and building electrification", "Carbon accounting and MRV",
        "Grid software and flexibility", "Hydrogen and carbon removal",
    ],
    "Education and careers": [
        "AI tutoring and homework assistance", "Language learning",
        "Workforce upskilling", "Hiring and recruiting tech",
        "Credentials and skills verification", "Creator-led education",
    ],
    "Home, food, and lifestyle": [
        "Smart home and home energy devices", "Pet care and pet tech",
        "Wellness and nutrition apps", "Food tech and alternative proteins",
        "Beauty tech and biotech personal care", "Travel and outdoor gear",
    ],
    "Industrial, logistics, agtech, and space": [
        "Industrial robotics and cobots", "Supply chain visibility",
        "Warehouse automation", "Additive manufacturing", "Precision agriculture",
        "Space economy and Earth observation",
    ],
}

WEIGHTS = {
    "AI and developer platforms": {"news": .15, "attention": .10, "conversation": .15, "adoption": .40, "research": .20},
    "Cybersecurity and digital trust": {"news": .18, "attention": .10, "conversation": .20, "adoption": .32, "research": .20},
    "Enterprise software and future of work": {"news": .20, "attention": .15, "conversation": .10, "adoption": .40, "research": .15},
    "Consumer internet and creator economy": {"news": .18, "attention": .35, "conversation": .30, "adoption": .12, "research": .05},
    "Fintech and commerce": {"news": .22, "attention": .18, "conversation": .10, "adoption": .35, "research": .15},
    "Health, biotech, and longevity": {"news": .12, "attention": .18, "conversation": .05, "adoption": .20, "research": .45},
    "Climate, energy, and mobility": {"news": .20, "attention": .15, "conversation": .05, "adoption": .25, "research": .35},
    "Education and careers": {"news": .15, "attention": .30, "conversation": .20, "adoption": .20, "research": .15},
    "Home, food, and lifestyle": {"news": .18, "attention": .35, "conversation": .25, "adoption": .12, "research": .10},
    "Industrial, logistics, agtech, and space": {"news": .15, "attention": .10, "conversation": .05, "adoption": .40, "research": .30},
}

FETCHERS = {**EDITORIAL_FETCHERS, **SOCIAL_FETCHERS}

# Which sources are safe to run with no credentials at all. The UI defaults to these.
DEFAULT_SOURCES = [
    "Google News", "Bing News", "GDELT", "AP News", "Techmeme",
    "ScienceDaily", "Phys.org", "Hacker News", "GitHub", "OpenAlex",
    "Reddit", "Google Trends", "YouTube",
]

DEFAULT_SIGNAL_SOURCES = [
    "GDELT TimelineVol", "Wikimedia pageviews", "Hacker News velocity",
    "Reddit conversation", "GitHub repository growth", "OpenAlex publications",
    "PubMed publications",
]

_HALF_LIVES = {"news": 25, "attention": 14, "conversation": 10, "adoption": 45, "research": 100}


def _normalise_title(title: str) -> str:
    title = re.sub(r"\s+[-–—|]\s+[^-–—|]{2,50}$", "", title)
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def _clean_words(text: str, niche_words: set[str]) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+'-]{2,}", text.lower())
    return [w.strip("-") for w in words if w not in STOPWORDS and w not in niche_words]


def _relevance(item: Evidence, niche: str) -> float:
    niche_terms = set(_clean_words(niche, set()))
    title_terms = set(_clean_words(item.title, set()))
    if not niche_terms:
        return 1.0
    overlap = len(niche_terms & title_terms)
    return overlap / min(3, len(niche_terms))


def _prepare_evidence(items: list[Evidence], niche: str, days: int) -> list[Evidence]:
    """Cut off stale/noisy records, deduplicate syndication, and cap source dominance."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)
    accepted: list[Evidence] = []
    seen_titles: set[str] = set()
    seen_title_tokens: list[set[str]] = []
    seen_urls: set[str] = set()
    source_counts: Counter = Counter()
    for item in sorted(items, key=lambda value: value.published, reverse=True):
        title_key = _normalise_title(item.title)
        title_tokens = set(title_key.split())
        near_duplicate = any(
            len(title_tokens & previous) / max(1, len(title_tokens | previous)) >= .82
            for previous in seen_title_tokens
        )
        try:
            url_key = f"{urlparse(item.url).netloc}{urlparse(item.url).path}".rstrip("/")
        except ValueError:
            url_key = item.url
        if (
            item.published < cutoff
            or len(title_key) < 12
            or _relevance(item, niche) < .25
            or title_key in seen_titles
            or near_duplicate
            or (url_key and url_key in seen_urls)
            or source_counts[item.source] >= 50
        ):
            continue
        seen_titles.add(title_key)
        seen_title_tokens.append(title_tokens)
        if url_key:
            seen_urls.add(url_key)
        source_counts[item.source] += 1
        accepted.append(item)
    return accepted


def _phrases(items: Iterable[Evidence], niche: str) -> Counter:
    niche_words = set(_clean_words(niche, set()))
    counts: Counter = Counter()
    for item in items:
        words = _clean_words(item.title, niche_words)
        unique = set(words)
        counts.update(unique)
        counts.update(" ".join(words[i:i + 2]) for i in range(len(words) - 1))
        counts.update(" ".join(words[i:i + 3]) for i in range(len(words) - 2))
    return counts


def _topic_label(phrase: str) -> str:
    keep_upper = {"AI", "API", "EV", "RAG", "B2B", "UGC", "LLM"}
    return " ".join(w.upper() if w.upper() in keep_upper else w.capitalize() for w in phrase.split())


def _apply_measurements(topic: Topic, weights: dict[str, float], niche: str) -> None:
    scored = [item for item in topic.measurements if item.score is not None and not item.context_only]
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in scored:
        grouped[item.family].append(float(item.score))

    family_scores: dict[str, float] = {}
    for family in FAMILY_LABELS:
        values = sorted(grouped.get(family, []), reverse=True)[:3]
        family_scores[family] = (
            min(100.0, sum(values) / len(values) + min(6.0, 2.0 * (len(values) - 1)))
            if values else 0.0
        )
    topic.family_scores = family_scores

    available = [family for family, values in grouped.items() if values and family in weights]
    available_weight = sum(weights[family] for family in available)
    if not available_weight:
        topic.score = round(min(49.0, topic.discovery_score * .65), 1)
        topic.confidence = min(topic.confidence, 35)
        topic.tier = "Evidence only"
        topic.angle = (
            f"Treat {topic.query} as an editorial lead in {niche}, but verify it manually: "
            "the selected historical metrics did not return a usable baseline."
        )
        return

    topic.score = round(
        sum(family_scores[family] * weights[family] for family in available) / available_weight,
        1,
    )
    source_count = len({item.source for item in scored})
    family_count = len(available)
    nonzero_baselines = sum(item.baseline > 0 for item in scored)
    topic.confidence = min(
        96,
        round(18 + 12 * family_count + 4 * min(5, source_count) + min(10, 2 * nonzero_baselines)),
    )
    topic.tier = (
        "High momentum" if topic.score >= 75 and topic.confidence >= 70
        else "Emerging" if topic.score >= 60 and topic.confidence >= 55
        else "Watch" if topic.score >= 45
        else "Monitor"
    )
    top_family = max(available, key=lambda family: family_scores[family])
    topic.angle = (
        f"Explain what is changing around {topic.query} in {niche}. Lead with "
        f"{FAMILY_LABELS[top_family].lower()}, then test whether the other measured "
        "families confirm or contradict it."
    )


def _cluster(evidence: list[Evidence], niche: str, group: str, max_topics: int) -> list[Topic]:
    phrase_counts = _phrases(evidence, niche)
    ranked_phrases = sorted(
        phrase_counts.items(),
        key=lambda item: (item[1] * min(3, len(item[0].split())), len(item[0])),
        reverse=True,
    )
    candidates = [
        phrase for phrase, count in ranked_phrases[:160]
        if count >= 2 and len(phrase.split()) >= 2
    ]
    if len(candidates) < max_topics:
        candidates += [
            phrase for phrase, _ in ranked_phrases[:160]
            if phrase not in candidates and len(phrase.split()) >= 2
        ][: max_topics - len(candidates)]

    now = datetime.now(timezone.utc)
    weights = WEIGHTS[group]
    topics: list[Topic] = []
    claimed: set[str] = set()

    for phrase in candidates:
        tokens = set(phrase.split())
        if any(len(tokens & set(old.split())) / max(1, len(tokens | set(old.split()))) > .55 for old in claimed):
            continue
        matches = [item for item in evidence if tokens.issubset(set(_clean_words(item.title, set())))]
        if len(matches) < 2:
            continue
        claimed.add(phrase)

        raw: dict[str, float] = defaultdict(float)
        source_contrib: dict[str, float] = defaultdict(float)
        for item in matches:
            age = max(0, (now - item.published).days)
            value = item.strength * math.exp(-math.log(2) * age / _HALF_LIVES[item.family])
            raw[item.family] += value
            source_contrib[item.source] += value

        family_scores = {f: min(100.0, 24 * math.log1p(raw.get(f, 0))) for f in FAMILY_LABELS}
        active = sum(value >= 12 for value in family_scores.values())
        weighted = sum(family_scores[f] * weights[f] for f in FAMILY_LABELS)
        corroboration = min(1.45, 1 + .15 * max(0, active - 1))

        # Snapshot sources (Google Trends, TikTok, Amazon, Twitter/X) all stamp "now",
        # so they would otherwise hand out the persistence bonus for free. Only dated
        # streams get a vote on whether a story has held across several days.
        dated = {item.published.date() for item in matches if item.source not in SNAPSHOT_SOURCES}
        persistence = 1.1 if len(dated) >= 3 else .9

        domination = max(source_contrib.values()) / max(sum(source_contrib.values()), .01)
        quality = .78 if domination > .55 else 1.0
        jitter = int(hashlib.md5((niche + phrase).encode()).hexdigest()[:2], 16) / 255
        score = min(100, weighted * corroboration * persistence * quality + jitter)

        tier = (
            "High momentum" if score >= 70 and active >= 3
            else "Emerging" if score >= 52 and active >= 2
            else "Watch" if score >= 35
            else "Monitor"
        )
        confidence = min(98, round(35 + active * 13 + min(20, len(matches) * 2) - (10 if domination > .55 else 0)))
        top_family = max(family_scores, key=family_scores.get)

        topics.append(Topic(
            label=_topic_label(phrase),
            query=phrase,
            evidence=sorted(matches, key=lambda x: x.published, reverse=True),
            family_scores=family_scores,
            score=round(score, 1),
            discovery_score=round(score, 1),
            tier=tier,
            confidence=confidence,
            angle=(
                f"Explain why {phrase} is showing current momentum in {niche}, using "
                f"{FAMILY_LABELS[top_family].lower()} as the lead signal and contrasting hype with adoption."
            ),
        ))
        if len(topics) >= max_topics * 2:
            break

    topics.sort(key=lambda item: item.discovery_score, reverse=True)
    return topics[:max_topics]


def analyze(
    niche: str,
    group: str,
    days: int,
    sources: list[str],
    max_topics: int = 10,
    signal_sources: list[str] | None = None,
    config: dict | None = None,
    hf_token: str = "",
    sentiment_model: str = "",
) -> tuple[list[Topic], list[str], str]:
    """Run the whole pipeline. Returns (ranked topics, source-health notes, tone note)."""
    config = config or {}
    known = [source for source in sources if source in FETCHERS]
    evidence: list[Evidence] = []
    errors: list[str] = []

    # Feed endpoints are independent I/O calls. Fetching them concurrently keeps a
    # broad source stack responsive even when one optional endpoint is slow.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(known)))) as pool:
        futures = {pool.submit(FETCHERS[source], niche, days, config): source for source in known}
        for future in as_completed(futures):
            source = futures[future]
            try:
                evidence.extend(future.result())
            except Exception as exc:  # noqa: BLE001 — one bad feed is a health note, not a failure
                errors.append(f"{source}: {type(exc).__name__}: {exc}")

    evidence = _prepare_evidence(evidence, niche, days)
    if not evidence:
        return [], errors, ""

    topics = _cluster(evidence, niche, group, max_topics)
    if not topics:
        return [], errors, ""

    if signal_sources:
        measurements, signal_errors = measure_topics(
            [topic.query for topic in topics], days, signal_sources, config
        )
        errors.extend(f"Measurement · {error}" for error in signal_errors)
        weights = WEIGHTS[group]
        for topic in topics:
            topic.measurements = sorted(
                measurements.get(topic.query, []),
                key=lambda item: (item.context_only, -(item.score or 0)),
            )
            _apply_measurements(topic, weights, niche)
        topics.sort(key=lambda item: (item.score, item.confidence, item.discovery_score), reverse=True)

    sentiment_note = apply_sentiment(topics, hf_token, sentiment_model)
    return topics, errors, sentiment_note
