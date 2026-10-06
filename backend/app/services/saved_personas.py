"""Read saved persona research used by Marketing Plan and Buyer Persona."""
from __future__ import annotations

import json
import re

from .. import db

# Labels needed to interpret existing answers. The removed wizard's question
# types, validation, credential fields and UI help are not part of this reader.
ANSWER_LABELS = {
    "A1": "What do you sell in one sentence?",
    "A2": "Who buys it?",
    "A3": "Industry and website URL",
    "A4": "What are the top three problems you solve?",
    "A5": "What makes your offer different?",
    "A6": "Price range and purchase frequency",
    "A7": "Markets and languages",
    "B1": "Describe up to three best customers: who, why they bought, and how they found you",
    "B2": "Who is a poor fit?",
    "B3": "What objections come up most often?",
    "B4": "How long is the buying cycle and what triggers a decision?",
    "B5": "What questions do buyers ask before purchasing?",
    "B6": "Rough customer share by channel",
    "C1": "List three to five alternatives, including DIY or doing nothing",
    "C2": "Where does your audience spend time online?",
    "C3": "What words do customers use for their problem?",
    "D1": "How many buyer types do you expect (1–6), and what are they?",
    "D2": "B2B buying roles, company sizes and industries",
    "D3": "Optional B2C age, life stage, location or device assumptions",
    "E4": "Is this a regulated industry?",
}
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{8,}\d)(?!\w)")
_URL = re.compile(r"https?://\S+|\bwww\.\S+", re.I)
_HANDLE = re.compile(r"(?<!\w)@[\w.-]+")
_NAME = re.compile(r"\b(?:my name is|i am|i'm|signed by)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.I)
_FULL_NAME = re.compile(r"\b[A-Z][a-z]{2,24}\s+[A-Z][a-z]{2,24}\b")
_SPACE = re.compile(r"\s+")


def scrub(text: str) -> str:
    """Keep the existing PII cleanup for reused answers and feedback."""
    value = str(text or "")
    for pattern, replacement in ((_EMAIL, "[email]"), (_PHONE, "[phone]"), (_URL, "[link]"),
                                 (_HANDLE, "[handle]"), (_NAME, "[name]"), (_FULL_NAME, "[name]")):
        value = pattern.sub(replacement, value)
    return _SPACE.sub(" ", value).strip()[:1200]


def answer_lines(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip(" -•\t") for line in re.split(r"[\n;]+", str(value or "")) if line.strip(" -•\t")]


def get_run(run_id: str) -> dict | None:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM persona_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    return {**json.loads(row["document_json"]), "id": row["id"], "name": row["name"],
            "step": row["step"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def options() -> list[dict]:
    with db._connect() as conn:
        rows = conn.execute("SELECT id,name,document_json FROM persona_runs WHERE status='complete' ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [{"runId": row["id"], "runName": row["name"], "personaId": persona["id"],
             "label": persona["label"], "confidence": persona["confidence"]}
            for row in rows for persona in json.loads(row["document_json"]).get("personas", [])]
