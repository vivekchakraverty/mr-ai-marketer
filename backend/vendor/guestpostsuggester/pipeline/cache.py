"""Tiny keyed cache under DATA_DIR for JSON-serializable pipeline artifacts.

The generic get/put/run_key trio is reused verbatim from the sibling BlogWriter project.
The key-naming helpers below are specific to this Space's cache shapes: a singleton
catalog-wide OpenPageRank map, plus per-domain availability/title-library/suggestions
entries that are populated lazily as searches and Analyze clicks happen.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from . import config


def run_key(*parts: str) -> str:
    """Stable short hash identifying an entry from its inputs."""
    joined = " ".join(p or "" for p in parts)
    h = hashlib.sha256(joined.encode("utf-8"))
    return h.hexdigest()[:16]


def _path(key: str, name: str):
    return config.CACHE_DIR / f"{key}.{name}.json"


def get(key: str, name: str) -> Optional[Any]:
    p = _path(key, name)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def put(key: str, name: str, value: Any) -> None:
    p = _path(key, name)
    try:
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Cache is best-effort; never fail the pipeline over it.
        pass


# --- key-naming conventions for this Space's cache shapes ---

def availability_key(domain: str) -> str:
    return run_key("availability", domain)


def title_library_key(domain: str) -> str:
    return run_key("titles", domain)


def suggestions_key(domain: str, topic: str) -> str:
    return run_key("suggest", domain, topic)
