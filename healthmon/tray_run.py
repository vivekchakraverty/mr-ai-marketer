"""Entry point for the tray watcher, callable by absolute path from any directory.

Same reason healthmon/run.py exists, and the same trap: ``python -m healthmon.tray`` needs
the repo root as the working directory, and a login launched from the HKCU\\...\\Run key does
not set one — so the module form dies with "No module named healthmon" before anything is
on screen. There is no console to see it in either, because the entry uses pythonw, so the
only symptom is a tray icon that never appears.

Putting the package's parent on sys.path here removes the dependency on the working
directory, which is one less thing that has to be true at login.

    pythonw.exe "…/healthmon/tray_run.py"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from healthmon.tray import main  # noqa: E402 — must follow the sys.path fix above

if __name__ == "__main__":
    sys.exit(main())
