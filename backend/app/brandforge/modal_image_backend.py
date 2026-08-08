"""Modal image worker shared by BrandForge and Social Post Generator.

The target bucket is intentionally separate from the BrandForge text model:
the two pipelines have very different dependencies and keeping them in separate
Modal apps prevents text-only requests from carrying image-model cold starts.
The bucket must contain a standard Diffusers pipeline (``model_index.json`` plus
its weights and configuration).
"""
from __future__ import annotations

import sys

import os

import modal

APP_NAME = "mr-ai-marketer-image-generator"
FUNCTION_NAME = "generate_image"
SOURCE_MODEL = "black-forest-labs/FLUX.2-klein-4B"
# Your own HF Bucket holding the FLUX.2 klein weights. See the README for creating one.
BUCKET_ID = os.environ.get("BRANDFORGE_IMAGE_BUCKET", "").strip()
BUCKET_URI = f"hf://buckets/{BUCKET_ID}"
BUCKET_PAGE = f"https://huggingface.co/buckets/{BUCKET_ID}"
MODEL_DIR = "/models/image-generator-marketer"

# FLUX.2 klein 4B needs about 13 GB of VRAM; an L4 provides enough headroom for
# BF16 inference while matching the existing BrandForge GPU deployment.
GPU_KIND = "L4"
SCALEDOWN_WINDOW = 300
CALL_TIMEOUT = 600
MAX_PROMPT_CHARS = 4_000


def _build_image(secret: modal.Secret) -> modal.Image:
    """Bake the selected Diffusers repository into the Modal image.

    This deliberately fails at deploy time when the bucket is absent, private to
    another account, or not a Diffusers export. It is much clearer
    than making the first user image request wait for a multi-gigabyte download.
    """
    return (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("git")
        .pip_install(
            "torch",
            "transformers>=4.46,<6",
            "accelerate>=1.2",
            "huggingface_hub>=1.6",
            "safetensors>=0.4",
            "Pillow>=10.4",
        )
        .run_commands(
            # FLUX.2 klein support is currently released from Diffusers main.
            "pip install --no-cache-dir git+https://github.com/huggingface/diffusers.git",
            # Buckets are object storage, not model repositories, so sync their
            # files into a local Diffusers directory before the container starts.
            f"hf buckets sync {BUCKET_URI} {MODEL_DIR}",
            secrets=[secret],
        )
    )


def _generate_image(
    prompt: str,
    width: int,
    height: int,
    seed: int | None = None,
    steps: int = 4,
) -> bytes:
    """Render one image and return PNG bytes over Modal's authenticated RPC."""
    import builtins
    import io

    import torch
    from diffusers import Flux2KleinPipeline

    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("An image prompt is required.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"Image prompts must be at most {MAX_PROMPT_CHARS} characters.")
    if width < 512 or height < 512 or width > 1_536 or height > 1_536:
        raise ValueError("Image dimensions must be between 512 and 1536 pixels.")
    if width % 8 or height % 8:
        raise ValueError("Image dimensions must be divisible by 8.")
    if not 1 <= steps <= 50:
        raise ValueError("Image inference steps must be between 1 and 50.")

    state = getattr(builtins, "_marketer_image_state", None)
    if state is None:
        state = {}
        builtins._marketer_image_state = state  # type: ignore[attr-defined]

    if "pipeline" not in state:
        pipe = Flux2KleinPipeline.from_pretrained(MODEL_DIR, torch_dtype=torch.bfloat16)
        pipe.set_progress_bar_config(disable=True)
        # FLUX.2 klein 4B fits on the L4. Keeping every component on GPU avoids
        # PCIe transfers on each request and is materially faster for the Space.
        pipe.to("cuda")
        state["pipeline"] = pipe

    generator = None
    if seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(int(seed))

    image = state["pipeline"](
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=1.0,
        generator=generator,
    ).images[0]
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def build_app(hf_token: str) -> modal.App:
    """Build the independent image-generation app in the user's workspace."""
    from modal._vendor import cloudpickle

    cloudpickle.register_pickle_by_value(sys.modules[__name__])
    secret = modal.Secret.from_dict({"HF_TOKEN": hf_token or ""})

    app = modal.App(APP_NAME)
    app.function(
        name=FUNCTION_NAME,
        image=_build_image(secret),
        gpu=GPU_KIND,
        secrets=[secret],
        timeout=CALL_TIMEOUT,
        scaledown_window=SCALEDOWN_WINDOW,
        serialized=True,
        include_source=False,
    )(_generate_image)
    return app
