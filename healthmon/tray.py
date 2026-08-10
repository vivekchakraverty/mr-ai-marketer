"""System-tray watcher for the hosted Spaces the app depends on.

    python -m healthmon.tray

Why a tray app rather than the scheduled cloud routine this replaces: that routine ran in
an environment whose egress proxy denies huggingface.co and *.hf.space, so it could not
reach a single Space. Locally there is no proxy, and there is a Hugging Face token — which
buys three things the cloud version could never have: exact runtime stages for the
protected Spaces instead of blind reachability probes, the ability to restart a paused or
crashed Space, and no credential leaving the machine.

Why not the main app: it can already do this, but it loads torch, transformers and chromadb
before it serves anything and takes over a minute to start. Something that runs at login
purely to keep Spaces warm has no business dragging that in.

The one thing this cannot do is run while the machine is off. If the PC sleeps overnight the
Spaces drift back to sleep, which is the honest trade for everything above.

Configuration lives at DATA_DIR/healthmon.json (see config_path()), written as a template on
first run. Nothing is hardcoded here: this repo is public, and a shipped Space id would point
every clone at one account — the same reason backend/app/config.py refuses to default them.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger("healthmon.tray")

APP_NAME = "MrAIMarketerSpaceWatch"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

STATUS_TIMEOUT = 20
# A wake is a nudge. The GET starts the container and then hangs for as long as the cold
# start takes, so it is abandoned early rather than blocking the poll thread for minutes.
WAKE_TIMEOUT = 8
# How long to leave a woken Space before believing a second failure. Cold starts on
# cpu-basic routinely run past a minute; anything shorter reports healthy Spaces as broken,
# which is how a monitor teaches you to ignore it.
RECHECK_DELAY = 90

_HEALTHY = {"RUNNING", "RUNNING_BUILDING"}
_STARTING = {"RUNNING_APP_STARTING", "APP_STARTING", "BUILDING"}
_ERROR = {"PAUSED", "RUNTIME_ERROR", "BUILD_ERROR", "CONFIG_ERROR", "NO_APP_FILE", "DELETING"}


def data_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "mr-ai-marketer"


def config_path() -> Path:
    return data_dir() / "healthmon.json"


TEMPLATE = {
    "_comment": "Spaces this watcher keeps an eye on. Labels are yours; ids are owner/name.",
    "intervalHours": 6,
    "wakeSleeping": True,
    "notifyOnError": True,
    "spaces": {},
    "mailTrackerUrl": "",
}


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")
        log.info("wrote a config template to %s", path)
        return dict(TEMPLATE)
    try:
        return {**TEMPLATE, **json.loads(path.read_text(encoding="utf-8"))}
    except Exception as err:  # noqa: BLE001 — a broken config should not stop the tray
        log.warning("could not read %s (%s); using defaults", path, str(err)[:80])
        return dict(TEMPLATE)


def hf_token() -> str:
    """The token, from the environment or the `hf auth login` cache.

    Never written to the config file — a token in a plaintext JSON next to the app is a
    worse place for it than the two it already lives in.
    """
    token = (os.environ.get("HF_TOKEN") or "").strip()
    if token:
        return token
    cached = Path.home() / ".cache" / "huggingface" / "token"
    try:
        return cached.read_text(encoding="utf-8").strip() if cached.exists() else ""
    except Exception:  # noqa: BLE001
        return ""


@dataclass
class Result:
    label: str
    status: str  # ok | starting | asleep | error | unreachable
    detail: str
    woke: bool = False


@dataclass
class State:
    results: list[Result] = field(default_factory=list)
    checked_at: str = ""
    checking: bool = False

    @property
    def worst(self) -> str:
        order = {"error": 3, "unreachable": 3, "asleep": 2, "starting": 1, "ok": 0}
        return max((r.status for r in self.results), key=lambda s: order.get(s, 0), default="ok")


def _subdomain(space_id: str) -> str:
    return space_id.replace("/", "-").replace(".", "-").replace("_", "-").lower()


def _probe(space_id: str, path: str = "/") -> tuple[str, str]:
    """Reachability of a Space's running app. The fallback for protected Spaces, whose
    repo metadata is private even though the app itself answers."""
    try:
        resp = requests.get(f"https://{_subdomain(space_id)}.hf.space{path}", timeout=STATUS_TIMEOUT)
        if resp.status_code < 400 or resp.status_code == 404:
            # 404 counts as up: a plain API Space has no route at /.
            return "ok", "Answering."
        return "error", f"Answered with {resp.status_code}."
    except requests.Timeout:
        return "asleep", "Not answering yet — asleep or starting."
    except Exception as err:  # noqa: BLE001
        return "unreachable", f"Could not reach it ({type(err).__name__})."


def check_space(label: str, space_id: str, token: str) -> Result:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(f"https://huggingface.co/api/spaces/{space_id}",
                            headers=headers, timeout=STATUS_TIMEOUT)
    except Exception as err:  # noqa: BLE001
        return Result(label, "unreachable", f"Could not reach Hugging Face ({type(err).__name__}).")

    if resp.status_code == 200:
        stage = ((resp.json() or {}).get("runtime") or {}).get("stage") or ""
        if stage in _HEALTHY:
            return Result(label, "ok", "Running.")
        if stage in _STARTING:
            return Result(label, "starting", "Starting up.")
        if stage == "SLEEPING":
            return Result(label, "asleep", "Asleep.")
        if stage in _ERROR:
            return Result(label, "error", f"{stage.replace('_', ' ').title()}.")
        # 200 but no stage: a protected Space hides its metadata. Ask the app instead.
        status, detail = _probe(space_id)
        return Result(label, status, detail)

    # 401/404 here means protected or private rather than broken, so fall through to the
    # app itself, which a protected Space still serves publicly.
    status, detail = _probe(space_id)
    return Result(label, status, detail)


def wake(space_id: str) -> None:
    """Start a sleeping Space. The timeout is the expected outcome, not a failure."""
    try:
        requests.get(f"https://{_subdomain(space_id)}.hf.space/", timeout=WAKE_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass


def restart(space_id: str, token: str) -> bool:
    """Restart a paused or crashed Space. Needs write access, which the cloud routine
    never had and this does."""
    if not token:
        return False
    try:
        from huggingface_hub import HfApi

        HfApi().restart_space(repo_id=space_id, token=token)
        return True
    except Exception as err:  # noqa: BLE001
        log.info("restart of %s failed: %s", space_id, str(err)[:100])
        return False


def run_checks(cfg: dict) -> list[Result]:
    token = hf_token()
    spaces: dict[str, str] = {k: v for k, v in (cfg.get("spaces") or {}).items() if v}
    results = [check_space(label, sid, token) for label, sid in spaces.items()]

    tracker = (cfg.get("mailTrackerUrl") or "").strip().rstrip("/")
    if tracker:
        try:
            code = requests.get(f"{tracker}/docs", timeout=STATUS_TIMEOUT).status_code
            results.append(Result("Email tracking", "ok" if code < 400 else "error",
                                  "Answering." if code < 400 else f"Answered with {code}."))
        except Exception:  # noqa: BLE001
            results.append(Result("Email tracking", "asleep", "Not answering yet."))

    if not cfg.get("wakeSleeping", True):
        return results

    woken = [r for r in results if r.status == "asleep"]
    for r in woken:
        sid = spaces.get(r.label)
        if sid:
            wake(sid)
            r.woke = True
            r.detail = "Waking it up."
    # Only pay the re-check wait if something was actually woken.
    if woken:
        time.sleep(RECHECK_DELAY)
        for r in woken:
            sid = spaces.get(r.label)
            if not sid:
                continue
            again = check_space(r.label, sid, token)
            r.status, r.detail = again.status, again.detail
            if r.status == "asleep":
                # Still not up after the wait. Starting, most likely — say so rather than
                # calling a slow cold start a failure.
                r.status, r.detail = "starting", "Still starting after the wake."
    return results


# --------------------------------------------------------------------------- tray

# Colour carries the whole status at a glance, so the meanings have to be worth trusting.
# Asleep is grey, not red: a free Space sleeping after 48h idle is working as designed, and
# a tray icon that shows an alarm for normal behaviour is one you stop looking at.
_COLOURS = {
    "ok": (63, 125, 88),
    "starting": (201, 138, 43),
    "asleep": (130, 130, 130),
    "error": (176, 58, 46),
    "unreachable": (176, 58, 46),
}


def _icon_image(status: str):
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=_COLOURS.get(status, _COLOURS["ok"]))
    if status in ("error", "unreachable"):
        d.rectangle((28, 16, 36, 38), fill=(255, 255, 255))
        d.rectangle((28, 44, 36, 52), fill=(255, 255, 255))
    return img


def _notify(icon, title: str, message: str) -> None:
    try:
        icon.notify(message, title)
    except Exception:  # noqa: BLE001 — notifications are a nicety, never a failure path
        pass


def _status_window(state: State) -> None:
    """A plain list of what is up. tkinter because it is in the standard library — a tray
    watcher should not pull a UI toolkit in behind it."""
    import tkinter as tk

    root = tk.Tk()
    root.title("Space health")
    root.configure(bg="#faf6ef")
    root.geometry("460x300")
    tk.Label(root, text=f"Checked {state.checked_at or 'not yet'}", bg="#faf6ef",
             fg="#7a7268", font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(12, 6))
    for r in state.results:
        row = tk.Frame(root, bg="#faf6ef")
        row.pack(fill="x", padx=14, pady=3)
        colour = "#%02x%02x%02x" % _COLOURS.get(r.status, _COLOURS["ok"])
        tk.Canvas(row, width=10, height=10, bg="#faf6ef", highlightthickness=0).pack(side="left")
        row.winfo_children()[-1].create_oval(1, 1, 9, 9, fill=colour, outline="")
        tk.Label(row, text=r.label, bg="#faf6ef", fg="#2f2a26",
                 font=("Segoe UI", 10, "bold"), width=16, anchor="w").pack(side="left", padx=(8, 0))
        tk.Label(row, text=r.detail, bg="#faf6ef", fg="#5c554e",
                 font=("Segoe UI", 9), anchor="w").pack(side="left")
    if not state.results:
        tk.Label(root, text="Nothing configured yet — see healthmon.json.", bg="#faf6ef",
                 fg="#7a7268", font=("Segoe UI", 9)).pack(anchor="w", padx=14)
    root.mainloop()


# --------------------------------------------------------------------------- startup

def _startup_command() -> str:
    """The exact command the Run key will execute at login.

    Two things this has to get right, both of which fail silently:

    * pythonw.exe, so no console window appears at every login.
    * tray_run.py by absolute path, NOT ``-m healthmon.tray``. A Run-key launch inherits no
      working directory, so the module form dies with "No module named healthmon" — and
      with pythonw there is no console to see it in. The only symptom would be a tray icon
      that never appears. healthmon/cli.py hit exactly this with Task Scheduler.
    """
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    launcher = Path(__file__).resolve().parent / "tray_run.py"
    return f'"{pyw if pyw.exists() else exe}" "{launcher}"'


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    # HKCU rather than HKLM: this is a per-user convenience and must not need admin rights.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------- app

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    import pystray

    cfg = load_config()
    state = State()
    stop = threading.Event()
    wake_now = threading.Event()

    if not (cfg.get("spaces") or {}):
        log.warning("no Spaces configured — edit %s", config_path())

    def refresh(icon) -> None:
        if state.checking:
            return
        state.checking = True
        icon.title = "Space health — checking…"
        try:
            previous = {r.label: r.status for r in state.results}
            state.results = run_checks(cfg)
            state.checked_at = datetime.now().strftime("%H:%M")
            icon.icon = _icon_image(state.worst)
            summary = ", ".join(f"{r.label}: {r.status}" for r in state.results) or "nothing configured"
            icon.title = f"Space health — {summary}"[:127]  # Windows caps tooltips
            if cfg.get("notifyOnError", True):
                # Only on the transition into failure. Re-notifying every cycle for a Space
                # that is still down is how a tray app gets muted.
                newly_bad = [r for r in state.results
                             if r.status in ("error", "unreachable")
                             and previous.get(r.label) not in ("error", "unreachable")]
                if newly_bad:
                    _notify(icon, "Space problem",
                            "; ".join(f"{r.label}: {r.detail}" for r in newly_bad)[:250])
        except Exception as err:  # noqa: BLE001 — the loop must survive any single check
            log.exception("check failed")
            icon.title = f"Space health — check failed: {str(err)[:60]}"
        finally:
            state.checking = False

    def loop(icon) -> None:
        icon.visible = True
        refresh(icon)
        interval = max(1, int(cfg.get("intervalHours", 6))) * 3600
        while not stop.is_set():
            # Waiting on an event rather than sleeping means "Check now" is instant and
            # Quit does not hang for up to six hours.
            if wake_now.wait(timeout=interval):
                wake_now.clear()
            if stop.is_set():
                break
            refresh(icon)

    def on_check(icon, _item) -> None:
        wake_now.set()

    def on_wake_all(icon, _item) -> None:
        token = hf_token()
        for r in state.results:
            sid = (cfg.get("spaces") or {}).get(r.label)
            if not sid:
                continue
            if r.status == "asleep":
                wake(sid)
            elif r.status == "error":
                restart(sid, token)
        wake_now.set()

    def on_window(_icon, _item) -> None:
        threading.Thread(target=_status_window, args=(state,), daemon=True).start()

    def on_startup(icon, item) -> None:
        set_startup(not item.checked)
        icon.update_menu()

    def on_config(_icon, _item) -> None:
        os.startfile(config_path())  # noqa: S606 — opening the user's own config file

    def on_quit(icon, _item) -> None:
        stop.set()
        wake_now.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Check now", on_check, default=True),
        pystray.MenuItem("Wake / restart all", on_wake_all),
        pystray.MenuItem("Show status…", on_window),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start at login", on_startup, checked=lambda _i: startup_enabled()),
        pystray.MenuItem("Edit configuration…", on_config),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("space-health", _icon_image("ok"), "Space health — starting…", menu)
    icon.run(setup=loop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
