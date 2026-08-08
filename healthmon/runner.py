"""Runs a set of checks, records the outcome, and decides the exit code.

History is append-only JSONL so a run never has to read what came before it in order to
write — the report reads the tail. Keeping every run (rather than only the latest) is the
point of a monitor: "the engine was down at 09:00 and up at 21:00" is the thing you
actually want to know, and it can only be answered from history.
"""
from __future__ import annotations

import json
import platform
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config
from .checks import Check, CheckResult, run_check

# Checks are almost all network waits, so running them together turns a ~2 minute serial
# walk into a few seconds. Bounded because several probe the same local backend.
_MAX_PARALLEL = 8


def run_all(checks: list[Check], parallel: bool = True) -> list[CheckResult]:
    if not parallel:
        return [run_check(c) for c in checks]
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL) as pool:
        return list(pool.map(run_check, checks))


def summarise(results: list[CheckResult]) -> dict:
    counts = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def record(mode: str, results: list[CheckResult]) -> dict:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "mode": mode,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": platform.node(),
        "summary": summarise(results),
        "results": [
            {"name": r.name, "category": r.category, "status": r.status,
             "detail": r.detail, "ms": r.ms}
            for r in results
        ],
    }
    with config.HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_history(limit: int = 40) -> list[dict]:
    if not config.HISTORY_PATH.exists():
        return []
    lines = config.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a truncated final line (killed mid-write) must not break reporting
    return entries


def exit_code(results: list[CheckResult]) -> int:
    """0 all good, 1 something degraded, 2 something is down.

    Separated so a scheduled task can distinguish "look at this soon" from "this is broken
    now" — Task Scheduler surfaces the last result code, and warn-vs-fail is the difference
    between a sleeping Space and a dead one.
    """
    counts = summarise(results)
    if counts.get("fail"):
        return 2
    if counts.get("warn"):
        return 1
    return 0
