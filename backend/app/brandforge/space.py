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

# No default. Brand Studio runs on the Space *you* deploy — set it in Settings, or via
# the BRANDFORGE_SPACE environment variable.
DEFAULT_SPACE_ID = config.BRANDFORGE_SPACE

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
