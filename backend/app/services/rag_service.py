"""Optional remote retrieval for the Marketing Plan tool.

The default stays local: the app downloads the corpus index once and searches it on the
user's own machine, so nothing about what they are planning leaves the computer. This module
is the alternative for people who would rather not hold 1.1 GB — point RAG_SERVICE_URL at a
retrieval Space (see resources/rag-retrieval-space) and queries go there instead.

Deliberately opt-in, and deliberately not defaulted to anyone's Space. A shared retrieval
endpoint means every user's plan generation runs through whoever deployed it, which is the
exact coupling the rest of this app was changed to avoid. If you turn this on, it should be
because you host it.

What moves is more than the lookup. `compose()` sends the *prompt* to the Space, which fills
in the passages and runs the model there, and returns only the prose. That is deliberate: a
retrieval endpoint hands the corpus out a few extracts at a time and enough calls rebuild it,
while a compose endpoint hands out writing. If you host a corpus you cannot freely
redistribute, this is the difference that matters.

Who pays is unchanged: the caller's own Hugging Face token goes with the request and the
inference is billed to it, exactly as it was when the call ran on their machine. What *is*
new is that the token leaves the machine to get there. Nothing here can make that safe on
someone else's Space — only a fine-grained, inference-scoped token and a Space you trust can
— which is the other reason this path is opt-in and unset by default.
"""
from __future__ import annotations

import base64
import json
import os
import re

from .. import config

# Seconds to wait for a retrieval. Generous because a sleeping Space has to wake and sync a
# multi-GB index — but bounded, because grounding is an enhancement and a plan generated
# without it beats a user watching a spinner.
TIMEOUT = int(os.environ.get("RAG_SERVICE_TIMEOUT", "90"))

_client = None
_client_src = ""


class RagServiceError(RuntimeError):
    """Raised when the remote service could not answer. Callers fall back to local/none."""


def is_configured() -> bool:
    return bool(config.RAG_SERVICE_URL)


def _get_client():
    """A cached gradio_client for the configured Space.

    gradio_client rather than hand-rolled HTTP, deliberately. Gradio's endpoint layout is a
    version-dependent detail — posting to a guessed path returns 405 rather than anything
    useful — and the client resolves the right route from the Space's own config, the same
    way blog_writer and email_writer already talk to their Spaces.
    """
    global _client, _client_src
    from gradio_client import Client

    src = config.RAG_SERVICE_URL
    if _client is None or _client_src != src:
        try:
            _client = Client(src, verbose=False)
        except Exception as err:  # noqa: BLE001
            raise RagServiceError(f"could not connect to {src}: {err}") from err
        _client_src = src
    return _client


def _category_arg(category) -> str:
    """The vendored pipeline passes a category as a string, a list, or nothing.

    Flattened to a comma-separated string because the Space's entry point takes scalars; it
    splits the value back out and builds an `$in` filter when there is more than one.
    """
    if not category:
        return ""
    if isinstance(category, (list, tuple, set)):
        return ",".join(str(c).strip() for c in category if str(c).strip())
    return str(category).strip()


def retrieve(query: str, top_k: int = 8, category=None) -> list[str]:
    """Passages for a query, via the configured retrieval Space.

    Returns [] rather than raising when the service answers but has nothing for us — a Space
    still warming up after a sleep is a normal state, not an error worth failing a plan over.
    A genuine failure (unreachable, refused, malformed) raises so the caller can log it.
    """
    if not is_configured():
        return []

    client = _get_client()
    try:
        result = client.predict(
            query,
            int(top_k),
            _category_arg(category),
            config.RAG_SERVICE_KEY,
            api_name="/search",
        )
    except Exception as err:  # noqa: BLE001 — any transport/protocol failure
        raise RagServiceError(f"retrieval call failed: {err}") from err

    if not isinstance(result, dict):
        raise RagServiceError(f"unexpected response shape: {type(result).__name__}")

    if not result.get("ok"):
        status = result.get("status")
        # "unauthorised" is a configuration mistake, not a transient state — say so loudly
        # rather than letting every plan quietly lose its grounding.
        if status == "unauthorised":
            raise RagServiceError(
                "the retrieval Space rejected our key — set RAG_SERVICE_KEY to match the "
                "Space's RAG_APP_KEY"
            )
        print(f"[rag-service] not ready: {status}")
        return []
    return list(result.get("passages") or [])


