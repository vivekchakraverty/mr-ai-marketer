"""Central configuration: paths, constants, and model defaults."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- External services ---
OPR_API_KEY = os.environ.get("OPR_API_KEY", "")
OPR_ENDPOINT = "https://openpagerank.com/api/v1.0/getPageRank"

# --- Catalog ---
CATALOG_XLSX_PATH = Path(os.environ.get(
    "CATALOG_XLSX_PATH", str(PROJECT_ROOT / "data" / "guest_post_database.xlsx"),
))
CATALOG_SHEET_NAME = "Master Consolidated"
# Prebuilt OpenPageRank cache shipped in the repo (see pipeline/openpagerank.py).
OPR_SCORES_PATH = Path(os.environ.get(
    "OPR_SCORES_PATH", str(PROJECT_ROOT / "data" / "opr_scores.json"),
))

# --- Search / ranking ---
SHORTLIST_SIZE = int(os.environ.get("SHORTLIST_SIZE", "400"))     # TF-IDF top-N before liveness+OPR
RESULTS_PAGE_SIZE = int(os.environ.get("RESULTS_PAGE_SIZE", "10"))
MAX_RESULT_SLOTS = int(os.environ.get("MAX_RESULT_SLOTS", "100"))  # pre-allocated UI slots

# --- Availability check ---
AVAILABILITY_CONCURRENCY = int(os.environ.get("AVAILABILITY_CONCURRENCY", "50"))
AVAILABILITY_TIMEOUT_S = float(os.environ.get("AVAILABILITY_TIMEOUT_S", "7"))
AVAILABILITY_TTL_DAYS = int(os.environ.get("AVAILABILITY_TTL_DAYS", "21"))

# --- Crawler ---
CRAWLER_MAX_LISTING_PAGES = int(os.environ.get("CRAWLER_MAX_LISTING_PAGES", "10"))
CRAWLER_MAX_TITLES = int(os.environ.get("CRAWLER_MAX_TITLES", "300"))
CRAWLER_HTTP_TIMEOUT = float(os.environ.get("CRAWLER_HTTP_TIMEOUT", "12"))

# --- LLM (gap-analysis + crawler-fallback tier); billed to the user's HF token ---
MODEL_SUGGEST = os.environ.get("MODEL_SUGGEST", "openai/gpt-oss-120b")
MODEL_SUGGEST_FALLBACK = os.environ.get("MODEL_SUGGEST_FALLBACK", "Qwen/Qwen2.5-72B-Instruct")
N_SUGGESTIONS = int(os.environ.get("N_SUGGESTIONS", "5"))


def _resolve_data_dir() -> Path:
    """Prefer HF persistent storage (/data); fall back to a local dir if not writable."""
    candidate = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate
    except Exception:
        fallback = PROJECT_ROOT / ".cache"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = _resolve_data_dir()
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
