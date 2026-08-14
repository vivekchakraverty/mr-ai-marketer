from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.routers import mastodon_post as mp


def test_corpus_key_separates_instances():
    a = mp._corpus_niche("gamedev", "hachyderm.io")
    b = mp._corpus_niche("gamedev", "toot.garden")
    assert a != b
    # Still carries the platform, which is what keeps the Bluesky scheduler's
    # refresh_exemplars from deactivating these rows along with its own.
    assert "mastodon" in a and "mastodon" in b


def test_legacy_key_is_the_pre_split_shape():
    # Rows written before the split are found by this exact string, so it must not drift.
    assert mp._legacy_corpus_niche("gamedev") == "gamedev · mastodon"
    assert mp._corpus_niche("gamedev", "x.social") == "gamedev · mastodon · x.social"


def test_fallback_excludes_the_instance_itself_and_includes_legacy(monkeypatch):
    monkeypatch.setattr(
        mp.db,
        "list_mastodon_acks",
        lambda: [
            {"instance": "hachyderm.io"},
            {"instance": "toot.garden"},
            {"instance": "https://Mas.to/"},  # stored with scheme/case/slash
        ],
    )
    keys = mp._fallback_keys("gamedev", "hachyderm.io")

    # Borrowing from yourself would double-count your own pool against the threshold.
    assert mp._corpus_niche("gamedev", "hachyderm.io") not in keys
    assert mp._corpus_niche("gamedev", "toot.garden") in keys
    # Normalised, or an instance saved with a scheme would never match its own rows.
    assert mp._corpus_niche("gamedev", "mas.to") in keys
    assert mp._legacy_corpus_niche("gamedev") in keys


def test_fallback_survives_an_empty_ack_table(monkeypatch):
    # A first run has no acks at all; the legacy pool is still worth offering.
    monkeypatch.setattr(mp.db, "list_mastodon_acks", lambda: [])
    assert mp._fallback_keys("gamedev", "hachyderm.io") == [mp._legacy_corpus_niche("gamedev")]


def test_corpus_key_round_trips_back_to_niche_and_host():
    assert mp._split_corpus_niche(mp._corpus_niche("gamedev", "toot.garden")) == (
        "gamedev",
        "toot.garden",
    )
    # A legacy row carries no instance, so there is nothing to hand _rebuild_pool.
    assert mp._split_corpus_niche(mp._legacy_corpus_niche("gamedev")) is None
    # Niche names are user-supplied: one containing the separator must still round-trip,
    # which a plain split on " · " would get wrong.
    odd = "ai · mastodon · robots"
    assert mp._split_corpus_niche(mp._corpus_niche(odd, "mas.to")) == (odd, "mas.to")


# ---------------------------------------------------------------------------
# The snapshot job's rebuild step
# ---------------------------------------------------------------------------


class _FakeQuery:
    """Ignores filters and returns the whole fake table; the tests supply only the
    rows the query under test is meant to see."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows))


class _FakeSpgDb:
    def __init__(self, tables):
        self._tables = tables
        self.upserts: list[tuple[str, list[dict]]] = []
        self.client = SimpleNamespace(table=lambda name: _FakeQuery(self._tables.get(name, [])))

    def get_client(self):
        return self.client

    def utcnow(self):
        return datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def iso(self, value):
        return value.isoformat()

    def list_niches(self):
        return [{"name": "science", "active": True}]

    def upsert(self, table, rows, on_conflict=None):
        self.upserts.append((table, list(rows)))


def _fake_status():
    return SimpleNamespace(
        favourites=12,
        reblogs=3,
        replies=4,
        account=SimpleNamespace(followers=200),
    )


def test_snapshot_rebuilds_the_pool_for_the_niche_and_instance_it_measured(monkeypatch):
    """Regression: the rebuild was called with one argument and the TypeError was
    swallowed, so a user's own measured posts never reached the exemplar pool."""
    # The double below stands in for the real function, so keep it honest about arity.
    assert list(inspect.signature(mp._rebuild_pool).parameters) == ["niche", "host"]

    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    settled = (now - timedelta(days=3)).isoformat()
    mine = "mastodon://toot.garden/111"
    legacy = "mastodon://toot.garden/222"
    spg_db = _FakeSpgDb(
        {
            "generations": [
                {
                    "id": "g1",
                    "posted_uri": mine,
                    "niche": mp._corpus_niche("science", "toot.garden"),
                },
                {
                    "id": "g2",
                    "posted_uri": legacy,
                    "niche": mp._legacy_corpus_niche("science"),
                },
            ],
            "posts": [
                {"uri": mine, "created_at": settled},
                {"uri": legacy, "created_at": settled},
            ],
            "engagement_snapshots": [],  # nothing captured yet, so every bucket is due
        }
    )

    calls: list[tuple[str, str]] = []

    def fake_rebuild(niche: str, host: str) -> int:
        calls.append((niche, host))
        return 7

    monkeypatch.setattr(mp, "_spg", lambda: (spg_db, None, None))
    monkeypatch.setattr(mp, "_accepted_hosts", lambda: ["toot.garden"])
    monkeypatch.setattr(mp, "_rebuild_pool", fake_rebuild)
    monkeypatch.setattr(mp.masto, "get_status", lambda host, status_id: _fake_status())

    mp._run_mastodon_snapshot()

    # The plain niche and the host, not the half-stripped key the old code passed.
    assert calls == [("science", "toot.garden")]
    # The measurements themselves still land, legacy post included.
    written = [rows for table, rows in spg_db.upserts if table == "engagement_snapshots"]
    assert written and {r["post_uri"] for r in written[0]} == {mine, legacy}


def test_thin_pool_threshold_is_a_floor_not_a_target():
    # Guards the relationship rather than the number: borrowing has to kick in while the
    # pool is still smaller than one retrieval, or a draft can be "grounded" in fewer
    # exemplars than it asks for.
    assert mp.MIN_INSTANCE_EXEMPLARS > mp.N_EXEMPLARS
