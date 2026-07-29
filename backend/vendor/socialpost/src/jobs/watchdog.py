"""watchdog — detect when generated posts underperform and force a relearn.

Schedule: weekly (.github/workflows/watchdog.yml).

For each niche, compare the mean 48h engagement_rate of posts this system
generated AND the user actually published against that niche's baseline. If
generations are running below UNDERPERFORM_RATIO of baseline with at least
MIN_DATA_POINTS behind the number, dispatch refresh_exemplars.yml.

The decision is recorded in job_runs either way — a watchdog that only logs when
it fires is indistinguishable from a broken watchdog.

How the refresh actually gets triggered depends on where you run:

  Actions   GH_PAT is set, so it workflow_dispatches refresh_exemplars.yml.
            GH_PAT must be a real PAT: a workflow dispatched with the built-in
            GITHUB_TOKEN does not itself trigger further workflow runs, which is
            GitHub's guard against recursion, so the refresh would silently
            never fire.

  Local     No GH_PAT and no workflow to dispatch, so it runs refresh_exemplars
            directly as a subprocess. Without this the self-correcting loop —
            the thing that makes the system "self-evolving" — would simply not
            exist in local mode.

Run:
    python -m src.jobs.watchdog --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests

from ..db import JobRun, configure_logging, get_client, require_env, rpc

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 28
MIN_DATA_POINTS = 5
UNDERPERFORM_RATIO = 0.8

WORKFLOW_FILE = "refresh_exemplars.yml"
DISPATCH_REF = "main"
GITHUB_API = "https://api.github.com"


def _repo() -> str:
    """owner/repo, from GH_REPO or the Actions-provided GITHUB_REPOSITORY."""
    repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError(
            "Cannot determine the repository. Set GH_REPO=owner/repo in your .env "
            "(GITHUB_REPOSITORY is set automatically inside Actions)."
        )
    return repo


def dispatch_refresh(reason: str) -> None:
    """Fire refresh_exemplars.yml via workflow_dispatch."""
    token = require_env("GH_PAT")
    repo = _repo()
    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches"

    response = requests.post(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": DISPATCH_REF, "inputs": {"reason": reason[:200]}},
        timeout=30,
    )
    if response.status_code == 204:
        log.info("Dispatched %s on %s", WORKFLOW_FILE, repo)
        return

    # 404 here almost always means a token scope problem rather than a missing
    # workflow — GitHub returns 404 rather than 403 for unauthorised resources.
    hint = ""
    if response.status_code == 404:
        hint = (
            " (404 usually means GH_PAT lacks Actions: read/write on this repo, or "
            f"{WORKFLOW_FILE} is not on the {DISPATCH_REF} branch yet)"
        )
    raise RuntimeError(
        f"workflow_dispatch failed: HTTP {response.status_code} {response.text[:200]}{hint}"
    )


def refresh_locally(reason: str) -> None:
    """Run refresh_exemplars directly, for when there is no workflow to dispatch.

    Subprocess rather than an import: refresh_exemplars loads sentence-transformers
    (~1GB resident), and the watchdog has no other reason to pay that. It also
    keeps this identical to what the scheduler and a human would run.
    """
    log.info("Running refresh_exemplars locally — %s", reason)
    # Module path and cwd are derived from this module's package rather than
    # hardcoded, so the package still works when vendored inside another app.
    package_parent = Path(__file__).resolve().parents[len((__package__ or "src.jobs").split("."))]
    proc = subprocess.run(
        [sys.executable, "-m", f"{__package__}.refresh_exemplars"],
        cwd=package_parent,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-3:]
        raise RuntimeError(
            f"local refresh_exemplars exited {proc.returncode}: {' | '.join(tail)}"
        )


def trigger_refresh(reason: str) -> str:
    """Force an exemplar refresh by whatever means this environment has.

    GH_PAT is the signal for "there is a GitHub workflow to dispatch". It is set
    in Actions and absent locally, so this needs no extra configuration to do the
    right thing in both places. Returns a short description of what it did, for
    the job_runs note.
    """
    if os.environ.get("GH_PAT"):
        dispatch_refresh(reason)
        return f"dispatched {WORKFLOW_FILE}"
    refresh_locally(reason)
    return "ran refresh_exemplars locally (no GH_PAT; not in Actions)"


def _baselines() -> dict[str, float]:
    """Current 48h niche baselines, keyed by niche."""
    rows = (
        get_client()
        .table("performance_baselines")
        .select("scope_key, avg_engagement_rate")
        .eq("scope", "niche")
        .eq("window_label", "48h")
        .execute()
        .data
        or []
    )
    return {
        r["scope_key"]: float(r["avg_engagement_rate"])
        for r in rows
        if r["avg_engagement_rate"] is not None
    }


def run(dry_run: bool = False) -> None:
    with JobRun("watchdog", dry_run=dry_run) as job:
        baselines = _baselines()
        if not baselines:
            job.note("no niche baselines yet; nothing to compare against")
            return

        performance = rpc("generation_performance", {"lookback_days": LOOKBACK_DAYS})
        if not performance:
            job.note(
                f"no published generations with a 48h snapshot in {LOOKBACK_DAYS}d; "
                f"nothing to judge"
            )
            return

        underperforming: list[str] = []

        for row in performance:
            niche = row["niche"]
            n = int(row["n"] or 0)
            avg = float(row["avg_engagement"] or 0.0)
            baseline = baselines.get(niche)

            if baseline is None:
                job.note(f"{niche}: {n} generations but no baseline; skipped")
                continue

            if n < MIN_DATA_POINTS:
                job.note(
                    f"{niche}: only {n}/{MIN_DATA_POINTS} data points "
                    f"(avg={avg:.5f} vs baseline={baseline:.5f}); not acting"
                )
                continue

            ratio = avg / baseline if baseline else 0.0
            verdict = "UNDER" if ratio < UNDERPERFORM_RATIO else "ok"
            job.note(
                f"{niche}: n={n} avg={avg:.5f} baseline={baseline:.5f} "
                f"ratio={ratio:.2f} -> {verdict}"
            )
            if ratio < UNDERPERFORM_RATIO:
                underperforming.append(niche)

        if not underperforming:
            job.note("all niches at or above threshold; no refresh dispatched")
            return

        reason = (
            f"watchdog: {', '.join(underperforming)} below "
            f"{UNDERPERFORM_RATIO:.0%} of baseline"
        )
        if dry_run:
            how = (
                f"dispatch {WORKFLOW_FILE}"
                if os.environ.get("GH_PAT")
                else "run refresh_exemplars locally"
            )
            job.note(f"would {how} — {reason}")
            return

        did = trigger_refresh(reason)
        job.note(f"{did} — {reason}")
        job.count("refresh_triggered")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger an exemplar refresh when published generations underperform."
    )
    parser.add_argument("--dry-run", action="store_true", help="Decide and log, but do not dispatch.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
