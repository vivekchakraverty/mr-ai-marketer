"""Publish Distribute link cards with the same account used by the Bluesky creator."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from atproto import Client

from .. import config
from . import bluesky_link_card

_lock = RLock()
_runtime: tuple[str, str] | None = None
_client_cache: Client | None = None


def _account_file() -> Path:
    return config.DATA_DIR / "bluesky_distribution_account.json"


def set_credentials(identifier: str = "", password: str = "") -> bool:
    """Remember the Distribute account for this process, without persisting its secret."""
    global _runtime, _client_cache
    with _lock:
        _runtime = (identifier.strip(), password.strip()) if identifier.strip() and password.strip() else None
        _client_cache = None
    path = _account_file()
    if _runtime:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"identifier": _runtime[0]}), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return _runtime is not None


def _client() -> Client:
    global _client_cache
    with _lock:
        if _runtime:
            if _client_cache is None:
                client = Client()
                client.login(*_runtime)
                _client_cache = client
            return _client_cache

    from vendor.socialpost.src import bluesky as spg_bluesky
    from vendor.socialpost.src import config as spg_config  # noqa: F401 - loads saved settings

    import os

    expected = ""
    try:
        expected = str(json.loads(_account_file().read_text(encoding="utf-8")).get("identifier") or "")
    except (OSError, ValueError, TypeError):
        pass
    actual = (os.environ.get("BLUESKY_HANDLE") or "").strip()
    if expected and expected.lower() != actual.lower():
        raise RuntimeError(
            "The Distribute Bluesky account differs from the Bluesky Post Creator account. "
            "Reconnect Bluesky in Distribute before sending a link card."
        )
    return spg_bluesky.get_client()


def publish(payload: dict) -> str:
    """Send a post with an external embed; return its AT URI."""
    text = str(payload.get("text") or "").strip()
    url = str(payload.get("externalUrl") or "").strip() or bluesky_link_card.first_url(text)
    card = bluesky_link_card.describe(url) if url else None
    if not card:
        raise RuntimeError("The link in this Bluesky post could not be embedded. Check the link and retry.")
    client = _client()
    created = client.send_post(text, embed=bluesky_link_card.sdk_embed(client, card))
    return str(created.uri)
