from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from vendor.guestpostsuggester.pipeline import availability, catalog, crawler, openpagerank, search, suggest
from vendor.guestpostsuggester.pipeline import config as gp_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/guest-post", tags=["guest-post"])

_sites: list[catalog.Site] = []
_by_domain: dict[str, catalog.Site] = {}
_index: search.CatalogSearchIndex | None = None


def initialize() -> None:
    """Load the ~32k-site catalog and build the TF-IDF index once at startup."""
    global _sites, _by_domain, _index
    sites = catalog.load_catalog()
    opr_scores = openpagerank.load_cached_scores()
    for s in sites:
        s.page_rank = opr_scores.get(s.domain, 0.0)

    _sites = sites
    _by_domain = {s.domain: s for s in sites}
    _index = search.CatalogSearchIndex(sites)
    logger.info("Guest post catalog loaded: %d sites", len(sites))


class SearchRequest(BaseModel):
    topic: str = ""
    minAuthority: float = 0.0
    maxResults: int = 25


class SearchResponse(BaseModel):
    sites: list[dict]
    libraryId: str


@router.post("/search", response_model=SearchResponse)
def do_search(body: SearchRequest) -> SearchResponse:
    if _index is None:
        raise HTTPException(status_code=503, detail="Catalog is still loading — try again in a moment.")

    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required.")

    shortlist = _index.shortlist(topic, k=gp_config.SHORTLIST_SIZE)
    if not shortlist:
        return SearchResponse(sites=[], libraryId="")

    alive_map = availability.check_availability([s.domain for s in shortlist])
    live = [s for s in shortlist if alive_map.get(s.domain) and s.page_rank >= body.minAuthority]
    live.sort(key=lambda s: s.page_rank, reverse=True)
    live = live[: body.maxResults]

    result_sites = [
        {
            "domain": s.domain,
            "title": s.title or s.website_name or s.domain,
            "niche": s.niche,
            "page_rank": round(s.page_rank, 1),
            "guest_posts_url": s.guest_posts_url,
        }
        for s in live
    ]
    item = db.add_item(tool="Guest", title=f"“{topic}” guest-post targets", subtitle="Guest research")
    return SearchResponse(sites=result_sites, libraryId=item["id"])


class AnalyzeRequest(BaseModel):
    domain: str
    topic: str = ""
    hfToken: str = ""


class AnalyzeResponse(BaseModel):
    contactUrl: str
    guestPostsUrl: str
    titleCount: int
    tierUsed: str
    sampleTitles: list[str]
    suggestions: list[str]


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest) -> AnalyzeResponse:
    site = _by_domain.get(body.domain)
    if not site:
        raise HTTPException(status_code=404, detail="Unknown domain.")
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="A Hugging Face token is required to analyze a site.")

    try:
        lib = crawler.get_title_library(site, body.hfToken)
    except Exception as err:  # noqa: BLE001 — surface crawl failures as a normal API error
        raise HTTPException(status_code=502, detail=f"Couldn't crawl {body.domain}: {err}") from err

    suggestions: list[str] = []
    if lib.titles:
        try:
            suggestions = suggest.suggest_topics(site, body.topic, lib.titles, body.hfToken)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Suggestion generation failed: {err}") from err

    return AnalyzeResponse(
        contactUrl=site.contact_url or f"https://{site.domain}",
        guestPostsUrl=site.guest_posts_url or f"https://{site.domain}",
        titleCount=len(lib.titles),
        tierUsed=lib.tier_used,
        sampleTitles=lib.titles[:8],
        suggestions=suggestions,
    )
