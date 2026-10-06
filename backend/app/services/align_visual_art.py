"""Visual Art labels and aggregate PAMELA preference evidence."""

from __future__ import annotations

import base64
import io
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MODEL = "Qwen/Qwen3.5-9B"
VISION_MODELS = (MODEL, "google/gemma-3-27b-it")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ARTSY_URL = "https://www.artsy.net/categories"
BUCKET_URL = "https://huggingface.co/buckets/vivekchakraverty/images"

# A small subset of the Artsy Art Genome page that can be visually inferred.
# Medium is omitted: a photograph of a sculpture cannot establish its medium.
ARTSY_CATEGORIES = {
    "Abstract Art": "https://www.artsy.net/gene/abstract-art",
    "Animals": "https://www.artsy.net/gene/animals",
    "Architecture": "https://www.artsy.net/gene/architecture-1",
    "Cityscapes": "https://www.artsy.net/gene/cityscapes",
    "Cubism": "https://www.artsy.net/gene/cubism",
    "Expressionism": "https://www.artsy.net/gene/expressionism",
    "Flora": "https://www.artsy.net/gene/flora",
    "Impressionism": "https://www.artsy.net/gene/impressionism",
    "Landscapes": "https://www.artsy.net/gene/landscapes",
    "Minimalism": "https://www.artsy.net/gene/minimalism",
    "Nature": "https://www.artsy.net/gene/nature",
    "Pop Art": "https://www.artsy.net/gene/pop-art",
    "Portrait": "https://www.artsy.net/gene/portrait",
    "Still Life": "https://www.artsy.net/gene/still-life",
    "Surrealism": "https://www.artsy.net/gene/surrealism",
}


class VisualArtError(ValueError):
    pass


class VisualArtInferenceError(VisualArtError):
    """The uploaded image was valid, but the remote model could not serve it."""

    def __init__(self, message: str, *, http_status: int = 502) -> None:
        super().__init__(message)
        self.http_status = http_status


def _provider_detail(err: Exception) -> str:
    response = getattr(err, "response", None)
    if response is None:
        return ""
    try:
        body = response.json()
    except (TypeError, ValueError):
        return ""
    if not isinstance(body, dict):
        return ""
    value = body.get("error") or body.get("message") or body.get("detail")
    if isinstance(value, dict):
        value = value.get("message") or value.get("detail")
    return value if isinstance(value, str) else ""


def _model_unavailable(err: Exception) -> bool:
    status = getattr(getattr(err, "response", None), "status_code", None)
    if status in {404, 405, 501}:
        return True
    if status not in {None, 400}:
        return False
    message = (_provider_detail(err) or str(err)).lower()
    return any(marker in message for marker in (
        "not supported by any provider", "no inference provider",
        "not supported by provider", "model is not supported",
    ))


def _inference_error(err: Exception, token: str) -> VisualArtInferenceError:
    response = getattr(err, "response", None)
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {})
    request_id = headers.get("x-request-id") or headers.get("x-amzn-requestid")
    detail = _provider_detail(err)
    # Provider errors can echo request data. Never put a credential or image bytes in the UI.
    if detail:
        detail = detail.replace(token, "[redacted]")
        detail = re.sub(r"hf_[A-Za-z0-9]+", "[redacted]", detail)
        detail = re.sub(r"data:image/[^\s]+", "[image omitted]", detail)
        detail = " ".join(detail.split())[:250]
    suffix = f" Provider detail: {detail}" if detail else ""
    if request_id:
        suffix += f" Request ID: {str(request_id)[:100]}"
    if status in {401, 403}:
        return VisualArtInferenceError(
            f"Hugging Face rejected access to image analysis (HTTP {status}). "
            f"Check that your token has Inference Providers permission and this model is allowed.{suffix}"
        )
    if status == 402:
        return VisualArtInferenceError(
            f"Hugging Face requires billing or credits for image analysis (HTTP 402). "
            f"Check your Inference Providers billing.{suffix}"
        )
    if status == 429:
        return VisualArtInferenceError(
            f"Hugging Face rate-limited image analysis (HTTP 429). "
            f"Wait a few minutes and retry.{suffix}", http_status=429
        )
    if _model_unavailable(err):
        return VisualArtInferenceError(
            "Hugging Face could not route image analysis to an enabled provider. "
            f"Enable a provider that serves {MODEL} in your Hugging Face Inference Providers "
            f"settings, or choose labels manually.{suffix}", http_status=503
        )
    if status in {500, 502, 503, 504}:
        return VisualArtInferenceError(
            f"Hugging Face could not serve image analysis (HTTP {status}). "
            f"Try again later or choose labels manually.{suffix}", http_status=503
        )
    if status is not None:
        return VisualArtInferenceError(
            f"Hugging Face rejected the image analysis request (HTTP {status}). "
            f"Try a smaller image or choose labels manually.{suffix}"
        )
    if isinstance(err, ValueError):
        return VisualArtInferenceError(
            "Hugging Face could not route this image model to an Inference Provider. "
            "Check the model's current provider availability or choose labels manually.",
            http_status=503,
        )
    return VisualArtInferenceError(
        "Could not connect to Hugging Face for image analysis. "
        "Check your connection and try again, or choose labels manually.", http_status=503
    )


