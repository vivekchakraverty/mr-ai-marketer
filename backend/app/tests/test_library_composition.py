"""Saving a finished post — words, tags and picture — as one Library row.

The image arrives as an /outputs URL because that is what the renderer holds, and the
renderer is a browser: anything it can be made to send, it will eventually send. So the
interesting cases are the refusals, not the happy path.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import config
from app.routers import library
from app.services import share_links


@pytest.fixture()
def outputs(tmp_path, monkeypatch, app_db):
    root = tmp_path / "outputs"
    (root / "social" / "run").mkdir(parents=True)
    image = root / "social" / "run" / "post-image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")
    return image


def _save(**kwargs):
    return library.save_library_item(library.SaveRequest(**kwargs))


def test_the_post_its_tags_and_its_picture_land_in_one_row(outputs, app_db):
    result = _save(
        tool="Social",
        title="Bluesky post · ai tools",
        subtitle="Bluesky post",
        content="We shipped a thing.\n\n#ai #tools",
        imageUrl="/outputs/social/run/post-image.png",
    )
    item = app_db.get_item(result["libraryId"])
    assert item["content"] == "We shipped a thing.\n\n#ai #tools"
    # One row carrying both, which is the entire point — the picture and the words used to
    # arrive as two unrelated entries with nothing linking them.
    assert item["output_path"] == str(outputs)


def test_an_image_from_outside_the_outputs_tree_is_refused(outputs, app_db):
    with pytest.raises(HTTPException) as err:
        _save(
            tool="Social",
            title="x",
            content="words",
            imageUrl="/outputs/../../../Windows/System32/drivers/etc/hosts",
        )
    assert err.value.status_code == 400


def test_a_url_this_app_did_not_generate_is_refused(outputs, app_db):
    with pytest.raises(HTTPException) as err:
        _save(tool="Social", title="x", content="words", imageUrl="https://example.com/theirs.png")
    assert err.value.status_code == 400


def test_saving_without_a_picture_still_works(outputs, app_db):
    """Most posts never get an image, and the button has to keep those too."""
    result = _save(tool="Social", title="x", subtitle="Bluesky post", content="just words")
    item = app_db.get_item(result["libraryId"])
    assert item["output_path"] is None
    assert item["content"] == "just words"


def test_nothing_at_all_is_still_refused(outputs, app_db):
    with pytest.raises(HTTPException) as err:
        _save(tool="Social", title="x", content="   ")
    assert err.value.status_code == 400
