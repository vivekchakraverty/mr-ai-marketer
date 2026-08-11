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

# The BrandForge weights, and the two formats they are published in.
#
# `qwen3-1.7b-brandforge` is the DPO checkpoint merged into standalone weights — verified
# against the adapter rather than taken from the name: base + lora_B@lora_A * (alpha/r)
# reproduces its q_proj to 2.4e-4, which is bf16 rounding. So it and the -dpo-lora repo are
# the same model, and there is no SFT-only 1.7B published.
#
# It is the default because it is the only variant a user's own HF token can fetch (public,
# gating on automatic approval) and because it measured better than the quantisation below.
#
# The GGUF variant is the *same* checkpoint at Q4_K_M — precision is the only difference.
# Measured against it on three sections, the quant repeated whole lines noticeably more
# (0.34 vs 0.19 duplicate-line ratio), had lower lexical variety, and opened a <think>
# block every time, spending budget that the fp16 path puts into the section. Selecting it
# is just BRANDFORGE_MODEL_FILE, which flips the loader below.
#
# Getting the format wrong here is what broke this module before: it used to
# `snapshot_download` a GGUF-only repo and hand it to `AutoModelForCausalLM`, which cannot
# read GGUF, and with an empty default it could never deploy at all.
DEFAULT_MODEL = "vivekchakraverty/qwen3-1.7b-brandforge"
DEFAULT_MODEL_FILE = ""  # empty => a full safetensors repo, loaded with transformers

MODEL = os.environ.get("BRANDFORGE_MODEL", "").strip() or DEFAULT_MODEL
MODEL_FILE = os.environ.get("BRANDFORGE_MODEL_FILE", DEFAULT_MODEL_FILE).strip()
MODEL_PAGE = f"https://huggingface.co/{MODEL}"

# One named file ending in .gguf means llama.cpp; anything else means transformers. The
# whole format decision reduces to this flag, and both halves below branch on it.
IS_GGUF = MODEL_FILE.endswith(".gguf")

# Where a single GGUF lands. Unused on the transformers path, which loads by repo id from
# the image's own HF cache.
MODEL_PATH = f"/models/{MODEL_FILE}"

# The fp16 1.7B load is ~3.4 GB and the Q4 quant ~1.1 GB, so a T4 has headroom either way
# and is the cheaper card per second. This asked for an L4 when it was sized for a larger
# checkpoint than either of these.
GPU_KIND = "T4"

# Stay warm 5 min after the last call. A full document is ~12 sequential calls,
# so this keeps everything after the first section on a hot container.
SCALEDOWN_WINDOW = 300
CALL_TIMEOUT = 600

MAX_NEW_TOKENS = 1500


def _build_image(secret: modal.Secret) -> modal.Image:
    """Weights are baked into the image so cold starts don't re-download them.

    Done as shell commands rather than `.run_function()` because `run_function`
    would need to serialize a local callable at build time, and a plain string
    has no such dependency on the local environment. The build needs the secret
    so that a repo the token cannot read fails here — loudly, at setup, with the
    model page named — rather than on the user's first document.

    transformers is floored at 4.56 because that is where `dtype=` replaced the
    now-removed `torch_dtype=`, and the image always resolves to the newest.
    """
    if not IS_GGUF:
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

    return (
        # A CUDA base rather than debian_slim: the prebuilt llama-cpp wheel is linked
        # against the CUDA runtime and cuBLAS, and on a slim image it installs cleanly
        # and then fails at `import llama_cpp` with a missing libcublas. The -runtime tag
        # carries those libraries without the compiler toolchain, which nothing needs.
        #
        # The wheel comes prebuilt because compiling it against CUDA takes upwards of
        # twenty minutes inside the image build — the difference between setup being slow
        # and setup looking hung.
        modal.Image.from_registry(
            "nvidia/cuda:12.4.1-runtime-ubuntu22.04", add_python="3.11"
        )
        .pip_install(
            "llama-cpp-python>=0.3.4,<0.4",
            extra_index_url="https://abetlen.github.io/llama-cpp-python/whl/cu124",
        )
        # >=0.34 is where the `hf` CLI replaced `huggingface-cli`; the older name is gone
        # in 1.x, so anything lower can resolve to a version without the command below.
        .pip_install("huggingface_hub>=0.34")
        .run_commands(
            # One named file rather than the whole repo.
            f"hf download {MODEL} {MODEL_FILE} --local-dir /models",
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

    state = getattr(builtins, "_brandforge_state", None)
    if state is None:
        state = {}
        builtins._brandforge_state = state  # type: ignore[attr-defined]

    if IS_GGUF:
        from llama_cpp import Llama

        if "model" not in state:
            # Baked into the image at build time, so this is a local file read, not a
            # download. n_gpu_layers=-1 puts every layer on the GPU: the quant is small
            # enough to fit whole, and a partial offload is the slow path. n_ctx has to
            # cover the intake prompt plus max_tokens of output.
            state["model"] = Llama(
                model_path=MODEL_PATH, n_gpu_layers=-1, n_ctx=8192, verbose=False
            )

        # The chat template ships inside the GGUF, so llama.cpp applies it itself and the
        # container needs no tokenizer files. What it does not give us is transformers'
        # `enable_thinking=False`: the "/no_think" hint is the only lever, and measured
        # against this checkpoint it does not suppress the block — the model opens <think>
        # anyway. So the hint is a nudge and the stripping below is the guarantee.
        prepared = list(messages)
        if prepared and prepared[-1].get("role") == "user":
            prepared[-1] = {
                **prepared[-1],
                "content": f"{prepared[-1].get('content', '')}\n/no_think",
            }

        out = state["model"].create_chat_completion(
            messages=prepared, max_tokens=max_tokens, temperature=0.7, top_p=0.9
        )
        text = (out["choices"][0]["message"].get("content") or "") if out.get("choices") else ""
    else:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if "model" not in state:
            # HF_TOKEN comes from the inlined secret. The weights are already in the
            # image, so this normally reads from the local cache rather than the Hub.
            token = os.environ.get("HF_TOKEN")
            state["tokenizer"] = AutoTokenizer.from_pretrained(MODEL, token=token)
            model = AutoModelForCausalLM.from_pretrained(
                MODEL, dtype=torch.float16, token=token
            ).to("cuda")
            model.eval()
            state["model"] = model

        tokenizer = state["tokenizer"]
        # Here the template *does* take the switch, so thinking is genuinely off rather
        # than merely stripped afterwards.
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
        with torch.no_grad():
            out = state["model"].generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id,
            )
        text = tokenizer.decode(
            out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )

    # Take everything after the final </think> rather than regex-replacing the pair.
    # The pair-matching version fails open on the one case that actually matters: if the
    # token budget runs out mid-thought there is no closing tag, nothing matches, and the
    # model's reasoning gets handed to the user as their brand document.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    elif "<think>" in text:
        # Opened and never closed: the whole response was thinking, so there is no section
        # in it. Say so, rather than returning a fragment of reasoning as content.
        return ""
    return text.lstrip()


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