@lru_cache(maxsize=1)
def benchmark() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("pamela_affinity.json").read_text(encoding="utf-8"))


def choices() -> dict[str, list[str]]:
    dimensions = benchmark()["dimensions"]
    return {
        "groups": sorted(dimensions["group"]),
        "styles": sorted(dimensions["style"]),
        "artsy_categories": list(ARTSY_CATEGORIES),
    }


def prepare_image(content: bytes) -> str:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise VisualArtError("Choose an image up to 20 MB.")
    try:
        image = Image.open(io.BytesIO(content))
        if image.format not in {"PNG", "JPEG", "WEBP"}:
            raise VisualArtError("Use a PNG, JPEG or WebP image.")
        if image.width * image.height > 40_000_000:
            raise VisualArtError("This image has too many pixels; use a smaller export.")
        image = ImageOps.exif_transpose(image)
        image.thumbnail((1024, 1024))
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            image = image.convert("RGBA")
            canvas = Image.new("RGB", image.size, "white")
            canvas.paste(image, mask=image.getchannel("A"))
            image = canvas
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (UnidentifiedImageError, OSError, ValueError) as err:
        if isinstance(err, VisualArtError):
            raise
        raise VisualArtError("This image could not be read. Use a valid PNG, JPEG or WebP file.") from None


def parse_classification(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise VisualArtError("The image model did not return usable labels. Choose labels manually.")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        raise VisualArtError("The image model did not return usable labels. Choose labels manually.") from None
    options = choices()
    group = data.get("group")
    style = data.get("style")
    categories = data.get("artsy_categories")
    if group not in options["groups"]:
        group = ""
    if style not in options["styles"]:
        style = ""
    if not isinstance(categories, list):
        categories = []
    categories = [item for item in categories if item in ARTSY_CATEGORIES][:4]
    description = str(data.get("description") or "").strip()[:600]
    return {"group": group, "style": style, "artsy_categories": categories, "description": description}


def classify(content: bytes) -> dict[str, Any]:
    image_uri = prepare_image(content)
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise VisualArtError("Add a Hugging Face token in Settings, or choose labels manually.")
    from huggingface_hub import InferenceClient

    options = choices()
    prompt = (
        "Describe the visible artwork, then choose ONE closest PAMELA visual group, "
        "one style only if visually supported, and up to four Artsy categories. "
        "Do not infer the artist, materials, age, market demand, or viewer identity. "
        "Return JSON only with keys description, group, style, artsy_categories. "
        f"Allowed groups: {', '.join(options['groups'])}. "
        f"Allowed styles: {', '.join(options['styles'])}; use null if uncertain. "
        f"Allowed Artsy categories: {', '.join(ARTSY_CATEGORIES)}."
    )
    client = InferenceClient(api_key=token, timeout=120)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_uri}},
    ]}]
    for model in VISION_MODELS:
        try:
            kwargs = {}
            if model == MODEL:
                # Qwen otherwise spends the small label budget on hidden reasoning.
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            response = client.chat_completion(
                model=model, messages=messages, max_tokens=650, temperature=0.2, **kwargs,
            )
            raw = response.choices[0].message.content or ""
            if not isinstance(raw, str):
                raise VisualArtError("The image model returned an unexpected response. Choose labels manually.")
            result = parse_classification(raw)
            result["model"] = model
            return result
        except VisualArtError:
            raise
        except Exception as err:
            # The router respects the user's enabled providers. Try another vision model
            # for this specific routing failure; access, billing and other errors stop here.
            if _model_unavailable(err) and model != VISION_MODELS[-1]:
                continue
            raise _inference_error(err, token) from err
    raise VisualArtInferenceError("No artwork vision model is available.", http_status=503)


def match(group: str, style: str, artsy_categories: list[str]) -> dict[str, Any]:
    options = choices()
    if group not in options["groups"]:
        raise VisualArtError("Choose a PAMELA visual group.")
    if style and style not in options["styles"]:
        raise VisualArtError("Choose a listed PAMELA style or leave it blank.")
    if len(artsy_categories) > 4 or any(item not in ARTSY_CATEGORIES for item in artsy_categories):
        raise VisualArtError("Choose up to four listed Artsy categories.")
    dimensions = benchmark()["dimensions"]
    evidence = [{"dimension": "group", "name": group, **dimensions["group"][group]}]
    if style:
        evidence.append({"dimension": "style", "name": style, **dimensions["style"][style]})
    return {
        "artsy_categories": list(dict.fromkeys(artsy_categories)),
        "artsy_category_links": {name: ARTSY_CATEGORIES[name] for name in artsy_categories},
        "pamela_evidence": evidence,
        "sources": {
            "artsy": ARTSY_URL,
            "pamela": BUCKET_URL,
            "pamela_dataset": "https://huggingface.co/datasets/bethgelab/PAMELA",
        },
        "sample": {"ratings": benchmark()["rating_count"], "participants": benchmark()["participant_count"], "split": "pamela_train"},
        "method": benchmark()["method"],
        "boundary": (
            "These are ratings of other AI-generated images sharing a broad group or style. "
            "They do not measure reactions to your artwork or identify real admirers. "
            "The benchmark participants are not representative of the general public."
        ),
    }
