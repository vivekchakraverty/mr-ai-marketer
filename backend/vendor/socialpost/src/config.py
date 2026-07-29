"""Runtime configuration — read and edit the .env settings from code or the UI.

The settings *schema* is derived from `.env.example`, not hand-maintained here: its
section headers become groups, the comment block above each `KEY=` becomes help
text, and the `KEY=default` line supplies the name and default. So documenting a
new variable in `.env.example` makes it appear in the UI automatically, and the two
can never drift.

Values are read from the live process environment (which python-dotenv has already
populated from `.env`) and written back to `.env` with dotenv's `set_key`, which
preserves the rest of the file. After a write, cached clients are cleared so the
change takes effect without a restart.

Secrets are never rendered back to a caller. `masked_value` returns a placeholder
when a secret is set, so a settings screen can show "configured" without exposing
the credential in the page/DOM.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass, field

from dotenv import set_key, unset_key

from .db import ENV_FILE, REPO_ROOT

log = logging.getLogger(__name__)

# Same file db.py loads at import, so reads and writes agree. Overridable via
# SPG_ENV_FILE for host apps that store per-user settings outside the install dir.
ENV_PATH = ENV_FILE
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# Values a setting is restricted to. Anything not listed is free text.
CHOICES: dict[str, list[str]] = {
    "DB_BACKEND": ["supabase", "sqlite"],
    "LLM_PROVIDER": ["gemini", "hf"],
    "ALLOW_UI_CONFIG": ["true", "false"],
}

# Underscore-delimited tokens that mark a variable as a secret (masked, password
# field, never echoed). Token-matched, not substring-matched, so SQLITE_PATH
# ("PATH") is not mistaken for a secret because it contains "PAT".
_SECRET_TOKENS = {"KEY", "TOKEN", "PASSWORD", "SECRET", "PAT"}

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

    Rules, matching the file's own conventions:
      * `# --- Title --- …`  starts a new group (Title is the group name).
      * contiguous `# comment` lines accumulate as the next setting's help.
      * `KEY=default`         emits a setting; the accumulated comments are its help.
      * a blank line          resets the comment buffer (so only adjacent comments
                              attach to a setting).
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
            # A section header looks like "--- Storage backend -------".
            if body.startswith("---"):
                group = body.strip("- ").strip()
                buffer = []
            else:
                buffer.append(body)
            continue
        if "=" in line and not line.lstrip().startswith("#"):
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
    """Settings grouped by section, preserving file order."""
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
    """Whether a setting has a non-empty value right now."""
    return bool((os.environ.get(name) or "").strip())


def masked_value(name: str) -> str:
    """Display value: real for non-secrets, a placeholder for a set secret.

    Never returns a secret's actual value — a settings screen must be able to show
    "this is configured" without putting the credential into the page.
    """
    setting = get_setting(name)
    if setting and setting.is_secret:
        return SECRET_PLACEHOLDER if is_set(name) else ""
    return current(name)


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def editing_allowed() -> bool:
    """Whether UI/CLI edits are permitted.

    On by default (the target is a local desktop/standalone run). Set
    ALLOW_UI_CONFIG=false on a shared or public deployment, where writing secrets
    to a host file from a web request would be a real exposure.
    """
    return (os.environ.get("ALLOW_UI_CONFIG") or "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


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
    """Write changes to .env, update the live process, and clear caches.

    Only known settings are written — an arbitrary key cannot be injected. A change
    whose value equals SECRET_PLACEHOLDER is ignored: it means the UI showed the
    masked placeholder and the user did not retype the secret, so leave it as-is.

    Returns the list of names actually changed.
    """
    if not editing_allowed():
        raise ConfigError(
            "Editing configuration is disabled here (ALLOW_UI_CONFIG=false). Edit "
            ".env on the host directly."
        )

    changed: list[str] = []
    for name, value in changes.items():
        validate(name, value)
        value = (value or "").strip()

        # The user left a masked secret untouched — not a real change.
        if value == SECRET_PLACEHOLDER:
            continue
        if value == (os.environ.get(name) or ""):
            continue

        if value:
            # quote_mode="auto" keeps simple values unquoted (KEY=value) and only
            # quotes when the value needs it, matching the .env.example style.
            set_key(str(ENV_PATH), name, value, quote_mode="auto")
            os.environ[name] = value
        else:
            # Empty means "unset": remove the line and drop it from the process.
            if ENV_PATH.exists():
                unset_key(str(ENV_PATH), name)
            os.environ.pop(name, None)
        changed.append(name)

    if changed:
        reload_runtime()
        log.info("Applied config changes: %s", ", ".join(changed))
    return changed


def reload_runtime() -> None:
    """Drop cached clients so new credentials/backends take effect immediately."""
    from . import db, llm

    db.get_client.cache_clear()
    # backend()/sqlite_path() read env fresh, so only the client cache is stale.
    try:
        llm._model.cache_clear()
    except AttributeError:
        pass
    load_schema.cache_clear()


# ---------------------------------------------------------------------------
# Verification — "does what I entered actually work?"
#
# Each returns (ok, detail). Shapes match the master app's settings router so the
# same checks are reusable when this becomes a vendored module.
# ---------------------------------------------------------------------------


def verify_supabase() -> tuple[bool, str]:
    if (os.environ.get("DB_BACKEND") or "supabase") == "sqlite":
        return True, "Using local SQLite; no Supabase needed."
    try:
        from . import db

        db.get_client.cache_clear()
        n = db.get_client().table("job_runs").select("id", count="exact").limit(0).execute().count
        return True, f"Connected. job_runs has {n or 0} rows."
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_bluesky() -> tuple[bool, str]:
    if not (os.environ.get("BLUESKY_HANDLE") and os.environ.get("BLUESKY_APP_PASSWORD")):
        return False, "Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD first."
    try:
        from . import bluesky

        bluesky.get_client.cache_clear()
        bluesky.get_client()  # performs the login
        return True, f"Logged in as {os.environ['BLUESKY_HANDLE']}."
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_llm() -> tuple[bool, str]:
    """One cheap generation with the configured provider/model."""
    try:
        from . import llm

        llm._model.cache_clear()
        out = llm._call("Reply with exactly: OK", temperature=0.0, max_output_tokens=200)
        return True, f"{llm.provider()}/{llm.model_name()} responded: {out[:20]!r}"
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


def verify_hf_token() -> tuple[bool, str]:
    """Verify the HF token the app will actually use.

    Resolved via llm.hf_token() — HF_TOKEN if set, else the `hf auth login` cache —
    so this tests reality, not just the env var. That distinction matters: a stale
    HF_TOKEN silently shadows a good cached login, and this is where you'd see it.
    """
    try:
        from . import llm

        token = llm.hf_token()
    except Exception:  # noqa: BLE001 — LLMError when nothing is configured
        return False, "No HF token: set HF_TOKEN or run `hf auth login`."
    try:
        from huggingface_hub import HfApi

        return True, f"Valid for {HfApi().whoami(token=token)['name']}."
    except Exception as err:  # noqa: BLE001
        return False, _one_line(err)


VERIFIERS = {
    "supabase": verify_supabase,
    "bluesky": verify_bluesky,
    "llm": verify_llm,
    "hf": verify_hf_token,
}


def _one_line(err: Exception) -> str:
    return str(err).splitlines()[0][:200] if str(err) else type(err).__name__


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    from .db import configure_logging

    parser = argparse.ArgumentParser(description="View, edit, and verify configuration.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="Show all settings (secrets masked).")
    g.add_argument("--get", metavar="NAME", help="Print one value (secrets masked).")
    g.add_argument("--set", metavar="NAME=VALUE", action="append", help="Set a value (repeatable).")
    g.add_argument("--verify", choices=[*VERIFIERS, "all"], help="Test a credential.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    configure_logging(args.verbose)

    if args.list:
        for group, settings in schema_by_group().items():
            print(f"\n[{group}]")
            for s in settings:
                mark = " (secret)" if s.is_secret else ""
                shown = masked_value(s.name) or "(empty)"
                print(f"  {s.name}{mark} = {shown}")
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
