"""Embedder: keyword injection, blob round-trip, and output shape (with a fake model so the
test needs no network / no torch)."""

import numpy as np

from vendor.leadgen.ml import embedder


def test_inject_keywords():
    assert embedder.inject_keywords("a clinic", "dental austin") == "a clinic\ndental austin"
    assert embedder.inject_keywords("a clinic", "") == "a clinic"
    assert embedder.inject_keywords("", "terms") == "terms"


def test_blob_roundtrip():
    vec = np.arange(embedder.DIM, dtype=np.float32)
    restored = embedder.from_blob(embedder.to_blob(vec))
    assert restored.shape == (embedder.DIM,)
    assert np.allclose(restored, vec)


def test_embed_shape_and_dtype(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADGEN_DATA_DIR", str(tmp_path))

    class FakeModel:
        def encode(self, text, normalize_embeddings=True):
            return np.ones(embedder.DIM, dtype=np.float64)  # wrong dtype on purpose

    monkeypatch.setattr(embedder, "_get_model", lambda: FakeModel())
    vec = embedder.embed("hello world")
    assert vec.shape == (embedder.DIM,)
    assert vec.dtype == np.float32  # embed() must coerce to float32 for the BLOB column


def test_embed_empty_is_zero_vector():
    assert np.count_nonzero(embedder.embed("   ")) == 0
