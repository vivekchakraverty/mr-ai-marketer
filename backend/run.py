"""PyInstaller entry point — runs the FastAPI app with uvicorn, bound to a port passed on
the command line (matching how electron/src/main/backend.ts spawns the packaged exe).

Not used in dev (dev runs `python -m uvicorn app.main:app` directly, see backend.ts) — this
script only exists so PyInstaller has a plain-Python entry point to freeze.
"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8756)
    args = parser.parse_args()

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
