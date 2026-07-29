"""Local scheduler — the replacement for GitHub Actions in local mode.

    python -m src.scheduler --status      # what's due, what ran when
    python -m src.scheduler --once        # run everything overdue, then exit
    python -m src.scheduler --daemon      # stay up and keep doing that
    python -m src.scheduler --install     # printable OS scheduler entry

Catch-up, not cron
------------------
Actions fires jobs at wall-clock times. A laptop cannot: it sleeps, it travels,
it gets closed at 6pm on Friday. A cron-shaped local scheduler would simply skip
every job that came due while the lid was shut, and skip them silently.

So this schedules on *elapsed time since the last run* instead, read from the
job_runs table the jobs already write. Close the laptop for three days and the
next tick runs each overdue job exactly once — not 72 times, and not never.
That makes the same schedule survive a machine that is off more than it is on.

What catch-up cannot fix
------------------------
snapshot is the exception, and it is worth being honest about. Its whole purpose
is to catch posts inside a narrow age window (1h ±30min, 24h ±2h, 48h ±2h). If
the machine was asleep when a post passed through its window, no later run can
recover that measurement — the moment is gone. Catch-up keeps the job healthy; it
does not rewind time. A machine that sleeps a lot will have gappier data than one
that does not, and `snapshot --backfill-48h` is the only partial remedy.

If you need real reliability, use the Supabase backend and GitHub Actions. Local
mode trades some completeness for owning your own stack.

Why subprocesses
----------------
Each job runs as `python -m src.jobs.<name>`, exactly as if you had typed it.
Three reasons: refresh_exemplars loads sentence-transformers (~1GB resident) and a
long-lived daemon should hand that back to the OS when the job ends; a job that
crashes hard cannot take the scheduler with it; and there is no second code path
to diverge from the manual one.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .db import REPO_ROOT, backend, configure_logging, get_client, utcnow

log = logging.getLogger("scheduler")

# Jobs run as `python -m <package>.jobs.<name>`, derived from this module's own
# package rather than hardcoded, so the package works wherever it is installed —
# standalone it resolves to "src.jobs", vendored into another app it resolves to
# e.g. "vendor.socialpost.src.jobs". _PACKAGE_PARENT is the directory that has to
# be the working directory for that module path to import, computed the same way.
_JOBS_PACKAGE = f"{__package__}.jobs"
_PACKAGE_PARENT = Path(__file__).resolve().parents[len((__package__ or "src").split("."))]


@dataclass(frozen=True)
class ScheduledJob:
    name: str  # module under src.jobs, and the job_runs.job_name it writes
    every: timedelta
    why: str


# Ordered by data flow: collect, then measure, then learn, then judge, then tidy.
# Within a single tick that means a fresh post can be ingested and measured in the
# same pass rather than waiting a whole cycle.
#
# Intervals mirror .github/workflows/*.yml. Keep them in step.
SCHEDULE: tuple[ScheduledJob, ...] = (
    ScheduledJob("ingest", timedelta(hours=6), "collect posts by niche keyword"),
    ScheduledJob("snapshot", timedelta(hours=1), "measure engagement at 1h/24h/48h"),
    ScheduledJob("ingest_kb", timedelta(days=1), "pull platform updates from RSS"),
    ScheduledJob("refresh_exemplars", timedelta(days=1), "rebuild the exemplar pool"),
    ScheduledJob("watchdog", timedelta(days=7), "check published posts vs baseline"),
    ScheduledJob("cleanup", timedelta(days=7), "decay the KB, prune old rows"),
    # Inert unless TELEMETRY_ENDPOINT is set; the job self-skips otherwise, so it
    # is safe to schedule unconditionally.
    ScheduledJob("telemetry", timedelta(hours=6), "collect outcomes, drain send queue"),
)

# How often --daemon wakes to re-check. Jobs decide their own due-ness, so this
# only bounds how late a job can start, not how often it runs.
TICK_SECONDS = 60

# A job that hangs should not wedge the scheduler forever. Generous: the exemplar
# refresh downloads a model on first run.
JOB_TIMEOUT_SECONDS = 1800


def _last_started(job_name: str) -> datetime | None:
    """When this job last started, from job_runs. None if it never has.

    Deliberately keyed on started_at regardless of status: a job that failed
    should wait out its interval before trying again rather than retrying every
    tick against whatever is broken.
    """
    rows = (
        get_client()
        .table("job_runs")
        .select("started_at")
        .eq("job_name", job_name)
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    return datetime.fromisoformat(rows[0]["started_at"])


def due_jobs(now: datetime | None = None) -> list[tuple[ScheduledJob, datetime | None]]:
    """Jobs whose interval has elapsed, in SCHEDULE order."""
    now = now or utcnow()
    out = []
    for job in SCHEDULE:
        last = _last_started(job.name)
        if last is None or (now - last) >= job.every:
            out.append((job, last))
    return out


def run_job(job: ScheduledJob, dry_run: bool = False) -> bool:
    """Run one job as a subprocess. Returns True on a clean exit."""
    cmd = [sys.executable, "-m", f"{_JOBS_PACKAGE}.{job.name}"]
    if dry_run:
        cmd.append("--dry-run")

    log.info("-> %s (%s)", job.name, job.why)
    try:
        proc = subprocess.run(
            cmd,
            # The dir the top-level package lives in, so `-m <package>.jobs.x`
            # imports. Standalone that is REPO_ROOT; vendored it is higher up.
            cwd=_PACKAGE_PARENT,
            capture_output=True,
            text=True,
            timeout=JOB_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # The job's own job_runs row is left NULL; cleanup's reaper resolves it.
        log.error("%s exceeded %ds and was killed", job.name, JOB_TIMEOUT_SECONDS)
        return False

    # Jobs log to stderr via logging; surface the tail either way.
    output = (proc.stderr or "") + (proc.stdout or "")
    tail = [ln for ln in output.strip().splitlines() if ln.strip()][-3:]
    for line in tail:
        log.info("   %s", line)

    if proc.returncode != 0:
        log.error("%s exited %d", job.name, proc.returncode)
        return False
    return True


def tick(dry_run: bool = False) -> int:
    """Run everything currently due. Returns the number of jobs that failed."""
    pending = due_jobs()
    if not pending:
        log.debug("nothing due")
        return 0

    failures = 0
    for job, last in pending:
        ago = "never run" if last is None else f"last {_ago(utcnow() - last)} ago"
        log.info("%s is due (%s, every %s)", job.name, ago, _human(job.every))
        if not run_job(job, dry_run=dry_run):
            failures += 1
    return failures


def _human(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def _ago(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600:02d}h"


def status() -> None:
    """Print each job's last run and when it is next due."""
    now = utcnow()
    print(f"backend: {backend()}\n")
    print(f"  {'job':<20} {'every':>6}  {'last run':>12}  {'next due':>12}")
    print(f"  {'-' * 20} {'-' * 6}  {'-' * 12}  {'-' * 12}")
    for job in SCHEDULE:
        last = _last_started(job.name)
        if last is None:
            last_s, next_s = "never", "now"
        else:
            last_s = _ago(now - last) + " ago"
            remaining = (last + job.every) - now
            next_s = "now" if remaining.total_seconds() <= 0 else "in " + _ago(remaining)
        print(f"  {job.name:<20} {_human(job.every):>6}  {last_s:>12}  {next_s:>12}")
    print()
    pending = [j.name for j, _ in due_jobs(now)]
    print(f"due now: {', '.join(pending) if pending else 'nothing'}")


