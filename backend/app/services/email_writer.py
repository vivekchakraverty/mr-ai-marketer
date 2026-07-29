"""Shared Email Writer generation — the fine-tuned marketing-email model on the user's own
free HF Space, plus the CTR estimate.

Extracted from routers/email_writer.py so more than one caller can reuse it: the Email
Writer tool (which also saves to the Library) and the Lead Gen Agent (which turns the output
into an outreach draft). The Space call and CTR prediction live here; each caller does its
own thing with the result.
"""

from __future__ import annotations

from gradio_client import Client

from . import ctr_predictor

# The marketing-email model (a QLoRA fine-tune of Qwen2.5-7B) runs on the user's own free
# Hugging Face CPU Space, called exactly the way blog_writer.py calls its Space. Public, runs
# on its own CPU, so no HF token is passed.
_SPACE_ID = "vivekchakraverty/marketing-email-writer"
_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(_SPACE_ID)
    return _client


def generate_marketing_email(instruction: str) -> dict:
    """Generate a finished email from one freeform instruction, with a CTR estimate.

    Returns {"text", "predictedClickRate", "ctrBucket"}. Raises on an empty instruction or a
    Space/network failure so callers can surface a clean error.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("Tell me what the email should be about.")

    # The Space's /generate takes one freeform instruction and returns the finished email as a
    # single string (subject + body together), already leak-filtered.
    text = (_get_client().predict(instruction, api_name="/generate") or "").strip()
    if not text:
        raise RuntimeError("The model returned an empty email — try again.")

    ctr = ctr_predictor.predict_ctr(text)
    return {"text": text, "predictedClickRate": ctr.predictedClickRate, "ctrBucket": ctr.bucket}
