"""Shared pytest fixtures. Each test gets an isolated SQLite DB under a temp data dir, so no
test touches the network, a real model, or the user's real data."""

import pathlib
import sys

import pytest

# Make `import vendor.leadgen...` work when pytest is run from anywhere: add backend/ (four
# parents up from this file: tests -> leadgen -> vendor -> backend).
_BACKEND = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture()
def lg_db(tmp_path, monkeypatch):
    """A fresh leadgen SQLite DB in a temp dir."""
    monkeypatch.setenv("LEADGEN_DATA_DIR", str(tmp_path))
    from vendor.leadgen import db

    db.init_db()
    return db
