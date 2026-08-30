"""Shared Email Writer generation — the fine-tuned marketing-email model on the user's own
free HF Space, plus the CTR estimate.

Extracted from routers/email_writer.py so more than one caller can reuse it: the Email
Writer tool (which also saves to the Library) and the Lead Gen Agent (which turns the output
into an outreach draft). The Space call and CTR prediction live here; each caller does its
own thing with the result.
"""

from __future__ import annotations

import os

from gradio_client import Client

from .. import config
from . import ctr_predictor

# The marketing-email model (a QLoRA fine-tune of Qwen2.5-7B) runs on a Hugging Face CPU
# Space, which serves the GGUF itself rather than forwarding to an inference API.
#
# THE TOKEN IS FOR ACCESS, NOT FOR BILLING. A free CPU Space costs nobody anything, so there
# is no charge to attribute — passing a token cannot move a cost that does not exist. What it
# does is authenticate the caller, which is what lets the Space be private: config.py points
# at the repo owner's deployment by default, and private means an account without access gets
# a clean refusal and goes and deploys its own, rather than silently queueing on somebody
# else's container.
#
# Cached per token: the client holds a connection bound to whoever opened it, so a changed
# token has to produce a new one rather than reuse a session opened as someone else.

_clients: dict[str, Client] = {}


def _get_client(hf_token: str | None = None) -> Client:
    space = config.require_space(config.EMAIL_WRITER_SPACE, "EMAIL_WRITER_SPACE", "Email Writer")
    # Falling back to the environment is not a convenience. The Lead Gen Agent drafts through
    # this same service and its injected writer takes only an instruction — no token — so a
    # private Space would refuse every outreach draft while the Email Writer screen, which
    # does send one, carried on working. Electron hands HF_TOKEN over at spawn, so the same
    # account is available to both paths.
    key = (hf_token or "").strip() or (os.environ.get("HF_TOKEN") or "").strip()
    client = _clients.get(key)
    if client is None:
        # `token`, not `hf_token`: gradio_client renamed the argument, and the installed
        # 2.5.0 rejects the old name outright with a TypeError rather than ignoring it.
        client = Client(space, token=key or None)
        _clients[key] = client
    return client


def _modal_config(hf_token: str | None):
    """The user's Modal credentials, or None when they have not set any.

    Handed over at spawn by Electron, the same route Brand Studio's take. No credentials is
    the normal state and means the Space is used, so this returns None rather than raising.
    """
    token_id = (os.environ.get("EMAIL_WRITER_MODAL_TOKEN_ID") or "").strip()
    token_secret = (os.environ.get("EMAIL_WRITER_MODAL_TOKEN_SECRET") or "").strip()
    if not (token_id and token_secret):
        return None
    from ..emailwriter import modal_runtime

    return modal_runtime.ModalConfig(
        token_id=token_id,
        token_secret=token_secret,
        hf_token=(hf_token or "").strip() or (os.environ.get("HF_TOKEN") or "").strip(),
    )


def generate_marketing_email(instruction: str, hf_token: str | None = None) -> dict:
    """Generate a finished email from one freeform instruction, with a CTR estimate.

    Returns {"text", "predictedClickRate", "ctrBucket"}. Raises on an empty instruction or a
    Space/network failure so callers can surface a clean error.

    Runs on the user's own Modal GPU when they have configured one — seconds rather than the
    Space's minute and a half, on hardware nobody else is queueing for — and on the free
    Hugging Face Space otherwise. The Space stays the default because it costs nothing and
    never runs out; Modal is faster and spends credits.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("Tell me what the email should be about.")

    cfg = _modal_config(hf_token)
    if cfg is not None:
        from ..emailwriter import modal_runtime

        # A deliberate hard failure rather than a silent fall back to the Space. Someone who
        # configured a GPU wants to know it stopped working — quietly taking ninety seconds
        # on shared CPU instead looks like the tool got slower for no reason.
        text = modal_runtime.generate_email(cfg, instruction).strip()
    else:
        # The Space's /generate takes one freeform instruction and returns the finished email
        # as a single string (subject + body together), already leak-filtered.
        text = (_get_client(hf_token).predict(instruction, api_name="/generate") or "").strip()
    if not text:
        raise RuntimeError("The model returned an empty email — try again.")

    # The token only matters on the first call of a fresh install, where the CTR model has
    # to be fetched from Hugging Face; after that it is already on disk.
    ctr = ctr_predictor.predict_ctr(text, hf_token=hf_token)
    return {"text": text, "predictedClickRate": ctr.predictedClickRate, "ctrBucket": ctr.bucket}
