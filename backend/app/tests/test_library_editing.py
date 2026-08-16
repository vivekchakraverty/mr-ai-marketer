"""Editing a saved Library item — what the Library's autosaving editor relies on."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import db
from app.routers import library


@pytest.fixture()
def item(tmp_path, monkeypatch):
    """A saved item in a throwaway database, so nothing touches the real Library."""
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "test.sqlite3")
    db.init_db()
    return db.add_item(tool="Social", title="A post", subtitle="bluesky", content="first draft")


def test_content_edit_is_persisted(item):
    library.update_library_item(item["id"], library.UpdateRequest(content="second draft"))
    assert db.get_item(item["id"])["content"] == "second draft"


def test_only_the_fields_sent_are_touched(item):
    """An autosaving editor sends content alone and must not blank the title."""
    library.update_library_item(item["id"], library.UpdateRequest(content="edited"))
    stored = db.get_item(item["id"])
    assert stored["title"] == "A post"
    assert stored["subtitle"] == "bluesky"


def test_title_can_be_edited_without_touching_content(item):
    library.update_library_item(item["id"], library.UpdateRequest(title="Renamed"))
    stored = db.get_item(item["id"])
    assert stored["title"] == "Renamed"
    assert stored["content"] == "first draft"


def test_editing_does_not_reorder_the_shelf(item):
    """created_at is when the thing was generated, and the Library sorts by it.

    Bumping it per save would make an item climb to the top while its author was still
    typing into it.
    """
    before = db.get_item(item["id"])["created_at"]
    library.update_library_item(item["id"], library.UpdateRequest(content="edited"))
    assert db.get_item(item["id"])["created_at"] == before


def test_an_emptied_box_stays_empty(item):
    """Saving rejects blank input; editing must not resurrect old text over a deliberate clear."""
    library.update_library_item(item["id"], library.UpdateRequest(content=""))
    assert db.get_item(item["id"])["content"] == ""


def test_whitespace_is_stored_as_typed(item):
    # The editor sends exactly what is in the box; trimming here would fight the cursor.
    library.update_library_item(item["id"], library.UpdateRequest(content="a line\n\n"))
    assert db.get_item(item["id"])["content"] == "a line\n\n"


def test_editing_something_that_no_longer_exists_is_a_404(item):
    with pytest.raises(HTTPException) as excinfo:
        library.update_library_item("does-not-exist", library.UpdateRequest(content="x"))
    assert excinfo.value.status_code == 404


def test_a_file_backed_item_keeps_its_document(tmp_path, monkeypatch):
    """output_path points at the tool's own document; editing the note must not touch it."""
    monkeypatch.setattr(db.config, "DB_PATH", tmp_path / "test.sqlite3")
    db.init_db()
    doc = tmp_path / "report.docx"
    doc.write_bytes(b"original bytes")
    saved = db.add_item(
        tool="Docs", title="Report", subtitle="", content="a note", output_path=str(doc)
    )

    library.update_library_item(saved["id"], library.UpdateRequest(content="edited note"))

    assert doc.read_bytes() == b"original bytes"
    assert db.get_item(saved["id"])["output_path"] == str(doc)
