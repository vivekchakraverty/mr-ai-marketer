"""Backups of the app's databases.

Everything the app has ever made for you lives in SQLite files under the data directory: the
Library, the tracker workbooks, the Community config, the lead pipeline, the social-post
corpus. There is no server holding a second copy, which is the point of the app and also the
risk — a corrupted file or a mistaken reset is unrecoverable unless a backup exists.

**Why not just copy the file.** A live SQLite database is not safe to copy with the
filesystem: a write in progress leaves the copy torn, and a `-wal` sidecar means the bytes on
disk are not the whole story. `sqlite3.Connection.backup()` is SQLite's own online-backup
API — it takes a consistent snapshot of a database that is being written to, and produces a
single file with nothing outstanding. That is the difference between a backup and a file that
looks like one.

Backups are plain `.sqlite3` files. No archive format, no manifest: the recovery path when
things are genuinely bad should be "copy this file over that one", not "find the tool that
reads this".
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import config

BACKUP_DIR = config.DATA_DIR / "backups"

# Every SQLite database the app owns, by the name it gets inside a backup set. The
# social-post and lead-gen databases belong to vendored projects but hold the user's own
# corpus and pipeline, so they are theirs to keep.
SOURCES: dict[str, Path] = {
    "app": config.DB_PATH,
    "social-post": config.DATA_DIR / "social-post.sqlite3",
    "leadgen": config.DATA_DIR / "leadgen" / "leadgen.sqlite3",
    "topic-scout": config.DATA_DIR / "topic-scout-history.sqlite3",
}


class BackupError(RuntimeError):
    pass


def _snapshot(source: Path, target: Path) -> int:
    """One consistent copy of a live database. Returns the byte size written."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    try:
        dest = sqlite3.connect(target)
        try:
            # backup() holds a read lock only for each batch, so a long backup does not block
            # writers for its whole duration.
            src.backup(dest, pages=256, sleep=0.01)
        finally:
            dest.close()
    finally:
        src.close()
    return target.stat().st_size


def create(label: str = "") -> dict:
    """Snapshot every database into a new timestamped folder."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label.strip() if c.isalnum() or c in " -_").strip().replace(" ", "-")
    name = f"{stamp}-{safe}" if safe else stamp
    folder = BACKUP_DIR / name
    folder.mkdir(parents=True, exist_ok=True)

    written: list[dict] = []
    try:
        for key, source in SOURCES.items():
            if not source.is_file():
                continue  # a tool the user has never opened has no database yet
            size = _snapshot(source, folder / f"{key}.sqlite3")
            written.append({"name": key, "bytes": size})
    except Exception as err:  # noqa: BLE001
        # A half-written backup is worse than none — it invites restoring from it later.
        shutil.rmtree(folder, ignore_errors=True)
        raise BackupError(f"backup failed and was discarded: {err}") from None

    if not written:
        shutil.rmtree(folder, ignore_errors=True)
        raise BackupError("there are no databases to back up yet")
    return {"id": name, "createdAt": datetime.now(timezone.utc).isoformat(),
            "databases": written, "bytes": sum(w["bytes"] for w in written),
            "path": str(folder)}


def listing() -> list[dict]:
    """Backups on disk, newest first."""
    if not BACKUP_DIR.is_dir():
        return []
    out = []
    for folder in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        files = sorted(folder.glob("*.sqlite3"))
        if not files:
            continue
        out.append({
            "id": folder.name,
            "createdAt": datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).isoformat(),
            "databases": [{"name": f.stem, "bytes": f.stat().st_size} for f in files],
            "bytes": sum(f.stat().st_size for f in files),
            "path": str(folder),
        })
    return out


def delete(backup_id: str) -> None:
    folder = (BACKUP_DIR / backup_id).resolve()
    # Refuse anything that resolves outside the backup directory — the id arrives from a
    # request body, and "../.." is the obvious thing to try.
    if not str(folder).startswith(str(BACKUP_DIR.resolve())) or not folder.is_dir():
        raise BackupError("no such backup")
    shutil.rmtree(folder, ignore_errors=True)


def restore(backup_id: str) -> dict:
    """Put a backup back, after snapshotting what is there now.

    The pre-restore snapshot is not optional politeness: restoring is the one operation here
    that destroys data, and a user who picks the wrong backup has no other way back.
    """
    folder = (BACKUP_DIR / backup_id).resolve()
    if not str(folder).startswith(str(BACKUP_DIR.resolve())) or not folder.is_dir():
        raise BackupError("no such backup")

    safety = create(label="before-restore")

    restored: list[str] = []
    for key, target in SOURCES.items():
        source = folder / f"{key}.sqlite3"
        if not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copied rather than snapshotted: the source here is a static file, and the target is
        # about to be replaced wholesale. Any -wal/-shm beside the target would describe the
        # database being overwritten, so they go too.
        for sidecar in (f"{target}-wal", f"{target}-shm"):
            Path(sidecar).unlink(missing_ok=True)
        shutil.copy2(source, target)
        restored.append(key)

    if not restored:
        raise BackupError("that backup contains no databases")
    return {"restored": restored, "safetyBackup": safety["id"],
            "detail": "Restart the app so every tool reopens the restored databases."}
