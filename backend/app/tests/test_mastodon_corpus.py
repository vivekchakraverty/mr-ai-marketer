from __future__ import annotations

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


def test_thin_pool_threshold_is_a_floor_not_a_target():
    # Guards the relationship rather than the number: borrowing has to kick in while the
    # pool is still smaller than one retrieval, or a draft can be "grounded" in fewer
    # exemplars than it asks for.
    assert mp.MIN_INSTANCE_EXEMPLARS > mp.N_EXEMPLARS
