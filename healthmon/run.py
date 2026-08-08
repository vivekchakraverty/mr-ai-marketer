"""Entry point for scheduled runs, callable by absolute path from any directory.

``python -m healthmon`` needs the repo root to be the working directory, and Windows Task
Scheduler does not set one unless the task is created from XML — so a task registered with
``schtasks /tr`` silently fails with "No module named healthmon", reports a generic exit
code 1, and looks healthy in the Task Scheduler UI forever. (Confirmed the hard way: the
first registered task fired and wrote nothing.)

Putting the package's parent on sys.path here removes the dependency on the working
directory entirely, which is one less thing that has to be true at 09:00 on a Sunday.

    pythonw.exe "…/healthmon/run.py" health
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from healthmon.cli import main  # noqa: E402 — must follow the sys.path fix above

if __name__ == "__main__":
    sys.exit(main())
