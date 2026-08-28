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
    # Uploaded clips land under uploads/, which is the other thing a composed post can
    # carry into its Library row.
    (root / "uploads" / "run").mkdir(parents=True)
    (root / "uploads" / "run" / "clip.mp4").write_bytes(b"\x00\x00\x00 ftypmp42" + b"0" * 32)
    image = root / "social" / "run" / "post-image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")
    return image


def _save(**kwargs):
    return library.save_library_item(library.SaveRequest(**kwargs))


def _finish(item_id, **kwargs):
    """What the composition button does when the generator already filed a row."""
    return library.update_library_item(item_id, library.UpdateRequest(**kwargs))


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


def test_a_composition_finishes_the_generation_row_instead_of_adding_one(outputs, app_db):
    """The Mastodon and Bluesky generators file a row as they produce text. Sending the
    finished composition used to add a second, so one post arrived as two near-identical
    cards. It now lands on the row that already exists."""
    generated = app_db.add_item(
        tool="Social",
        title="Some days I feel like I am juggling three different realities",
        subtitle="mastodon · mastodon.social · personal",
        content="Some days I feel like I am juggling three different realities",
    )
    _finish(
        generated["id"],
        title="Mastodon post · personal",
        subtitle="Mastodon post",
        content="Some days I feel like I am juggling three different realities\n\n#dragons",
        imageUrl="/outputs/social/run/post-image.png",
    )

    assert app_db.count_items() == 1
    stored = app_db.get_item(generated["id"])
    assert stored["title"] == "Mastodon post · personal"
    assert stored["subtitle"] == "Mastodon post"
    assert stored["content"].endswith("#dragons")
    assert stored["output_path"] == str(outputs)


def test_finishing_a_row_applies_the_same_containment_check_as_saving(outputs, app_db):
    """An update is not a back door around the check the save path performs."""
    generated = app_db.add_item(tool="Social", title="x", subtitle="", content="words")
    with pytest.raises(HTTPException) as err:
        _finish(generated["id"], imageUrl="/outputs/../../../Windows/System32/config/SAM")
    assert err.value.status_code == 400
    assert app_db.get_item(generated["id"])["output_path"] is None


def test_an_uploaded_clip_is_filed_as_the_rows_attachment(outputs, app_db):
    """A composed video used to reach Engage and nowhere else, so distributing a saved post
    meant picking the same file again in the send dialog. It shares the picture's slot,
    which is sound because a post carries one embed."""
    result = _save(
        tool="Social",
        title="Bluesky post · personal",
        subtitle="Bluesky post",
        content="we're weird.\n\n#siblingenergy",
        videoFileUrl="/outputs/uploads/run/clip.mp4",
    )
    item = app_db.get_item(result["libraryId"])
    assert item["output_path"] == str(outputs.parents[2] / "uploads" / "run" / "clip.mp4")


def test_a_clip_can_finish_a_generation_row_too(outputs, app_db):
    generated = app_db.add_item(tool="Social", title="x", subtitle="", content="words")
    _finish(generated["id"], videoFileUrl="/outputs/uploads/run/clip.mp4")
    stored = app_db.get_item(generated["id"])
    assert stored["output_path"].endswith("clip.mp4")


def test_a_clip_from_outside_the_outputs_tree_is_refused(outputs, app_db):
    with pytest.raises(HTTPException) as err:
        _save(tool="Social", title="x", content="words", videoFileUrl="https://example.com/theirs.mp4")
    assert err.value.status_code == 400


def test_an_image_and_a_clip_together_are_refused(outputs, app_db):
    """One row, one file. Naming both is a caller bug rather than a choice to resolve
    silently — the send path refuses the same pairing for the same reason."""
    with pytest.raises(HTTPException) as err:
        _save(
            tool="Social",
            title="x",
            content="words",
            imageUrl="/outputs/social/run/post-image.png",
            videoFileUrl="/outputs/uploads/run/clip.mp4",
        )
    assert err.value.status_code == 400
