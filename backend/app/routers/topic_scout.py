"""Topic Scout — evidence-led topic discovery with a sentiment read.

Thin translation layer over vendor/topicscout: HTTP in, the vendored package's own
dataclasses out. All the actual judgement (which sources count as which evidence
family, how momentum is measured, how tone is aggregated) lives in the package.

Discovery is slow by nature — a dozen feeds, then a measurement fan-out, then one
sentiment pass — so this is a single blocking call the UI shows a spinner for,
rather than a job queue. The result is worthless if the user has navigated away.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..services.genqueue import queue_slot
from pydantic import BaseModel

from .. import db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/topic-scout", tags=["topic-scout"])


def _engine():
    """Imported lazily so backend startup does not pay for it."""
    from vendor.topicscout import engine, signals

    return engine, signals


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class OptionsResponse(BaseModel):
    groups: dict[str, list[str]]
    sources: list[str]
    signalSources: list[str]
    defaultSources: list[str]
    defaultSignalSources: list[str]
    sentimentModel: str


class DiscoverRequest(BaseModel):
    niche: str
    group: str
    subNiche: str = ""
    days: int = 30
    maxTopics: int = 10
    sources: list[str] = []
    signalSources: list[str] = []
    # Optional access settings. Every one of these is off-by-default and only
    # unlocks a source whose provider requires identification.
    contactEmail: str = ""
    githubToken: str = ""
    reliefwebAppname: str = ""
    fredApiKey: str = ""
    twitterAuthToken: str = ""
    twitterCt0: str = ""
    geo: str = "US"
    hfToken: str = ""
    sentimentModel: str = ""


class EvidenceOut(BaseModel):
    title: str
    url: str
    source: str
    family: str
    published: str
    sentimentLabel: str
    sentimentScore: float


class MeasurementOut(BaseModel):
    source: str
    family: str
    current: float
    baseline: float
    unit: str
    score: float | None
    changePct: float | None
    note: str
    url: str
    contextOnly: bool


class SentimentOut(BaseModel):
    label: str
    positive: int
    negative: int
    neutral: int
    polarity: float
    analyzed: int
    engine: str


class TopicOut(BaseModel):
    label: str
    query: str
    score: float
    discoveryScore: float
    tier: str
    confidence: int
    angle: str
    familyScores: dict[str, float]
    measuredFamilies: int
    sentiment: SentimentOut
    evidence: list[EvidenceOut]
    measurements: list[MeasurementOut]


class DiscoverResponse(BaseModel):
    topics: list[TopicOut]
    familyLabels: dict[str, str]
    sourceHealth: list[str]
    sentimentNote: str
    libraryId: str | None = None


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@router.get("/options", response_model=OptionsResponse)
def options() -> OptionsResponse:
    engine, signals = _engine()
    from vendor.topicscout.sentiment import DEFAULT_MODEL

    return OptionsResponse(
        groups=engine.GROUPS,
        sources=list(engine.FETCHERS),
        signalSources=list(signals.SIGNAL_FETCHERS),
        defaultSources=engine.DEFAULT_SOURCES,
        defaultSignalSources=engine.DEFAULT_SIGNAL_SOURCES,
        sentimentModel=DEFAULT_MODEL,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.post("/discover", response_model=DiscoverResponse, dependencies=[Depends(queue_slot("model"))])
def discover(body: DiscoverRequest) -> DiscoverResponse:
    engine, _ = _engine()

    niche = body.niche.strip()
    if len(niche) < 3:
        raise HTTPException(status_code=400, detail="Describe your niche a little more specifically.")
    if body.group not in engine.GROUPS:
        raise HTTPException(status_code=400, detail=f"Unknown market group: {body.group}")

    sources = body.sources or engine.DEFAULT_SOURCES
    signal_sources = body.signalSources or engine.DEFAULT_SIGNAL_SOURCES
    query = f"{niche} {body.subNiche}".strip() if body.subNiche else niche

    try:
        topics, errors, sentiment_note = engine.analyze(
            query,
            body.group,
            max(7, min(180, body.days)),
            sources,
            max_topics=max(3, min(20, body.maxTopics)),
            signal_sources=signal_sources,
            config={
                "contact_email": body.contactEmail.strip(),
                "github_token": body.githubToken.strip(),
                "reliefweb_appname": body.reliefwebAppname.strip(),
                "fred_api_key": body.fredApiKey.strip(),
                "twitter_auth_token": body.twitterAuthToken.strip(),
                "twitter_ct0": body.twitterCt0.strip(),
                "geo": body.geo.strip() or "US",
            },
            hf_token=body.hfToken,
            sentiment_model=body.sentimentModel,
        )
    except Exception as err:  # noqa: BLE001
        log.exception("[topic-scout] discovery failed")
        raise HTTPException(status_code=502, detail=str(err)) from None

    out = [
        TopicOut(
            label=topic.label,
            query=topic.query,
            score=topic.score,
            discoveryScore=topic.discovery_score,
            tier=topic.tier,
            confidence=topic.confidence,
            angle=topic.angle,
            familyScores={key: round(value, 1) for key, value in topic.family_scores.items()},
            measuredFamilies=sum(value > 0 for value in topic.family_scores.values()),
            sentiment=SentimentOut(
                label=topic.sentiment.label,
                positive=topic.sentiment.positive,
                negative=topic.sentiment.negative,
                neutral=topic.sentiment.neutral,
                polarity=topic.sentiment.polarity,
                analyzed=topic.sentiment.analyzed,
                engine=topic.sentiment.engine,
            ),
            evidence=[
                EvidenceOut(
                    title=item.title,
                    url=item.url,
                    source=item.source,
                    family=item.family,
                    published=item.published.isoformat(),
                    sentimentLabel=item.sentiment_label,
                    sentimentScore=item.sentiment_score,
                )
                for item in topic.evidence[:8]
            ],
            measurements=[
                MeasurementOut(
                    source=item.source,
                    family=item.family,
                    current=item.current,
                    baseline=item.baseline,
                    unit=item.unit,
                    score=item.score,
                    changePct=item.change_pct,
                    note=item.note,
                    url=item.url,
                    contextOnly=item.context_only,
                )
                for item in topic.measurements
            ],
        )
        for topic in topics
    ]

    library_id = None
    if out:
        library_id = db.add_item(
            tool="Topics",
            title=f"Topic Scout — {niche}",
            subtitle=f"{len(out)} topics · {body.days} days · {body.group}",
            content=_markdown_report(niche, body, out, errors, sentiment_note),
        )["id"]

    return DiscoverResponse(
        topics=out,
        familyLabels=engine.FAMILY_LABELS,
        sourceHealth=errors,
        sentimentNote=sentiment_note,
        libraryId=library_id,
    )


def _markdown_report(
    niche: str,
    body: DiscoverRequest,
    topics: list[TopicOut],
    errors: list[str],
    sentiment_note: str,
) -> str:
    """A readable snapshot for the Library — the UI is the live view, this is the record."""
    lines = [
        f"# Topic Scout — {niche}",
        "",
        f"**Market group:** {body.group}  ",
        f"**Window:** {body.days} days  ",
        f"**Topics:** {len(topics)}",
        "",
    ]
    if sentiment_note:
        lines += [f"> {sentiment_note}", ""]

    for index, topic in enumerate(topics, 1):
        tone = topic.sentiment
        lines += [
            f"## {index:02d}. {topic.label} — {topic.score:.0f}/100 ({topic.tier})",
            "",
            f"- Confidence: {topic.confidence}% · measured families: {topic.measuredFamilies}/5",
            (
                f"- Tone: {tone.label} (polarity {tone.polarity:+.2f}) across {tone.analyzed} headlines "
                f"— {tone.positive} positive / {tone.neutral} neutral / {tone.negative} negative"
            ),
            f"- Angle: {topic.angle}",
            "",
        ]
        if topic.measurements:
            lines.append("**Measured change**")
            lines.append("")
            for item in topic.measurements:
                change = "no baseline" if item.changePct is None else f"{item.changePct:+.1f}%"
                suffix = " (context only)" if item.contextOnly else ""
                lines.append(
                    f"- {item.source}: {item.current:g} vs {item.baseline:g} {item.unit} — {change}{suffix}"
                )
            lines.append("")
        if topic.evidence:
            lines.append("**Evidence**")
            lines.append("")
            for item in topic.evidence[:6]:
                label = f" [{item.sentimentLabel}]" if item.sentimentLabel else ""
                lines.append(
                    f"- [{item.title}]({item.url}) — {item.source}, {item.published[:10]}{label}"
                    if item.url
                    else f"- {item.title} — {item.source}, {item.published[:10]}{label}"
                )
            lines.append("")

    if errors:
        lines += ["## Source health", ""]
        lines += [f"- {error}" for error in errors]
        lines.append("")

    lines += [
        "---",
        "",
        "Scores are directional indicators for editorial discovery, not forecasts or "
        "financial advice. Sentiment describes the tone of the coverage, not whether "
        "the topic is worth pursuing.",
    ]
    return "\n".join(lines)
