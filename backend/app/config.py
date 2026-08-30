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
#
# 127.0.0.1 rather than localhost, deliberately. On Windows, `localhost` resolves to ::1 first
# and WSL2's port relay sometimes re-binds IPv4-only after the container restarts — at which
# point `requests` raises on the first address instead of falling back, and every call to the
# engine fails with a bare connection error. Reproduced on a healthy engine that curl reached
# on 127.0.0.1 and not on localhost. The engine is only ever addressed through that local
# forward, so pinning the family costs nothing.
ACTIVEPIECES_URL = "http://127.0.0.1:8081"


# ---------------------------------------------------------------------------
# Hosted model endpoints
# ---------------------------------------------------------------------------
# Three tools (Blog Writer, Email Writer, Brand Studio) run their fine-tuned models on a
# Hugging Face Space, one tool reads a RAG index from an HF Dataset, and mail tracking uses
# a small Space as its pixel/redirect host. Each of those is *your own* deployment.
#
# Every one of these is overridable from Settings, which hands the value over when the
# backend starts. Set one there and it wins over anything written here.
#
# Most have no default on purpose. A hardcoded id means anyone who clones this repo sends
# their generation traffic to someone else's account — burning that person's quota, and
# breaking the moment they rename or delete the Space. Deploy your own (see the README) and
# point these at it.
#
# Empty is a valid state: the tool that needs one says so clearly instead of failing deep
# inside an HTTP call.
BLOG_WRITER_SPACE = os.environ.get("BLOG_WRITER_SPACE", "").strip()

# The exception, at the repo owner's request: the Email Writer points at their own public
# Space so the tool works out of the box rather than refusing until it is configured. The
# trade is the one described above and it falls on that account — if you are running a fork,
# set EMAIL_WRITER_SPACE (or the Settings field) to a Space of your own, both so your
# generations are yours and so this one is not answering for you.
EMAIL_WRITER_SPACE = os.environ.get(
    "EMAIL_WRITER_SPACE", "vivekchakraverty/marketing-email-writer"
).strip()
BRANDFORGE_SPACE = os.environ.get("BRANDFORGE_SPACE", "").strip()
MARKETING_PLAN_RAG_DATASET = os.environ.get("MARKETING_PLAN_RAG_DATASET", "").strip()

# The Space that generates the marketing plan end to end — keyword research, the SEO,
# social and ads plans, the composed strategy, and its own grounding. Set it and none of
# the local pipeline below is used: no reference index to download, nothing to warm up.
#
# Unset is still a working configuration, and deliberately the default. The plan is then
# built here exactly as it always was, so a checkout with no Space configured is not a
# broken one. Point this at a Space you host or trust: the request carries your own
# Hugging Face token so the inference is billed to you, but the token does leave this
# machine to get there, which is the thing worth checking before you point it anywhere.
#
# Accepts either form gradio_client understands — "owner/space-name" or a full
# https://...hf.space URL.
MARKETING_PLAN_SPACE = os.environ.get("MARKETING_PLAN_SPACE", "").strip()
# --- Local API authentication -------------------------------------------------
#
# Set by Electron when it spawns this process, and required on every request. The reason is
# not paranoia about the network: this API binds to 127.0.0.1, which sounds private but is
# reachable from any web page the user happens to have open. A page on some other site can
# fetch http://127.0.0.1:8756/library and — with the permissive CORS policy this app needs
# for a file:// renderer — read the response. That was demonstrated, not theorised.
#
# A per-session token fixes it because a web page cannot learn one. Origin checks cannot do
# the job on their own: the packaged renderer runs from file:// and sends `Origin: null`,
# which any sandboxed iframe can also claim.
#
# Empty disables the check, which is what makes `uvicorn app.main:app` still work for
# development. main.py says so loudly at startup rather than failing open in silence.
API_TOKEN = os.environ.get("MRAIM_API_TOKEN", "").strip()

# Local tools that are not the renderer — the health monitor, scripts — read the token from
# here rather than being handed one. A file is the right channel: the threat is a web page,
# and a web page cannot read the filesystem.
API_TOKEN_FILE = DATA_DIR / "api-token"

MAIL_TRACKER_URL = os.environ.get("MAIL_TRACKER_URL", "").strip().rstrip("/")
# Shared secret the tracking Space checks on its /events feed. Must match that Space's own
# value. No default: a secret with a default is a secret everyone who installs the app has.
MAIL_TRACKER_SYNC_SECRET = os.environ.get("MAIL_TRACKER_SYNC_SECRET", "").strip()

# Optional: a retrieval Space to search the marketing corpus instead of downloading it
# (see resources/rag-retrieval-space). Empty means local retrieval, which is the default
# and keeps every query on the user's own machine. Set this only if you host the Space —
# a shared endpoint routes every user's plan through whoever deployed it.
RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "").strip().rstrip("/")
# Shared key the retrieval Space checks. Must match its RAG_APP_KEY. Without it the Space —
# if it has a key configured — refuses, because a "protected" Space hides its repo but leaves
# the running app queryable by anyone with the URL.
RAG_SERVICE_KEY = os.environ.get("RAG_SERVICE_KEY", "").strip()

# Datasets and models are not shipped in the build — they live in Hugging Face repos and are
# fetched on first use (see services/hf_assets.py, which reads these names from the
# environment at call time). Nothing is defaulted here: a repo id is not a credential, but
# whose repo it is remains the operator's decision, and a build with none set falls back to
# whatever the checkout still has on disk.
#
#   HF_ASSETS_CTR_MODEL_REPO    model   ctr_model.joblib, ctr_reference_stats.json
#   HF_ASSETS_INFLUENCER_REPO   dataset influencer_database.xlsx
#   HF_ASSETS_GUEST_POST_REPO   dataset guest_post_database.xlsx, opr_scores.json
#
# The CTR model's *training data* is deliberately absent from that list. Users need the
# fitted model; the campaign data behind it is only needed to retrain, so it stays in a
# private repo that scripts/hf/publish_assets.py knows about and the app never reads.


class NotConfiguredError(RuntimeError):
    """Raised when a tool needs a hosted endpoint the user has not set up yet."""


def require_space(value: str, env_var: str, tool: str) -> str:
    if not value:
        raise NotConfiguredError(
            f"{tool} needs its own Hugging Face Space. Set {env_var} in your environment "
            f"(or backend/.env) to the Space you deployed — see the README for how to "
            f"deploy one in a couple of clicks."
        )
    return value
