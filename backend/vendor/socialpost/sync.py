"""Re-sync the vendored Social Post Generator from its source repo.

    python backend/vendor/socialpost/sync.py [SOURCE_DIR]

Copies only what the backend needs. Deliberately excludes the source repo's own
Streamlit UI, tests, GitHub workflows and the telemetry Space — the master app
supplies its own UI and scheduling, and shipping those would just be dead weight
that drifts.

The vendored package is imported as `vendor.socialpost.src.*` (matching how
vendor/docmaker is imported), which works because the source package uses
relative imports internally and resolves its own config/ and migrations/ relative
to the package root.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path(r"C:\social media post generator")

# (relative path, is_directory)
INCLUDE: tuple[tuple[str, bool], ...] = (
    ("src", True),
    ("config", True),
    ("migrations", True),
    # The settings schema is parsed from this file at runtime, so it is not
    # documentation here — it is data the Settings screen depends on.
    (".env.example", False),
)

EXCLUDE_DIR_NAMES = {"__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}


def sync(source: Path) -> None:
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")

    for rel, is_dir in INCLUDE:
        src = source / rel
        dst = VENDOR_DIR / rel
        if not src.exists():
            print(f"  skip (missing): {rel}")
            continue
        if is_dir:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(
                src, dst, ignore=shutil.ignore_patterns(*EXCLUDE_DIR_NAMES, "*.pyc")
            )
        else:
            shutil.copy2(src, dst)
        print(f"  synced: {rel}")

    # A package marker so `vendor.socialpost` is importable as a package.
    (VENDOR_DIR / "__init__.py").touch()
    print(f"\nVendored into {VENDOR_DIR}")


if __name__ == "__main__":
    sync(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE)
