"""Reasoning prompts — fed to the HF chat LLM, all asking for strict JSON.

Kept as plain builder functions (no template engine) to match this app's house style;
llm.structured() handles the JSON extraction.
"""

from __future__ import annotations

import json
from typing import Any


def icp_seed(product_description: str, objective: str, country: str) -> str:
    """Turn a product description into a discovery seed + a small pool of clause values.

    Adapts OpenOutreach's `ICPSpec` (one value per filter family) to our free discovery
    backends: `seed` is the single most-precise starting query; the parallel lists feed the
    clause pool the query selector explores. `value_prop` is reused when writing outreach.
    """
    loc_hint = f"The user is targeting {country}. " if country.strip() else ""
    return (
        "You are a B2B go-to-market analyst. Given a product and its goal, define the ideal "
        "customer profile as concrete, searchable discovery criteria for finding matching "
        "BUSINESSES (companies/organizations), usable against OpenStreetMap business categories "
        "and web search.\n\n"
        f"PRODUCT:\n{product_description.strip()}\n\n"
        f"OBJECTIVE:\n{objective.strip() or '(not specified)'}\n\n"
        f"{loc_hint}"
        "Return ONLY JSON of this exact shape:\n"
        "{\n"
        '  "seed": {"category": "<one best business category or type>", '
        '"location": "<one best geography, or empty if global>", "keyword": "<one strong '
        'qualifier keyword>"},\n'
        '  "categories": ["<3-6 business categories/types to target>"],\n'
        '  "locations": ["<2-5 geographies, cities/regions/countries; empty list if global>"],\n'
        '  "keywords": ["<3-6 qualifier keywords that signal a good fit>"],\n'
        '  "value_prop": "<one sentence: what the product does for this customer>"\n'
        "}\n"
        "Categories should be things a business directory would list (e.g. 'dental clinic', "
        "'independent bookstore', 'boutique law firm'), not abstract segments."
    )


def qualify_lead(product_description: str, objective: str, profile_text: str) -> str:
    """Classify one discovered lead as a good/bad fit — the label that trains the GP."""
    return (
        "You are qualifying a potential B2B lead. Decide whether this business is a genuinely "
        "good fit to buy the product below. Be strict: a vague or off-segment match is a bad "
        "fit.\n\n"
        f"PRODUCT:\n{product_description.strip()}\n\n"
        f"OBJECTIVE:\n{objective.strip() or '(not specified)'}\n\n"
        f"LEAD PROFILE:\n{profile_text.strip()}\n\n"
        "Return ONLY JSON: {\"fit\": \"good\" | \"bad\", \"reason\": \"<one short sentence>\"}. "
        'Use "bad" (wrong_fit) when the business is clearly outside the target segment.'
    )


def follow_up_decision(
    product_description: str, thread: list[dict[str, Any]], latest_reply: str
) -> str:
    """Decide what to do about an inbound reply — the decision only, not the message text.

    Mirrors OpenOutreach's single structured follow-up call: one of send_message / wait /
    mark_completed. When send_message, an `intent` line tells the Email Writer what the
    reply should accomplish (the Email Writer writes the actual words).
    """
    rendered = "\n\n".join(
        f"[{m['direction'].upper()}] {m.get('subject','')}\n{m.get('body','')}" for m in thread
    )
    return (
        "You are managing a 1:1 sales conversation on behalf of the sender of the product below. "
        "Read the thread and the latest reply, then decide the single best next action.\n\n"
        f"PRODUCT:\n{product_description.strip()}\n\n"
        f"CONVERSATION SO FAR:\n{rendered}\n\n"
        f"LATEST REPLY FROM THE PROSPECT:\n{latest_reply.strip()}\n\n"
        "Return ONLY JSON of this shape:\n"
        "{\n"
        '  "action": "send_message" | "wait" | "mark_completed",\n'
        '  "intent": "<if send_message: one sentence on what the reply should accomplish>",\n'
        '  "outcome": "interested" | "meeting" | "not_interested" | "unsubscribe" | null,\n'
        '  "wait_hours": <int, only if action is wait>,\n'
        '  "reason": "<one short sentence>"\n'
        "}\n"
        "Choose mark_completed with outcome unsubscribe if they ask to stop being contacted, or "
        "not_interested if they clearly decline. Choose send_message to answer a question or move "
        "toward a call. Choose wait if a reply now would be premature."
    )


def parse_qualify(result: Any) -> tuple[str, str]:
    """(label, reason) from a qualify_lead response. label is 'positive' | 'negative'."""
    if not isinstance(result, dict):
        return "negative", "unparseable qualification"
    fit = str(result.get("fit", "")).strip().lower()
    reason = str(result.get("reason", "")).strip()
    return ("positive" if fit == "good" else "negative"), reason


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
