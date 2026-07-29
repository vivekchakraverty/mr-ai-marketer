"""Shared helpers for the task handlers: send-capacity gating and the single delivery path
that both auto-send and manual approval funnel through (so there is exactly one place that
touches SMTP, checks suppression, records the ChatMessage, and advances the deal)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .. import db
from ..email import sender, tracking

log = logging.getLogger(__name__)

FOLLOW_UP_DELAY_HOURS = 72  # default gap before the first follow-up if the model gives none


def _now() -> datetime:
    return datetime.now(timezone.utc)


def within_active_hours(campaign: dict, now: datetime | None = None) -> bool:
    """Whether autonomous sending is allowed for this campaign right now (local time)."""
    now = now or datetime.now().astimezone()
    start = int(campaign.get("active_hours_start", 9))
    end = int(campaign.get("active_hours_end", 18))
    hour = now.hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps past midnight (e.g. 22 -> 6)


def send_headroom(campaign: dict) -> int:
    """How many more emails may be SENT today for this campaign."""
    return int(campaign["daily_cap"]) - db.sent_today(campaign["id"])


def find_headroom(campaign: dict) -> int:
    """How many more emails may be RESOLVED today — send cap minus what's already sent and
    minus deals already holding capacity (finding/ready/drafted). This rations paid-style
    work to real send capacity, exactly like OpenOutreach's find-email gate."""
    return send_headroom(campaign) - db.in_flight_email_count(campaign["id"])


def deliver_draft(draft_id: str, follow_up_hours: int = FOLLOW_UP_DELAY_HOURS) -> tuple[bool, str]:
    """Send a drafted email over SMTP and advance its deal. The one send path.

    Returns (sent, detail). Refuses (without raising) on suppression or a missing address so
    callers — the auto-send handler and the manual approve-send endpoint alike — can surface
    a clean message.
    """
    draft = db.get_draft(draft_id)
    if not draft or draft["status"] not in ("draft", "approved"):
        return False, "Draft not found or already handled."

    deal = db.get_deal(draft["deal_id"])
    if not deal:
        return False, "Deal not found."
    lead = db.get_lead(deal["lead_id"])
    to_email = (lead or {}).get("email")
    if not to_email:
        db.update_draft(draft_id, status="discarded")
        return False, "No verified address for this lead."
    if db.is_suppressed(to_email):
        db.update_draft(draft_id, status="discarded")
        db.update_deal(deal["id"], state=db.STATE_FAILED, reason="suppressed", outcome="unsubscribe")
        return False, f"{to_email} is suppressed — not sent."

    # Thread a follow-up onto the prospect's most recent inbound message.
    in_reply_to = None
    if draft["kind"] == "follow_up":
        for m in reversed(db.thread_for_deal(deal["id"])):
            if m["direction"] == "in" and m["message_id"]:
                in_reply_to = m["message_id"]
                break

    prepared = tracking.prepare(to_email, draft["subject"], draft["body"], deal["id"])
    mail_message_id, html_body = prepared if prepared else (None, None)

    try:
        message_id = sender.send(
            to_email, draft["subject"], draft["body"], in_reply_to=in_reply_to, html_body=html_body
        )
    except sender.SendError as err:
        status = "bounced" if getattr(err, "refused", None) else "failed"
        tracking.finalize(mail_message_id, None, status, str(err))
        return False, str(err)

    tracking.finalize(mail_message_id, message_id, "sent")
    db.add_chat_message(
        deal["id"], "out", draft["subject"], draft["body"], message_id=message_id, in_reply_to=in_reply_to
    )
    db.update_draft(draft_id, status="sent", sent_at=_now().isoformat())
    next_at = (_now() + timedelta(hours=follow_up_hours)).isoformat()
    db.update_deal(deal["id"], state=db.STATE_EMAILED, next_follow_up_at=next_at)
    log.info("[leadgen] delivered %s draft for deal %s", draft["kind"], deal["id"][:8])
    return True, "sent"


def deliver_pending(campaign: dict, kind: str) -> bool:
    """Auto-send path: deliver pending drafts of a kind, up to today's send headroom and only
    within the campaign's active-hours window. Returns True if anything was sent."""
    if not within_active_hours(campaign):
        return False
    sent_any = False
    for draft in db.list_drafts(campaign["id"], status="draft"):
        if draft["kind"] != kind:
            continue
        if send_headroom(campaign) <= 0:
            break
        sent, _ = deliver_draft(draft["id"])
        sent_any = sent_any or sent
    return sent_any
