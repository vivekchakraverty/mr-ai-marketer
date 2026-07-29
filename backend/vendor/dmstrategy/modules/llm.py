"""Thin wrapper over huggingface_hub.InferenceClient, billed to the user's own token."""

from __future__ import annotations

from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError

# Reassessed 2026-07-05, revised twice same day:
#
# 1st pass picked GLM-5.2 on general benchmarks. 2nd pass reverted to Llama-3.3
# after checking RAG-faithfulness specifically (Vectara leaderboard: Llama-3.3
# 4.1% hallucination vs GLM-5's 10.1%). That treated the RAG context as a hard
# limit the model must not go beyond.
#
# But the RAG context here is meant to *guide*, not limit: "Source N" citation
# exists to avoid inventing titles/authors (a copyright-safety measure, see
# rag.py), not to cap the plan's content at what's retrieved — most of a good
# plan (budget math, channel mix, roadmap, team allocation) comes from general
# marketing expertise, not the 8 retrieved excerpts (see composer.py's prompt).
# Under that framing, a model that abstains/hedges more to stay strictly
# faithful (DeepSeek-V3.2's lower answer rate) is a worse fit, not a better
# one — the job is closer to structured planning/synthesis than strict
# retrieval-QA. GLM-5.2's scale, 1M context, and long-horizon *planning*
# specialization (originally built for agentic task decomposition, which is
# structurally similar to "decompose a brief into a 10-section plan with a
# 90-day roadmap") outweigh its higher but still-moderate hallucination rate
# once citations aren't held to a strict-grounding standard.
DEFAULT_MODEL = "zai-org/GLM-5.2"

AVAILABLE_MODELS = [
    "zai-org/GLM-5.2",  # best general reasoning/planning capability, 1M context, MIT
    "deepseek-ai/DeepSeek-V4-Pro",  # frontier reasoning, strong alternative for complex synthesis
    "meta-llama/Llama-3.3-70B-Instruct",  # most conservative/faithful option, if you'd rather cap creative synthesis
    "deepseek-ai/DeepSeek-V3.2",  # most faithful (6.3% hallucination) but abstains more — best if you want strict grounding back
    "openai/gpt-oss-120b",  # Apache 2.0, cheap/fast MoE, decent all-rounder
]


class LLMError(Exception):
    """User-facing error for LLM call failures."""


def chat(
    hf_token: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.4,
) -> str:
    if not hf_token or not hf_token.strip():
        raise LLMError("Please enter your Hugging Face access token.")

    client = InferenceClient(api_key=hf_token.strip())
    try:
        response = client.chat_completion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except HfHubHTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status == 401:
            raise LLMError(
                "Invalid Hugging Face token. Double-check the token and try again."
            ) from exc
        if status == 402:
            raise LLMError(
                "Your Hugging Face account doesn't have billing enabled for Inference "
                "Providers, or you're out of credit. Check https://huggingface.co/settings/billing."
            ) from exc
        if status == 429:
            raise LLMError(
                "Rate limited by Hugging Face Inference Providers. Wait a moment and try again."
            ) from exc
        raise LLMError(f"LLM request failed ({status or 'unknown error'}): {exc}") from exc
    except Exception as exc:  # network errors, timeouts, etc.
        raise LLMError(f"LLM request failed: {exc}") from exc

    return response.choices[0].message.content
