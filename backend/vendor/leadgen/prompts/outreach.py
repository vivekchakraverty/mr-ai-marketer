"""Outreach instruction templates — the freeform `instruction` string handed to the app's
Email Writer Space, which writes the actual email.

The Email Writer is a fine-tune on ~10.5k *marketing* emails (promotional, blast tone).
Cold 1:1 outreach is a different register, so these instructions explicitly steer it:
personalized, one concrete hook from the lead's profile, a single soft CTA, plain text,
no salesy blast language — and a mandatory unsubscribe line for compliance.
"""

from __future__ import annotations

from typing import Any

_SHARED_RULES = (
    "Rules: write in plain text (no HTML). Keep it under 120 words. Personalized 1:1 tone, "
    "not a mass marketing blast — no 'Dear valued customer', no ALL-CAPS, no emoji spam, at "
    "most one exclamation mark. Exactly one soft call to action (suggest a brief reply or a "
    "short call, do not pressure). Open by referencing the specific hook provided. End with a "
    "one-line, polite unsubscribe note telling them they can reply 'unsubscribe' to stop "
    "hearing from us. Output the subject line first, then the body."
)


def opener_instruction(campaign: dict[str, Any], lead: dict[str, Any]) -> str:
    """Instruction for a first-touch cold opener, grounded in the lead's profile."""
    sender = (campaign.get("name") or "our team").strip()
    from_name = (campaign.get("from_name") or sender).strip()
    company = (lead.get("company") or "the recipient's business").strip()
    contact = (lead.get("contact_name") or "").strip()
    who = f"{contact} at {company}" if contact else company
    hook = (lead.get("profile_text") or company).strip()
    value_prop = (campaign.get("value_prop") or campaign.get("product_description") or "").strip()

    return (
        f"Write a short, personalized cold outreach email from {from_name} to {who}.\n\n"
        f"WHAT WE OFFER THEM: {value_prop}\n\n"
        f"A SPECIFIC HOOK ABOUT THE RECIPIENT (reference this naturally in the opening line):\n"
        f"{hook}\n\n"
        f"GOAL: {campaign.get('objective') or 'start a conversation and gauge interest'}.\n\n"
        f"{_SHARED_RULES}"
    )


def followup_instruction(
    campaign: dict[str, Any], lead: dict[str, Any], thread: list[dict[str, Any]], intent: str
) -> str:
    """Instruction for a threaded follow-up reply, given the conversation and the decided
    intent from prompts.reasoning.follow_up_decision."""
    from_name = (campaign.get("from_name") or campaign.get("name") or "our team").strip()
    company = (lead.get("company") or "the recipient's business").strip()
    last_in = next(
        (m for m in reversed(thread) if m.get("direction") == "in"), {"body": ""}
    )

    rendered = "\n\n".join(
        f"[{m['direction'].upper()}] {m.get('body','')}" for m in thread[-4:]
    )
    return (
        f"Write a short, warm follow-up REPLY from {from_name} to their message, continuing an "
        f"existing 1:1 conversation with {company}. This is a reply in an ongoing thread, so do "
        f"not reintroduce yourself from scratch.\n\n"
        f"WHAT THIS REPLY SHOULD ACCOMPLISH: {intent.strip() or 'move the conversation forward'}.\n\n"
        f"THEIR LATEST MESSAGE:\n{(last_in.get('body') or '').strip()}\n\n"
        f"RECENT THREAD (for context):\n{rendered}\n\n"
        f"{_SHARED_RULES}"
    )


def split_subject_body(text: str) -> tuple[str, str]:
    """The Email Writer returns subject + body as one string. Split the first non-empty line
    as the subject (stripping a leading 'Subject:' label if present); the rest is the body."""
    lines = [ln for ln in text.strip().splitlines()]
    subject = ""
    body_start = 0
    for i, ln in enumerate(lines):
        if ln.strip():
            subject = ln.strip()
            body_start = i + 1
            break
    if subject.lower().startswith("subject:"):
        subject = subject.split(":", 1)[1].strip()
    body = "\n".join(lines[body_start:]).strip()
    # Some generations put "Subject: x\n\nBody..." — if body is empty, fall back to whole text.
    if not body:
        body = text.strip()
    return subject or "Quick question", body
