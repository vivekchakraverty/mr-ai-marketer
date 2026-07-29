"""Regression test for a real bug: GET /leadgen/deals/{id} returned the lead row straight
from `SELECT *`, which includes `leads.embedding` — a raw float32 BLOB (Python bytes).
FastAPI/Pydantic treats bytes as UTF-8 text when encoding a plain dict response and hard-
crashes on non-UTF-8 bytes, so opening the Analytics lead detail drawer 500'd and the
frontend's silent .catch() left it spinning on "Loading…" forever.

Calls the router handler directly (not via TestClient/app boot) — booting the full app
pulls in every other router's on_startup() work (RAG index, guest-post catalog, ...), which
is slow and irrelevant to what's being tested here.
"""

import json

import numpy as np
from fastapi.encoders import jsonable_encoder

from app.routers import leadgen as leadgen_router
from vendor.leadgen.ml import embedder


def _lead_with_embedding(db, campaign_id):
    return db.upsert_lead(
        campaign_id,
        domain="x.com",
        company="X Co",
        contact_name="Jane",
        profile_text="p",
        embedding=embedder.to_blob(np.ones(embedder.DIM, dtype=np.float32)),
    )


def test_deal_detail_strips_embedding_and_is_json_encodable(lg_db):
    c = lg_db.create_campaign("Repro", "we sell widgets")
    lead = _lead_with_embedding(lg_db, c["id"])
    deal = lg_db.create_deal(c["id"], lead["id"])
    lg_db.add_chat_message(deal["id"], "out", "Hi", "body", message_id="<m1@x>")

    result = leadgen_router.deal_detail(deal["id"])

    assert "embedding" not in result["lead"]
    assert result["lead"]["company"] == "X Co"
    assert len(result["thread"]) == 1

    # The actual failure mode: this must not raise.
    json.dumps(jsonable_encoder(result))


def test_lead_out_strips_embedding_but_keeps_everything_else(lg_db):
    c = lg_db.create_campaign("Repro2", "we sell widgets")
    lead = _lead_with_embedding(lg_db, c["id"])

    raw = lg_db.get_lead(lead["id"])
    assert "embedding" in raw  # sanity: the bug's precondition really exists

    cleaned = leadgen_router._lead_out(raw)
    assert "embedding" not in cleaned
    assert cleaned["company"] == raw["company"]
    assert cleaned["domain"] == raw["domain"]


def test_lead_out_handles_none():
    assert leadgen_router._lead_out(None) is None
