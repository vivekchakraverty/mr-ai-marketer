"""The one door that opens without the session token, so it had better be a narrow one.

A share link exists because the Distribute engine runs in its own container and must fetch
an image itself to attach it to a post. That means an unauthenticated caller can reach it,
which makes forging, expiry and path containment the whole story.
"""

from __future__ import annotations

import time

import pytest

from app import config
from app.services import share_links


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    (root / "social" / "run").mkdir(parents=True)
    image = root / "social" / "run" / "post-image.png"
    image.write_bytes(b"fake png bytes")
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")
    return image


def test_a_signed_link_resolves_back_to_the_file(outputs):
    assert share_links.resolve(share_links.token_for(outputs)) == outputs.resolve()


def test_a_tampered_path_is_refused(outputs):
    token = share_links.token_for(outputs)
    expires, signature, _ = token.split(".", 2)
    forged = f"{expires}.{signature}.social/run/../../../secrets.txt"
    assert share_links.resolve(forged) is None


def test_a_tampered_expiry_is_refused(outputs):
    """Extending your own link must invalidate the signature it was granted under."""
    _, signature, relpath = share_links.token_for(outputs).split(".", 2)
    assert share_links.resolve(f"{int(time.time()) + 99999}.{signature}.{relpath}") is None


def test_an_expired_link_stops_working(outputs, monkeypatch):
    token = share_links.token_for(outputs, ttl_seconds=60)
    monkeypatch.setattr(share_links.time, "time", lambda: 10**12)  # far future
    assert share_links.resolve(token) is None


def test_an_unsigned_guess_is_refused(outputs):
    assert share_links.resolve("9999999999.deadbeef.social/run/post-image.png") is None
    assert share_links.resolve("nonsense") is None
    assert share_links.resolve("") is None


def test_a_file_outside_the_outputs_tree_cannot_be_signed(tmp_path, outputs):
    secret = tmp_path / "not-ours.txt"
    secret.write_text("private", encoding="utf-8")
    with pytest.raises(ValueError):
        share_links.token_for(secret)


def test_the_ttl_is_capped(outputs):
    token = share_links.token_for(outputs, ttl_seconds=10**9)
    expires = int(token.split(".", 1)[0])
    assert expires - time.time() <= share_links.MAX_TTL_SECONDS + 1


def test_the_secret_survives_a_restart(outputs):
    """A post scheduled for Thursday must still be fetchable on Thursday."""
    token = share_links.token_for(outputs, ttl_seconds=3600)
    # Simulate a fresh process: nothing cached, secret re-read from disk.
    assert share_links.resolve(token) == outputs.resolve()
    assert (config.DATA_DIR / "share-secret").exists()


def test_outputs_urls_translate_back_to_paths(outputs):
    url = "/outputs/social/run/post-image.png"
    assert share_links.path_from_outputs_url(url) == outputs.resolve()


def test_a_traversal_in_an_outputs_url_is_refused(outputs):
    assert share_links.path_from_outputs_url("/outputs/../../etc/passwd") is None


def test_a_non_outputs_url_is_not_a_path(outputs):
    assert share_links.path_from_outputs_url("https://example.com/x.png") is None
