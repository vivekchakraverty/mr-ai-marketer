"""Shared helpers for talking to HF Inference Providers with the user's token.

Every call here is billed to whoever owns `hf_token`. Reused verbatim from the sibling
BlogWriter project.
"""
from __future__ import annotations

from typing import List, Optional

from huggingface_hub import InferenceClient


def make_client(hf_token: str) -> InferenceClient:
    """Build an InferenceClient bound to the user's token (auto provider selection)."""
    token = (hf_token or "").strip()
    if not token:
        raise ValueError("A Hugging Face token is required (paid calls are billed to it).")
    return InferenceClient(token=token)


def chat(
    client: InferenceClient,
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    fallback_model: Optional[str] = None,
) -> str:
    """Run a chat completion and return the assistant text.

    Falls back to `fallback_model` once if the primary model errors (e.g. no provider).
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    models: List[str] = [model] + ([fallback_model] if fallback_model else [])
    last_err: Optional[Exception] = None
    for m in models:
        try:
            resp = client.chat.completions.create(
                model=m,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001 - surface a clean error after trying fallback
            last_err = e
            continue
    raise RuntimeError(f"LLM call failed for {models}: {last_err}")
