"""Shrink the marketing-plan RAG index by dropping Chroma's unused full-text tables.

Chroma keeps two indexes over the same corpus: the vector index (what this app searches)
and an FTS5 keyword index backing `where_document={"$contains": ...}`. modules/rag.py only
ever queries `query_embeddings` with a `category` metadata filter, so the FTS side is a
second full copy of every document plus an inverted index that nothing reads.

Dropping it took a real index from 2,235 MB to 921 MB — 2.4 GB to 1.1 GB including the HNSW
segment files — with retrieval provably unchanged: identical chunk ids and identical
distances across test queries.

Safety, because this rewrites a multi-GB file in place:

* Nothing happens until a baseline is captured from the live index.
* A full backup is taken first, and restored automatically if verification fails.
* Verification compares ids *and* distances, not just "it didn't crash".
* Refuses to run while something else holds the database open.

    python scripts/rag/compact_index.py                 # compact the default index
    python scripts/rag/compact_index.py --dry-run       # measure only, change nothing
    python scripts/rag/compact_index.py --keep-backup   # leave the .bak copy behind
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INDEX = REPO_ROOT / "backend" / "vendor" / "dmstrategy" / "rag_index"
COLLECTION = "dm_rag"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# Spread across the corpus on purpose: a single query could match identically by luck even if
# something had shifted, so verification uses several unrelated topics.
VERIFY_QUERIES = [
    "how to price a saas product",
    "email marketing subject lines",
    "brand positioning strategy",
    "customer retention tactics",
    "paid advertising budget allocation",
]
TOP_K = 8


def _mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB"


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fingerprint(index_dir: Path) -> dict:
    """Top-K ids and distances per query — the thing that must not change."""
    import chromadb
    from sentence_transformers import SentenceTransformer

    embedder = SentenceTransformer(EMBED_MODEL)
    collection = chromadb.PersistentClient(path=str(index_dir)).get_collection(COLLECTION)

    out = {"count": collection.count(), "queries": {}}
    for query in VERIFY_QUERIES:
        vector = embedder.encode([query], normalize_embeddings=True).tolist()
        result = collection.query(query_embeddings=vector, n_results=TOP_K)
        out["queries"][query] = {
            "ids": result["ids"][0],
            # Rounded because float formatting can differ across runs in the last bits
            # without meaning anything changed about which chunks matched.
            "distances": [round(d, 6) for d in result["distances"][0]],
        }
    return out


def _fts_objects(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'embedding_fulltext_search%'"
        ).fetchall()
    ]


def _compact(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        # VACUUM writes a full temporary copy. Keep it beside the database rather than in
        # the system temp directory, which may be on a different (and possibly full) drive.
        conn.execute(f"PRAGMA temp_store_directory = '{db_path.parent.as_posix()}'")
        # Dropping the FTS5 virtual table removes its shadow tables with it.
        conn.execute("DROP TABLE IF EXISTS embedding_fulltext_search")
        conn.commit()
        leftover = _fts_objects(conn)
        if leftover:
            raise RuntimeError(f"FTS objects survived the drop: {leftover}")
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--dry-run", action="store_true", help="measure only, change nothing")
    parser.add_argument("--keep-backup", action="store_true", help="don't delete the .bak copy")
    args = parser.parse_args()

    index_dir: Path = args.index_dir.resolve()
    db_path = index_dir / "chroma.sqlite3"
    if not db_path.exists():
        print(f"No index at {db_path}")
        return 1

    before_db, before_dir = db_path.stat().st_size, _dir_size(index_dir)
    conn = sqlite3.connect(str(db_path))
    fts = _fts_objects(conn)
    conn.close()

    print(f"index      : {index_dir}")
    print(f"database   : {_mb(before_db)}   whole directory: {_mb(before_dir)}")
    print(f"FTS objects: {len(fts)}{' — nothing to drop, already compacted' if not fts else ''}")
    if not fts:
        return 0
    if args.dry_run:
        print("\n--dry-run: stopping here.")
        return 0

    # A locked database means the app (or a stray backend) still has it open. Compacting
    # underneath a live reader is how you get a corrupt index, so refuse rather than risk it.
    try:
        probe = sqlite3.connect(str(db_path), timeout=2)
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
        probe.close()
    except sqlite3.OperationalError:
        print("\nThe index is open in another process — close Mr. AI Marketer and retry.")
        return 1

    print(f"\n[1/4] capturing baseline over {len(VERIFY_QUERIES)} queries…")
    baseline = _fingerprint(index_dir)
    print(f"      {baseline['count']:,} chunks")

    backup = index_dir.with_name(index_dir.name + ".bak")
    print(f"[2/4] backing up to {backup.name}…")
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(index_dir, backup)

    print("[3/4] dropping FTS tables and vacuuming (takes a few minutes)…")
    started = time.time()
    try:
        _compact(db_path)
    except Exception as err:  # noqa: BLE001
        print(f"      failed: {err}\n      restoring from backup…")
        shutil.rmtree(index_dir)
        shutil.move(str(backup), str(index_dir))
        return 1
    print(f"      done in {time.time() - started:.0f}s")

    print("[4/4] verifying retrieval is unchanged…")
    after = _fingerprint(index_dir)
    same = after["count"] == baseline["count"] and all(
        after["queries"][q] == baseline["queries"][q] for q in VERIFY_QUERIES
    )
    if not same:
        print("      RETRIEVAL CHANGED — restoring the backup and leaving the index as it was.")
        Path(index_dir / "_compact_mismatch.json").write_text(
            json.dumps({"before": baseline, "after": after}, indent=2), encoding="utf-8"
        )
        shutil.rmtree(index_dir)
        shutil.move(str(backup), str(index_dir))
        return 1

    after_db, after_dir = db_path.stat().st_size, _dir_size(index_dir)
    print(f"      identical ids and distances across all {len(VERIFY_QUERIES)} queries\n")
    print(f"database  : {_mb(before_db)} -> {_mb(after_db)}")
    print(f"directory : {_mb(before_dir)} -> {_mb(after_dir)}"
          f"   (saved {_mb(before_dir - after_dir)})")

    if args.keep_backup:
        print(f"\nbackup kept at {backup}")
    else:
        shutil.rmtree(backup)
        print("\nbackup removed (pass --keep-backup to retain it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
