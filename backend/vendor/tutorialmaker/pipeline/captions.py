"""Stage 8: caption each selected screenshot with an HF vision model.

Calls a vision-language chat model (default Qwen2.5-VL-72B) via HF Inference Providers,
billed to the user's token. The image is sent inline as a base64 ``data:`` URL and the
step heading is given as context so captions are relevant, not just literal. Failures
degrade gracefully to the step heading so the .docx is always produced.
"""
from __future__ import annotations

import base64

from huggingface_hub import InferenceClient

DEFAULT_VLM = "Qwen/Qwen2.5-VL-72B-Instruct"
FALLBACK_VLM = "Qwen/Qwen2.5-VL-7B-Instruct"

_PROMPT = (
    "Write a single concise caption (one sentence, no preamble) describing what this "
    "tutorial screenshot shows. Context for this step: {heading}."
)


def _data_url(path: str) -> str:
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return f"data:image/jpeg;base64,{b64}"


def _caption_one(client: InferenceClient, model: str, path: str, heading: str) -> str:
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": _PROMPT.format(heading=heading or "tutorial step")},
            {"type": "image_url", "image_url": {"url": _data_url(path)}},
        ],
    }]
    resp = client.chat.completions.create(model=model, messages=messages, max_tokens=120)
    return (resp.choices[0].message.content or "").strip()


def caption_frames(selected: dict[int, dict], steps: list[dict], hf_token: str,
                   model: str = DEFAULT_VLM, progress=None) -> dict[int, str]:
    """Return ``{step_index: caption}`` for each selected screenshot.

    Tries ``model`` first, then ``FALLBACK_VLM``, then the step heading.
    """
    if not selected:
        return {}
    if not hf_token:
        raise ValueError("An HF token is required for image captioning (billed to your key).")

    client = InferenceClient(token=hf_token)
    captions: dict[int, str] = {}
    items = list(selected.items())
    for n, (idx, sel) in enumerate(items):
        heading = steps[idx].get("heading", "")
        if progress:
            progress((n + 1) / len(items), desc=f"Captioning {n + 1}/{len(items)}")
        text = ""
        for m in (model, FALLBACK_VLM):
            try:
                text = _caption_one(client, m, sel["path"], heading)
                if text:
                    break
            except Exception:
                continue
        captions[idx] = text or heading or "Screenshot"
    return captions
