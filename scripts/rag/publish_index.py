"""Publish the marketing-plan RAG index to a private Hugging Face Dataset.

The index is ~1.1 GB after compaction (see compact_index.py) — too big for the installer, so
the app pulls it on first plan generation instead. modules/rag.py already knows how:
`_maybe_download_private_dataset` calls snapshot_download(repo_type="dataset") using
RAG_DATASET_ID and the user's own HF token. This script is the other half — getting the
index up there in the first place.

The repo is created **private** by default. It is a derived work of whatever corpus the index
was built from, and publishing that publicly is a licensing decision nobody should make by
accident; pass --public only if you know the corpus permits it.

    # one-time upload (asks nothing, reads HF_TOKEN from the environment)
    python scripts/rag/publish_index.py --repo your-username/dm-rag-index

    # check what would happen first
    python scripts/rag/publish_index.py --repo your-username/dm-rag-index --dry-run

    # verify a published dataset downloads and answers correctly
    python scripts/rag/publish_index.py --repo your-username/dm-rag-index --verify-only

Afterwards set MARKETING_PLAN_RAG_DATASET to the same repo id (see the README) so the app
knows where to fetch it.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INDEX = REPO_ROOT / "backend" / "vendor" / "dmstrategy" / "rag_index"
COLLECTION = "dm_rag"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# Files snapshot_download must land for modules/rag.py to find a usable index. The HNSW
# segment lives in a uuid-named subdirectory, so it is matched by shape rather than name.
REQUIRED_FILES = ("chroma.sqlite3",)


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB"


def _index_files(path: Path) -> list[Path]:
    """The index's own files.

    Skips `.cache/`, which upload_large_folder creates inside the folder it is uploading to
    track resumable progress. It is the uploader's bookkeeping, not part of the index, and
    counting it makes the reported size and file list wrong.
    """
    return [f for f in path.rglob("*") if f.is_file() and ".cache" not in f.parts]


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in _index_files(path))


def _token() -> str | None:
    """Resolve credentials without a token needing to be pasted anywhere.

    Order is deliberate: an explicit HF_TOKEN wins, but otherwise this returns None and lets
    huggingface_hub use the login `huggingface-cli login` already stored. That keeps the
    secret inside the Hub's own credential handling, so it never has to appear in a shell
    history, a script argument, or a chat window on the way here.
    """
    explicit = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or "").strip()
    if explicit:
        return explicit

    from huggingface_hub import get_token

    if get_token():
        return None  # the hub resolves its own stored login

    print(
        "No Hugging Face credentials found.\n\n"
        "Log in once — the token is then stored by the Hub's own tooling and never has to\n"
        "be pasted into a command:\n\n"
        "    huggingface-cli login\n\n"
        "It needs **write** access to your namespace. Alternatively, set HF_TOKEN in the\n"
        "environment for this session only.\n"
    )
    raise SystemExit(2)


def _whoami() -> str:
    from huggingface_hub import whoami

    try:
        return whoami(_token()).get("name", "?")
    except Exception:  # noqa: BLE001
        return "?"


def _describe(index_dir: Path) -> None:
    print(f"index     : {index_dir}")
    print(f"size      : {_mb(_dir_size(index_dir))}")
    files = sorted(f.relative_to(index_dir).as_posix() for f in _index_files(index_dir))
    print(f"files     : {len(files)}")
    for f in files[:6]:
        print(f"            {f}")
    if len(files) > 6:
        print(f"            … and {len(files) - 6} more")


def _check_compacted(index_dir: Path) -> None:
    """Refuse to upload an index still carrying the unused full-text tables.

    Uploading before compaction means pushing (and every user pulling) 1.3 GB that provably
    changes nothing about retrieval.
    """
    import sqlite3

    conn = sqlite3.connect(str(index_dir / "chroma.sqlite3"))
    fts = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'embedding_fulltext_search%'"
    ).fetchone()[0]
    conn.close()
    if fts:
        print(
            f"\nThis index still has {fts} unused full-text objects (~1.3 GB).\n"
            "Run `python scripts/rag/compact_index.py` first — it is verified lossless."
        )
        raise SystemExit(1)


def _query(index_dir: Path, queries: list[str]) -> dict:
    import chromadb
    from sentence_transformers import SentenceTransformer

    embedder = SentenceTransformer(EMBED_MODEL)
    collection = chromadb.PersistentClient(path=str(index_dir)).get_collection(COLLECTION)
    out = {"count": collection.count(), "queries": {}}
    for q in queries:
        vec = embedder.encode([q], normalize_embeddings=True).tolist()
        res = collection.query(query_embeddings=vec, n_results=5)
        out["queries"][q] = res["ids"][0]
    return out


def publish(index_dir: Path, repo: str, private: bool) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=_token())
    print(f"\nauthenticated as: {_whoami()}")
    print(f"creating dataset repo {repo} ({'private' if private else 'PUBLIC'})…")
    api.create_repo(repo_id=repo, repo_type="dataset", private=private, exist_ok=True)

    print("uploading — this is a multi-GB transfer and will take a while.")
    print("It resumes if interrupted, so re-running after a drop is safe.\n")
    started = time.time()
    # upload_large_folder chunks, parallelises and resumes; upload_folder would try to hold
    # the whole thing in one commit and is far more fragile at this size.
    api.upload_large_folder(repo_id=repo, repo_type="dataset", folder_path=str(index_dir))
    print(f"\nuploaded in {time.time() - started:.0f}s")


def publish_bucket(index_dir: Path, bucket_id: str, private: bool) -> None:
    """Upload to a private HF Bucket instead of a Dataset.

    Buckets suit this better than Datasets once a retrieval Space is in the picture: the
    Space is the only thing that ever reads the corpus, so it wants plain private storage
    rather than a dataset repo with versioning and a viewer it will never use.
    """
    from huggingface_hub import HfApi

    api = HfApi(token=_token())
    print(f"\nauthenticated as: {_whoami()}")
    print(f"creating bucket {bucket_id} ({'private' if private else 'PUBLIC'})…")
    api.create_bucket(bucket_id, private=private, exist_ok=True)

    print("syncing — a multi-GB transfer; it skips anything already uploaded.\n")
    started = time.time()
    # Exclude the uploader's own bookkeeping. upload_large_folder writes .cache/huggingface
    # inside the folder it uploads; syncing that to the bucket would ship resume metadata
    # for a different transfer to every consumer of the index.
    api.sync_bucket(
        source=str(index_dir),
        dest=f"hf://buckets/{bucket_id}",
        exclude=[".cache/*", ".cache/**"],
    )
    print(f"\nsynced in {time.time() - started:.0f}s")


def verify(repo: str) -> int:
    """Download the published dataset to a temp dir and confirm it actually answers."""
    from huggingface_hub import snapshot_download

    queries = ["how to price a saas product", "brand positioning strategy"]
    token = _token()  # resolved before anything is announced, so a missing token fails first
    # ignore_cleanup_errors because Chroma keeps the HNSW file mapped after the collection is
    # used, and Windows refuses to unlink an open file — without this, a *successful*
    # verification ends in a PermissionError traceback and looks like a failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        print(f"downloading {repo} to a temporary directory…")
        started = time.time()
        snapshot_download(repo_id=repo, repo_type="dataset", token=token, local_dir=tmp)
        print(f"downloaded in {time.time() - started:.0f}s ({_mb(_dir_size(Path(tmp)))})")

        missing = [f for f in REQUIRED_FILES if not (Path(tmp) / f).exists()]
        if missing:
            print(f"FAILED: published dataset is missing {missing}")
            return 1
        result = _query(Path(tmp), queries)
        print(f"collection opened: {result['count']:,} chunks")
        for q, ids in result["queries"].items():
            print(f"   {q[:38]:40} -> {len(ids)} hits")
        if result["count"] == 0 or not all(result["queries"].values()):
            print("FAILED: the downloaded index returned nothing.")
            return 1
    print("\nverified: the published dataset downloads and retrieves correctly.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="dataset repo id, e.g. you/dm-rag-index")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--bucket", action="store_true",
                        help="publish to an HF Bucket instead of a Dataset (pairs with the "
                             "retrieval Space, which is the only reader)")
    parser.add_argument("--public", action="store_true",
                        help="publish publicly — check the corpus licence first")
    parser.add_argument("--dry-run", action="store_true", help="describe only, upload nothing")
    parser.add_argument("--verify-only", action="store_true",
                        help="skip upload; download the published dataset and test it")
    args = parser.parse_args()

    if args.verify_only:
        return verify(args.repo)

    index_dir: Path = args.index_dir.resolve()
    if not (index_dir / "chroma.sqlite3").exists():
        print(f"No index at {index_dir}")
        return 1

    _describe(index_dir)
    _check_compacted(index_dir)

    if args.dry_run:
        print(f"\n--dry-run: would upload the above to {args.repo} "
              f"({'public' if args.public else 'private'}).")
        return 0

    if args.bucket:
        publish_bucket(index_dir, args.repo, private=not args.public)
        print(f"\nNow set RAG_BUCKET_ID={args.repo} in the retrieval Space's settings.")
        return 0
    publish(index_dir, args.repo, private=not args.public)
    print(f"\nNow set MARKETING_PLAN_RAG_DATASET={args.repo} so the app fetches it.")
    print(f"Verify anytime with:  python {Path(__file__).name} --repo {args.repo} --verify-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
