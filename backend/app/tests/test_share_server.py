"""The one socket in this app that answers a caller outside the machine.

The Distribute engine runs in a container and attaches an image by fetching it, so a
generated image has to be reachable from outside. The main API stays on 127.0.0.1; this
second listener carries signed links and nothing else, and what it *refuses* is the whole
reason it exists rather than a wider bind on the main API.

Bound to 127.0.0.1 in these tests. The real bind address is the WSL adapter, which is not a
property of the code — it is measured from the running system — so it is passed in.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from app import config
from app.services import share_links, share_server


@pytest.fixture()
def serving(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    (root / "social" / "run").mkdir(parents=True)
    image = root / "social" / "run" / "post-image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pretend pixels" * 40)

    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")

    # Port 0 lets the OS pick a free one, so a developer already running the app does not
    # collide with these tests.
    share_server.stop()
    assert share_server.start("127.0.0.1", 0)
    port = share_server._server.server_address[1]  # noqa: SLF001 — the test owns this server
    yield f"http://127.0.0.1:{port}", image
    share_server.stop()


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, b""


def test_a_signed_link_returns_the_exact_bytes(serving):
    base, image = serving
    status, body = _get(f"{base}/shared/{share_links.token_for(image)}")
    assert status == 200
    assert body == image.read_bytes()


def test_a_forged_signature_is_refused(serving):
    base, image = serving
    token = share_links.token_for(image)
    expires, _, relpath = token.split(".", 2)
    status, _ = _get(f"{base}/shared/{expires}.{'0' * 64}.{relpath}")
    assert status == 404


def test_an_expired_link_is_refused(serving):
    base, image = serving
    # token_for clamps the floor to 60s, so expiry is forced by moving the clock instead.
    token = share_links.token_for(image, 60)
    import time

    real = time.time
    try:
        time.time = lambda: real() + 3600
        status, _ = _get(f"{base}/shared/{token}")
    finally:
        time.time = real
    assert status == 404


def test_the_rest_of_the_api_is_not_on_this_socket(serving):
    """The point of a second listener instead of binding the main API wider.

    If any of these ever answered, the narrow-bind design has quietly become a wide one.
    """
    base, _ = serving
    for path in ("/library", "/health", "/queue", "/", "/outputs/social/run/post-image.png"):
        status, _ = _get(f"{base}{path}")
        assert status == 404, f"{path} answered on the share socket"


def test_it_does_not_serve_outside_the_outputs_tree(serving, tmp_path):
    """Traversal is caught by share_links.resolve; asserted here on the live socket too,
    because this is the path that is actually exposed to another machine."""
    base, image = serving
    # Minting a link is what creates the signing secret on disk, and that file sitting one
    # directory above the outputs tree is exactly what a traversal would be reaching for.
    share_links.token_for(image)
    assert (tmp_path / "share-secret").exists()
    status, _ = _get(f"{base}/shared/9999999999.{'a' * 64}.../share-secret")
    assert status == 404
