"""SQLite-backed, bounded local jobs for private gameplay analysis."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .. import config as app_config, db
from . import config, igdb, modal_runtime, observer, report, video

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=config.MAX_ACTIVE, thread_name_prefix="game-analysis")
_lock = threading.Lock()
_active = 0
_cancelled: set[str] = set()
_running: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize() -> None:
    upload_dir = app_config.DATA_DIR / "private-game-analysis-tmp"
    upload_dir.mkdir(parents=True, exist_ok=True)
    for stale in upload_dir.glob("game-analysis-*"):
        if stale.is_file():
            stale.unlink(missing_ok=True)
    with db._connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS game_analysis_jobs (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          completed_at TEXT, metadata_json TEXT, chunks_json TEXT, report_json TEXT,
          metrics_json TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS game_analysis_hash ON game_analysis_jobs(sha256,status);
        """)
        conn.execute("UPDATE game_analysis_jobs SET status='failed', error='Analysis interrupted by app restart', updated_at=? WHERE status NOT IN ('completed','failed')", (_now(),))


def _set(job_id: str, status: str, **fields: object) -> None:
    values = {"status": status, "updated_at": _now(), **fields}
    with db._connect() as conn:
        conn.execute(f"UPDATE game_analysis_jobs SET {', '.join(key + '=?' for key in values)} WHERE id=?",
                     (*values.values(), job_id))


def get(job_id: str) -> dict | None:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM game_analysis_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "filename": row["filename"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "completed_at": row["completed_at"], "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
            "report": json.loads(row["report_json"]) if row["report_json"] else None,
            "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else None,
            "error": row["error"]}


def list_recent() -> list[dict]:
    with db._connect() as conn:
        rows = conn.execute("SELECT id FROM game_analysis_jobs ORDER BY created_at DESC LIMIT 30").fetchall()
    return [job for row in rows if (job := get(row["id"]))]


