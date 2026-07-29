"""Reasoning LLM client — the ICP synthesis, lead qualification, and follow-up-decision
calls (NOT the outreach emails themselves, which the Email Writer Space writes).

Two swappable backends behind one `complete()` / `structured()` surface, selected by
LLM_BACKEND, both speaking the OpenAI-compatible `/chat/completions` shape:

  * hf      Hugging Face Inference Providers router. The token IS the billing identity;
            every call is billed to HF_TOKEN, so we fail fast with a clear message if it
            is missing or lacks the "Make calls to Inference Providers" permission.
  * ollama  A local Ollama server (offline dev), no token, no billing.

Every call logs the prompt/completion token counts so spend is watchable (mirrors the
requirement the app already has for its other billed HF paths).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)

HF_ROUTER_URL = "https://router.huggingface.co/v1"
HF_TIMEOUT_SECONDS = 60
_RETRYABLE = ("429", "rate", "quota", "500", "502", "503", "timeout", "unavailable")


class LLMError(Exception):
    """A permanent LLM failure, with a message safe to show the user."""


def backend() -> str:
    return (config.current("LLM_BACKEND") or "hf").strip().lower()


def model_name() -> str:
    if backend() == "ollama":
        return config.current("OLLAMA_MODEL") or "qwen2.5:7b-instruct"
    return config.current("LLM_MODEL") or "Qwen/Qwen3-Next-80B-A3B-Instruct"


def hf_token() -> str:
    token = (os.environ.get("HF_TOKEN") or "").strip()
    if not token:
        raise LLMError(
            "LLM_BACKEND=hf needs a Hugging Face token. Set HF_TOKEN in Settings — it is the "
            "billing identity, and reasoning calls are billed to it (pay-as-you-go)."
        )
    return token


# The httpx client is cheap to recreate; reset_client() exists so config.apply() can drop
# any cached state after a settings change without a restart.
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=HF_TIMEOUT_SECONDS)
    return _client


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None


def _endpoint_and_headers() -> tuple[str, dict[str, str]]:
    if backend() == "ollama":
        base = (config.current("OLLAMA_URL") or "http://localhost:11435").rstrip("/")
        return f"{base}/v1/chat/completions", {}
    return f"{HF_ROUTER_URL}/chat/completions", {"Authorization": f"Bearer {hf_token()}"}


def _log_usage(resp_json: dict[str, Any]) -> None:
    usage = resp_json.get("usage") or {}
    if usage:
        log.info(
            "[leadgen.llm] %s/%s tokens prompt=%s completion=%s",
            backend(),
            model_name(),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )


def _once(prompt: str, temperature: float, max_output_tokens: int) -> str:
    url, headers = _endpoint_and_headers()
    resp = _http().post(
        url,
        headers=headers,
        json={
            "model": model_name(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        },
    )

    if backend() == "hf":
        if resp.status_code == 401:
            raise LLMError(
                "Hugging Face rejected the token. It needs the 'Make calls to Inference "
                "Providers' permission — a fine-grained token without it authenticates fine "
                "everywhere else and still fails here."
            )
        if resp.status_code == 402:
            raise LLMError(
                "Hugging Face says this account is out of inference credits. Enable "
                "pay-as-you-go, or point LLM_MODEL at a cheaper model."
            )
        if resp.status_code == 404:
            raise LLMError(
                f"No provider currently serves {model_name()!r}. Model availability on the "
                f"router changes; try another instruct model in Settings."
            )
    if resp.status_code >= 400:
        # Let _call() decide whether this is worth retrying.
        raise httpx.HTTPStatusError(
            f"{resp.status_code}: {resp.text[:200]}", request=resp.request, response=resp
        )

    data = resp.json()
    _log_usage(data)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as err:
        raise LLMError(f"Unexpected LLM response shape: {err}") from err


def complete(prompt: str, temperature: float = 0.3, max_output_tokens: int = 800) -> str:
    """One completion, with a single retry on transient failures (shared by both backends)."""
    for attempt in range(2):
        try:
            return _once(prompt, temperature, max_output_tokens)
        except LLMError:
            raise  # permanent — retrying cannot help
        except Exception as err:  # noqa: BLE001
            message = str(err).lower()
            transient = any(tok in message for tok in _RETRYABLE)
            if attempt == 0 and transient:
                time.sleep(1.5)
                continue
            raise LLMError(f"LLM call failed: {str(err).splitlines()[0][:200]}") from err
    raise LLMError("LLM call failed after retry.")  # unreachable, satisfies type checker


def structured(prompt: str, temperature: float = 0.1, max_output_tokens: int = 800) -> Any:
    """Complete, then parse the model's reply as JSON.

    Instruct models often wrap JSON in prose or ```json fences; we extract the first
    balanced object/array rather than trusting the whole reply to be clean JSON.
    """
    raw = complete(prompt, temperature=temperature, max_output_tokens=max_output_tokens)
    return _extract_json(raw)


def _extract_json(raw: str) -> Any:
    text = raw.strip()
    # Strip a ```json ... ``` fence if present.
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} or [...] span.
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as err:
            raise LLMError(f"Model did not return valid JSON: {err}") from err
    raise LLMError("Model did not return any JSON.")
