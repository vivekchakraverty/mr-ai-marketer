"""follow_up — the agentic reply loop.

Three things per tick: ingest new IMAP replies and attach them to their deals; run the
follow-up *decision* (one structured LLM call) on the next due deal and act on it (draft a
threaded reply / wait / mark completed); and, in auto-send mode, deliver pending follow-up
drafts within today's headroom. The reply *text* is written by the Email Writer, not the
decision call — the decision only chooses the action and the intent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .. import db, llm
from ..email import inbox, tracking, writer
from ..prompts import reasoning
from .common import deliver_pending

log = logging.getLogger(__name__)

_UNSUB_HINTS = ("unsubscribe", "opt out", "opt-out", "stop emailing", "remove me", "take me off")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matched_deal(campaign_id: str, reply: dict) -> str | None:
    ref_ids = [reply.get("in_reply_to", "")] + (reply.get("references", "") or "").split()
    for rid in ref_ids:
        rid = rid.strip()
        if rid:
            deal_id = db.deal_id_for_sent_message(rid)
            if deal_id:
                deal = db.get_deal(deal_id)
                if deal and deal["campaign_id"] == campaign_id:
                    return deal_id
    return None


def _ingest_replies(campaign_id: str) -> bool:
    acted = False
    for reply in inbox.fetch_recent():
        if db.inbound_exists(reply.get("message_id", "")):
            continue
        if tracking.handle_possible_bounce(reply):
            # DSN bounces essentially never carry In-Reply-To/References pointing
            # at our Message-ID (that's a human-reply threading convention;
            # bounce-generating MTAs don't populate it), so without this they'd
            # silently fall through _matched_deal returning None and get
            # discarded exactly as they were before this feature existed.
            continue
        deal_id = _matched_deal(campaign_id, reply)
        if not deal_id:
            continue
        db.add_chat_message(
            deal_id,
            "in",
            reply.get("subject", ""),
            reply.get("body", ""),
            message_id=reply.get("message_id"),
            in_reply_to=reply.get("in_reply_to"),
            refs=reply.get("references"),
        )
        blob = f"{reply.get('subject','')} {reply.get('body','')}".lower()
        if any(h in blob for h in _UNSUB_HINTS):
            deal = db.get_deal(deal_id)
            lead = db.get_lead(deal["lead_id"]) if deal else None
            if lead and lead.get("email"):
                db.add_suppression(lead["email"], "reply: unsubscribe")
            db.update_deal(deal_id, state=db.STATE_COMPLETED, outcome="unsubscribe", next_follow_up_at=None)
        else:
            # A reply is due for a decision now.
            db.update_deal(deal_id, state=db.STATE_REPLIED, next_follow_up_at=_now_iso())
        acted = True
    return acted


def _handle_one_due(campaign_id: str) -> bool:
    due = db.due_followups(campaign_id, _now_iso(), limit=1)
    if not due:
        return False
    deal = due[0]
    campaign = db.get_campaign(campaign_id)
    lead = db.get_lead(deal["lead_id"])
    thread = db.thread_for_deal(deal["id"])
    latest_in = next((m["body"] for m in reversed(thread) if m["direction"] == "in"), "")

    try:
        decision = llm.structured(
            reasoning.follow_up_decision(campaign["product_description"], thread, latest_in),
            max_output_tokens=300,
        )
    except llm.LLMError as err:
        log.warning("[leadgen] follow-up decision failed: %s", err)
        return False

    action = (decision or {}).get("action", "wait")
    if action == "mark_completed":
        outcome = decision.get("outcome")
        if outcome == "unsubscribe" and lead and lead.get("email"):
            db.add_suppression(lead["email"], "agent: unsubscribe")
        db.update_deal(deal["id"], state=db.STATE_COMPLETED, outcome=outcome, next_follow_up_at=None)
    elif action == "send_message":
        intent = decision.get("intent", "")
        try:
            result = writer.write_followup(campaign, lead, deal["id"], intent)
        except Exception as err:  # noqa: BLE001 — Email Writer failure: retry next tick
            log.warning("[leadgen] follow-up draft failed: %s", err)
            return True
        db.add_draft(
            deal["id"],
            "follow_up",
            result["subject"],
            result["body"],
            predicted_click_rate=result.get("predictedClickRate"),
            ctr_bucket=result.get("ctrBucket"),
        )
        # Park the clock until the draft is delivered (or a new reply arrives).
        db.update_deal(deal["id"], next_follow_up_at=None)
    else:  # wait
        hours = int(decision.get("wait_hours") or 48)
        db.update_deal(
            deal["id"],
            next_follow_up_at=(datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
        )
    return True


def run(campaign_id: str) -> bool:
    campaign = db.get_campaign(campaign_id)
    did = _ingest_replies(campaign_id)
    did = _handle_one_due(campaign_id) or did
    if campaign and campaign["auto_send"]:
        did = deliver_pending(campaign, "follow_up") or did
    return did
