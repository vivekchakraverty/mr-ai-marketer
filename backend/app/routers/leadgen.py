"""Lead Gen Agent — HTTP surface over the vendored `leadgen` pipeline.

Thin translation layer only: it lazy-imports the vendored package (which pulls in
sentence-transformers/torch, so importing at module scope would slow every backend start),
shapes rows into Pydantic responses, and drives the daemon on demand. All real logic lives
in vendor/leadgen. Mirrors social_post.py's `_spg()` pattern.

The CRM read surface (deals, threads, stats) is consumed by the Analytics screen; the
operate surface (campaigns, drafts, suppression, run-once) by the Research/Strategy tool.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/leadgen", tags=["leadgen"])


# --- lazy imports -----------------------------------------------------------
def _lg():
    from vendor.leadgen import config as lg_config
    from vendor.leadgen import daemon as lg_daemon
    from vendor.leadgen import db as lg_db

    return lg_db, lg_config, lg_daemon


def ensure_draft_writer() -> None:
    """Wire the app's Email Writer into the vendored package's draft writer (idempotent)."""
    from vendor.leadgen.email import writer as lg_writer

    from ..services import email_writer as email_writer_service

    lg_writer.set_draft_writer(email_writer_service.generate_marketing_email)


def ensure_tracking_hooks() -> None:
    """Wire the app's mail-tracking service into the vendored package (idempotent) —
    same safety net as ensure_draft_writer() above, for a code path invoked
    without the FastAPI startup event ever having run."""
    from vendor.leadgen.email import tracking as lg_tracking

    from ..services import mail_tracking as mail_tracking_service

    lg_tracking.set_tracking_hooks(
        prepare=mail_tracking_service.prepare_for_leadgen,
        finalize=mail_tracking_service.finalize_for_leadgen,
        bounce=mail_tracking_service.record_bounce_for_leadgen,
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    llmBackend: str
    llmModel: str
    llmConfigured: bool
    smtpConfigured: bool
    imapConfigured: bool
    discoveryBackends: list[str]
    reacherUrl: str
    searxngUrl: str
    campaigns: int
    activeCampaigns: int


class CampaignRequest(BaseModel):
    name: str
    productDescription: str
    objective: str = ""
    country: str = ""
    dailyCap: int = 20
    autoSend: bool = False
    useBluesky: bool = False


class CampaignPatch(BaseModel):
    active: bool | None = None
    autoSend: bool | None = None
    useBluesky: bool | None = None
    dailyCap: int | None = None
    activeHoursStart: int | None = None
    activeHoursEnd: int | None = None


class SuppressionRequest(BaseModel):
    email: str
    reason: str = "manual"


class DraftEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


def _campaign_out(c: dict) -> dict[str, Any]:
    return {
        "id": c["id"],
        "name": c["name"],
        "productDescription": c["product_description"],
        "objective": c["objective"],
        "country": c["country"],
        "active": bool(c["active"]),
        "autoSend": bool(c["auto_send"]),
        "useBluesky": bool(c["use_bluesky"]),
        "dailyCap": c["daily_cap"],
        "activeHoursStart": c["active_hours_start"],
        "activeHoursEnd": c["active_hours_end"],
        "createdAt": c["created_at"],
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    lg_db, lg_config, _ = _lg()
    from vendor.leadgen.email import inbox, sender

    llm_ok = lg_config.current("LLM_BACKEND") == "ollama" or bool(
        (os.environ.get("HF_TOKEN") or "").strip()
    )
    campaigns = lg_db.list_campaigns()
    return StatusResponse(
        llmBackend=lg_config.current("LLM_BACKEND"),
        llmModel=lg_config.current("LLM_MODEL"),
        llmConfigured=llm_ok,
        smtpConfigured=sender.configured(),
        imapConfigured=inbox.configured(),
        discoveryBackends=lg_config.discovery_backends(),
        reacherUrl=lg_config.reacher_url(),
        searxngUrl=lg_config.searxng_url(),
        campaigns=len(campaigns),
        activeCampaigns=sum(1 for c in campaigns if c["active"]),
    )


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


@router.get("/campaigns")
def list_campaigns() -> list[dict]:
    lg_db, _, _ = _lg()
    return [_campaign_out(c) for c in lg_db.list_campaigns()]


@router.post("/campaigns")
def create_campaign(body: CampaignRequest) -> dict:
    lg_db, _, _ = _lg()
    if not body.productDescription.strip():
        raise HTTPException(status_code=400, detail="A product/service description is required.")
    c = lg_db.create_campaign(
        name=body.name.strip() or "Untitled campaign",
        product_description=body.productDescription.strip(),
        objective=body.objective.strip(),
        country=body.country.strip(),
        daily_cap=body.dailyCap,
        auto_send=body.autoSend,
        use_bluesky=body.useBluesky,
    )
    return _campaign_out(c)


@router.patch("/campaigns/{campaign_id}")
def patch_campaign(campaign_id: str, body: CampaignPatch) -> dict:
    lg_db, _, _ = _lg()
    if not lg_db.get_campaign(campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found.")
    fields: dict[str, Any] = {}
    if body.active is not None:
        fields["active"] = 1 if body.active else 0
    if body.autoSend is not None:
        fields["auto_send"] = 1 if body.autoSend else 0
    if body.useBluesky is not None:
        fields["use_bluesky"] = 1 if body.useBluesky else 0
    if body.dailyCap is not None:
        fields["daily_cap"] = body.dailyCap
    if body.activeHoursStart is not None:
        fields["active_hours_start"] = body.activeHoursStart
    if body.activeHoursEnd is not None:
        fields["active_hours_end"] = body.activeHoursEnd
    return _campaign_out(lg_db.update_campaign(campaign_id, **fields))


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str) -> dict:
    lg_db, _, _ = _lg()
    lg_db.delete_campaign(campaign_id)
    return {"deleted": True}


@router.post("/campaigns/{campaign_id}/run-once")
def run_once(campaign_id: str, maxSteps: int = 10) -> dict:
    """Drive one campaign forward synchronously (bounded) — a manual 'Run now'. The
    background scheduler does this automatically too.

    The daemon deliberately swallows per-handler errors so one bad tick can't kill the loop —
    which means a misconfigured token or an unreachable service looks like "nothing happened".
    So when a pass does zero work, we run the relevant service checks and report the real
    reason back, instead of leaving the user staring at an empty pipeline.
    """
    lg_db, lg_config, lg_daemon = _lg()
    if not lg_db.get_campaign(campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found.")
    ensure_draft_writer()
    ensure_tracking_hooks()
    steps = lg_daemon.run_campaign_until_idle(campaign_id, max_steps=maxSteps)

    diagnostic: str | None = None
    if steps == 0:
        # Discovery needs the reasoning LLM + at least one working discovery backend. Check the
        # ones this campaign actually uses (Bluesky included when it's on); report the first
        # that's down.
        from vendor.leadgen.discovery import icp as lg_icp

        campaign = lg_db.get_campaign(campaign_id)
        backends = lg_icp.enabled_backends(campaign)
        targets = ["llm"] + [b for b in backends if b in lg_config.VERIFIERS]
        for name in targets:
            ok, detail = lg_config.VERIFIERS[name]()
            if not ok:
                diagnostic = f"{name}: {detail}"
                break
        if diagnostic is None and not campaign.get("use_bluesky"):
            # Everything's healthy but nothing matched — for social-first audiences (indie
            # devs, creators) the directory/web backends legitimately come up empty. Nudge
            # toward Bluesky.
            diagnostic = (
                "no match — your backends (map + web) only find businesses with a website. "
                "For a social audience, turn on Bluesky search for this campaign."
            )

    return {"steps": steps, "states": lg_db.deal_state_counts(campaign_id), "diagnostic": diagnostic}


@router.get("/campaigns/{campaign_id}/stats")
def stats(campaign_id: str) -> dict:
    lg_db, _, _ = _lg()
    return {"states": lg_db.deal_state_counts(campaign_id), "sentToday": lg_db.sent_today(campaign_id)}


# ---------------------------------------------------------------------------
# Deals / CRM (read surface for Analytics)
# ---------------------------------------------------------------------------


def _lead_out(lead: dict | None) -> dict | None:
    """Strip internal ML fields before a lead crosses the API boundary. `embedding` is a raw
    float32 BLOB (Python bytes) — Pydantic's JSON encoder treats bytes as UTF-8 text and
    hard-crashes (PydanticSerializationError) on non-UTF-8 embedding bytes, which is exactly
    what get_lead()'s SELECT * pulls in. Nothing in the UI needs the vector itself."""
    if lead is None:
        return None
    return {k: v for k, v in lead.items() if k != "embedding"}


