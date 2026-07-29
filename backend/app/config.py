import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# vendor/dmstrategy/modules/*.py import each other as a top-level `modules` package
# (e.g. `from modules import llm`), matching their source repo's own layout — add
# vendor/dmstrategy to sys.path so `import modules` resolves there, unmodified.
_DMSTRATEGY_VENDOR_DIR = BACKEND_ROOT / "vendor" / "dmstrategy"
if str(_DMSTRATEGY_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_DMSTRATEGY_VENDOR_DIR))

# Electron sets DATA_DIR to app.getPath('userData') when it spawns the backend;
# fall back to a local ./data folder for standalone/dev runs outside Electron.
DATA_DIR = Path(os.environ.get("DATA_DIR", BACKEND_ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Vendored packages (backend/vendor/*) resolve their own writable cache dir from this same
# env var — set it here (before any router imports a vendored package) so standalone/dev
# runs outside Electron still get one consistent data directory instead of each vendored
# package falling back to its own default.
os.environ.setdefault("DATA_DIR", str(DATA_DIR))

# vendor/docmaker/src/config.py defaults its working directory to a `work/` folder next to
# the vendored source itself — redirect it into our own outputs tree instead.
os.environ.setdefault("DOCUMAKER_WORK_DIR", str(DATA_DIR / "outputs" / "docu"))

DB_PATH = DATA_DIR / "mr-ai-marketer.sqlite3"
OUTPUTS_DIR = DATA_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# vendor/socialpost keeps its settings in an env file and its corpus in its own
# database. Both belong in DATA_DIR (per-OS-user, and not inside the install dir,
# which is read-only once packaged) rather than next to the vendored source.
#
# This must run BEFORE any router imports the vendored package: its db module reads
# SPG_ENV_FILE at import time. main.py imports app.config first, which is why.
SOCIALPOST_ENV_FILE = DATA_DIR / "social-post.env"
os.environ["SPG_ENV_FILE"] = str(SOCIALPOST_ENV_FILE)

# First-run defaults, written to the file rather than the environment: python-dotenv
# does not override variables that already exist, so seeding os.environ here would
# permanently shadow whatever the user later saves in Settings.
if not SOCIALPOST_ENV_FILE.exists():
    SOCIALPOST_ENV_FILE.write_text(
        "# Social Post Generator settings for this user.\n"
        "# Managed by the app's Settings screen; safe to edit by hand when closed.\n"
        "DB_BACKEND=sqlite\n"
        f"SQLITE_PATH={DATA_DIR / 'social-post.sqlite3'}\n"
        # The app is already Hugging Face-centric — every generator bills to the
        # user's HF token — so the HF provider is the sane default here.
        "LLM_PROVIDER=hf\n",
        encoding="utf-8",
    )

# vendor/leadgen (the Lead Gen Agent) reads its settings from a per-user env file, same as
# vendor/socialpost. Point LEADGEN_ENV_FILE at DATA_DIR so the user's credentials live
# per-OS-user and outside the (read-only, once packaged) install dir. Must run BEFORE any
# router imports the vendored package: leadgen.config reads LEADGEN_ENV_FILE at import time.
LEADGEN_ENV_FILE = DATA_DIR / "leadgen.env"
os.environ.setdefault("LEADGEN_ENV_FILE", str(LEADGEN_ENV_FILE))

# First-run: create the file so python-dotenv has something to load and Settings can write to
# it. Values default from the vendored .env.example schema, so the file starts essentially
# empty (just a header). HF_TOKEN, if the user has already configured it for another HF tool,
# is inherited from the shared process environment automatically.
if not LEADGEN_ENV_FILE.exists():
    LEADGEN_ENV_FILE.write_text(
        "# Lead Gen Agent settings for this user.\n"
        "# Managed by the app's Settings screen; safe to edit by hand when the app is closed.\n",
        encoding="utf-8",
    )

# Matches ACTIVEPIECES_PORT in electron/src/main/activepieces.ts — the Distribution engine's
# container port is fixed, not env-configured, same as BACKEND_PORT in electron/src/main/backend.ts.
ACTIVEPIECES_URL = "http://localhost:8081"
