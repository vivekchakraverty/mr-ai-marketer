"""GP active-learning qualifier: cold-start guard, fit, prediction ordering, acquisition
modes, and consume-mode selection — all on tiny synthetic embeddings."""

import numpy as np

from vendor.leadgen.ml import embedder, qualifier


def _pos_vec():
    v = np.zeros(embedder.DIM, dtype=np.float32)
    v[0] = 1.0
    return v


def _neg_vec():
    v = np.zeros(embedder.DIM, dtype=np.float32)
    v[1] = 1.0
    return v


def _add_lead(db, campaign_id, vec, label=None):
    import uuid

    lead = db.upsert_lead(
        campaign_id,
        domain=f"{uuid.uuid4().hex[:8]}.com",
        company="Co",
        profile_text="x",
        embedding=embedder.to_blob(vec),
    )
    if label:
        db.update_lead(lead["id"], label=label)
    db.create_deal(campaign_id, lead["id"])
    return lead


def test_cold_start_returns_none_until_two_of_each(lg_db):
    c = lg_db.create_campaign("c", "p")
    _add_lead(lg_db, c["id"], _pos_vec(), "positive")
    _add_lead(lg_db, c["id"], _neg_vec(), "negative")
    # Only one of each so far -> unfitted.
    assert qualifier.train_and_persist(c["id"]) is None


def test_fits_and_predicts_ordering(lg_db):
    c = lg_db.create_campaign("c", "p")
    for _ in range(2):
        _add_lead(lg_db, c["id"], _pos_vec(), "positive")
        _add_lead(lg_db, c["id"], _neg_vec(), "negative")

    qual = qualifier.train_and_persist(c["id"])
    assert qual is not None and qual.is_fitted

    pos_mean, _ = qual.predict(_pos_vec().reshape(1, -1))
    neg_mean, _ = qual.predict(_neg_vec().reshape(1, -1))
    assert pos_mean[0] > neg_mean[0]  # learned the positive direction


def test_acquisition_modes_differ(lg_db):
    c = lg_db.create_campaign("c", "p")
    for _ in range(2):
        _add_lead(lg_db, c["id"], _pos_vec(), "positive")
        _add_lead(lg_db, c["id"], _neg_vec(), "negative")
    qual = qualifier.train_and_persist(c["id"])

    X = np.vstack([_pos_vec(), _neg_vec(), np.ones(embedder.DIM, dtype=np.float32)])
    exploit = qual.acquisition_scores(X, "exploit")  # predicted mean
    explore = qual.acquisition_scores(X, "explore")  # posterior std
    assert not np.allclose(exploit, explore)


def test_select_next_returns_unlabeled(lg_db):
    c = lg_db.create_campaign("c", "p")
    _add_lead(lg_db, c["id"], _pos_vec())  # unlabeled
    nxt = qualifier.select_next_to_qualify(c["id"])
    assert nxt is not None and nxt["label"] is None


def test_persisted_model_survives_reload(lg_db):
    c = lg_db.create_campaign("c", "p")
    for _ in range(2):
        _add_lead(lg_db, c["id"], _pos_vec(), "positive")
        _add_lead(lg_db, c["id"], _neg_vec(), "negative")
    qualifier.train_and_persist(c["id"])
    reloaded = qualifier.load(lg_db.get_campaign(c["id"]))
    assert reloaded is not None and reloaded.is_fitted
