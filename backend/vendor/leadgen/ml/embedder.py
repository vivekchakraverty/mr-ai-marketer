"""Local embeddings via sentence-transformers `BAAI/bge-small-en-v1.5` (384-dim) — the
same model OpenOutreach uses (via FastEmbed), reached here through the sentence-transformers
that this app already depends on, so there is no new embedding library and no embedding API.

Two OpenOutreach-faithful details:
  * keyword injection — a discovery lead is embedded as `profile_text + clause_terms(query)`,
    so the Gaussian Process learns query-term -> fit as a byproduct of labeling. The raw
    profile_text is kept separately (for the LLM) and only the embedding carries the terms.
  * an on-disk cache keyed by content hash, so re-embedding the same text (e.g. the handful
    of distinct clause phrases the query selector scores) is free.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

import numpy as np

from .. import config

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Lazy-load the model — importing sentence-transformers pulls in torch, which we don't
    want to pay for at backend startup for users who never run a campaign."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                log.info("[leadgen] loading embedding model %s", MODEL_NAME)
                _model = SentenceTransformer(MODEL_NAME)
    return _model


def _cache_dir() -> Path:
    d = config.data_dir() / "embed_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def inject_keywords(profile_text: str, clause_terms: str = "") -> str:
    """The text actually embedded: profile plus the retrieving query's terms."""
    profile_text = (profile_text or "").strip()
    clause_terms = (clause_terms or "").strip()
    return f"{profile_text}\n{clause_terms}".strip() if clause_terms else profile_text


def embed(text: str, use_cache: bool = True) -> np.ndarray:
    """Embed one string to a float32[384] unit vector, with an on-disk cache."""
    text = (text or "").strip()
    if not text:
        return np.zeros(DIM, dtype=np.float32)

    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cache_file = _cache_dir() / f"{key}.npy"
    if use_cache and cache_file.exists():
        try:
            return np.load(cache_file)
        except Exception:  # noqa: BLE001 — a corrupt cache entry should just be recomputed
            pass

    vec = _get_model().encode(text, normalize_embeddings=True).astype(np.float32)
    if use_cache:
        try:
            np.save(cache_file, vec)
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass
    return vec


def embed_many(texts: list[str]) -> np.ndarray:
    """Embed a batch (used by the query selector to score many clause phrases at once)."""
    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    return np.asarray(
        _get_model().encode(texts, normalize_embeddings=True), dtype=np.float32
    )


def to_blob(vec: np.ndarray) -> bytes:
    """Serialize a vector for the SQLite `embedding` BLOB column."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
