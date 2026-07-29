"""Beam serverless-GPU captioner for DocuMaker.

Replaces the local BLIP fallback in ``src/vision.py`` with a real vision-language
model. BLIP is a 0.25B COCO captioner with no OCR — on tutorial screenshots it
emits generic text like "a computer screen with a website on it". Qwen2.5-VL
reads on-screen text and understands UI affordances, which is what a step-by-step
guide actually needs.

The endpoint is *batched*: DocuMaker captions one frame per step (see
``src/guide.py``), so sending the whole set in one request turns N cold-start
round-trips into one.

Deploy:
    beam deploy beam_app.py:caption

Model weights are cached on a Beam Volume, so only the first container pays the
download cost.
"""
from __future__ import annotations

import base64
import io
import os

from beam import Image, QueueDepthAutoscaler, Volume, endpoint

# --- Tunables ---------------------------------------------------------------
# Full bf16 weights (~16.5GB) — comfortable on the 24GB A10G. The AWQ build was
# only needed to fit 16GB, and AutoAWQ is deprecated (last tested on torch 2.6 /
# transformers 4.51), so dropping it removes a fragile dependency. For a smaller
# card, Qwen/Qwen2.5-VL-3B-Instruct is ~7GB and still far better than BLIP.
MODEL_ID = os.getenv("DOCUMAKER_BEAM_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")
# A10G (24GB). No 16GB card is usable here: A4000 reports no capacity, and Beam
# rejects T4/V100 outright ("use an A10G or RTX 4090 instead"). The 7B bf16
# weights need ~16.5GB, leaving headroom for the vision encoder and KV cache.
GPU = os.getenv("DOCUMAKER_BEAM_GPU", "A10G")
CACHE_DIR = "./hf-cache"

# Qwen2.5-VL scales its visual token count with input resolution, so an
# unbounded screenshot can balloon VRAM. Cap it: 1280 * 28 * 28 keeps a typical
# 1080p screenshot well inside budget while preserving legible UI text.
MAX_PIXELS = int(os.getenv("DOCUMAKER_BEAM_MAX_PIXELS", str(1280 * 28 * 28)))
MIN_PIXELS = int(os.getenv("DOCUMAKER_BEAM_MIN_PIXELS", str(256 * 28 * 28)))

DEFAULT_PROMPT = (
    "In one concise sentence, describe what this screenshot from a tutorial shows, "
    "focusing on the on-screen UI element or the action being performed. "
    "Do not begin with phrases like 'The image shows'."
)

image = Image(
    python_version="python3.11",
    python_packages=[
        # PIN torch, do not float it. Beam's hosts run a CUDA 12.9 driver, and an
        # unpinned `torch` resolves to a cu13 wheel whose CUDA runtime the driver
        # is too old for — torch.cuda.is_available() silently returns False and
        # the container dies on device_map="cuda:0" with a bare 500.
        # torch 2.7.1 ships cu126 on PyPI, which the 12.9 driver runs fine.
        "torch==2.7.1",
        "torchvision==0.22.1",
        "transformers==4.53.2",
        "accelerate",
        "qwen-vl-utils",
        "pillow",
    ],
).with_envs([f"HF_HOME={CACHE_DIR}"])


def load_model():
    """Runs once per container (``on_start``), not once per request."""
    import torch
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    # Fail loudly here rather than with an opaque 500 from the request handler:
    # a driver/wheel CUDA mismatch shows up exactly as "no CUDA available".
    if not torch.cuda.is_available():
        raise RuntimeError(
            f"CUDA unavailable (torch {torch.__version__}). The pinned torch build "
            "must match Beam's host driver — see the pin note on `image` above."
        )
    print(f"[documaker-captioner] torch {torch.__version__} on "
          f"{torch.cuda.get_device_name(0)}")

    processor = AutoProcessor.from_pretrained(
        MODEL_ID, min_pixels=MIN_PIXELS, max_pixels=MAX_PIXELS, cache_dir=CACHE_DIR
    )

    # transformers v5 renamed ``torch_dtype`` to ``dtype``; v4 only knows the old
    # spelling. Try the new one first so this works on either.
    # bfloat16: A10G is Ampere, so bf16 is native and avoids the fp16 overflow
    # Qwen2.5-VL is prone to. Falls back to fp16 on pre-Ampere cards.
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    common = {"device_map": "cuda:0", "cache_dir": CACHE_DIR}
    try:
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID, dtype=dtype, **common
        )
    except TypeError:
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype=dtype, **common
        )

    model.eval()
    print(f"[documaker-captioner] loaded {MODEL_ID} on {model.device}")
    return processor, model


def _decode_image(raw: str):
    """Accept a bare base64 string or a full ``data:image/...;base64,`` URI."""
    from PIL import Image as PILImage

    if not raw:
        raise ValueError("empty image payload")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    return PILImage.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")


@endpoint(
    name="documaker-captioner",
    image=image,
    gpu=GPU,
    cpu=2,
    memory="16Gi",
    on_start=load_model,
    volumes=[Volume(name="documaker-hf-cache", mount_path=CACHE_DIR)],
    # Weights take ~40s to page in on a cold container. Staying warm for 5
    # minutes means a user processing several videos in a sitting pays that
    # once, while an idle endpoint still scales to zero.
    keep_warm_seconds=300,
    timeout=600,
    autoscaler=QueueDepthAutoscaler(max_containers=2, tasks_per_container=1),
)
def caption(context, **inputs):
    """Caption a batch of frames.

    Input::

        {"items": [{"image": "<b64|data-uri>", "context": "optional step text"}],
         "prompt": "optional override",
         "max_new_tokens": 96}

    Output::

        {"captions": ["...", ...], "model": "...", "count": N}

    Captions are returned positionally, so ``captions[i]`` belongs to
    ``items[i]``. A frame that fails to decode or generate yields ``""`` rather
    than failing the whole batch — DocuMaker treats an empty caption as "no
    caption" and the guide still builds.
    """
    import torch

    processor, model = context.on_start_value

    items = inputs.get("items") or []
    if not items:
        return {"captions": [], "model": MODEL_ID, "count": 0}

    base_prompt = inputs.get("prompt") or DEFAULT_PROMPT
    max_new_tokens = int(inputs.get("max_new_tokens") or 96)

    captions: list[str] = []
    for item in items:
        try:
            img = _decode_image(item.get("image", ""))
            prompt = base_prompt
            step_context = (item.get("context") or "").strip()
            if step_context:
                prompt += f" For context, this step is about: {step_context[:200]}"

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": img},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
            text = processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            model_inputs = processor(
                text=[text], images=[img], padding=True, return_tensors="pt"
            ).to(model.device)

            with torch.no_grad():
                generated = model.generate(
                    **model_inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                )
            # Strip the prompt tokens before decoding.
            trimmed = generated[0][model_inputs.input_ids.shape[1]:]
            caption_text = processor.decode(
                trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=True
            ).strip()
            captions.append(caption_text)
        except Exception as exc:  # one bad frame must not sink the batch
            print(f"[documaker-captioner] frame failed: {exc}")
            captions.append("")

    return {"captions": captions, "model": MODEL_ID, "count": len(captions)}
