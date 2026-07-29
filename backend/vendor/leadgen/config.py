"""Runtime configuration — read/edit/verify the Lead Gen Agent's settings.

The settings *schema* is derived from `.env.example` (section headers -> groups,
the comment block above each `KEY=` -> help text, `KEY=default` -> name + default),
exactly like `vendor/socialpost/src/config.py`, so documenting a variable there makes
it appear in the app's Settings screen with no UI change.

Values are read from the live process environment (python-dotenv populates it from the
per-user env file) and written back with dotenv's `set_key`. Secrets are never rendered
back to a caller — `masked_value` returns a placeholder when a secret is set.

The self-hosted service URLs and data directory are supplied by Electron via the
process environment (LEADGEN_REACHER_URL / LEADGEN_SEARXNG_URL / LEADGEN_DATA_DIR) and
are deliberately NOT part of the editable schema.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv, set_key, unset_key

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent

# The per-user env file. app/config.py points LEADGEN_ENV_FILE at a file under DATA_DIR;
# standalone/dev runs fall back to a `.env` next to this package.
ENV_PATH = Path(os.environ.get("LEADGEN_ENV_FILE") or (PACKAGE_ROOT / ".env"))
ENV_EXAMPLE_PATH = PACKAGE_ROOT / ".env.example"

# Load once at import so os.environ reflects the saved settings before anything reads them.
load_dotenv(ENV_PATH)

# Values a setting is restricted to. Anything not listed is free text.
CHOICES: dict[str, list[str]] = {
    "LLM_BACKEND": ["hf", "ollama"],
}

# Underscore-delimited tokens that mark a variable as a secret (masked, never echoed).
# Token-matched, not substring-matched, so e.g. a hypothetical *_PATH is never mistaken
# for a secret because it contains "PAT".
_SECRET_TOKENS = {"TOKEN", "PASSWORD", "SECRET", "KEY", "PAT"}

SECRET_PLACEHOLDER = "•••••••• (set)"


@dataclass
class Setting:
    name: str
    group: str
    help: str
    default: str = ""
    choices: list[str] = field(default_factory=list)

    @property
    def is_secret(self) -> bool:
        return bool(_SECRET_TOKENS & set(self.name.upper().split("_")))


# ---------------------------------------------------------------------------
# Schema, parsed from .env.example
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_schema() -> list[Setting]:
    """Parse .env.example into an ordered list of settings.

    Rules (matching the file's own conventions):
      * `# --- Title --- …`  starts a new group named Title.
      * contiguous `# comment` lines accumulate as the next setting's help.
      * `KEY=default`         emits a setting; the buffered comments are its help.
      * a blank line          resets the comment buffer.
    """
    if not ENV_EXAMPLE_PATH.exists():
        log.warning(".env.example not found; settings schema is empty")
        return []

    settings: list[Setting] = []
    group = "General"
    buffer: list[str] = []

    for raw in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip():
            buffer = []
            continue
        if line.lstrip().startswith("#"):
            body = line.lstrip()[1:].strip()
            if body.startswith("---"):
                group = body.strip("- ").strip()
                buffer = []
            else:
                buffer.append(body)
            continue
        if "=" in line:
            name, _, default = line.partition("=")
            name = name.strip()
            if name:
                settings.append(
                    Setting(
                        name=name,
                        group=group,
                        help="\n".join(buffer).strip(),
                        default=default.strip(),
                        choices=CHOICES.get(name, []),
                    )
                )
            buffer = []
    return settings


def schema_by_group() -> dict[str, list[Setting]]:
    out: dict[str, list[Setting]] = {}
    for s in load_schema():
        out.setdefault(s.group, []).append(s)
    return out


def get_setting(name: str) -> Setting | None:
    return next((s for s in load_schema() if s.name == name), None)


def known_names() -> set[str]:
    return {s.name for s in load_schema()}


# ---------------------------------------------------------------------------
# Reading values
# ---------------------------------------------------------------------------


def current(name: str) -> str:
    """The live value of a setting (env wins; falls back to the documented default)."""
    value = os.environ.get(name)
    if value is not None:
        return value
    setting = get_setting(name)
    return setting.default if setting else ""


def is_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def masked_value(name: str) -> str:
    """Display value: real for non-secrets, a placeholder for a set secret."""
    setting = get_setting(name)
    if setting and setting.is_secret:
        return SECRET_PLACEHOLDER if is_set(name) else ""
    return current(name)


# ---------------------------------------------------------------------------
# Resolved paths / service URLs (Electron-supplied, not user-editable)
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Where SQLite, the GP model blobs, and the embedding cache live.

    LEADGEN_DATA_DIR (set by Electron) wins; otherwise DATA_DIR/leadgen (shared with the
    rest of the app); otherwise a repo-local ./data/leadgen for standalone dev.
    """
    explicit = os.environ.get("LEADGEN_DATA_DIR")
    if explicit:
        base = Path(explicit)
    elif os.environ.get("DATA_DIR"):
        base = Path(os.environ["DATA_DIR"]) / "leadgen"
    else:
        base = PACKAGE_ROOT / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def reacher_url() -> str:
    return (os.environ.get("LEADGEN_REACHER_URL") or "http://localhost:8082").rstrip("/")


def searxng_url() -> str:
    return (os.environ.get("LEADGEN_SEARXNG_URL") or "http://localhost:8083").rstrip("/")


def overpass_url() -> str:
    return current("OVERPASS_URL") or "https://overpass-api.de/api/interpreter"


def discovery_backends() -> list[str]:
    raw = current("DISCOVERY_BACKENDS") or "overpass,searxng"
    return [b.strip() for b in raw.split(",") if b.strip()]


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Invalid settings edit. Message is safe to show a user."""


def validate(name: str, value: str) -> None:
    if name not in known_names():
        raise ConfigError(f"Unknown setting {name!r}.")
    setting = get_setting(name)
    if setting and setting.choices and value and value not in setting.choices:
        raise ConfigError(
            f"{name} must be one of: {', '.join(setting.choices)} (got {value!r})."
        )


def apply(changes: dict[str, str]) -> list[str]:
    """Write changes to the env file, update the live process, clear caches.

    A value equal to SECRET_PLACEHOLDER means the UI showed a masked secret and the user
    did not retype it — skip it so a save never blanks a credential the user couldn't see.
    Returns the names actually changed.
    """
    changed: list[str] = []
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.touch(exist_ok=True)

    for name, value in changes.items():
        validate(name, value)
        value = (value or "").strip()
        if value == SECRET_PLACEHOLDER:
            continue
        if value == (os.environ.get(name) or ""):
            continue
        if value:
            set_key(str(ENV_PATH), name, value, quote_mode="auto")
            os.environ[name] = value
        else:
            unset_key(str(ENV_PATH), name)
            os.environ.pop(name, None)
        changed.append(name)

    if changed:
        reload_runtime()
        log.info("Applied leadgen config changes: %s", ", ".join(changed))
    return changed


def reload_runtime() -> None:
    """Drop cached clients so new credentials/backends take effect immediately."""
    load_schema.cache_clear()
    try:
        from . import llm

        llm.reset_client()
    except Exception:  # noqa: BLE001 — llm may not import cleanly if misconfigured; non-fatal
        pass


# ---------------------------------------------------------------------------
# Verification — "does what I entered actually work?"
# Each returns (ok, detail). Shapes match the app's settings router.
# ---------------------------------------------------------------------------


def verify_hf() -> tuple[bool, str]:
    if current("LLM_BACKEND") != "hf":
        return True, "Using the local Ollama backend; no HF token needed."
    token = (os.environ.get("HF_TOKEN") or "").strip()
    if not token:
        return False, "Set HF_TOKEN — it's required and billed for reasoning calls."
    try:
        from huggingface_hub import HfApi

        return True, f"Valid for {HfApi().whoami(token=token)['name']}."
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_llm() -> tuple[bool, str]:
    try:
        from . import llm

        out = llm.complete("Reply with exactly: OK", temperature=0.0, max_output_tokens=16)
        return True, f"{llm.backend()}/{llm.model_name()} responded: {out.strip()[:20]!r}"
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_smtp() -> tuple[bool, str]:
    try:
        from .email import sender

        return sender.verify()
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_imap() -> tuple[bool, str]:
    try:
        from .email import inbox

        return inbox.verify()
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_reacher() -> tuple[bool, str]:
    try:
        from .email import verify as verifier

        return verifier.healthcheck()
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_searxng() -> tuple[bool, str]:
    try:
        from .discovery import searxng

        return searxng.healthcheck()
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_bluesky() -> tuple[bool, str]:
    try:
        from .discovery import bluesky

        return bluesky.healthcheck()
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


VERIFIERS = {
    "hf": verify_hf,
    "llm": verify_llm,
    "smtp": verify_smtp,
    "imap": verify_imap,
    "reacher": verify_reacher,
    "searxng": verify_searxng,
    "bluesky": verify_bluesky,
}


def _one_line(err: Exception) -> str:
    return str(err).splitlines()[0][:200] if str(err) else type(err).__name__


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="View, edit, and verify Lead Gen Agent config.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="Show all settings (secrets masked).")
    g.add_argument("--get", metavar="NAME", help="Print one value (secrets masked).")
    g.add_argument("--set", metavar="NAME=VALUE", action="append", help="Set a value (repeatable).")
    g.add_argument("--verify", choices=[*VERIFIERS, "all"], help="Test a credential/service.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list:
        for group, settings in schema_by_group().items():
            print(f"\n[{group}]")
            for s in settings:
                mark = " (secret)" if s.is_secret else ""
                print(f"  {s.name}{mark} = {masked_value(s.name) or '(empty)'}")
        return

    if args.get:
        print(masked_value(args.get))
        return

    if args.set:
        changes = {}
        for item in args.set:
            if "=" not in item:
                raise SystemExit(f"--set expects NAME=VALUE, got {item!r}")
            k, _, v = item.partition("=")
            changes[k.strip()] = v
        try:
            changed = apply(changes)
        except ConfigError as err:
            raise SystemExit(f"Error: {err}") from None
        print(f"Changed: {', '.join(changed) or '(nothing — values unchanged)'}")
        return

    if args.verify:
        names = list(VERIFIERS) if args.verify == "all" else [args.verify]
        failed = False
        for name in names:
            ok, detail = VERIFIERS[name]()
            print(f"  {'OK  ' if ok else 'FAIL'} {name:10} {detail}")
            failed |= not ok
        raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
