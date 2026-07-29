"""Sentence embeddings via all-MiniLM-L6-v2 (384 dims, CPU).

Chosen because it is small (~90MB), fast on a free Actions runner, and its 384
dims match the `vector(384)` column in the migration. Swapping models means
changing that column type and re-embedding every exemplar.

Vectors are L2-normalised, which makes cosine similarity equal to a dot product
and matches the `vector_cosine_ops` index in the migration.
"""

from __future__ import annotations

import functools
import logging
from typing import Sequence

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# MiniLM truncates at 256 word-pieces. Bluesky posts cap at 300 graphemes, so
# truncation effectively never bites — but batch encoding is where the runtime
# goes, so keep batches modest to stay inside the Actions runner's 7GB.
BATCH_SIZE = 64


@functools.lru_cache(maxsize=1)
def get_model():
    """Load the model once per process.

    Imported lazily: sentence-transformers pulls in torch, which costs seconds
    and ~1GB RSS. Jobs that never embed (ingest, snapshot, cleanup) must not pay
    that, and the Streamlit app should not pay it until the first generate.
    """
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model %s (first run downloads ~90MB)", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    return model


def embed(texts: Sequence[str]) -> np.ndarray:
    """Embed texts -> float32 array of shape (len(texts), 384), L2-normalised.

    Raises on empty input rather than returning a malformed array, since every
    caller treats the result as row-aligned with its input.
    """
    if not texts:
        raise ValueError("embed() called with no texts")

    model = get_model()
    vectors = model.encode(
        list(texts),
        batch_size=BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape[1] != EMBEDDING_DIM:
        raise RuntimeError(
            f"{MODEL_NAME} produced {vectors.shape[1]} dims, expected {EMBEDDING_DIM}. "
            f"The exemplars.embedding column is vector({EMBEDDING_DIM})."
        )
    return vectors


def embed_one(text: str) -> list[float]:
    """Embed a single string into a plain list, ready for JSON/pgvector."""
    return embed([text])[0].tolist()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors.

    embed() normalises, so this is a dot product for our vectors; the explicit
    norms keep it correct if a caller passes something unnormalised.
    """
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)
