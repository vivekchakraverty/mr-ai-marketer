"""Outreach draft assembly (via a fake Email Writer), subject/body split, and the agentic
reply triage (follow-up decision prompt + unsubscribe ingest)."""

from vendor.leadgen.email import inbox, writer
from vendor.leadgen.prompts import outreach, reasoning
from vendor.leadgen.tasks import follow_up


def test_split_subject_body():
    assert outreach.split_subject_body("Subject: Hi there\n\nBody line") == ("Hi there", "Body line")
    assert outreach.split_subject_body("Quick question\n\nsome body") == ("Quick question", "some body")


def test_opener_instruction_carries_hook_and_rules():
    campaign = {"name": "Us", "from_name": "Jamie", "value_prop": "we fill empty slots", "objective": "book a call"}
    lead = {"company": "Bright Dental", "contact_name": "Dr. Lee", "profile_text": "a busy clinic"}
    instr = outreach.opener_instruction(campaign, lead)
    assert "Bright Dental" in instr
    assert "we fill empty slots" in instr
    assert "unsubscribe" in instr.lower()  # compliance rule is present


def test_write_opener_uses_injected_writer(lg_db):
    writer.set_draft_writer(
        lambda instruction: {"text": "Subject: Hey\n\nHello. unsubscribe to stop.", "predictedClickRate": 0.2, "ctrBucket": "strong"}
    )
    c = lg_db.create_campaign("c", "we sell things")
    lead = {"company": "Acme", "contact_name": None, "profile_text": "x"}
    result = writer.write_opener(lg_db.get_campaign(c["id"]), lead)
    assert result["subject"] == "Hey"
    assert result["predictedClickRate"] == 0.2
    assert result["ctrBucket"] == "strong"


def test_follow_up_decision_prompt_shape():
    prompt = reasoning.follow_up_decision("we sell X", [{"direction": "out", "subject": "s", "body": "b"}], "sure, tell me more")
    for token in ("send_message", "wait", "mark_completed", "unsubscribe"):
        assert token in prompt


def test_unsubscribe_reply_suppresses_and_closes(lg_db, monkeypatch):
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="b.com", company="B", email="them@b.com", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    # We sent them a message; a reply references its Message-ID.
    lg_db.add_chat_message(deal["id"], "out", "Hi", "body", message_id="<sent-1@us>")

    monkeypatch.setattr(
        inbox,
        "fetch_recent",
        lambda limit=30: [
            {
                "from": "them@b.com",
                "subject": "please stop",
                "message_id": "<reply-1@them>",
                "in_reply_to": "<sent-1@us>",
                "references": "<sent-1@us>",
                "body": "Please unsubscribe me from this list.",
            }
        ],
    )

    acted = follow_up._ingest_replies(c["id"])
    assert acted is True
    assert lg_db.is_suppressed("them@b.com") is True
    assert lg_db.get_deal(deal["id"])["state"] == lg_db.STATE_COMPLETED
    assert lg_db.get_deal(deal["id"])["outcome"] == "unsubscribe"
