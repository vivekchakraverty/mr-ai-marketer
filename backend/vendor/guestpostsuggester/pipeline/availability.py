"""Reachability check for a shortlist of candidate domains. Deliberately scoped to a
per-search shortlist, never a full-catalog sweep — OpenPageRank batches 100 domains/call
(cheap to precompute for the whole catalog), but liveness has no batching (one HTTP
request per domain), so checking all ~32K domains individually would take hours. Any
received HTTP response counts as alive; connection errors, timeouts, and DNS failures
count as dead. This is a reachability check only, not a content-quality check — a parked
domain that soft-redirects with HTTP 200 is still "alive" here by design.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import httpx

from . import cache, config

_USER_AGENT = "Mozilla/5.0 (compatible; GuestPostSuggester/1.0)"


def _is_fresh(checked_at_iso: str) -> bool:
    try:
        checked_at = datetime.fromisoformat(checked_at_iso)
    except (TypeError, ValueError):
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at < timedelta(days=config.AVAILABILITY_TTL_DAYS)


async def _check_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, domain: str) -> bool:
    async with sem:
        for scheme in ("https://", "http://"):
            try:
                resp = await client.get(
                    f"{scheme}{domain}/",
                    timeout=config.AVAILABILITY_TIMEOUT_S,
                    follow_redirects=True,
                )
                return resp is not None  # any status code counts as alive
            except httpx.RequestError:
                continue  # try the other scheme before giving up
        return False


async def _check_many_async(domains: List[str]) -> Dict[str, bool]:
    sem = asyncio.Semaphore(config.AVAILABILITY_CONCURRENCY)
    headers = {"User-Agent": _USER_AGENT}
    async with httpx.AsyncClient(headers=headers) as client:
        tasks = {d: asyncio.create_task(_check_one(client, sem, d)) for d in domains}
        return {d: await t for d, t in tasks.items()}


def check_availability(domains: List[str]) -> Dict[str, bool]:
    """Sync entrypoint (Gradio event handlers are plain sync functions). Consults the TTL
    cache first; only live-checks domains that are missing or stale.
    """
    to_check: List[str] = []
    results: Dict[str, bool] = {}
    for d in domains:
        cached = cache.get(cache.availability_key(d), "status")
        if cached and _is_fresh(cached.get("checked_at", "")):
            results[d] = bool(cached.get("alive"))
        else:
            to_check.append(d)

    if to_check:
        fresh_results = asyncio.run(_check_many_async(to_check))
        now_iso = datetime.now(timezone.utc).isoformat()
        for d, alive in fresh_results.items():
            cache.put(cache.availability_key(d), "status", {"alive": alive, "checked_at": now_iso})
            results[d] = alive

    return results
