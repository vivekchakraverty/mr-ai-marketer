"""Calls the BrandForge inference Space (fine-tuned Qwen3, GGUF on free CPU)
via gradio_client — same pattern as routers/blog_writer.py.

The Space exposes `/generate_section(intake_json, section_name) -> markdown`.
We call it once per section (bounded CPU requests), assembling the document on
this side (see routers/brand_forge.py)."""
from __future__ import annotations

import json

from gradio_client import Client

from .. import config
from .client import BrandForgeError

# The Space Brand Studio talks to unless BRANDFORGE_SPACE says otherwise.
#
# This is a deliberate exception to the rule in config.py that no hosted endpoint gets a
# default, and it is worth being clear about the cost: every installation that has not set
# the variable generates on this one account's Space. It is free CPU hardware, so the price
# is a shared queue rather than a bill, and the owner is the one who chose to publish it.
#
# It works for anyone because the Space is *protected*, not private — Hugging Face keeps the
# source private while serving the running app publicly, so an anonymous gradio_client can
# call /generate_section without a token.
#
# The environment variable still wins, so an operator running their own copy overrides this
# without touching the code.
FALLBACK_SPACE_ID = "vivekchakraverty/brandforge-qwen3-small"
DEFAULT_SPACE_ID = config.BRANDFORGE_SPACE or FALLBACK_SPACE_ID

# Cache one client per (space_id, token) — building a Client fetches the Space
# config over the network, which also wakes a sleeping free Space.
_clients: dict[tuple[str, str], Client] = {}


def _get_client(space_id: str, hf_token: str) -> Client:
    key = (space_id, hf_token or "")
    client = _clients.get(key)
    if client is None:
        try:
            client = Client(space_id, token=hf_token or None)
        except Exception as exc:  # noqa: BLE001 - bad id, private without token, Space down
            raise BrandForgeError(
                f"Couldn't reach the BrandForge Space '{space_id}'. Check the Space ID in Settings "
                f"and that the Space is running. ({exc})"
            ) from exc
        _clients[key] = client
    return client


def generate_section(space_id: str, hf_token: str, intake: dict, section_name: str) -> str:
    """One section from the Space. Raises BrandForgeError on failure (including
    the Space's own 'ERROR:'-prefixed responses)."""
    space_id = (space_id or "").strip() or DEFAULT_SPACE_ID
    if not space_id:
        # A backstop, not the normal path: DEFAULT_SPACE_ID has a fallback, so this only
        # fires if that is cleared. It exists because the empty string otherwise reaches
        # huggingface_hub and comes back as "Repo id must use alphanumeric chars, '-', '_'
        # or '.'…" — a validation error about a field the user was never shown.
        raise BrandForgeError(
            "Brand Studio has no generator configured. Connect your Modal account in "
            "Settings → Brand Studio to run it on your own GPU, or set BRANDFORGE_SPACE "
            "to a Space you have deployed."
        )

    client = _get_client(space_id, hf_token)
    try:
        result = client.predict(
            json.dumps(intake, ensure_ascii=False),
            section_name,
            api_name="/generate_section",
        )
    except Exception as exc:  # noqa: BLE001 - network/queue failure
        # Drop a possibly-stale cached client so the next attempt rebuilds it.
        _clients.pop((space_id.strip(), hf_token or ""), None)
        raise BrandForgeError(f"BrandForge Space call failed: {exc}") from exc

    text = result if isinstance(result, str) else str(result)
    if text.strip().startswith("ERROR:"):
        raise BrandForgeError(text.strip()[len("ERROR:"):].strip() or "Space returned an error.")
    return text
