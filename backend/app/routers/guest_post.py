from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..services import hf_assets
from vendor.guestpostsuggester.pipeline import availability, catalog, crawler, openpagerank, search, suggest
from vendor.guestpostsuggester.pipeline import config as gp_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/guest-post", tags=["guest-post"])

_sites: list[catalog.Site] = []
_by_domain: dict[str, catalog.Site] = {}
_index: search.CatalogSearchIndex | None = None


def _point_vendor_at_assets() -> None:
    """Redirect the vendored pipeline to the fetched catalogue files.

    Its config module reads these paths from the environment at *import* time, which is long
    past by the time the app can download anything — but `catalog.py` and `openpagerank.py`
    both read the config attributes at *call* time. So rebinding the attributes works, and
    vendor/guestpostsuggester stays exactly as upstream wrote it, the same way the Marketing
    Plan's retrieval is redirected rather than forked.
    """
    catalog_path = hf_assets.path_for("guest-post-db", required=False)
    if catalog_path is not None:
        gp_config.CATALOG_XLSX_PATH = catalog_path
    scores_path = hf_assets.path_for("opr-scores", required=False)
    if scores_path is not None:
        gp_config.OPR_SCORES_PATH = scores_path


def initialize() -> None:
    """Load the ~32k-site catalog and build the TF-IDF index once at startup.

    Never raises: the catalogue now comes from Hugging Face on a fresh install, and a gated
    repo or an offline first launch must not stop the backend from starting. The tool reports
    its own unavailability instead, and `ensure_loaded()` retries on the next request — by
    which point a token has usually arrived.
    """
    global _sites, _by_domain, _index
    try:
        _point_vendor_at_assets()
        sites = catalog.load_catalog()
    except Exception as err:  # noqa: BLE001
        logger.warning("Guest post catalog unavailable for now: %s", err)
        return
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


def ensure_loaded() -> None:
    """Retry the one-time load if startup could not do it."""
    if _index is None:
        initialize()


@router.post("/search", response_model=SearchResponse)
def do_search(body: SearchRequest) -> SearchResponse:
    ensure_loaded()
    if _index is None:
        raise HTTPException(
            status_code=503,
            detail="The site catalogue isn't loaded yet. If this persists, check that "
                   "HF_ASSETS_GUEST_POST_REPO points at a Hugging Face dataset you can read.",
        )

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
