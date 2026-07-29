"""Central configuration for DocuMaker.

Every tunable is read from environment variables (optionally a local ``.env``
file), so model ids / devices can be swapped without touching code.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of the ``src`` package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root if present (silently ignored if missing).
load_dotenv(PROJECT_ROOT / ".env")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "")


# --- Paths -------------------------------------------------------------------
WORK_DIR = Path(os.getenv("DOCUMAKER_WORK_DIR", str(PROJECT_ROOT / "work"))).resolve()
WORK_DIR.mkdir(parents=True, exist_ok=True)

# --- HuggingFace credentials -------------------------------------------------
def _token_candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in (
        os.getenv("DOCUMAKER_HF_TOKEN"),
        os.getenv("HF_TOKEN"),
        os.getenv("HUGGINGFACEHUB_API_TOKEN"),
    ):
        value = (value or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


# Whether a UI token may be mirrored into the process environment. True for the
# default local single-user app; turned off automatically for shared/multi-user
# launches so one user's token can't leak to another via the global environment.
_ALLOW_ENV_TOKEN = True


def set_allow_env_token(allowed: bool) -> None:
    global _ALLOW_ENV_TOKEN
    _ALLOW_ENV_TOKEN = bool(allowed)


def apply_token(token: str | None) -> str | None:
    """Return the cleaned UI token and, **in single-user mode only**, mirror it
    into the process ``HF_TOKEN``/``HUGGINGFACEHUB_API_TOKEN`` so huggingface_hub
    (InferenceClient auto-discovery, model downloads) also uses it.

    In multi-user/shared mode the environment is left untouched — the token is
    still threaded explicitly to the LLM and captioner, so it stays scoped to the
    caller's session and nothing leaks across users. Returns None if empty.
    """
    token = (token or "").strip()
    if token and _ALLOW_ENV_TOKEN:
        os.environ["HF_TOKEN"] = token
        os.environ["HUGGINGFACEHUB_API_TOKEN"] = token
    return token or None


@functools.lru_cache(maxsize=1)
def resolve_hf_token() -> str | None:
    """Return the first *valid* HF token among the configured candidates.

    Environments often have a stale ``HF_TOKEN`` alongside a working
    ``HUGGINGFACEHUB_API_TOKEN`` (or vice versa). We validate via ``whoami`` and
    pick the one that authenticates, then point huggingface_hub's own
    auto-discovery (model downloads, etc.) at the same working token.
    """
    candidates = _token_candidates()
    if not candidates:
        return None

    chosen = candidates[0]
    try:
        from huggingface_hub import whoami

        for token in candidates:
            try:
                whoami(token=token)
                chosen = token
                break
            except Exception:
                continue
    except Exception:
        pass  # offline / hub import issue — fall back to the first candidate

    os.environ["HF_TOKEN"] = chosen
    os.environ["HUGGINGFACEHUB_API_TOKEN"] = chosen
    return chosen

# --- Text LLM (HF Inference API) --------------------------------------------
LLM_MODEL = os.getenv("DOCUMAKER_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
LLM_PROVIDER = os.getenv("DOCUMAKER_LLM_PROVIDER", "").strip() or None
LLM_MAX_TOKENS = int(os.getenv("DOCUMAKER_LLM_MAX_TOKENS", "4096"))
LLM_TEMPERATURE = float(os.getenv("DOCUMAKER_LLM_TEMPERATURE", "0.3"))
# Approx. characters of transcript per LLM chunk (keeps prompts within context).
LLM_CHUNK_CHARS = int(os.getenv("DOCUMAKER_LLM_CHUNK_CHARS", "6000"))

# --- Vision LLM (HF Inference API) + local fallback -------------------------
ENABLE_VISION = _flag("DOCUMAKER_ENABLE_VISION", "1")
VLM_MODEL = os.getenv("DOCUMAKER_VLM_MODEL", "Qwen/Qwen2-VL-7B-Instruct")
VLM_PROVIDER = os.getenv("DOCUMAKER_VLM_PROVIDER", "").strip() or None
LOCAL_CAPTION_MODEL = os.getenv(
    "DOCUMAKER_LOCAL_CAPTION_MODEL", "Salesforce/blip-image-captioning-base"
)

# --- Whisper (local faster-whisper) -----------------------------------------
WHISPER_MODEL = os.getenv("DOCUMAKER_WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("DOCUMAKER_WHISPER_DEVICE", "auto").strip().lower()
# Blank => choose automatically per device (int8_float16 on CUDA, int8 on CPU).
WHISPER_COMPUTE_TYPE = os.getenv("DOCUMAKER_WHISPER_COMPUTE_TYPE", "").strip()

# --- Frame extraction --------------------------------------------------------
SCENE_THRESHOLD = float(os.getenv("DOCUMAKER_SCENE_THRESHOLD", "27.0"))
SCENE_MIN_LEN_SEC = float(os.getenv("DOCUMAKER_SCENE_MIN_LEN_SEC", "1.0"))
DEDUP_HASH_DISTANCE = int(os.getenv("DOCUMAKER_DEDUP_HASH_DISTANCE", "6"))

# --- DOCX --------------------------------------------------------------------
DOCX_IMAGE_WIDTH_INCHES = float(os.getenv("DOCUMAKER_DOCX_IMAGE_WIDTH_INCHES", "5.5"))

# --- External binaries -------------------------------------------------------
FFMPEG_BIN = os.getenv("DOCUMAKER_FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("DOCUMAKER_FFPROBE_BIN", "ffprobe")


def session_dir(session_id: str) -> Path:
    """Return (creating if needed) the working directory for one session."""
    d = WORK_DIR / session_id
    (d / "frames").mkdir(parents=True, exist_ok=True)
    return d
