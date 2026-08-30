"""The Modal app the Email Writer deploys into the *user's own* Modal workspace.

WHY THIS EXISTS. The Email Writer's default home is a free Hugging Face CPU Space, which
serves the same model on two shared vCPUs: measured against the live Space, one email takes
about 84 seconds, and every caller in the world queues behind every other. That is the price
of genuinely free. The same GGUF on a T4 answers in seconds, on hardware nobody else shares.

WHAT IT COSTS, SAID PLAINLY. Modal's starter plan is $30 of credits a month, not an unlimited
free tier. A T4 bills at $0.000164/second and containers scale to zero, so a warm email is
well under a cent and the credits cover thousands a month — but they are credits, they run
out, and they are billed to whoever's API token is configured. That is the trade against the
Space: speed and exclusivity, paid for in something that can be exhausted.

SAME WEIGHTS AS THE SPACE. Deliberately the same Q4_K_M GGUF the Space serves, not a
re-quantisation or the unmerged adapter. Moving where inference runs should not quietly
change what the model writes, so the only variable is the hardware under it.

Structurally this mirrors app/brandforge/modal_backend.py, including the details that took
a while to get right there: a CUDA runtime base rather than debian_slim (the prebuilt
llama-cpp wheel links against cuBLAS and fails at import on a slim image), the prebuilt cu124
wheel (compiling against CUDA inside an image build takes twenty minutes and looks hung), and
weights baked in at build time so a cold start is a local file read.
"""
from __future__ import annotations

import os
import sys

import modal

# Deployed under this name in the user's workspace, namespaced so it cannot collide with an
# app they already have — and distinct from the Brand Studio one, which lives beside it.
APP_NAME = "mr-ai-marketer-email-writer"
FUNCTION_NAME = "generate"

# The marketing-email model, in the form the Space already serves. Overridable for anyone
# who has published their own fine-tune.
DEFAULT_MODEL = "vivekchakraverty/qwen2.5-7b-marketing-email-gguf"
DEFAULT_MODEL_FILE = "Qwen2.5-7B-Instruct.Q4_K_M.gguf"

MODEL = os.environ.get("EMAIL_WRITER_MODAL_MODEL", "").strip() or DEFAULT_MODEL
MODEL_FILE = os.environ.get("EMAIL_WRITER_MODAL_MODEL_FILE", "").strip() or DEFAULT_MODEL_FILE
MODEL_PATH = f"/models/{MODEL_FILE}"
MODEL_PAGE = f"https://huggingface.co/{MODEL}"

# A 7B at Q4_K_M is about 4.5 GB, so it fits a T4 whole — and a full offload is the fast
# path, where a partial one is barely better than CPU. T4 is also the cheapest card per
# second, which is the whole point of not needing a bigger one.
GPU_KIND = "T4"

# Five minutes warm. Writing several emails in a sitting is the normal way this tool is
# used, and only the first should pay a cold start. Idle containers bill nothing once they
# scale down, so this trades a few warm seconds against repeated model loads.
SCALEDOWN_WINDOW = 300
CALL_TIMEOUT = 600

# Matches the Space's own cap, so the two produce comparably-sized emails.
MAX_NEW_TOKENS = 512

# Must match the system prompt used during training exactly — the model was fine-tuned
# against this specific instruction, and the Space uses the same string.
SYSTEM_PROMPT = "You are an expert email marketing copywriter."


def _build_image(secret: modal.Secret) -> modal.Image:
    """Weights baked in at build time, so a cold start does not re-download 4.5 GB.

    The build takes the secret so that a repo the token cannot read fails here — loudly, at
    setup, with the model page named — rather than on the user's first email.
    """
    return (
        modal.Image.from_registry(
            "nvidia/cuda:12.4.1-runtime-ubuntu22.04", add_python="3.11"
        )
        .pip_install(
            "llama-cpp-python>=0.3.4,<0.4",
            extra_index_url="https://abetlen.github.io/llama-cpp-python/whl/cu124",
        )
        # >=0.34 is where the `hf` CLI replaced `huggingface-cli`, which is gone in 1.x.
        .pip_install("huggingface_hub>=0.34")
        .run_commands(
            f"hf download {MODEL} {MODEL_FILE} --local-dir /models",
            secrets=[secret],
        )
    )


# Reproduced from the Space's app.py rather than imported: the container ships only this
# function, and the filter has to travel with it. ~1,006 training pairs were synthesised
# from a source gallery's page metadata, and the model occasionally reproduces that page
# title as a subject line, so any line carrying one is dropped before it reaches a user.
LEAK_MARKERS = (
    "email inspiration from",
    "email love",
    "emaillove",
    "really good emails",
    "reallygoodemails",
    "just good copy",
    "goodemailcopy",
)


def _sanitize(text: str) -> str:
    low_markers = LEAK_MARKERS
    kept = [line for line in (text or "").split("\n")
            if not any(marker in line.lower() for marker in low_markers)]
    while kept and not kept[0].strip():
        kept.pop(0)
    return "\n".join(kept).strip()


def _generate(instruction: str, max_tokens: int = MAX_NEW_TOKENS) -> str:
    """Container-side. One finished email from one freeform brief.

    The loaded model is cached on `builtins` rather than a module global: this function
    arrives in the container as a cloudpickled object, and stashing state somewhere
    guaranteed to outlive a single deserialization is the difference between loading 4.5 GB
    once per container and once per request.
    """
    import builtins

    from llama_cpp import Llama

    state = getattr(builtins, "_emailwriter_state", None)
    if state is None:
        state = {}
        builtins._emailwriter_state = state  # type: ignore[attr-defined]

    if "model" not in state:
        # Baked into the image, so this is a local read. n_gpu_layers=-1 puts every layer
        # on the card; the quant fits whole and a partial offload is the slow path.
        state["model"] = Llama(
            model_path=MODEL_PATH, n_gpu_layers=-1, n_ctx=2048, verbose=False
        )

    out = state["model"].create_chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ],
        max_tokens=max_tokens,
        temperature=0.7,
    )
    raw = (out["choices"][0]["message"].get("content") or "") if out.get("choices") else ""
    return _sanitize(raw)


def build_app(hf_token: str) -> modal.App:
    """A deployable App bound to this user's HF token.

    The token is inlined with `Secret.from_dict` rather than referencing a named Modal
    secret, so nobody has to run `modal secret create` — pasting two API-token values in
    Settings is the whole setup.
    """
    # `serialized=True` alone is NOT enough. cloudpickle pickles functions from importable
    # modules *by reference* — a pointer to `app.emailwriter.modal_backend`, which does not
    # exist inside the container. Registering the module forces by-value pickling so the
    # code itself ships. Must be modal's vendored cloudpickle, not the top-level package.
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
        # Belt and braces with serialized=True: never try to resolve or mount local .py
        # files, which do not exist inside a PyInstaller freeze.
        include_source=False,
    )(_generate)
    return app