def submit(path: Path, filename: str, digest: str, sampling_fps: float) -> tuple[str, bool]:
    global _active
    with _lock:
        with db._connect() as conn:
            existing = conn.execute("SELECT id FROM game_analysis_jobs WHERE sha256=? AND status IN ('queued','preprocessing_video','analyzing_gameplay','merging_analysis','querying_igdb','ranking_comparables','modeling_audience','generating_recommendations','completed') ORDER BY created_at DESC LIMIT 1", (digest,)).fetchone()
        if existing:
            path.unlink(missing_ok=True)
            return existing["id"], True
        if _active >= config.MAX_ACTIVE:
            raise RuntimeError("Game analysis is busy. Try again when the current job finishes.")
        _active += 1
    job_id = uuid.uuid4().hex
    try:
        with _lock:
            _running.add(job_id)
        with db._connect() as conn:
            conn.execute("INSERT INTO game_analysis_jobs(id,filename,sha256,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                         (job_id, filename, digest, "queued", _now(), _now()))
        _pool.submit(_run, job_id, path, sampling_fps)
    except Exception:
        with _lock:
            _active -= 1
            _running.discard(job_id)
        raise
    return job_id, False


def _check(job_id: str) -> None:
    if job_id in _cancelled:
        raise InterruptedError("Analysis deleted")


def _catalog_context(comparables: list[dict]) -> str:
    close_games = [game for game in comparables[:5] if game["similarity"] >= 35]
    return ("IGDB metadata for " + ", ".join(game["name"] for game in close_games) +
            " shares structural traits with the footage. This supports a taste-cluster inference, not proven audience demand.") if close_games else "No strong IGDB comparable is available to refine player-fit estimates."


def refresh_comparables(job_id: str) -> dict:
    saved = get(job_id)
    if not saved:
        raise LookupError("Game analysis not found.")
    if saved["status"] != "completed" or not saved["report"]:
        raise ValueError("Complete the gameplay analysis before refreshing comparable games.")
    if not igdb.configured():
        raise ValueError("Add your Client ID and Client Secret in Settings > Games — IGDB, save, then restart the app.")
    result = saved["report"]
    candidates = igdb.candidates(result["profile"])
    comparables = report.rank(result["profile"], candidates)
    result["comparables"] = comparables
    result["platforms"] = report.platform_recommendations(result["profile"], comparables)
    result["audience"]["basis"] = "Observed gameplay first. " + _catalog_context(comparables)
    result["audience"]["comparable_titles"] = [game["name"] for game in comparables]
    result["igdb_note"] = ""
    metrics = {**(saved["metrics"] or {}), "igdb_candidates": len(candidates)}
    _set(job_id, "completed", report_json=json.dumps(result), metrics_json=json.dumps(metrics))
    return get(job_id)


def _analyze(job_id: str, path: Path, sampling_fps: float) -> None:
    _set(job_id, "preprocessing_video")
    metadata = video.probe(path)
    metadata["sampling_fps"] = sampling_fps
    _set(job_id, "analyzing_gameplay", metadata_json=json.dumps(metadata))
    parts = []
    frames_processed = 0
    modal_seconds = 0.0
    for start, end in video.chunks(metadata["duration"], sampling_fps):
        _check(job_id)
        frames = video.sample(path, start, end, sampling_fps)
        frames_processed += len(frames)
        started = time.monotonic()
        parts.append(observer.observe_chunk(frames, start, end))
        modal_seconds += time.monotonic() - started
        _set(job_id, "analyzing_gameplay", chunks_json=json.dumps(parts))
    _check(job_id)
    _set(job_id, "merging_analysis")
    profile = observer.merge(parts)
    started = time.monotonic()
    synthesis = observer.synthesize(profile)
    modal_seconds += time.monotonic() - started
    profile["description"] = synthesis["description"]
    profile["audience_archetypes"] = synthesis["audience_archetypes"]
    _check(job_id)
    _set(job_id, "querying_igdb")
    igdb_note = ""
    try:
        candidates = igdb.candidates(profile)
        if not igdb.configured():
            igdb_note = "IGDB credentials are not configured; comparable games and catalog evidence are unavailable."
    except igdb.IGDBError as exc:
        candidates = []
        igdb_note = str(exc)
    _set(job_id, "ranking_comparables")
    comparables = report.rank(profile, candidates)
    _set(job_id, "modeling_audience")
    context = _catalog_context(comparables)
    audience = {"archetypes": synthesis["audience_archetypes"], "summary": synthesis["audience_summary"],
                "basis": "Observed gameplay first. " + context,
                "comparable_titles": [game["name"] for game in comparables]}
    _set(job_id, "generating_recommendations")
    platforms = report.platform_recommendations(profile, comparables)
    result = {"description": profile["description"], "profile": profile, "comparables": comparables,
              "audience": audience, "platforms": platforms, "igdb_note": igdb_note,
              "source_note": "Observations come from uploaded frames; gameplay, audience and platform interpretations are estimates.",
              "overall_confidence": profile["confidence"]}
    metrics = {"video_duration_seconds": metadata["duration"], "frames_processed": frames_processed,
               "modal_inference_seconds": round(modal_seconds, 2), "model_calls": len(parts) + 1,
               "video_chunks": len(parts), "igdb_candidates": len(candidates)}
    _check(job_id)
    _set(job_id, "completed", report_json=json.dumps(result), metrics_json=json.dumps(metrics),
         completed_at=_now())
    log.info("Game analysis %s: %s", job_id, metrics)


def _run(job_id: str, path: Path, sampling_fps: float) -> None:
    global _active
    try:
        _analyze(job_id, path, sampling_fps)
    except InterruptedError:
        pass
    except Exception as exc:
        log.exception("Game analysis %s failed", job_id)
        # A readable failure without filesystem paths or upstream response bodies.
        stage = (get(job_id) or {}).get("status", "analysis").replace("_", " ")
        reason = str(exc) if isinstance(exc, (video.InvalidVideo, ValueError, modal_runtime.GameInferenceError)) else "An unexpected processing error occurred. Check the backend log."
        _set(job_id, "failed", error=f"During {stage}: {reason}"[:380])
    finally:
        try:
            if not config.RETAIN_UPLOADS or job_id in _cancelled:
                path.unlink(missing_ok=True)
            else:
                retained = app_config.DATA_DIR / "private-game-uploads"
                retained.mkdir(parents=True, exist_ok=True)
                path.replace(retained / f"{job_id}{path.suffix}")
        except OSError:
            log.exception("Could not clean up private game upload for %s", job_id)
        with _lock:
            _active -= 1
            _cancelled.discard(job_id)
            _running.discard(job_id)


def delete(job_id: str) -> bool:
    with _lock:
        if job_id in _running:
            _cancelled.add(job_id)
    with db._connect() as conn:
        row = conn.execute("SELECT id FROM game_analysis_jobs WHERE id=?", (job_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM game_analysis_jobs WHERE id=?", (job_id,))
    for suffix in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
        (app_config.DATA_DIR / "private-game-uploads" / f"{job_id}{suffix}").unlink(missing_ok=True)
    return bool(row)