# --------------------------------------------------------------------------- compose

# A placeholder the pipeline drops into its prompt where the retrieved passages belong. The
# Space substitutes it; nothing else ever does. It carries the query and filters with it so
# there is no second argument to keep in step and no state to hold between the two calls.
# ASCII on purpose. The obvious choice was a pair of bracket glyphs, but this string ends
# up inside prompts that get logged, and a Windows console defaulting to cp1252 turns a
# non-ASCII marker into a UnicodeEncodeError in the middle of a generation.
MARKER_PREFIX = "<<RAGREMOTE:"
MARKER_SUFFIX = ">>"


def marker(query: str, top_k: int = 8, category=None) -> str:
    """The stand-in for a block of passages, to be resolved on the far side."""
    spec = {"q": (query or "").strip()[:4000], "k": int(top_k or 8), "cat": _category_arg(category)}
    payload = base64.urlsafe_b64encode(json.dumps(spec, ensure_ascii=False).encode()).decode()
    return f"{MARKER_PREFIX}{payload}{MARKER_SUFFIX}"


def has_marker(text: str) -> bool:
    return MARKER_PREFIX in (text or "")


def strip_markers(text: str) -> str:
    """Remove any unresolved markers — used when falling back to a local model.

    A marker that reaches an LLM unresolved is a line of base64 in the middle of a prompt,
    which is worse than no grounding at all.
    """
    return re.sub(
        re.escape(MARKER_PREFIX) + r"[A-Za-z0-9+/=_-]+" + re.escape(MARKER_SUFFIX),
        "(no grounding context available for this run)",
        text or "",
    )


def compose(prompt: str, hf_token: str, model: str, max_tokens: int = 3500,
            temperature: float = 0.4) -> str:
    """Send a prompt to the Space, get back what the model wrote.

    This is the reason the remote path exists at all. `retrieve` brings passages back here to
    be pasted into a prompt, which means the corpus leaves the Space a few extracts at a time
    and enough calls rebuild it. `compose` sends the prompt *there*, so the passages are only
    ever assembled inside the Space and what comes back is prose.

    The caller's Hugging Face token goes with the request, because generation is billed to
    them rather than to whoever runs the Space. That is a real disclosure — a token leaving
    the machine — and the honest mitigation is a fine-grained, inference-only token and a
    Space you trust. It is why this whole path stays opt-in.
    """
    if not is_configured():
        raise RagServiceError("no retrieval service configured")

    client = _get_client()
    try:
        result = client.predict(
            prompt,
            hf_token,
            model,
            int(max_tokens),
            float(temperature),
            config.RAG_SERVICE_KEY,
            api_name="/compose",
        )
    except Exception as err:  # noqa: BLE001
        raise RagServiceError(f"compose call failed: {err}") from err

    if not isinstance(result, dict):
        raise RagServiceError(f"unexpected response shape: {type(result).__name__}")
    if not result.get("ok"):
        status = str(result.get("status") or "unknown error")
        if status == "unauthorised":
            raise RagServiceError(
                "the retrieval Space rejected our key — set RAG_SERVICE_KEY to match the "
                "Space's RAG_APP_KEY"
            )
        raise RagServiceError(status)

    text = str(result.get("text") or "")
    if not text.strip():
        raise RagServiceError("the service returned an empty completion")
    if not result.get("grounded"):
        # Worth saying: the plan is fine, but it was written without the corpus behind it.
        print("[rag-service] composed without grounding — the index had nothing for this query")
    return text
