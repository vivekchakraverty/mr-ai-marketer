"""OpenPageRank integration: batch-fetch domain authority scores.

API: GET https://openpagerank.com/api/v1.0/getPageRank
Auth: header  API-OPR: <key>
Params: repeated domains[]=<domain>  (up to 100 per call)
Docs: https://www.domcop.com/openpagerank/documentation
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import requests

from . import cache, config


def _chunks(items: List[str], size: int = 100):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_pageranks(domains: List[str]) -> Dict[str, float]:
    """Return {domain: page_rank_decimal} for up to 100 domains in one call.

    Raises RuntimeError if the API key is missing or the request fails.
    """
    if not config.OPR_API_KEY:
        raise RuntimeError(
            "OPR_API_KEY is not set. Add an OpenPageRank API key (free at domcop.com) "
            "as an environment variable / Space secret."
        )
    scores: Dict[str, float] = {d: 0.0 for d in domains}
    headers = {"API-OPR": config.OPR_API_KEY}
    params = [("domains[]", d) for d in domains]
    try:
        r = requests.get(
            config.OPR_ENDPOINT, params=params, headers=headers, timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"OpenPageRank request failed: {e}") from e

    for item in payload.get("response", []) or []:
        dom = (item.get("domain") or "").lower()
        if dom not in scores:
            continue
        try:
            scores[dom] = float(item.get("page_rank_decimal") or 0.0)
        except (TypeError, ValueError):
            scores[dom] = 0.0
    return scores


def precompute_all(
    domains: List[str], progress_cb: Optional[Callable[[float, str], None]] = None
) -> Dict[str, float]:
    """Fetch OPR scores for the entire catalog once, persisting incrementally so an
    interrupted run can resume without re-fetching domains already scored.
    """
    existing = cache.get("catalog", "opr_scores") or {}
    todo = [d for d in domains if d not in existing]
    if not todo:
        return existing

    scores = dict(existing)
    chunks = list(_chunks(todo, 100))
    for i, chunk in enumerate(chunks):
        chunk_scores = fetch_pageranks(chunk)
        scores.update(chunk_scores)
        cache.put("catalog", "opr_scores", scores)
        if progress_cb:
            progress_cb((i + 1) / len(chunks), f"OpenPageRank {i + 1}/{len(chunks)} batches")
    return scores


def load_cached_scores() -> Dict[str, float]:
    """Prefer the runtime cache (kept fresh by precompute_all); fall back to the prebuilt
    JSON shipped in the repo (data/opr_scores.json) so a fresh checkout works immediately.
    """
    cached = cache.get("catalog", "opr_scores")
    if cached:
        return cached
    if config.OPR_SCORES_PATH.exists():
        import json

        try:
            return json.loads(config.OPR_SCORES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}
