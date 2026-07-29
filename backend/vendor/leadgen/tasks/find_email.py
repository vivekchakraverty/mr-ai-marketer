"""find_email — resolve and verify a work address for the next qualified lead.

Send-gated: only runs while today's send capacity has room for the result (so we never
resolve more addresses than we could actually email), mirroring OpenOutreach. Reacher is
local and synchronous, so there's no separate async poll leg — candidates are verified
inline and the deal advances to READY_TO_EMAIL or FAILED in one step.
"""

from __future__ import annotations

import logging

from .. import db
from ..email import verify
from .common import find_headroom

log = logging.getLogger(__name__)


def run(campaign_id: str) -> bool:
    campaign = db.get_campaign(campaign_id)
    if not campaign or find_headroom(campaign) <= 0:
        return False

    ready = db.deals_in_state(campaign_id, db.STATE_QUALIFIED, limit=1)
    if not ready:
        return False
    deal = ready[0]
    lead = db.get_lead(deal["lead_id"])
    if not lead:
        return False

    db.update_deal(deal["id"], state=db.STATE_FINDING_EMAIL)
    known = db.domain_email_patterns(campaign_id, lead.get("domain") or "")
    address, note = verify.find_and_verify(
        lead.get("contact_name"),
        lead.get("domain") or "",
        known_localparts=known,
        scraped_email=lead.get("email"),
    )

    if address:
        source = "scraped" if lead.get("email") and address == lead["email"].lower() else "guessed"
        db.update_lead(lead["id"], email=address, email_source=source)
        db.update_deal(deal["id"], state=db.STATE_READY_TO_EMAIL, reason=note)
        log.info("[leadgen] resolved %s for %s (%s)", address, lead.get("company"), source)
    else:
        db.update_deal(deal["id"], state=db.STATE_FAILED, reason=note, outcome="no_email")
    return True
