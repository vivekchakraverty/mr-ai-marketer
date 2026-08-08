"""The Modal app that gets deployed into the *user's own* Modal workspace.

Everything here describes the remote side: the container image and the single
`generate` function that runs the fine-tuned model on a GPU. Nothing in this
module is imported by the container — it is deployed from
`modal_runtime.provision()` and the function body is shipped by value
(`serialized=True`, i.e. cloudpickled) rather than mounted from disk.

That last detail is load-bearing: the packaged app is a PyInstaller freeze with
no .py files on disk, so Modal's normal "mount the local source" path cannot
work. Serializing the function sidesteps it, and is also why the function takes
plain chat `messages` instead of an intake dict — the prompt is built app-side
by `sections.build_student_messages`, so the container needs no BrandForge code
and never has to be redeployed when a prompt changes.
"""
from __future__ import annotations

import sys

import os

import modal

# Deployed under this name in the user's workspace. Namespaced so it can't
# collide with an app they already have.
APP_NAME = "mr-ai-marketer-brandforge"
FUNCTION_NAME = "generate"

# Pre-merged weights: the LoRA is already folded into the base, so the container
# does no PEFT load and no merge_and_unload() on every cold start. The repo is
# gated with automatic approval, so the user's HF token still has to have
# accepted the terms once on the model page.
# Your own merged model repo. No default: pointing this at someone else's gated repo only
# works while they leave it public, and it is your HF token that has to have accepted its
# terms. Set BRANDFORGE_MODEL to the repo you pushed (see the README).
MODEL = os.environ.get("BRANDFORGE_MODEL", "").strip()
MODEL_PAGE = f"https://huggingface.co/{MODEL}"

# L4 rather than the T4 the hosted backend uses: roughly twice the throughput on
# this workload for a comparable per-second price, and Brand Studio generates 12
# sections back to back, so the difference is minutes per document.
GPU_KIND = "L4"

# Stay warm 5 min after the last call. A full document is ~12 sequential calls,
# so this keeps everything after the first section on a hot container.
SCALEDOWN_WINDOW = 300
CALL_TIMEOUT = 600

MAX_NEW_TOKENS = 1500


def _build_image(secret: modal.Secret) -> modal.Image:
    """Weights are baked into the image so cold starts don't re-download 3.4 GB.

    Done as a shell command rather than `.run_function()` because `run_function`
    would need to serialize a local callable at build time, and a plain string
    has no such dependency on the local environment. The build needs the secret
    because the model repo is gated — this is the step that fails, loudly and
    early, if the user hasn't accepted the terms yet.

    transformers is floored at 4.56 because that is where `dtype=` replaced the
    now-removed `torch_dtype=`, and the image always resolves to the newest.
    """
    return (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install(
            "torch",
            "transformers>=4.56,<6",
            "accelerate>=1.2",
            "huggingface_hub>=0.27",
        )
        .run_commands(
            "python -c \"from huggingface_hub import snapshot_download; "
            f"snapshot_download('{MODEL}')\"",
            secrets=[secret],
        )
    )


def _generate(messages: list[dict], max_tokens: int = MAX_NEW_TOKENS) -> str:
    """Container-side. One section from a prepared chat transcript.

    The loaded model is cached on `builtins` rather than in a module global:
    this function arrives in the container as a cloudpickled object, and
    stashing state somewhere guaranteed to outlive a single deserialization is
    the difference between loading the model once per container and once per
    request.
    """
    import builtins
    import os
    import re

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    state = getattr(builtins, "_brandforge_state", None)
    if state is None:
        state = {}
        builtins._brandforge_state = state  # type: ignore[attr-defined]

    if "model" not in state:
        # HF_TOKEN comes from the inlined secret. The weights are already in the
        # image, so this normally reads from the local cache rather than the Hub.
        token = os.environ.get("HF_TOKEN")
        tokenizer = AutoTokenizer.from_pretrained(MODEL, token=token)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL, dtype=torch.float16, token=token
        ).to("cuda")
        model.eval()
        state["tokenizer"] = tokenizer
        state["model"] = model

    tokenizer = state["tokenizer"]
    model = state["model"]

    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            pad_token_id=tokenizer.eos_token_id,
        )
    text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    # Qwen3 emits an empty <think></think> block even in non-thinking mode.
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.DOTALL).lstrip()


def build_app(hf_token: str) -> modal.App:
    """A deployable App bound to this user's HF token.

    The token is inlined with `Secret.from_dict` instead of referencing a named
    Modal secret, so the user never has to run `modal secret create` — pasting
    their two API-token values in Settings is the whole setup.
    """
    # `serialized=True` alone is NOT enough. cloudpickle pickles functions from
    # importable modules *by reference* — measured at 57 bytes, just a pointer to
    # `app.brandforge.modal_backend`, which does not exist inside the container.
    # Registering the module forces by-value pickling so the code itself ships.
    # Must be modal's vendored cloudpickle, not the top-level package.
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
        # Belt and braces with serialized=True: never try to resolve or mount
        # local .py files, which do not exist inside a PyInstaller freeze.
        include_source=False,
    )(_generate)
    return app