def install_instructions() -> None:
    """Print (never apply) an OS scheduler entry that calls --once.

    Printed rather than installed: creating a scheduled task edits system
    configuration, and doing that as a side effect of a --help-adjacent flag is
    not a decision this script gets to make for you. Copy the line if you want it.
    """
    python = sys.executable
    root = REPO_ROOT
    print("Two ways to keep local mode running.\n")
    print("1. Foreground, no system changes, works everywhere:\n")
    print(f"     cd {root}")
    print(f"     {python} -m src.scheduler --daemon\n")
    print("   Leave it in a terminal or a tmux/screen pane. Ctrl+C stops it.\n")
    print("2. Hand it to the OS scheduler, calling --once every 15 minutes.")
    print("   Nothing below is executed for you — copy it if you want it.\n")

    if sys.platform == "win32":
        print("   Windows (Task Scheduler):\n")
        print(
            f'     schtasks /create /tn "SocialPostGenerator" /sc minute /mo 15 ^\n'
            f'       /tr "\\"{python}\\" -m src.scheduler --once" /st 00:00\n'
        )
        print("   Remove it again with:\n")
        print('     schtasks /delete /tn "SocialPostGenerator" /f\n')
        print(
            "   Note: Task Scheduler does not set a working directory, so prefer\n"
            "   option 1 unless you wrap the command in a .bat that cd's to the repo."
        )
    else:
        print("   Linux/macOS (crontab -e), one line:\n")
        print(f"     */15 * * * * cd {root} && {python} -m src.scheduler --once >> /tmp/spg.log 2>&1\n")
        print("   macOS: give cron/Terminal Full Disk Access, or it cannot read the repo.")

    print("\n   Either way jobs only run when their interval has elapsed, so a")
    print("   15-minute poll does not mean a 15-minute ingest.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the scheduled jobs locally, without GitHub Actions.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run whatever is due, then exit.")
    mode.add_argument("--daemon", action="store_true", help="Keep running; check every minute.")
    mode.add_argument("--status", action="store_true", help="Show last run and next due per job.")
    mode.add_argument("--install", action="store_true", help="Print an OS scheduler entry.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to every job it starts (nothing is written).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    if args.install:
        install_instructions()
        return
    if args.status:
        status()
        return
    if args.once:
        raise SystemExit(1 if tick(dry_run=args.dry_run) else 0)

    log.info(
        "Scheduler up (backend=%s, checking every %ds). Ctrl+C to stop.",
        backend(),
        TICK_SECONDS,
    )
    for job in SCHEDULE:
        log.info("  %-18s every %s", job.name, _human(job.every))
    try:
        while True:
            try:
                tick(dry_run=args.dry_run)
            except Exception:  # noqa: BLE001 — a bad tick must not end the daemon
                log.exception("Tick failed; continuing")
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
