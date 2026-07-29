"""Multimodal pass: caption frames and score them for "informativeness".

Captioning prefers a vision LLM on the HuggingFace Inference API and falls back
to a local BLIP model (only if torch/transformers are installed). Frame scoring
uses a cheap sharpness heuristic (variance of the Laplacian) so the guide builder
can prefer crisp, content-rich frames over blurry scene-transition frames.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from . import config

_LOCAL_PROC = None
_LOCAL_MODEL = None
_LOCAL_DEVICE = "cpu"
_LOCAL_FAILED = False
# Many free HF accounts have no provider that serves a vision-chat model. Once
# the API VLM fails, stop retrying it for the session and use local BLIP.
_API_VLM_DISABLED = False
# Same idea for the Beam GPU endpoint: one hard failure (bad URL, bad token,
# endpoint deleted) disables it for the session rather than paying the timeout
# on every frame.
_BEAM_DISABLED = False

_CAPTION_PROMPT = (
    "In one concise sentence, describe what this screenshot from a tutorial shows, "
    "focusing on the on-screen UI element or the action being performed. "
    "Do not begin with phrases like 'The image shows'."
)


def _data_uri(image_path: str | Path, max_side: int = 1024) -> str:
    """Downscale + JPEG-encode an image into a data URI (saves API bandwidth)."""
    from PIL import Image

    with Image.open(image_path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _get_vlm_client(token: str | None):
    from huggingface_hub import InferenceClient

    kwargs = {"model": config.VLM_MODEL}
    if token:
        kwargs["token"] = token
    if config.VLM_PROVIDER:
        kwargs["provider"] = config.VLM_PROVIDER
    return InferenceClient(**kwargs)


def _caption_via_api(image_path: str | Path, prompt: str, token: str | None) -> str:
    client = _get_vlm_client(token)
    resp = client.chat_completion(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _data_uri(image_path)}},
                ],
            }
        ],
        max_tokens=120,
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def _beam_post(items: list[tuple[str | Path, str]], prompt: str) -> list[str] | None:
    """One request to the Beam endpoint. ``None`` on any failure."""
    global _BEAM_DISABLED

    import requests

    payload = {
        "items": [
            {"image": _data_uri(path), "context": ctx or ""} for path, ctx in items
        ],
        "prompt": prompt,
    }
    headers = {"Content-Type": "application/json"}
    if config.BEAM_CAPTION_TOKEN:
        headers["Authorization"] = f"Bearer {config.BEAM_CAPTION_TOKEN}"

    try:
        resp = requests.post(
            config.BEAM_CAPTION_URL,
            json=payload,
            headers=headers,
            timeout=config.BEAM_CAPTION_TIMEOUT,
        )
        resp.raise_for_status()
        captions = resp.json().get("captions")
        if not isinstance(captions, list) or len(captions) != len(items):
            return None
        return [str(c or "").strip() for c in captions]
    except Exception:
        # Auth/URL problems repeat on every call, so stop trying this session.
        _BEAM_DISABLED = True
        return None


def _beam_caption_batch(
    items: list[tuple[str | Path, str]], prompt: str
) -> list[str] | None:
    """Caption frames via the Beam GPU endpoint, chunked.

    ``items`` is a list of ``(image_path, context)``. Returns captions aligned to
    ``items``, or ``None`` if the endpoint is unconfigured or any chunk fails —
    the caller then falls back to the HF API and local BLIP for the whole set,
    which keeps the outcome predictable rather than half-Beam/half-BLIP.
    """
    if _BEAM_DISABLED or not config.BEAM_CAPTION_URL or not items:
        return None

    size = max(1, config.BEAM_CAPTION_BATCH_SIZE)
    captions: list[str] = []
    for start in range(0, len(items), size):
        chunk = _beam_post(items[start:start + size], prompt)
        if chunk is None:
            return None
        captions.extend(chunk)
    return captions


def caption_batch(
    items: list[tuple[str | Path, str]], *, token: str | None = None
) -> list[str]:
    """Caption a list of ``(image_path, context)`` pairs.

    Prefers one batched call to the Beam GPU endpoint. Without it, falls back to
    per-frame captioning via :func:`caption_image` so behaviour is unchanged when
    Beam is not configured.
    """
    if not config.ENABLE_VISION or not items:
        return ["" for _ in items]

    captions = _beam_caption_batch(items, _CAPTION_PROMPT)
    if captions is not None:
        return captions

    return [
        caption_image(path, token=token, context=ctx) or "" for path, ctx in items
    ]


def _load_local_captioner() -> None:
    """Load the BLIP captioner directly (the image-to-text pipeline task was
    removed in transformers 5). Uses the GPU if a CUDA build of torch is present.
    """
    global _LOCAL_PROC, _LOCAL_MODEL, _LOCAL_DEVICE
    from transformers import AutoProcessor

    try:
        from transformers import AutoModelForImageTextToText as _AutoCaptionModel
    except Exception:  # older transformers
        from transformers import AutoModelForVision2Seq as _AutoCaptionModel

    proc = AutoProcessor.from_pretrained(config.LOCAL_CAPTION_MODEL)
    model = _AutoCaptionModel.from_pretrained(config.LOCAL_CAPTION_MODEL)

    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
            model = model.to(device)
    except Exception:
        pass

    _LOCAL_PROC, _LOCAL_MODEL, _LOCAL_DEVICE = proc, model, device


def _caption_via_local(image_path: str | Path) -> str:
    """Local BLIP captioner. Returns '' if torch/transformers are unavailable."""
    global _LOCAL_FAILED
    if _LOCAL_FAILED:
        return ""
    if _LOCAL_MODEL is None:
        try:
            _load_local_captioner()
        except Exception:
            _LOCAL_FAILED = True
            return ""
    try:
        import torch
        from PIL import Image

        with Image.open(image_path) as im:
            img = im.convert("RGB")
        inputs = _LOCAL_PROC(images=img, return_tensors="pt")
        if _LOCAL_DEVICE != "cpu":
            inputs = {k: v.to(_LOCAL_DEVICE) for k, v in inputs.items()}
        with torch.no_grad():
            out = _LOCAL_MODEL.generate(**inputs, max_new_tokens=40)
        return _LOCAL_PROC.decode(out[0], skip_special_tokens=True).strip()
    except Exception:
        return ""


def caption_image(
    image_path: str | Path, *, token: str | None = None, context: str = ""
) -> str | None:
    """Return a one-line caption for a frame, or None if captioning is off/failed.

    Order of preference: the Beam GPU endpoint (if ``DOCUMAKER_BEAM_CAPTION_URL``
    is set), then an API vision-chat model (if any provider serves one), then
    local BLIP. After a backend fails once it is skipped for the rest of the
    session to avoid repeated dead calls. Local BLIP needs no token.
    """
    global _API_VLM_DISABLED
    if not config.ENABLE_VISION:
        return None
    prompt = _CAPTION_PROMPT
    if context:
        prompt += f" For context, this step is about: {context[:200]}"

    beam = _beam_caption_batch([(image_path, context)], _CAPTION_PROMPT)
    if beam and beam[0]:
        return beam[0]

    if token and not _API_VLM_DISABLED:
        try:
            caption = _caption_via_api(image_path, prompt, token)
            if caption:
                return caption
        except Exception:
            _API_VLM_DISABLED = True  # no usable provider — switch to local BLIP

    caption = _caption_via_local(image_path)
    return caption or None


def frame_score(image_path: str | Path) -> float:
    """Sharpness score (variance of Laplacian). Higher = crisper/more detailed."""
    try:
        import cv2

        img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return 0.0
