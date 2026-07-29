"""discover_qualify — one step of the discovery + active-learning loop.

Per tick: top up the unlabeled pool with one discovery query when it's running low, have the
LLM label the single most informative unlabeled lead (the active-learning oracle), retrain
the GP, then promote any unlabeled leads the GP is now confident about — advancing them to
email-finding without spending an LLM call (consume mode).
"""

from __future__ import annotations

import logging

from .. import db, llm
from ..discovery import bluesky, enrich, icp, overpass, searxng
from ..discovery.base import clause_terms
from ..ml import embedder, qualifier
from ..prompts import reasoning

log = logging.getLogger(__name__)

# Keep at least this many unlabeled leads on hand so the qualifier always has candidates.
_MIN_UNLABELED = 8
_BACKENDS = {"overpass": overpass, "searxng": searxng, "bluesky": bluesky}


def _discover_page(campaign_id: str) -> int:
    """Run the next selected discovery query across every enabled backend, merge + enrich +
    embed the results, and store new leads. Returns how many new leads were added."""
    selection = icp.next_query(campaign_id)
    if not selection:
        return 0
    clauses, query_id = selection

    campaign = db.get_campaign(campaign_id)
    raws = []
    seen_domains: set[str] = set()
    for backend_name in icp.enabled_backends(campaign):
        if not icp.backend_can_handle(backend_name, clauses):
            continue
        for raw in _BACKENDS[backend_name].search(clauses):
            if raw.domain and raw.domain not in seen_domains:
                seen_domains.add(raw.domain)
                raws.append(raw)

    if not raws:
        db.mark_query(query_id, status="empty")  # anti-monotone: this clause set matched nobody
        return 0

    terms = clause_terms(clauses)
    added = 0
    for raw in raws:
        raw = enrich.enrich(raw)
        vec = embedder.embed(embedder.inject_keywords(raw.profile_text, terms))
        lead = db.upsert_lead(
            campaign_id,
            domain=raw.domain,
            company=raw.company,
            contact_name=raw.contact_name,
            website=raw.website,
            email=raw.email,
            email_source=raw.extra.get("email_source") if raw.email else None,
            region=raw.region,
            source=raw.source,
            source_url=raw.source_url,
            profile_text=raw.profile_text,
            embedding=embedder.to_blob(vec),
            discovered_by_query_id=query_id,
        )
        if lead:  # None means deduped away
            db.create_deal(campaign_id, lead["id"], state=db.STATE_NEW)
            added += 1
    db.mark_query(query_id, status="exhausted")
    log.info("[leadgen] discovered %d new leads for '%s'", added, terms)
    return added


def _qualify_one(campaign_id: str) -> bool:
    """LLM-label the most informative unlabeled lead. Returns True if one was labeled."""
    lead = qualifier.select_next_to_qualify(campaign_id)
    if not lead:
        return False

    campaign = db.get_campaign(campaign_id)
    prompt = reasoning.qualify_lead(
        campaign["product_description"], campaign.get("objective", ""), lead["profile_text"]
    )
    try:
        result = llm.structured(prompt, max_output_tokens=200)
    except llm.LLMError as err:
        log.warning("[leadgen] qualification LLM call failed: %s", err)
        return False

    label, reason = reasoning.parse_qualify(result)
    db.update_lead(lead["id"], label=label, gp_score=1.0 if label == "positive" else 0.0)

    deal = db.deal_for_lead(lead["id"])
    if deal:
        if label == "positive":
            db.update_deal(deal["id"], state=db.STATE_QUALIFIED, reason=reason)
        else:
            db.update_deal(deal["id"], state=db.STATE_FAILED, outcome="wrong_fit", reason=reason)
    return True


def _promote_confident(campaign_id: str) -> int:
    """Advance GP-confident unlabeled leads to QUALIFIED without an LLM call."""
    promoted = 0
    for lead, score in qualifier.confident_unlabeled(campaign_id):
        deal = db.deal_for_lead(lead["id"])
        if deal and deal["state"] == db.STATE_NEW:
            db.update_lead(lead["id"], gp_score=score)
            db.update_deal(deal["id"], state=db.STATE_QUALIFIED, reason="GP-confident fit")
            promoted += 1
    if promoted:
        log.info("[leadgen] promoted %d GP-confident leads to QUALIFIED", promoted)
    return promoted


def run(campaign_id: str) -> bool:
    """One discovery+qualification step. Returns True if it did meaningful work."""
    did = False
    if len(db.unlabeled_leads(campaign_id)) < _MIN_UNLABELED:
        did = _discover_page(campaign_id) > 0 or did
    did = _qualify_one(campaign_id) or did
    qualifier.train_and_persist(campaign_id)
    did = _promote_confident(campaign_id) > 0 or did
    return did
