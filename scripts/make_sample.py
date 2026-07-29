"""Synthesize a tiny tutorial-style test clip with no external assets.

Produces ``work/sample/sample.mp4``: four solid-color scenes (so scene detection
has clear cuts) plus spoken narration generated with the built-in Windows SAPI
voice (so Whisper has real speech to transcribe).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:  # Windows consoles default to cp1252 and choke on non-ASCII output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402


def _powershell() -> str:
    """Locate powershell.exe (it lives in a v1.0\\ subdir not always on PATH)."""
    found = shutil.which("powershell") or shutil.which("pwsh")
    if found:
        return found
    candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("Could not find powershell.exe to synthesize narration audio.")

NARRATION = (
    "Welcome to this quick tutorial. "
    "First, open the application from your desktop. "
    "Next, click the File menu in the top left corner. "
    "Then choose the Export option from the list. "
    "Finally, pick a folder and save your document."
)
# Textured, visually distinct patterns so scene detection finds clear cuts and
# perceptual-hash dedup keeps them (solid colors collapse to one pHash).
PATTERNS = [
    "smptebars=size=1280x720",
    "testsrc2=size=1280x720",
    "rgbtestsrc=size=1280x720",
    "mandelbrot=size=1280x720",
]


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stderr}")


def make_narration(out_wav: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{out_wav.as_posix()}'); "
        f"$s.Speak('{NARRATION}'); $s.Dispose();"
    )
    _run([_powershell(), "-NoProfile", "-Command", ps])


def make_slides(dirpath: Path) -> list[Path]:
    paths = []
    for i, pattern in enumerate(PATTERNS):
        p = dirpath / f"slide_{i}.png"
        _run(
            [
                config.FFMPEG_BIN, "-y",
                "-f", "lavfi", "-i", pattern,
                "-frames:v", "1", str(p),
            ]
        )
        paths.append(p)
    return paths


def main() -> Path:
    out_dir = config.WORK_DIR / "sample"
    out_dir.mkdir(parents=True, exist_ok=True)

    narration = out_dir / "narration.wav"
    make_narration(narration)
    slides = make_slides(out_dir)

    listfile = out_dir / "slides.txt"
    lines: list[str] = []
    for p in slides:
        lines.append(f"file '{p.as_posix()}'")
        lines.append("duration 3")
    lines.append(f"file '{slides[-1].as_posix()}'")  # concat needs the last file twice
    listfile.write_text("\n".join(lines), encoding="utf-8")

    out_mp4 = out_dir / "sample.mp4"
    _run(
        [
            config.FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(listfile),
            "-i", str(narration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out_mp4),
        ]
    )
    print("Sample video:", out_mp4)
    return out_mp4


if __name__ == "__main__":
    main()
