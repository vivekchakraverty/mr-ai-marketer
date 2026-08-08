"""Run every security scanner over this repo, correctly.

    python scripts/security/scan.py                # everything that is installed
    python scripts/security/scan.py --only trivy   # one tool
    python scripts/security/scan.py --json out.json

Each of these tools passes cleanly on this repo when invoked the obvious way, and each is
wrong when it does. That is the entire reason this file exists — the incantations below were
arrived at by watching a green result and then proving it was empty.

**gitleaks** skips merge commits. `gitleaks git .` scanned 2 of this repo's 4 commits and
found one secret; with `-m` it scans 3 and finds it twice; the 4th is a parentless squashed
subtree root that needs its own pass. A clean report from the default invocation means very
little.

**trivy** cannot read an unpinned requirements.txt — `>=` resolves to nothing, so it reported
zero Python packages while 16 real findings sat in the installed tree. Now that the file is
pinned it parses, but the *lock* still does not: trivy's pip analyzer matches the exact
filename `requirements.txt`, and `requirements.lock.txt`, `requirements-lock.txt`,
`requirements.lock` and `requirements-dev.txt` were all tested and all yield zero packages.
The lock has to be handed over under a name trivy accepts. Separately, `trivy fs` never reads
site-packages at all; only `trivy rootfs` does.

**semgrep** ships a default ignore list that contains `vendor/`. Pointing it at
backend/vendor prints "Ran 393 rules on 0 files: 0 findings" and exits 0. The vendored code
ships inside the app, so it gets staged somewhere without that word in the path.

Nothing here uploads anything: semgrep runs with --metrics=off, and trivy/gitleaks only
fetch rule and vulnerability databases.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN", "INFO"]


@dataclass
class Finding:
    tool: str
    severity: str
    what: str
    where: str
    detail: str = ""


@dataclass
class ToolRun:
    name: str
    ok: bool
    note: str = ""
    findings: list[Finding] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _which(name: str, extra: list[Path] | None = None) -> str | None:
    """Find a tool on PATH, or in the places Windows installers hide them."""
    found = shutil.which(name)
    if found:
        return found
    for candidate in extra or []:
        if candidate.is_file():
            return str(candidate)
    return None


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO), **kw)


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — an unreadable report is a failed scan, not a crash
        return None


# --------------------------------------------------------------------------- gitleaks


def run_gitleaks(out: Path) -> ToolRun:
    exe = _which("gitleaks", [Path("C:/ProgramData/chocolatey/bin/gitleaks.exe")])
    if not exe:
        return ToolRun("gitleaks", False, "not installed — `choco install gitleaks`")

    findings: list[Finding] = []
    seen: set[tuple] = set()

    # Pass 1: history. `-m` forces merge diffs; without it, merges are skipped silently.
    # Pass 2: every root commit on its own — a squashed subtree (`git subtree add --squash`)
    # has no parent, and gitleaks does not diff it against the empty tree by default.
    roots = _run(["git", "rev-list", "--max-parents=0", "--all"]).stdout.split()
    passes = [["--log-opts=--all --full-history -m"]]
    passes += [[f"--log-opts={r} --root"] for r in roots]

    for i, extra in enumerate(passes):
        report = out / f"gitleaks-git-{i}.json"
        _run([exe, "git", ".", *extra, "--report-format", "json",
              "--report-path", str(report), "--exit-code", "0"])
        for row in _load(report) or []:
            key = (row.get("RuleID"), row.get("File"), row.get("StartLine"))
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(
                "gitleaks", "HIGH", row.get("RuleID", "secret"),
                f"{row.get('File')}:{row.get('StartLine')} (commit {str(row.get('Commit'))[:8]})",
                "in git history — rotate the secret; removing the file does not remove it",
            ))

    # Pass 3: the working tree, including files git ignores. A local .env legitimately holds
    # secrets and will show up here; that is informational, not a leak, so it is labelled.
    report = out / "gitleaks-dir.json"
    _run([exe, "dir", ".", "--report-format", "json",
          "--report-path", str(report), "--exit-code", "0"])
    for row in _load(report) or []:
        path = str(row.get("File", ""))
        ignored = _run(["git", "check-ignore", "-q", path]).returncode == 0
        findings.append(Finding(
            "gitleaks", "LOW" if ignored else "HIGH", row.get("RuleID", "secret"),
            f"{path}:{row.get('StartLine')}",
            "gitignored — expected for local config" if ignored else "in a tracked file",
        ))
    return ToolRun("gitleaks", True, f"{len(passes)} history passes + working tree", findings)


# --------------------------------------------------------------------------- trivy


def run_trivy(out: Path) -> ToolRun:
    exe = _which("trivy", [
        Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
        / "AquaSecurity.Trivy_Microsoft.Winget.Source_8wekyb3d8bbwe/trivy.exe",
    ])
    if not exe:
        return ToolRun("trivy", False, "not installed — `winget install AquaSecurity.Trivy`")

    findings: list[Finding] = []
    notes: list[str] = []
    # The same package is described by requirements.txt, the lock and the installed venv, so
    # one CVE would otherwise be reported three times and a single critical would read as
    # three. Keyed by package+CVE; the sources it was seen in are merged into one line.
    vulns: dict[tuple, Finding] = {}
    vuln_sources: dict[tuple, list[str]] = {}

    def collect(report: Path, source: str) -> None:
        data = _load(report) or {}
        for res in data.get("Results") or []:
            for v in res.get("Vulnerabilities") or []:
                key = (v.get("PkgName"), v.get("InstalledVersion"), v.get("VulnerabilityID"))
                vuln_sources.setdefault(key, []).append(source)
                vulns.setdefault(key, Finding(
                    "trivy", v.get("Severity", "UNKNOWN"),
                    f"{v.get('PkgName')}@{v.get('InstalledVersion')}",
                    str(v.get("VulnerabilityID")),
                    f"fix: {v.get('FixedVersion') or 'none available'}",
                ))
            for m in res.get("Misconfigurations") or []:
                findings.append(Finding(
                    "trivy", m.get("Severity", "UNKNOWN"), m.get("ID", "misconfig"),
                    res.get("Target", source), m.get("Title", ""),
                ))
            for s in res.get("Secrets") or []:
                findings.append(Finding(
                    "trivy", s.get("Severity", "UNKNOWN"), s.get("RuleID", "secret"),
                    f"{res.get('Target')}:{s.get('StartLine')}", s.get("Title", ""),
                ))

    skip = ",".join([
        "./electron/release", "./electron/out", "./backend/dist", "./backend/build",
        "./backend/.venv", "./backend/data", "./.git", "./healthmon/reports",
    ])
    report = out / "trivy-fs.json"
    _run([exe, "fs", ".", "--scanners", "vuln,misconfig,secret", "--skip-dirs", skip,
          "--format", "json", "--output", str(report), "--timeout", "60m"])
    collect(report, "repo")
    notes.append("repo tree")

    # The lock, under a name trivy's pip analyzer will actually look at. Every other name was
    # tested and silently yields zero packages.
    lock = BACKEND / "requirements.lock.txt"
    if lock.is_file():
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(lock, Path(tmp) / "requirements.txt")
            report = out / "trivy-lock.json"
            _run([exe, "fs", tmp, "--scanners", "vuln", "--format", "json",
                  "--output", str(report), "--timeout", "30m"])
            collect(report, "requirements.lock.txt")
        notes.append("python lock")
    else:
        notes.append("no lock file — transitive deps unscanned")

    # rootfs, not fs: `trivy fs` does not read site-packages, so a venv scanned with `fs`
    # reports nothing at all.
    venv = BACKEND / ".venv"
    if venv.is_dir():
        report = out / "trivy-venv.json"
        _run([exe, "rootfs", str(venv), "--scanners", "vuln", "--format", "json",
              "--output", str(report), "--timeout", "30m"])
        collect(report, "installed venv")
        notes.append("installed venv")

    for key, finding in vulns.items():
        finding.where = f"{finding.where}  (seen in: {', '.join(dict.fromkeys(vuln_sources[key]))})"
        findings.append(finding)
    return ToolRun("trivy", True, ", ".join(notes), findings)


# --------------------------------------------------------------------------- semgrep


SEMGREP_CONFIGS = ["p/python", "p/typescript", "p/react", "p/security-audit", "p/secrets"]


def run_semgrep(out: Path) -> ToolRun:
    exe = _which("semgrep", [
        REPO / ".tools/semgrep/Scripts/semgrep.exe",
        REPO / ".tools/semgrep/bin/semgrep",
        Path(sys.prefix) / "Scripts/semgrep.exe",
        Path(sys.prefix) / "bin/semgrep",
    ] + ([Path(os.environ["SEMGREP_EXE"])] if os.environ.get("SEMGREP_EXE") else []))
    if not exe:
        return ToolRun("semgrep", False,
                       "not found — put a venv at .tools/semgrep (pip install semgrep), "
                       "or set SEMGREP_EXE to its path")

    findings: list[Finding] = []
    sev_map = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}

    def collect(report: Path, relabel: tuple[str, str] | None = None) -> None:
        for r in (_load(report) or {}).get("results", []):
            extra = r.get("extra", {})
            path = r.get("path", "")
            if relabel:
                path = path.replace(relabel[0], relabel[1])
            findings.append(Finding(
                "semgrep", sev_map.get(extra.get("severity", ""), "LOW"),
                r.get("check_id", "").split(".")[-1],
                f"{path}:{r.get('start', {}).get('line')}",
                (extra.get("message") or "").strip().replace("\n", " ")[:150],
            ))

    common = [*sum((["--config", c] for c in SEMGREP_CONFIGS), []),
              "--metrics=off", "--oss-only", "--no-git-ignore", "--json"]

    report = out / "semgrep-app.json"
    _run([exe, "scan", *common, "--output", str(report),
          "--exclude", "node_modules", "--exclude", ".venv", "--exclude", "dist",
          "--exclude", "release", "--exclude", "out", "--exclude", "build",
          "--exclude", "vendor",
          "backend/app", "backend/ml", "electron/src", "resources", "scripts", "healthmon"])
    collect(report)

    # Vendored code ships inside the app, so it has to be scanned — but semgrep's built-in
    # ignore list contains `vendor/`, and pointing it at backend/vendor scans zero files while
    # reporting success. Staging it under a different path is the only reliable way in.
    vendor = BACKEND / "vendor"
    if vendor.is_dir():
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "shipped-third-party"
            shutil.copytree(vendor, staged,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "node_modules"))
            report = out / "semgrep-vendor.json"
            _run([exe, "scan", *common, "--output", str(report), str(staged)])
            collect(report, relabel=(str(staged), "backend/vendor"))

    return ToolRun("semgrep", True, "app code + vendored code", findings)


# --------------------------------------------------------------------------- report


def report(runs: list[ToolRun], as_json: Path | None) -> int:
    print("\n" + "=" * 78)
    print("SECURITY SCAN")
    print("=" * 78)

    worst = 0
    for run in runs:
        if not run.ok:
            print(f"\n{run.name.upper():10} SKIPPED — {run.note}")
            continue
        counts = {s: sum(1 for f in run.findings if f.severity == s) for s in SEVERITY_ORDER}
        counts = {k: v for k, v in counts.items() if v}
        print(f"\n{run.name.upper():10} {run.note}")
        print(f"           {counts or 'no findings'}")
        for sev in SEVERITY_ORDER:
            for f in [x for x in run.findings if x.severity == sev]:
                print(f"   [{sev:8}] {f.what}")
                print(f"              {f.where}")
                if f.detail:
                    print(f"              {f.detail}")
        if any(f.severity in ("CRITICAL", "HIGH") for f in run.findings):
            worst = 1

    if as_json:
        as_json.write_text(json.dumps(
            [{"tool": r.name, "ok": r.ok, "note": r.note,
              "findings": [vars(f) for f in r.findings]} for r in runs],
            indent=2), encoding="utf-8")
        print(f"\nwrote {as_json}")

    print("\nA clean result here is only as good as the invocations above — read this file's"
          "\ndocstring before trusting a green run from any of these tools directly.")
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["gitleaks", "trivy", "semgrep"], help="run one tool")
    ap.add_argument("--json", type=Path, help="also write findings as JSON")
    ap.add_argument("--keep-reports", action="store_true", help="keep the raw tool output")
    args = ap.parse_args()

    # Raw reports go to a temp directory *outside* the repo. Writing them inside it meant the
    # working-tree pass scanned gitleaks' own output and re-reported every secret it had just
    # found, from the report file. Copied back in afterwards only if asked for.
    out = Path(tempfile.mkdtemp(prefix="mraim-scan-"))

    runners = {"gitleaks": run_gitleaks, "trivy": run_trivy, "semgrep": run_semgrep}
    chosen = [args.only] if args.only else list(runners)

    runs = []
    try:
        for name in chosen:
            print(f"running {name} …", flush=True)
            runs.append(runners[name](out))
        code = report(runs, args.json)
        if args.keep_reports:
            kept = REPO / ".security-reports"
            shutil.rmtree(kept, ignore_errors=True)
            shutil.copytree(out, kept)
            print(f"raw reports kept in {kept}")
    finally:
        shutil.rmtree(out, ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
