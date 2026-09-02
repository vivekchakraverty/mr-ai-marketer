"""Configuration for the health monitor.

Deliberately its own configuration rather than the app's. Mr. AI Marketer keeps the user's
secrets in ``config.enc``, encrypted with Electron's safeStorage (DPAPI on Windows), which
nothing outside Electron can decrypt — so the monitor reads its own environment instead of
pretending it can borrow the app's. Everything here is optional: without a token the
monitor still checks every public Space, every local service and every module, and reports
the token-gated checks as skipped rather than failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Frozen by PyInstaller, `__file__` resolves inside the onefile extraction directory — a
# temp folder that is deleted the moment the process exits. Writing history there would
# throw away the whole point of keeping it ("the engine was down at 09:00 and up at 21:00"
# can only be answered from history), and would silently do so, because each run would find
# an empty file and carry on. So the frozen build keeps state beside the app's own data, and
# looks for its .env next to the .exe where somebody can actually put one.
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(__file__).resolve().parent
_EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
_DEFAULT_STATE = (
    Path(os.environ.get("APPDATA", Path.home())) / "mr-ai-marketer" / "healthmon"
    if FROZEN else ROOT / "state"
)

STATE_DIR = Path(os.environ.get("HEALTHMON_STATE_DIR", _DEFAULT_STATE))
HISTORY_PATH = STATE_DIR / "history.jsonl"
REPORT_PATH = STATE_DIR / "report.html"


def _load_dotenv() -> None:
    """Reads healthmon/.env if present. Hand-rolled to keep this app dependency-light —
    it should be able to run on a bare Python with only `requests` available."""
    env_file = _EXE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# The app's own local services. Ports mirror electron/src/main/{backend,activepieces,leadgen}.ts
# and backend/app/config.py — 127.0.0.1 rather than localhost throughout, for the same
# reason the backend pins it: on Windows `localhost` resolves to ::1 first and WSL2's port
# relay sometimes re-binds IPv4-only, which makes a healthy service look dead.
BACKEND_URL = os.environ.get("HEALTHMON_BACKEND_URL", "http://127.0.0.1:8756")


def _api_token() -> str:
    """The backend's per-session token, read from the file it publishes.

    The backend rejects unauthenticated callers because binding to 127.0.0.1 does not stop a
    web page from reaching it. This monitor is not a web page — it runs locally with the
    user's own permissions — so it reads the token from disk, which is a channel a browser
    has no way to use.

    Empty is fine and expected when the app is not running: the probes fail with a connection
    error long before the token would matter.
    """
    override = os.environ.get("HEALTHMON_API_TOKEN", "").strip()
    if override:
        return override
    for candidate in (
        Path(os.environ.get("DATA_DIR", "")) / "api-token" if os.environ.get("DATA_DIR") else None,
        Path(os.environ.get("APPDATA", "")) / "mr-ai-marketer" / "api-token" if os.environ.get("APPDATA") else None,
        Path.home() / ".config" / "mr-ai-marketer" / "api-token",
        Path.home() / "Library" / "Application Support" / "mr-ai-marketer" / "api-token",
    ):
        try:
            if candidate and candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def api_headers() -> dict:
    token = _api_token()
    return {"X-MRAIM-Token": token} if token else {}
ACTIVEPIECES_URL = os.environ.get("HEALTHMON_ACTIVEPIECES_URL", "http://127.0.0.1:8081")
LEADGEN_REACHER_URL = os.environ.get("HEALTHMON_REACHER_URL", "http://127.0.0.1:8082")
LEADGEN_SEARXNG_URL = os.environ.get("HEALTHMON_SEARXNG_URL", "http://127.0.0.1:8083")

WSL_DISTRO = os.environ.get("HEALTHMON_WSL_DISTRO", "Ubuntu")

# Needed only for the gated BrandForge Space and for the weekly end-to-end run.
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
# The end-to-end run calls real generation endpoints; without a token they would all fail
# on auth rather than tell us anything, so e2e refuses to run pointlessly.
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()

# The Spaces to watch. Same variable names the backend reads, so one .env configures both.
# Empty means "not deployed yet" and the check reports as skipped rather than inventing an
# account to probe.
WATCHED_SPACES = {
    "Brand Studio": os.environ.get("BRANDFORGE_SPACE", "").strip(),
    "Blog Writer": os.environ.get("BLOG_WRITER_SPACE", "").strip(),
    "Email Writer": os.environ.get("EMAIL_WRITER_SPACE", "").strip(),
}
MAIL_TRACKER_URL = os.environ.get("MAIL_TRACKER_URL", "").strip().rstrip("/")

# The user's own poster Space. Not in WATCHED_SPACES: those are checked through the Spaces
# API by repo id, and this one is per-install and better judged by whether it answers.
CLOUD_POSTER_URL = os.environ.get("CLOUD_POSTER_URL", "").strip().rstrip("/")

DEFAULT_TIMEOUT = int(os.environ.get("HEALTHMON_TIMEOUT", "25"))
# Generation is slow by nature — a free CPU Space cold-starting can take minutes.
E2E_TIMEOUT = int(os.environ.get("HEALTHMON_E2E_TIMEOUT", "600"))
