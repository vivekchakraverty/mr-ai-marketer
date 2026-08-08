"""Command line for the health monitor.

    python -m healthmon health      # everything, cheaply — the twice-daily job
    python -m healthmon e2e         # real generation through real endpoints — the weekly job
    python -m healthmon report      # re-render the HTML from history, run nothing
    python -m healthmon install     # register both Windows scheduled tasks
    python -m healthmon uninstall   # remove them

Scheduling uses Windows Task Scheduler rather than a resident daemon. A daemon would have
to survive reboots, sleep and its own crashes to be trusted twice a day; schtasks already
does all three, and it runs the check even if nothing else is open.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import config, report, runner
from .checks import e2e_checks, health_checks

HEALTH_TASK = "MrAIMarketer-HealthCheck"
E2E_TASK = "MrAIMarketer-E2E"


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252, which turns the separators in this output into
    replacement characters. Harmless in a terminal, but the scheduled task redirects to a
    log, and a log full of mojibake is a log nobody reads."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _run(mode: str, checks, open_report: bool) -> int:
    results = runner.run_all(checks)
    entry = runner.record(mode, results)
    path = report.write(entry, runner.load_history())

    order = {"fail": 0, "warn": 1, "skip": 2, "ok": 3}
    for r in sorted(results, key=lambda r: (order[r.status], r.category, r.name)):
        print(f"  {r.status.upper():5} {r.name:38} {r.detail[:70]}")

    s = entry["summary"]
    print(f"\n{mode}: {s.get('ok',0)} ok · {s.get('warn',0)} degraded · "
          f"{s.get('fail',0)} down · {s.get('skip',0)} skipped")
    print(f"report: {path}")

    if open_report:
        subprocess.run(["cmd", "/c", "start", "", path], shell=False, check=False)
    return runner.exit_code(results)


def _task_command(mode: str) -> str:
    """The exact command Task Scheduler will run.

    Two things this gets right that are easy to get wrong:

    * It uses the same interpreter that installed the task, so a scheduled run cannot drift
      onto a different Python than the one just proven to work.
    * It calls run.py by absolute path rather than ``-m healthmon``. Task Scheduler sets no
      working directory for a task created with ``schtasks /tr``, so the module form fails
      with "No module named healthmon" — while still reporting a plausible exit code and
      looking perfectly healthy in the UI.
    """
    python = Path(sys.executable)
    # pythonw avoids a console window flashing on every scheduled run.
    windowless = python.with_name("pythonw.exe")
    exe = windowless if windowless.exists() else python
    launcher = Path(__file__).resolve().parent / "run.py"
    return f'"{exe}" "{launcher}" {mode}'


def _schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def install() -> int:
    root = Path(__file__).resolve().parent.parent  # the directory containing healthmon/
    print(f"working directory for both tasks: {root}\n")

    specs = [
        (HEALTH_TASK, _task_command("health"),
         ["/sc", "daily", "/st", "09:00", "/ri", "720", "/du", "24:00"],
         "twice daily (09:00 and 21:00)"),
        (E2E_TASK, _task_command("e2e"),
         ["/sc", "weekly", "/d", "SUN", "/st", "03:00"],
         "weekly (Sunday 03:00)"),
    ]

    failed = False
    for name, command, when, human in specs:
        # /ri with /du is how schtasks expresses "every N minutes within a window", which is
        # the only way to get a second daily run out of one task definition.
        proc = _schtasks(["/create", "/tn", name, "/tr", command, *when, "/f"])
        if proc.returncode == 0:
            print(f"  installed  {name:32} {human}")
        else:
            failed = True
            print(f"  FAILED     {name:32} {(proc.stderr or proc.stdout).strip()[:160]}")

    if failed:
        print("\nIf the failures mention access, run this from an elevated prompt.")
        return 1
    print("\nBoth tasks are registered. Verify with:  schtasks /query /tn "
          f"{HEALTH_TASK} /v /fo list")
    return 0


def uninstall() -> int:
    code = 0
    for name in (HEALTH_TASK, E2E_TASK):
        proc = _schtasks(["/delete", "/tn", name, "/f"])
        print(f"  {'removed  ' if proc.returncode == 0 else 'not found'}  {name}")
        code |= 0 if proc.returncode == 0 else 0  # absent is not an error worth failing on
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="healthmon", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["health", "e2e", "report", "install", "uninstall"])
    parser.add_argument("--open", action="store_true", help="open the HTML report when done")
    parser.add_argument("--serial", action="store_true", help="run checks one at a time")
    args = parser.parse_args(argv)
    _force_utf8_stdout()

    if args.mode == "install":
        return install()
    if args.mode == "uninstall":
        return uninstall()
    if args.mode == "report":
        history = runner.load_history()
        if not history:
            print("No runs recorded yet — run `python -m healthmon health` first.")
            return 1
        print("report:", report.write(history[-1], history))
        return 0

    if args.mode == "e2e" and not config.HF_TOKEN:
        # Every generation endpoint needs it; without one this would report five auth
        # failures that say nothing about the app's health.
        print("e2e needs HF_TOKEN (set it in healthmon/.env or the environment).")
        return 1

    checks = health_checks() if args.mode == "health" else e2e_checks()
    if args.serial:
        results = runner.run_all(checks, parallel=False)
        entry = runner.record(args.mode, results)
        report.write(entry, runner.load_history())
        return runner.exit_code(results)
    return _run(args.mode, checks, args.open)
