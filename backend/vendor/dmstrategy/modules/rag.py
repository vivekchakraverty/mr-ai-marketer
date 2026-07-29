"""
Loads the prebuilt local RAG index (rag_index/, built by
scripts/build_index_from_crawler.py) and retrieves anonymized context chunks.
Never crawls or chunks anything itself.

Retrieved chunks carry no title/author/URL/domain — the index physically does not
store that metadata — so callers can only ever label them "Source 1", "Source 2", ...
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_INDEX_DIR = Path(__file__).resolve().parent.parent / "rag_index"
_COLLECTION_NAME = "dm_rag"
_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_collection = None
_embedder = None
_load_attempted = False


def _maybe_download_private_dataset(target_dir: Path) -> None:
    """Optional fallback: pull the index from a private HF Dataset via secrets."""
    dataset_repo_id = os.environ.get("RAG_DATASET_ID")
    if not dataset_repo_id:
        return
    hf_token = os.environ.get("RAG_DATASET_TOKEN") or os.environ.get("HF_TOKEN")
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=dataset_repo_id,
        repo_type="dataset",
        token=hf_token,
        local_dir=str(target_dir),
    )


def _load() -> None:
    global _collection, _embedder, _load_attempted
    if _load_attempted:
        return
    _load_attempted = True

    index_dir = _DEFAULT_INDEX_DIR
    if not (index_dir / "chroma.sqlite3").exists():
        try:
            _maybe_download_private_dataset(index_dir)
        except Exception as exc:
            print(f"[rag] private dataset fallback failed: {exc}")

    if not (index_dir / "chroma.sqlite3").exists():
        print("[rag] no rag_index/ found — RAG stage will be skipped")
        return

    try:
        import chromadb
        from sentence_transformers import SentenceTransformer

        client = chromadb.PersistentClient(path=str(index_dir))
        _collection = client.get_collection(_COLLECTION_NAME)
        _embedder = SentenceTransformer(_EMBED_MODEL)
    except Exception as exc:
        print(f"[rag] failed to load index: {exc}")
        _collection = None
        _embedder = None


def is_available() -> bool:
    _load()
    return _collection is not None


def chunk_count() -> int:
    _load()
    return _collection.count() if _collection is not None else 0


def retrieve(query: str, top_k: int = 8, category: str | list[str] | None = None) -> list[str]:
    """Returns ["Source 1: <text>", "Source 2: <text>", ...], or [] if unavailable.

    category optionally filters to a coarse topical bucket ("seo", "social_media",
    "online_ads", "general") stored as chunk metadata — not identifying information,
    so this doesn't weaken the "Source N only" anonymization guarantee. Pass a list
    to match any of several categories (e.g. a task's own domain plus "general")."""
    _load()
    if _collection is None or _embedder is None:
        return []

    where = None
    if isinstance(category, str):
        where = {"category": category}
    elif category:
        where = {"category": {"$in": list(category)}}

    try:
        query_embedding = _embedder.encode([query], normalize_embeddings=True).tolist()
        results = _collection.query(query_embeddings=query_embedding, n_results=top_k, where=where)
    except Exception as exc:
        print(f"[rag] retrieval failed: {exc}")
        return []

    documents = results.get("documents", [[]])[0]
    return [f"Source {i + 1}: {doc}" for i, doc in enumerate(documents)]


def grounding_block(chunks: list[str]) -> str:
    """Formats retrieved chunks with the "guide, don't limit" framing shared by
    every module that grounds against this index: retrieved context informs and
    inspires, it isn't a ceiling on what the model may say."""
    if not chunks:
        return "(no grounding context available for this run)"
    intro = (
        "These excerpts are here to inform and inspire your recommendations — a starting "
        "point, not a ceiling. Draw on your own broader expertise freely; you are not "
        'limited to what appears below. When a specific recommendation *is* drawn directly '
        'from one of these excerpts, cite it as "(Source N)" using the numbering below — '
        "but only do this for material you actually took from that excerpt, and never state "
        "or guess a title, author, publication name, or URL; you do not have that "
        "information. Don't hedge or omit a useful recommendation just because it isn't "
        "covered here."
    )
    return intro + "\n\n" + "\n\n".join(chunks)
