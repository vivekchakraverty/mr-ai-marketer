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


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point every test at a throwaway SQLite file, whether it asked or not.

    Autouse because the tests that needed isolating were not the ones that requested it.
    Anything that files something in the Library reaches db.add_item several layers down —
    generating an image saves the picture to the shelf, so `test_social_post_image_…` was
    quietly adding rows to the real Library on every run, with nothing in the test to
    suggest a database was involved. An opt-in fixture cannot catch that; the writes are
    incidental to what each test is about.

    Patching config.DB_PATH rather than the env var: it is a module-level constant
    computed once at import, so the env var has long since been read.
    """
    from app import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")


@pytest.fixture()
def app_db(_isolated_db):
    """The core-app DB (library/distribution_jobs/mail_messages/mail_events), initialised.

    Isolation comes from the autouse fixture above; this only creates the schema, for
    tests that read tables rather than just writing through code that creates its own.
    """
    from app import db

    db.init_db()
    return db
