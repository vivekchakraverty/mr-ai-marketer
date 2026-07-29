"""Self-hosted SearXNG discovery backend.

Turns a clause set into a web-search query, reads SearXNG's JSON API, and takes the result
URLs as candidate company sites (enriched later by enrich.py). Broader than Overpass — works
for non-local niches — at the cost of a scrape step during enrichment.
"""

from __future__ import annotations

import logging

import httpx

from .. import config
from .base import RawLead, clause_terms, domain_of

log = logging.getLogger(__name__)

_TIMEOUT = 25

# Aggregators/directories we don't want as "leads" — we want the businesses themselves.
_SKIP_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
    "yelp.com", "tripadvisor.com", "wikipedia.org", "amazon.com", "reddit.com", "pinterest.com",
    "maps.google.com", "google.com", "bing.com", "yellowpages.com", "crunchbase.com",
}


def healthcheck() -> tuple[bool, str]:
    """Confirm the local SearXNG answers the JSON API (which is disabled by default and
    enabled by our bundled settings.yml)."""
    try:
        resp = httpx.get(
            f"{config.searxng_url()}/search",
            params={"q": "test", "format": "json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200 and "results" in resp.json():
            return True, "SearXNG JSON API responding."
        return False, f"SearXNG returned HTTP {resp.status_code} (is the JSON format enabled?)."
    except Exception as err:  # noqa: BLE001
        return False, f"Could not reach SearXNG: {str(err).splitlines()[0][:160]}"


def _query_string(clauses: dict) -> str:
    base = clause_terms(clauses)
    # Nudge toward company homepages rather than listicles/directories.
    return f"{base} company official website".strip()


def search(clauses: dict, limit: int = 20) -> list[RawLead]:
    try:
        resp = httpx.get(
            f"{config.searxng_url()}/search",
            params={"q": _query_string(clauses), "format": "json", "safesearch": "0"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as err:  # noqa: BLE001
        log.warning("[leadgen] searxng query failed: %s", str(err).splitlines()[0][:160])
        return []

    leads: list[RawLead] = []
    seen: set[str] = set()
    for r in results:
        url = r.get("url", "")
        domain = domain_of(url)
        if not domain or domain in _SKIP_DOMAINS or domain in seen:
            continue
        seen.add(domain)
        title = (r.get("title") or "").strip()
        content = (r.get("content") or "").strip()
        leads.append(
            RawLead(
                company=title or domain,
                website=f"https://{domain}",
                domain=domain,
                source="searxng",
                source_url=url,
                profile_text=f"{title}. {content}".strip(),
            )
        )
        if len(leads) >= limit:
            break
    log.info("[leadgen] searxng '%s' -> %d candidates", clause_terms(clauses), len(leads))
    return leads
