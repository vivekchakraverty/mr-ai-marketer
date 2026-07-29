"""Shared pytest fixtures for app/ tests. Each test gets an isolated SQLite DB
in a temp dir, so no test touches the network or the user's real data.

Unlike vendor/leadgen's own db.py (whose data_dir() is a function re-evaluated
on every call, so its tests can just monkeypatch the LEADGEN_DATA_DIR env var),
app/config.py's DB_PATH is a module-level constant computed once at import —
monkeypatching the env var after that import has already happened would do
nothing, so these fixtures patch the already-computed attribute directly.
"""

import pathlib
import sys

import pytest

# Make `import app...` work when pytest is run from anywhere: add backend/ (two
# parents up from this file: tests -> app -> backend).
_BACKEND = pathlib.Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    """A fresh core-app SQLite DB (library/distribution_jobs/mail_messages/
    mail_events) in a temp dir."""
    from app import config, db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db.init_db()
    return db
