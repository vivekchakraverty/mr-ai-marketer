"""Headless end-to-end backend test (no Gradio UI).

Runs: sample video -> audio -> Whisper -> scene frames -> LLM step draft ->
assemble -> DOCX, asserting each stage produced output. The LLM step falls back
to a naive sentence-per-step draft if the HuggingFace API is unreachable, so the
DOCX assembly is always validated.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:  # Windows consoles default to cp1252 and choke on emoji/non-ASCII output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import make_sample  # noqa: E402
from src import config, docx_export, video  # noqa: E402
from src import frames as frames_lib  # noqa: E402
from src import guide as guide_lib  # noqa: E402
from src import llm, transcribe  # noqa: E402
from src.llm import GuideDraft, StepDraft  # noqa: E402


def naive_draft(tr) -> GuideDraft:
    steps = [
        StepDraft(heading=f"Step {i}", text=seg.text.strip(), approx_timestamp=seg.start)
        for i, seg in enumerate(tr.segments, start=1)
        if seg.text.strip()
    ]
    return GuideDraft(title="Sample Guide", intro="Generated offline.", steps=steps)


def main() -> None:
    sample = config.WORK_DIR / "sample" / "sample.mp4"
    if not sample.exists():
        sample = make_sample.main()

    sdir = config.session_dir("smoke")

    # The app takes the token from the UI; this headless test reads it from the
    # environment (validating which of HF_TOKEN / HUGGINGFACEHUB_API_TOKEN works).
    token = config.resolve_hf_token()
    print(f"HF token: {'present' if token else 'MISSING (LLM step will use naive fallback)'}")

    print("\n[1/5] Extract audio + transcribe…")
    wav = video.extract_audio(sample, sdir / "audio.wav")
    tr = transcribe.transcribe(wav)
    print(f"  device={tr.device} segments={len(tr.segments)} text={tr.text[:120]!r}")
    assert tr.text.strip(), "Transcript is empty"

    print("[2/5] Build guide draft (LLM)…")
    try:
        draft = llm.build_guide_draft(tr, token=token)
        if not draft.steps:
            raise RuntimeError("LLM returned no steps")
        print(f"  LLM ok: '{draft.title}' ({len(draft.steps)} steps)")
    except Exception as exc:
        print(f"  LLM unavailable ({exc}); using naive fallback draft.")
        draft = naive_draft(tr)
    assert draft.steps, "No steps in draft"

    print("[3/5] Auto-extract frames (step-aligned)…")
    spoken = [(s.start, s.end) for s in tr.segments] if tr.segments else None
    step_ts = [s.approx_timestamp for s in draft.steps if s.approx_timestamp is not None]
    recs = frames_lib.extract_auto_frames(
        sample, sdir, spoken_intervals=spoken, step_timestamps=step_ts or None
    )
    print(f"  frames={len(recs)} (from {len(step_ts)} step timestamps)")
    assert recs, "No frames were extracted"

    print("[4/5] Assemble (align + caption)…")
    spoken_range = (
        (min(s.start for s in tr.segments), max(s.end for s in tr.segments))
        if tr.segments else None
    )
    g = guide_lib.assemble_guide(
        draft, recs, video_path=str(sample), session_dir=sdir, do_caption=True,
        token=token, spoken_range=spoken_range,
    )

    print("[5/5] Export DOCX…")
    out = sdir / "guide.docx"
    docx_export.export_docx(g, out)
    assert out.exists() and out.stat().st_size > 0, "DOCX not written"

    for s in g.steps:
        ts = f"{int((s.timestamp or 0))//60:02d}:{int((s.timestamp or 0))%60:02d}"
        print(f"   - [{ts}] {s.heading!r}: img={'yes' if s.image_path else 'no'} cap={s.caption!r}")
    n_imgs = sum(1 for s in g.steps if s.image_path)
    n_caps = sum(1 for s in g.steps if s.caption)
    print(
        f"\nOK ✅  {out}  ({out.stat().st_size} bytes, "
        f"{len(g.steps)} steps, {n_imgs} images, {n_caps} captions)"
    )


if __name__ == "__main__":
    main()
