"""The autonomous worker — runs the pipeline for every active campaign.

Per active campaign, one tick tries the handlers in opportunity-cost priority order
(follow_up > find_email > email > discover_qualify) and performs the first that has work to
do — so the highest-value action always goes first, but a tick never busy-loops.

The active-hours guard lives in the delivery path (tasks.common), not here: discovery,
qualification, email-finding, and *drafting* run any time (they send nothing), while the one
thing that actually reaches a prospect — autonomous auto-send — is held to the campaign's
configured local window. Manual approve-send from the UI is never time-gated. State is fully
in the DB, so a stop/restart resumes cleanly.
"""

from __future__ import annotations

import logging

from . import db, tasks

log = logging.getLogger(__name__)


def tick_campaign(campaign: dict) -> bool:
    """Do at most one unit of work for a campaign. Returns True if something happened."""
    for name in tasks.PRIORITY:
        try:
            if tasks.HANDLERS[name](campaign["id"]):
                return True
        except Exception:  # noqa: BLE001 — one failing handler must not stop the loop
            log.exception("[leadgen] handler %s failed for campaign %s", name, campaign["id"][:8])
    return False


def tick() -> int:
    """One pass over all active campaigns. Returns how many did work."""
    worked = 0
    for campaign in db.active_campaigns():
        if tick_campaign(campaign):
            worked += 1
    return worked


def run_campaign_until_idle(campaign_id: str, max_steps: int = 200) -> int:
    """Drive a single campaign forward until it has no more work (or a safety cap). Used by
    the CLI `run-once` and by tests. Returns the number of steps that did work."""
    steps = 0
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        return 0
    while steps < max_steps and tick_campaign(campaign):
        steps += 1
        campaign = db.get_campaign(campaign_id)  # refresh (auto_send/state may have changed)
        if not campaign:
            break
    return steps
