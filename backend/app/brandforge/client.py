"""HF image inference for BrandForge, billed to the user's own HF token.

Text generation now runs on the BrandForge Space (see brandforge/space.py).
Images stay on HF Inference Providers text-to-image (FLUX.1-dev).
"""
from __future__ import annotations

from typing import Optional

from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError

REQUEST_TIMEOUT = 180
IMAGE_MODEL = "black-forest-labs/FLUX.1-dev"


class BrandForgeError(Exception):
    """User-facing error for BrandForge inference failures."""


def _friendly_http_error(status: Optional[int], exc: Exception) -> BrandForgeError:
    if status == 401:
        return BrandForgeError("Invalid Hugging Face token. Check the token in Settings.")
    if status == 402:
        return BrandForgeError(
            "Your Hugging Face account doesn't have billing enabled, or you're out of credit. "
            "See https://huggingface.co/settings/billing."
        )
    if status == 429:
        return BrandForgeError("Rate limited by Hugging Face. Wait a moment and try again.")
    return BrandForgeError(f"Image request failed ({status or 'unknown error'}): {exc}")


def text_to_image(hf_token: str, prompt: str, model: str = IMAGE_MODEL):
    """One FLUX.1-dev text-to-image call via HF Inference. Returns a PIL.Image."""
    if not hf_token or not hf_token.strip():
        raise BrandForgeError("Please connect your Hugging Face account in Settings.")
    client = InferenceClient(api_key=hf_token.strip(), timeout=REQUEST_TIMEOUT)
    try:
        return client.text_to_image(prompt, model=model)
    except HfHubHTTPError as exc:
        raise _friendly_http_error(getattr(exc.response, "status_code", None), exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise BrandForgeError(f"Image generation failed: {exc}") from exc
