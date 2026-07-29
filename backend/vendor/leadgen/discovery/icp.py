"""ICP synthesis + query selection.

OpenOutreach turns a product description into an `ICPSpec` seed, folds it into a clause
pool, and picks the next query to run by GP acquisition — with lazy backoff and
anti-monotone empty-recording so dead axes are pruned. We reproduce that behavior, adapted
to our two free backends:

  * synthesize()  one LLM pass -> {seed, categories, locations, keywords, value_prop},
                  stored on the campaign (and value_prop reused when writing outreach).
  * next_query()  build candidate clause sets (category x location x keyword), drop ones
                  already run or pruned by a recorded empty, then choose by GP acquisition
                  (explore/exploit, balance-driven) when the GP is fitted, else seed-first.
"""

from __future__ import annotations

import json
import logging

from .. import config, db, llm
from ..ml import embedder, qualifier
from ..prompts import reasoning
from .base import clause_key, clause_terms

log = logging.getLogger(__name__)

_MAX_CANDIDATES = 60


def synthesize(campaign_id: str) -> dict:
    """Ensure the campaign has a clause pool; synthesize it from the product description on
    first use. Returns the pool dict."""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return {}
    if campaign.get("clauses"):
        return json.loads(campaign["clauses"])

    prompt = reasoning.icp_seed(
        campaign["product_description"], campaign.get("objective", ""), campaign.get("country", "")
    )
    spec = llm.structured(prompt, max_output_tokens=600)
    pool = {
        "seed": spec.get("seed", {}) if isinstance(spec, dict) else {},
        "categories": [c for c in (spec.get("categories") or []) if c][:6],
        "locations": [l for l in (spec.get("locations") or []) if l][:5],
        "keywords": [k for k in (spec.get("keywords") or []) if k][:6],
        "value_prop": (spec.get("value_prop") or "").strip(),
    }
    db.update_campaign(campaign_id, clauses=json.dumps(pool))
    log.info("[leadgen] campaign %s ICP synthesized: %s", campaign_id, clause_terms(pool.get("seed", {})))
    return pool


def value_prop(campaign_id: str) -> str:
    campaign = db.get_campaign(campaign_id)
    if campaign and campaign.get("clauses"):
        return json.loads(campaign["clauses"]).get("value_prop", "")
    return ""


def _candidate_clause_sets(pool: dict) -> list[dict]:
    """The frontier: the seed plus the Cartesian product of the pool's families (capped)."""
    categories = pool.get("categories") or ([pool.get("seed", {}).get("category")] if pool.get("seed") else [])
    locations = pool.get("locations") or [pool.get("seed", {}).get("location", "")]
    keywords = pool.get("keywords") or [pool.get("seed", {}).get("keyword", "")]
    locations = locations or [""]
    keywords = keywords or [""]

    candidates: list[dict] = []
    seed = pool.get("seed") or {}
    if seed.get("category"):
        candidates.append({k: v for k, v in seed.items() if v})

    for cat in categories:
        if not cat:
            continue
        for loc in locations:
            for kw in keywords:
                clause = {"category": cat}
                if loc:
                    clause["location"] = loc
                if kw:
                    clause["keyword"] = kw
                candidates.append(clause)

    # De-dup by key, preserving order.
    seen: set[str] = set()
    unique: list[dict] = []
    for c in candidates:
        k = clause_key(c)
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique[:_MAX_CANDIDATES]


def _pruned_by_empty(clause: dict, empties: list[dict]) -> bool:
    """Anti-monotone pruning: a candidate is dead iff some recorded-empty clause set is a
    subset of it (e.g. a size-1 empty {location: Oman} kills every query mentioning Oman)."""
    items = set(clause.items())
    return any(set(e.items()) <= items for e in empties)


def enabled_backends(campaign: dict) -> list[str]:
    """Discovery backends for a campaign: the globally-configured ones (overpass/searxng),
    plus Bluesky when the campaign opts in. De-duped, order preserved."""
    backends = list(config.discovery_backends())
    if campaign.get("use_bluesky") and "bluesky" not in backends:
        backends.append("bluesky")
    return backends


def backend_can_handle(backend: str, clause: dict) -> bool:
    """Overpass needs a place to search within; SearXNG and Bluesky search by keyword and can
    run any clause."""
    if backend == "overpass":
        return bool(clause.get("location"))
    return backend in ("searxng", "bluesky")


def next_query(campaign_id: str) -> tuple[dict, str] | None:
    """(clauses, query_id) for the next discovery query, or None when the frontier is
    exhausted. Backend selection happens at discovery time (every enabled backend that can
    handle the clause runs it), so this just picks *which clause* to chase next."""
    pool = synthesize(campaign_id)
    if not pool:
        return None

    campaign = db.get_campaign(campaign_id)
    enabled = enabled_backends(campaign) if campaign else config.discovery_backends()
    already = db.query_keys(campaign_id)
    empties = db.recorded_empties(campaign_id)

    candidates = [
        c
        for c in _candidate_clause_sets(pool)
        if clause_key(c) not in already
        and not _pruned_by_empty(c, empties)
        and any(backend_can_handle(b, c) for b in enabled)
    ]
    if not candidates:
        return None

    qual = qualifier.load(campaign) if campaign else None
    if qual is not None and qual.is_fitted:
        # Score each candidate by its clause terms alone (keyword injection makes the GP able
        # to judge a never-run query), explore/exploit by current label balance.
        labeled = db.labeled_leads(campaign_id)
        n_pos = sum(1 for l in labeled if l["label"] == "positive")
        n_neg = sum(1 for l in labeled if l["label"] == "negative")
        mode = "exploit" if n_neg > n_pos else "explore"
        X = embedder.embed_many([clause_terms(c) for c in candidates])
        scores = qual.acquisition_scores(X, mode)
        chosen = candidates[int(scores.argmax())]
    else:
        chosen = candidates[0]  # cold start: seed-first

    query = db.upsert_query(campaign_id, ",".join(enabled), clause_key(chosen), chosen)
    return chosen, query["id"]
