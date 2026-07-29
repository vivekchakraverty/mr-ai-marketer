"""Bridge to the app's Email Writer — turns a lead + campaign into a drafted outreach email.

The Email Writer (a fine-tuned marketing-email model on the user's free HF Space, plus a CTR
predictor) is injected as a `draft_writer` callable by the FastAPI layer at startup, so this
vendored package stays decoupled from `app.*` and is trivially testable with a fake writer.

Flow: prompts.outreach builds a freeform `instruction` -> draft_writer(instruction) returns
the finished email text + CTR estimate -> we split subject/body and hand back a draft dict.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from .. import config, db
from ..prompts import outreach

# Signature: instruction -> {"text": str, "predictedClickRate": float, "ctrBucket": str}
DraftWriter = Callable[[str], dict]

_draft_writer: Optional[DraftWriter] = None


def set_draft_writer(fn: DraftWriter) -> None:
    """Injected once by app/routers/leadgen.py (the Email Writer service). Tests inject a fake."""
    global _draft_writer
    _draft_writer = fn


def _require_writer() -> DraftWriter:
    if _draft_writer is None:
        raise RuntimeError(
            "No email draft writer configured — the Email Writer service was not injected."
        )
    return _draft_writer


def _campaign_ctx(campaign: dict) -> dict[str, Any]:
    """Assemble the fields the outreach prompts want from a campaign row + config."""
    pool = json.loads(campaign["clauses"]) if campaign.get("clauses") else {}
    return {
        "name": campaign.get("name", ""),
        "from_name": config.current("MAILBOX_FROM_NAME") or campaign.get("name", ""),
        "product_description": campaign.get("product_description", ""),
        "objective": campaign.get("objective", ""),
        "value_prop": pool.get("value_prop", ""),
    }


def _finish(instruction: str) -> dict:
    result = _require_writer()(instruction)
    subject, body = outreach.split_subject_body(result.get("text", ""))
    return {
        "subject": subject,
        "body": body,
        "predictedClickRate": result.get("predictedClickRate"),
        "ctrBucket": result.get("ctrBucket"),
    }


def write_opener(campaign: dict, lead: dict) -> dict:
    instruction = outreach.opener_instruction(_campaign_ctx(campaign), lead)
    return _finish(instruction)


def write_followup(campaign: dict, lead: dict, deal_id: str, intent: str) -> dict:
    thread = db.thread_for_deal(deal_id)
    instruction = outreach.followup_instruction(_campaign_ctx(campaign), lead, thread, intent)
    return _finish(instruction)
