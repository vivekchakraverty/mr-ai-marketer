"""Bounded background analysis and SQLite report persistence."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ... import db
from . import media, public_search, scoring

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viral-footprint")
_lock = threading.Lock()
_active = 0
MAX_QUEUED = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize() -> None:
    with db._connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS viral_footprint_jobs (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          metadata_json TEXT, fingerprint_json TEXT, report_json TEXT, error TEXT,
          search_terms TEXT NOT NULL DEFAULT '', reference_url TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS viral_footprint_cache (
          source_key TEXT PRIMARY KEY, version TEXT NOT NULL,
          fingerprint_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(viral_footprint_jobs)")}
        for column in ("search_terms", "reference_url"):
            if column not in columns:
                conn.execute(f"ALTER TABLE viral_footprint_jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE viral_footprint_jobs SET status='failed', error='Analysis interrupted by restart', updated_at=? WHERE status NOT IN ('completed','failed')", (_now(),))


def _set(job_id: str, status: str, **fields) -> None:
    values = {"status": status, "updated_at": _now(), **fields}
    assignments = ", ".join(f"{key}=?" for key in values)
    with db._connect() as conn:
        conn.execute(f"UPDATE viral_footprint_jobs SET {assignments} WHERE id=?", (*values.values(), job_id))


def get(job_id: str) -> dict | None:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM viral_footprint_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    report = json.loads(row["report_json"]) if row["report_json"] else None
    if report and not report.get("matches"):
        # Old reports encoded missing evidence as zero. Correct that at read
        # time too, so reopening a saved result does not retain the misleading score.
        report["content_virality"] = scoring.content_virality([])
    return {"id": row["id"], "filename": row["filename"], "status": row["status"],
            "search_terms": row["search_terms"], "reference_url": row["reference_url"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
            "report": report,
            "error": row["error"]}


def submit(path: Path, filename: str, search_terms: str = "", reference_url: str = "") -> str:
    global _active
    with _lock:
        if _active >= MAX_QUEUED:
            raise RuntimeError("Video analysis queue is full. Try again shortly.")
        _active += 1
    job_id = uuid.uuid4().hex
    try:
        with db._connect() as conn:
            conn.execute("INSERT INTO viral_footprint_jobs(id, filename, status, created_at, updated_at, search_terms, reference_url) VALUES (?,?,?,?,?,?,?)",
                         (job_id, filename, "queued", _now(), _now(), search_terms, reference_url))
        _pool.submit(_run, job_id, path, search_terms, reference_url)
    except Exception:
        with _lock:
            _active -= 1
        raise
    return job_id


def _transcribe(path: Path) -> tuple[str, str | None]:
    if os.environ.get("MRAIM_VIRAL_FOOTPRINT_TRANSCRIBE") == "0":
        return "", "Local Whisper transcription is off."
    try:
        from vendor.docmaker.src.transcribe import transcribe
        return transcribe(path).text, None
    except Exception as exc:
        log.warning("Whisper transcription unavailable: %s", exc)
        return "", "Local Whisper transcription was unavailable."


def _cached_fingerprint(source_id: str, version: str) -> dict | None:
    with db._connect() as conn:
        row = conn.execute("SELECT version, fingerprint_json FROM viral_footprint_cache WHERE source_key=?", (source_id,)).fetchone()
    return json.loads(row["fingerprint_json"]) if row and row["version"] == version else None


def _cache(source_id: str, version: str, fingerprint: dict) -> None:
    with db._connect() as conn:
        conn.execute("INSERT OR REPLACE INTO viral_footprint_cache VALUES (?,?,?,?)",
                     (source_id, version, json.dumps(fingerprint), _now()))


def _analyze(job_id: str, path: Path, search_terms: str, reference_url: str = "") -> None:
    _set(job_id, "extracting_media")
    info = media.probe(path)
    _set(job_id, "fingerprinting", metadata_json=json.dumps(info))
    if (search_terms or reference_url) and os.environ.get("MRAIM_VIRAL_FOOTPRINT_TRANSCRIBE") != "1":
        transcript, transcript_note = "", "Transcript matching was skipped because search terms were supplied."
    else:
        transcript, transcript_note = _transcribe(path)
    uploaded = media.fingerprint(path, info, transcript)
    _set(job_id, "finding_candidates", fingerprint_json=json.dumps(uploaded))
    query = public_search.query_terms(search_terms, transcript) if search_terms or transcript or not reference_url else ""
    rows = public_search.search(query) if query else []
    if reference_url:
        direct = public_search.reference_candidate(reference_url)
        rows = [direct, *(row for row in rows if row["video_id"] != direct["video_id"])]
    candidates, eligible = public_search.select_candidates(rows, info["duration"])
    _set(job_id, "comparing")
    comparisons: list[dict] = []
    checked = 0
    errors = 0
    for row in candidates:
        video_id = str(row["video_id"])
        cache_key = f"youtube:{video_id}"
        version = str(row.get("duration_s") or "unknown")
        with tempfile.TemporaryDirectory(prefix="viral-footprint-candidate-") as temp:
            try:
                candidate = _cached_fingerprint(cache_key, version)
                details = {}
                if candidate is None:
                    candidate_path, details = public_search.download_video(video_id, Path(temp))
                    candidate = media.fingerprint(candidate_path)
                    _cache(cache_key, version, candidate)
                checked += 1
                if media.visual_similarity(uploaded, candidate) >= scoring.DEFAULT_MATCH_CONFIG.minimum_visual and transcript:
                    candidate["transcript"] = public_search.transcript(video_id)
                result = scoring.score_match(uploaded, candidate)
                if result["is_match"]:
                    if not details:
                        details = public_search.video_metadata(video_id)
                upload_date = str(details.get("upload_date") or "")
                posted_at = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}" if len(upload_date) == 8 else None
                comparisons.append({"video_id": video_id, "platform": "YouTube", "title": details.get("title") or row.get("title") or "Untitled",
                                    "post_url": row["url"], "creator": details.get("channel") or row.get("channel"),
                                    "views": details.get("view_count") if details.get("view_count") is not None else row.get("views"),
                                    "likes": details.get("like_count"), "comments": details.get("comment_count"),
                                    "shares": None, "posted_at": posted_at, "discovered_at": _now(),
                                    "duration_seconds": candidate["duration"],
                                    "post_virality_score": None, "outlier_multiplier": None, "creator_baseline": None,
                                    **result})
            except (media.MediaError, public_search.PublicSearchUnavailable, OSError, ValueError) as exc:
                errors += 1
                log.info("Public candidate %s could not be compared: %s", video_id, exc)
    _set(job_id, "collecting_metrics")
    if candidates and checked == 0 and errors:
        raise public_search.PublicSearchUnavailable("YouTube returned candidates, but none could be downloaded or decoded. Try different search terms or retry later.")
    comparisons.sort(key=lambda item: item["visual_similarity"], reverse=True)
    matches = [item for item in comparisons if item["is_match"]]
    matches.sort(key=lambda m: m["similarity"], reverse=True)
    _set(job_id, "calculating_virality")
    report = {"content_virality": scoring.content_virality(matches), "matches": matches,
              "comparisons": comparisons, "benchmark": scoring.comparison_benchmark(eligible),
              "coverage": {"source": "YouTube public search", "query": query, "search_results": len(rows),
                           "queries": public_search.search_queries(query) if query else [],
                           "reference_url": reference_url or None,
                           "duration_candidates": len(candidates), "duration_excluded": len(rows) - len(eligible),
                           "limit_excluded": len(eligible) - len(candidates),
                           "checked": checked, "comparison_errors": errors},
              "notes": ["Search covers only returned YouTube candidates, not every public video.",
                        "A missing verified copy means virality is unknown; this report does not predict future views.",
                        "YouTube search and video access may change or be rate limited.",
                        "Creator baseline and per-post virality are unavailable without channel history.",
                        "Observed views sum post counters and are not unique viewers.",
                        "TikTok, Douyin, Instagram, and Reddit video search are unavailable in this mode."]
                       + ([transcript_note] if transcript_note else [])}
    _set(job_id, "completed", report_json=json.dumps(report))


def _run(job_id: str, path: Path, search_terms: str, reference_url: str = "") -> None:
    global _active
    try:
        _analyze(job_id, path, search_terms, reference_url)
    except Exception as exc:
        if isinstance(exc, (media.MediaError, public_search.PublicSearchUnavailable)):
            log.info("Viral Footprint analysis %s could not complete: %s", job_id, exc)
        else:
            log.exception("Viral Footprint analysis %s failed", job_id)
        detail = str(exc) if isinstance(exc, (media.MediaError, public_search.PublicSearchUnavailable)) else "Video analysis failed. Check the backend log."
        _set(job_id, "failed", error=detail)
    finally:
        path.unlink(missing_ok=True)
        with _lock:
            _active -= 1
