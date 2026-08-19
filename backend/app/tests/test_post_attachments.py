"""Attaching a generated image to a post you send from Engage.

Engage posts through each network's own API from this process, so unlike Distribute it
can read the picture off disk — which is exactly why the read has to be fenced. The
`imageUrl` on a compose request arrives from the renderer, and the renderer is a browser:
anything it can be made to send, it will eventually send. So the two tests that matter are
that a path outside the outputs tree is refused, and that a refusal is a 400 the user can
read rather than a 500 traceback.
"""

from __future__ import annotations

import pytest

from app import config
from app.services import image_prompt, share_links


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    (root / "social" / "run").mkdir(parents=True)
    image = root / "social" / "run" / "companion.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")
    return image


def test_an_outputs_url_reads_back_as_bytes(outputs):
    filename, content = image_prompt.attachment_bytes("/outputs/social/run/companion.png")
    assert filename == "companion.png"
    assert content == outputs.read_bytes()


@pytest.mark.parametrize(
    "url",
    [
        "/outputs/../../../Windows/System32/drivers/etc/hosts",
        "https://example.com/someone-elses.png",
        "C:/Users/someone/private.png",
        "",
    ],
)
def test_anything_not_ours_is_refused(outputs, url):
    with pytest.raises(image_prompt.ImageRenderError):
        image_prompt.attachment_bytes(url)


def test_an_oversized_image_is_refused_before_the_upload(outputs, monkeypatch):
    """The cap is checked here, not by the network.

    Every one of the three has its own limit and its own way of saying no; failing at
    upload time means a post that half-happened. Refusing first is one message, and it
    names the size."""
    monkeypatch.setattr(image_prompt, "MAX_ATTACHMENT_BYTES", 8)
    with pytest.raises(image_prompt.ImageRenderError) as err:
        image_prompt.attachment_bytes("/outputs/social/run/companion.png")
    assert "limit" in str(err.value).lower()
