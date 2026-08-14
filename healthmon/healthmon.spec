# PyInstaller spec for the Space Health Monitor tray utility.
#
#     pyinstaller healthmon/healthmon.spec --noconfirm
#
# Onefile, unlike backend.spec's onedir. The backend is launched by Electron from a known
# install directory, where a folder of DLLs is invisible; this is a thing a person puts on a
# desktop and pins to startup, so it has to survive being moved as a single file.
#
# THE EXCLUDES ARE THE LOAD-BEARING PART. This is built from backend/.venv, which contains
# torch, transformers, chromadb and the whole model stack for the app itself. PyInstaller
# follows imports transitively and will happily bundle several gigabytes of CUDA kernels into
# a tray icon that makes HTTP requests. Nothing in healthmon/ touches any of it — the monitor
# deliberately imports none of the app's code — so they are cut explicitly rather than left
# to chance.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # the repo root; SPECPATH is healthmon/

hiddenimports = [
    # Resolved at runtime by pystray/Pillow rather than by an import statement the analyser
    # can see: pystray picks its backend from the platform, and Pillow's tkinter bridge is
    # loaded by name when the status window opens.
    "pystray._win32",
    "PIL._tkinter_finder",
    # Imported inside functions so the monitor still runs without them (see check_space and
    # check_yt_search, which degrade to "skipped"). Bundled anyway — a build that silently
    # skips two checks looks healthy while testing less than it claims to.
    *collect_submodules("huggingface_hub"),
    "yt_dlp",
    # healthmon's own modules are imported inside functions in tray.py, to keep the tray
    # starting fast when only the Space poll is due.
    "healthmon.checks",
    "healthmon.cli",
    "healthmon.config",
    "healthmon.report",
    "healthmon.runner",
]

a = Analysis(
    [str(Path(SPECPATH) / "tray_run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # The app's model stack. None of it is reachable from healthmon.
        "torch", "torchvision", "torchaudio", "transformers", "sentence_transformers",
        "chromadb", "onnxruntime", "tokenizers", "safetensors", "accelerate", "peft",
        # Scientific and plotting stacks pulled in behind those.
        "numpy", "scipy", "pandas", "sklearn", "matplotlib", "sympy", "networkx",
        # The app's own web/UI layers — the monitor talks to the backend over HTTP, it does
        # not import it.
        "fastapi", "uvicorn", "starlette", "gradio", "gradio_client", "modal",
        "docx", "openpyxl", "pypdf", "selenium", "playwright",
        "IPython", "pytest", "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SpaceHealthMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries trip Windows Defender far more often than they save space.
    runtime_tmpdir=None,
    # No console: this is a tray application, and the current shortcut already runs it under
    # pythonw for the same reason. A console window at login would be the most visible thing
    # about a utility whose whole point is to sit quietly in the notification area.
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "build" / "icon.ico"),
)
