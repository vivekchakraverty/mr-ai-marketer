"""email — draft a personalized opener for the next lead with a verified address.

Drafting always happens (it isn't gated by the send cap); the draft lands in the review
queue at state DRAFTED. Sending is the gated, human-approved step: in review mode nothing
leaves until approved in the UI; in auto-send mode this handler also delivers pending opener
drafts up to today's headroom. Either way the opener is written by the Email Writer.
"""

from __future__ import annotations

import logging

from .. import db
from ..email import writer
from .common import deliver_pending

log = logging.getLogger(__name__)


def _draft_opener(campaign: dict, lead: dict, deal: dict) -> bool:
    try:
        result = writer.write_opener(campaign, lead)
    except Exception as err:  # noqa: BLE001 — Email Writer/Space failure: retry next tick
        log.warning("[leadgen] opener draft failed for %s: %s", lead.get("company"), err)
        return False
    db.add_draft(
        deal["id"],
        "opener",
        result["subject"],
        result["body"],
        predicted_click_rate=result.get("predictedClickRate"),
        ctr_bucket=result.get("ctrBucket"),
    )
    db.update_deal(deal["id"], state=db.STATE_DRAFTED)
    log.info("[leadgen] drafted opener for %s", lead.get("company"))
    return True


def run(campaign_id: str) -> bool:
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return False
    did = False

    ready = db.deals_in_state(campaign_id, db.STATE_READY_TO_EMAIL, limit=1)
    if ready:
        deal = ready[0]
        lead = db.get_lead(deal["lead_id"])
        address = (lead or {}).get("email")
        if not address:
            db.update_deal(deal["id"], state=db.STATE_FAILED, reason="no address")
            did = True
        elif db.is_suppressed(address):
            db.update_deal(deal["id"], state=db.STATE_FAILED, reason="suppressed", outcome="unsubscribe")
            did = True
        else:
            did = _draft_opener(campaign, lead, deal) or did

    if campaign["auto_send"]:
        did = deliver_pending(campaign, "opener") or did
    return did
