"""Hashtag Suggester — one endpoint the post composers call.

Thin HTTP layer over app/services/hashtags.py, which does the actual cross-source
ranking. The niche's keywords are resolved here from the shared socialpost niche
table (the same niches the composer's dropdown is populated from) so the caller
only has to send the niche name.

Lazy imports: the service pulls in the embedding model (torch) and the vendored
corpus, so nothing is imported until a request actually arrives — backend startup
must not pay for a tool the user may never open.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/hashtags", tags=["hashtags"])

# When the composer is not the Mastodon one, there is no user-chosen instance to
# read fediverse trends from. mastodon.social is the largest public instance and
# serves the trends endpoint unauthenticated — a reasonable cross-network proxy,
# and named in the response's sources so it is never a silent assumption.
DEFAULT_TREND_INSTANCE = "mastodon.social"


class SuggestRequest(BaseModel):
    draft: str = ""
    niche: str = ""
    platform: str = "bluesky"
    # The instance to read live hashtag data from. The Mastodon composer sends the
    # user's own; the others send "" and get the public default.
    mastodonInstance: str = ""


class SuggestionOut(BaseModel):
    tag: str
    score: float
    suitability: float
    trend: str
    reach: str
    volume: int | None
    accounts: int | None
    bucket: str
    sources: list[str]


class SuggestResponse(BaseModel):
    platform: str
    niche: str
    recommendedCount: int
    trendInstance: str
    suggestions: list[SuggestionOut]
    sourcesUsed: list[str]
    sourcesUnavailable: list[str]
    note: str


def _niche_keywords(niche: str) -> list[str]:
    """The keywords defining a niche, or [] if it has none / the store is empty."""
    if not niche.strip():
        return []
    try:
        from vendor.socialpost.src import db as spg_db

        return spg_db.load_niches(active_only=False).get(niche, [])
    except Exception as err:  # noqa: BLE001 — an unconfigured store is not an error here
        log.debug("could not load niche keywords for %r: %s", niche, str(err)[:80])
        return []


@router.post("/suggest", response_model=SuggestResponse)
def suggest(body: SuggestRequest) -> SuggestResponse:
    from ..services import hashtags

    if not body.draft.strip() and not body.niche.strip():
        raise HTTPException(
            status_code=400,
            detail="Give it a draft or pick a niche first — it has nothing to work from otherwise.",
        )

    trend_instance = body.mastodonInstance.strip() or DEFAULT_TREND_INSTANCE

    try:
        report = hashtags.suggest(
            draft=body.draft,
            niche=body.niche,
            platform=body.platform,
            keywords=_niche_keywords(body.niche),
            mastodon_host=trend_instance,
        )
    except Exception as err:  # noqa: BLE001 — the service degrades internally; this is a real fault
        log.exception("hashtag suggestion failed")
        raise HTTPException(status_code=502, detail=str(err)) from None

    return SuggestResponse(
        platform=report.platform,
        niche=report.niche,
        recommendedCount=report.recommendedCount,
        trendInstance=trend_instance,
        suggestions=[
            SuggestionOut(
                tag=s.tag,
                score=s.score,
                suitability=s.suitability,
                trend=s.trend,
                reach=s.reach,
                volume=s.volume,
                accounts=s.accounts,
                bucket=s.bucket,
                sources=s.sources,
            )
            for s in report.suggestions
        ],
        sourcesUsed=report.sourcesUsed,
        sourcesUnavailable=report.sourcesUnavailable,
        note=report.note,
    )
