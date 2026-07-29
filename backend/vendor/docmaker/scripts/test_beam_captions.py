"""Smoke-test the Beam GPU captioner against real frames.

Usage (from the repo root, with DOCUMAKER_BEAM_CAPTION_URL / DOCUMAKER_BEAM_TOKEN
set in .env or the environment):

    python scripts/test_beam_captions.py [frame.png ...]

With no arguments it picks a few frames out of ``work/``. Prints the caption each
backend produces so you can see the Qwen2.5-VL vs BLIP difference directly.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, vision  # noqa: E402


def find_frames(limit: int = 3) -> list[Path]:
    work = Path(__file__).resolve().parent.parent / "work"
    frames = sorted(work.glob("*/frames/*.png"))
    return frames[:limit]


def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]] or find_frames()
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("No frames found. Pass image paths explicitly.")
        return 1

    print(f"Beam URL   : {config.BEAM_CAPTION_URL or '(unset)'}")
    print(f"Beam token : {'set' if config.BEAM_CAPTION_TOKEN else '(unset)'}")
    print(f"Frames     : {len(paths)}\n")

    if not config.BEAM_CAPTION_URL:
        print("DOCUMAKER_BEAM_CAPTION_URL is unset — caption_batch would fall back "
              "to the HF API / local BLIP. Set it to test the GPU path.")
        return 1

    start = time.time()
    captions = vision.caption_batch([(p, "") for p in paths])
    elapsed = time.time() - start

    for path, caption in zip(paths, captions):
        print(f"--- {path.name}")
        print(f"    {caption or '(empty)'}\n")

    ok = sum(1 for c in captions if c)
    print(f"{ok}/{len(paths)} captioned in {elapsed:.1f}s "
          f"({elapsed / max(len(paths), 1):.1f}s per frame)")
    # _BEAM_DISABLED flips on the first hard failure (bad URL/token/endpoint).
    if vision._BEAM_DISABLED:
        print("\nNOTE: the Beam backend errored and was disabled for this run — "
              "captions above (if any) came from the HF API or local BLIP.")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