@router.get("/campaigns/{campaign_id}/deals")
def list_deals(campaign_id: str) -> list[dict]:
    lg_db, _, _ = _lg()
    return lg_db.list_deals_with_leads(campaign_id)


@router.get("/deals/{deal_id}")
def deal_detail(deal_id: str) -> dict:
    lg_db, _, _ = _lg()
    deal = lg_db.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found.")
    lead = lg_db.get_lead(deal["lead_id"])
    return {"deal": deal, "lead": _lead_out(lead), "thread": lg_db.thread_for_deal(deal_id)}


# ---------------------------------------------------------------------------
# Review queue (operate surface for Research)
# ---------------------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/drafts")
def list_drafts(campaign_id: str) -> list[dict]:
    lg_db, _, _ = _lg()
    return lg_db.list_drafts(campaign_id, status="draft")


@router.patch("/drafts/{draft_id}")
def edit_draft(draft_id: str, body: DraftEdit) -> dict:
    lg_db, _, _ = _lg()
    fields = {k: v for k, v in {"subject": body.subject, "body": body.body}.items() if v is not None}
    updated = lg_db.update_draft(draft_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return updated


@router.post("/drafts/{draft_id}/approve-send")
def approve_send(draft_id: str) -> dict:
    from vendor.leadgen.tasks import common

    ensure_draft_writer()
    ensure_tracking_hooks()
    sent, detail = common.deliver_draft(draft_id)
    if not sent:
        raise HTTPException(status_code=400, detail=detail)
    return {"sent": True, "detail": detail}


@router.post("/drafts/{draft_id}/discard")
def discard_draft(draft_id: str) -> dict:
    lg_db, _, _ = _lg()
    lg_db.update_draft(draft_id, status="discarded")
    return {"discarded": True}


# ---------------------------------------------------------------------------
# Suppression list
# ---------------------------------------------------------------------------


@router.get("/suppression")
def get_suppression() -> list[dict]:
    lg_db, _, _ = _lg()
    return lg_db.list_suppression()


@router.post("/suppression")
def add_suppression(body: SuppressionRequest) -> dict:
    lg_db, _, _ = _lg()
    if "@" not in body.email:
        raise HTTPException(status_code=400, detail="Not a valid email address.")
    lg_db.add_suppression(body.email, body.reason)
    return {"added": body.email.strip().lower()}
