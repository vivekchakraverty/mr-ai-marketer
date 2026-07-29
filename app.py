"""DocuMaker — Gradio app: video -> frames + transcript -> LLM guide -> DOCX.

Run with:  python app.py
Then open the printed local URL in your browser.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

import gradio as gr

from src import config
from src import docx_export
from src import guide as guide_lib
from src import llm
from src import transcribe as transcribe_lib
from src import video
from src.frames import FrameRecord, extract_auto_frames, save_manual_frame
from src.transcribe import Transcript, TranscriptSegment

# --- Static assets -----------------------------------------------------------
PLAYER_TEMPLATE = (Path(__file__).parent / "src" / "web" / "player.html").read_text(
    encoding="utf-8"
)

# JS run in the browser when the user clicks "Capture current frame". It draws
# the *currently displayed* video frame onto a canvas and returns the PNG data
# URL + the playback time. The 4 returned values replace the 4 wired inputs
# (session and frames pass through unchanged; the last two carry the capture).
CAPTURE_JS = """
(session, frames, _url, _time) => {
  const v = document.getElementById('dm-video');
  if (!v || !v.videoWidth) { return [session, frames, '', 0]; }
  const c = document.createElement('canvas');
  c.width = v.videoWidth;
  c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
  let url = '';
  try { url = c.toDataURL('image/png'); } catch (e) { url = ''; }
  return [session, frames, url, v.currentTime];
}
"""

# Full-screen lightbox, run on app load via demo.load(js=...). It injects its CSS
# and wires a delegated click on the gallery (elem_id "dm-gallery") to open the
# clicked frame over the whole viewport; click anywhere or press Escape to close.
# Done at the Blocks level (not launch(head=...)) because HF Spaces launches the
# app itself and drops launch() arguments.
LIGHTBOX_JS = """
() => {
  if (window.__dmLightbox) return;
  window.__dmLightbox = true;
  var st = document.createElement('style');
  st.textContent = `#dm-lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:99999;align-items:center;justify-content:center;cursor:zoom-out;}
#dm-lightbox.dm-open{display:flex;}
#dm-lightbox img{max-width:95vw;max-height:95vh;object-fit:contain;border-radius:6px;box-shadow:0 0 50px rgba(0,0,0,.7);}
#dm-lightbox-close{position:fixed;top:12px;right:24px;font-size:42px;color:#fff;cursor:pointer;line-height:1;user-select:none;z-index:100000;}`;
  document.head.appendChild(st);
  function hide(){ var lb=document.getElementById('dm-lightbox'); if(lb) lb.classList.remove('dm-open'); }
  function ensure(){
    var lb=document.getElementById('dm-lightbox');
    if(lb) return lb;
    lb=document.createElement('div');
    lb.id='dm-lightbox';
    lb.innerHTML='<span id="dm-lightbox-close">&times;</span><img id="dm-lightbox-img" alt="preview">';
    lb.addEventListener('click', hide);
    document.body.appendChild(lb);
    return lb;
  }
  function show(src){ var lb=ensure(); document.getElementById('dm-lightbox-img').src=src; lb.classList.add('dm-open'); }
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') hide(); });
  document.addEventListener('click', function(e){
    if(!e.target||!e.target.closest) return;
    var gal=document.getElementById('dm-gallery');
    if(!gal||!gal.contains(e.target)) return;
    var img=e.target.closest('img');
    if(!img){ var b=e.target.closest('button'); if(b) img=b.querySelector('img'); }
    if(img && img.getAttribute('src')) show(img.src);
  }, true);
}
"""


# --- Small helpers -----------------------------------------------------------
def _fmt_ts(seconds: float | int | None) -> str:
    s = int(seconds or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _player_html(file_path_posix: str) -> str:
    # The template builds the Gradio file URL (with a legacy-prefix fallback).
    return PLAYER_TEMPLATE.replace("__VIDEO_PATH__", file_path_posix)


def _gallery_value(frames: list[dict]) -> list[tuple[str, str]]:
    return [
        (f["path"], f"{f.get('source', '')} @ {_fmt_ts(f.get('timestamp', 0))}")
        for f in frames
    ]


def _parse_timestamped_text(text: str) -> Transcript:
    """Re-parse the (possibly user-edited) '[mm:ss] text' transcript box."""
    segments: list[TranscriptSegment] = []
    for line in text.splitlines():
        m = re.match(r"\s*\[(\d{1,2}):(\d{2})\]\s*(.*)", line)
        if m:
            mm, ss, body = m.groups()
            start = float(int(mm) * 60 + int(ss))
            segments.append(TranscriptSegment(start=start, end=start, text=body))
        elif line.strip():
            if segments:
                segments[-1].text += " " + line.strip()
            else:
                segments.append(TranscriptSegment(0.0, 0.0, line.strip()))
    return Transcript(segments=segments)


def _draft_to_md(draft: llm.GuideDraft) -> str:
    lines = [f"## {draft.title}", "", draft.intro or "", ""]
    if draft.prerequisites:
        lines.append("**Prerequisites**")
        lines += [f"- {p}" for p in draft.prerequisites]
        lines.append("")
    for i, step in enumerate(draft.steps, start=1):
        ts = ""
        if step.approx_timestamp is not None:
            ts = f"  _(~{_fmt_ts(step.approx_timestamp)})_"
        lines.append(f"**Step {i}: {step.heading}**{ts}")
        lines.append(step.text)
        lines.append("")
    return "\n".join(lines)


# --- Event handlers ----------------------------------------------------------
def init_session():
    sid = uuid.uuid4().hex[:12]
    config.session_dir(sid)
    return sid, [], "", None, None, "Session ready — upload a video to begin."


def on_upload(file_path: str | None, session: str):
    if not file_path:
        return gr.update(), "", "No file received."
    sdir = config.session_dir(session)
    dest = sdir / f"source{Path(file_path).suffix or '.mp4'}"
    shutil.copyfile(file_path, dest)
    duration = video.get_duration(dest)
    html = _player_html(dest.as_posix())
    return (
        html,
        str(dest),
        f"Loaded video ({duration:.1f}s). Seek + capture frames, or auto-extract.",
    )


def on_capture(session: str, frames: list[dict], data_url: str, current_time: float):
    rec = save_manual_frame(data_url, current_time or 0.0, config.session_dir(session))
    if rec is None:
        return _gallery_value(frames), frames, "Capture failed — let the video load, then retry."
    frames = frames + [asdict(rec)]
    return (
        _gallery_value(frames),
        frames,
        f"Captured frame at {_fmt_ts(rec.timestamp)} ({len(frames)} total).",
    )


def on_auto(
    session: str,
    frames: list[dict],
    video_path: str,
    transcript_obj,
    draft_obj,
    hf_token: str,
    progress=gr.Progress(),
):
    # Outputs: gallery, frames_state, transcript_box, transcript_state, guide_md, draft_state, status
    if not video_path:
        return (_gallery_value(frames), frames, gr.update(), transcript_obj,
                gr.update(), draft_obj, "Upload a video first.")

    token = config.apply_token(hf_token)
    notes: list[str] = []

    # 1) Transcript — needed to anchor/gate frames to the narration.
    auto_tr = False
    if not (transcript_obj and getattr(transcript_obj, "segments", None)):
        progress(0.0, "Transcribing first…")
        transcript_obj = _run_transcription(session, video_path, progress)
        auto_tr = True

    # 2) LLM step outline — so frames anchor to the actual guide steps (the same
    #    LLM timestamps the per-step selection weights heavily).
    auto_draft = False
    if not (draft_obj and getattr(draft_obj, "steps", None)):
        if token:
            progress(0.5, "Generating step outline (LLM)…")
            new_draft, msg = _generate_draft(transcript_obj, token, progress)
            if new_draft:
                draft_obj, auto_draft = new_draft, True
            else:
                notes.append(msg)
        else:
            notes.append("add your HF token for step-aligned frames")

    # 3) Extract — at step timestamps when available, else narration-gated scenes.
    spoken = (
        [(s.start, s.end) for s in transcript_obj.segments]
        if transcript_obj and transcript_obj.segments else None
    )
    steps_ts = (
        [s.approx_timestamp for s in draft_obj.steps if s.approx_timestamp is not None]
        if draft_obj and getattr(draft_obj, "steps", None) else None
    )
    progress(0.9, "Extracting frames…")
    recs = extract_auto_frames(
        video_path, config.session_dir(session),
        spoken_intervals=spoken, step_timestamps=steps_ts,
    )
    merged = frames + [asdict(r) for r in recs]
    progress(1.0, "Done.")

    kind = "step-aligned" if steps_ts else ("narration-gated" if spoken else "scene")
    box_out = transcript_obj.to_timestamped_text() if auto_tr else gr.update()
    md_out = _draft_to_md(draft_obj) if auto_draft else gr.update()
    note = ("  ·  " + "; ".join(notes)) if notes else ""
    return (
        _gallery_value(merged),
        merged,
        box_out,
        transcript_obj,
        md_out,
        draft_obj,
        f"Auto-extracted {len(recs)} {kind} frames ({len(merged)} total).{note}",
    )


def on_select_frame(evt: gr.SelectData):
    """Remember which gallery image the user clicked, for deletion."""
    return evt.index, f"Selected frame #{evt.index + 1}. Click '🗑️ Delete selected' to remove it."


def on_delete_frame(frames: list[dict], selected):
    frames = frames or []
    if selected is None or selected < 0 or selected >= len(frames):
        return _gallery_value(frames), frames, None, "Click an image in the pool first, then delete."
    removed = frames[selected]
    try:  # remove the file too (it isn't referenced once out of the pool)
        Path(removed["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    frames = [f for i, f in enumerate(frames) if i != selected]
    return _gallery_value(frames), frames, None, f"Deleted 1 frame — {len(frames)} remaining."


def on_clear():
    return [], [], None, "Cleared all frames."


def _run_transcription(session: str, video_path: str, progress):
    """Extract audio and transcribe — shared by Transcribe and Auto-extract."""
    sdir = config.session_dir(session)
    progress(0.05, "Extracting audio…")
    wav = video.extract_audio(video_path, sdir / "audio.wav")
    progress(0.1, "Loading Whisper…")
    return transcribe_lib.transcribe(wav, progress=progress)


def on_transcribe(session: str, video_path: str, progress=gr.Progress()):
    if not video_path:
        return "", None, "Upload a video first."
    tr = _run_transcription(session, video_path, progress)
    return (
        tr.to_timestamped_text(),
        tr,
        f"Transcribed {len(tr.segments)} segments "
        f"(lang={tr.language or '?'}, device={tr.device or 'cpu'}).",
    )


def on_token_set(hf_token: str):
    """Mirror the UI token into the HF_TOKEN environment variable."""
    token = config.apply_token(hf_token)
    if token:
        return "🔑 HuggingFace token set for this session."
    return "Enter your HuggingFace token to generate the guide."


def _generate_draft(tr, token: str, progress):
    """Build the LLM step draft. Returns (draft|None, message). Shared by the
    Generate button and Auto-extract."""
    try:
        draft = llm.build_guide_draft(tr, token=token, progress=progress)
    except RuntimeError as exc:
        return None, f"⚠️ {exc}"
    if not draft.steps:
        return None, "The LLM returned no steps — try a different DOCUMAKER_LLM_MODEL."
    return draft, f"Drafted {len(draft.steps)} steps."


def on_generate(transcript_text: str, transcript_obj, hf_token: str, progress=gr.Progress()):
    # Show any failure reason in the guide panel itself (not just the bottom
    # status line, which is easy to miss far down the page).
    token = config.apply_token(hf_token)
    if not token:
        m = "⚠️ Enter your HuggingFace token in the 🔑 box at the **top** of the page, then click Generate again."
        return m, None, m
    tr = _parse_timestamped_text(transcript_text) if transcript_text.strip() else transcript_obj
    if tr is None or not tr.segments:
        m = "⚠️ Transcribe the audio first (step 2), or paste a transcript, then generate."
        return m, None, m
    draft, msg = _generate_draft(tr, token, progress)
    if draft is None:
        return f"⚠️ {msg}", None, msg
    return _draft_to_md(draft), draft, msg + " Review, then build the DOCX."


def on_build(
    session: str,
    draft,
    frames: list[dict],
    video_path: str,
    do_caption: bool,
    hf_token: str,
    transcript_obj,
    progress=gr.Progress(),
):
    if draft is None or not getattr(draft, "steps", None):
        return None, "Generate the step-by-step guide first."
    token = config.apply_token(hf_token)
    recs = [FrameRecord(**d) for d in frames]
    spoken_range = None
    if transcript_obj and transcript_obj.segments:
        spoken_range = (
            min(s.start for s in transcript_obj.segments),
            max(s.end for s in transcript_obj.segments),
        )
    progress(0.1, "Matching images to steps…")
    g = guide_lib.assemble_guide(
        draft,
        recs,
        video_path=video_path or None,
        session_dir=config.session_dir(session),
        do_caption=do_caption,
        token=token,
        spoken_range=spoken_range,
        progress=progress,
    )
    out = config.session_dir(session) / "guide.docx"
    docx_export.export_docx(g, out)
    n_imgs = sum(1 for s in g.steps if s.image_path)
    progress(1.0, "Done.")
    return str(out), f"Built {out.name}: {len(g.steps)} steps, {n_imgs} images."


# --- UI ----------------------------------------------------------------------
def build_ui() -> gr.Blocks:
    # Register the work dir as servable here (module/Blocks level) so the custom
    # video player works even on HF Spaces, which ignores launch(allowed_paths=).
    gr.set_static_paths([str(config.WORK_DIR)])
    with gr.Blocks(title="DocuMaker") as demo:
        gr.Markdown(
            "# 🎬➜📄 DocuMaker\n"
            "Turn a tutorial video into a step-by-step **DOCX** guide with screenshots. "
            "Transcription runs locally (Whisper); the guide text uses a free HuggingFace "
            "model via your token; image captions use local BLIP."
        )

        with gr.Accordion("🔑 HuggingFace token (required to generate the guide)", open=True):
            hf_token = gr.Textbox(
                label="HuggingFace token",
                placeholder="hf_…  (paste your token — used only for this session, never stored)",
                type="password",
                autofocus=True,
            )
            gr.Markdown(
                "Create a token at "
                "[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) "
                "(a free **Read** token works). It's kept in memory for this session only."
            )

        session_state = gr.State("")
        frames_state = gr.State([])
        selected_state = gr.State(None)  # index of the gallery image the user clicked
        video_state = gr.State("")
        transcript_state = gr.State(None)
        draft_state = gr.State(None)
        # Hidden carriers that the capture JS fills in.
        cap_url = gr.Textbox(visible=False)
        cap_time = gr.Number(visible=False)

        # 1 · Upload & preview
        gr.Markdown("### 1 · Upload & preview")
        upload = gr.File(
            label="Upload a video",
            type="filepath",
            file_types=[".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"],
        )
        player = gr.HTML()
        capture_btn_top = gr.Button(
            "📸 Capture current frame", variant="primary",
        )

        # 2 · Transcribe  →  3 · Generate guide
        gr.Markdown("### 2 · Transcribe → 3 · Generate guide")
        with gr.Row():
            with gr.Column():
                transcribe_btn = gr.Button("🎙️ Transcribe audio (Whisper)")
                transcript_box = gr.Textbox(
                    label="Transcript (editable — '[mm:ss] text' per line)", lines=12
                )
            with gr.Column():
                generate_btn = gr.Button("📝 Generate step-by-step guide (LLM)")
                guide_md = gr.Markdown()

        # 4 · Capture & extract frames (after transcription + the step outline)
        gr.Markdown(
            "### 4 · Capture & extract frames\n"
            "_**Auto-extract** uses the transcript + step outline (steps 2–3) to pull frames "
            "at the guide's step moments and skip recorder intro/idle screens — it will run "
            "those first if needed. **Capture current frame** grabs the exact moment showing "
            "in the player above._"
        )
        with gr.Row():
            with gr.Column(scale=2):
                auto_btn = gr.Button("✨ Auto-extract frames", variant="primary")
                capture_btn = gr.Button("📸 Capture current frame")
            with gr.Column(scale=3):
                gallery = gr.Gallery(
                    label="Frames pool — click an image to enlarge / select it",
                    elem_id="dm-gallery",
                    columns=3, height=320, object_fit="contain", allow_preview=False,
                )
                with gr.Row():
                    delete_btn = gr.Button("🗑️ Delete selected")
                    clear_btn = gr.Button("Clear all")

        # 5 · Build the document
        gr.Markdown("### 5 · Build the document")
        with gr.Row():
            caption_chk = gr.Checkbox(value=config.ENABLE_VISION, label="Caption images with vision model")
            build_btn = gr.Button("📄 Build DOCX", variant="primary")
            download = gr.File(label="Download guide.docx")

        status = gr.Markdown("")

        # --- wiring ---
        demo.load(
            init_session,
            outputs=[session_state, frames_state, transcript_box, transcript_state, draft_state, status],
        )
        demo.load(None, js=LIGHTBOX_JS)  # set up the full-screen lightbox on the frontend
        # Mirror the token into HF_TOKEN as soon as it's entered (so even model
        # downloads during transcription authenticate with it).
        hf_token.blur(on_token_set, [hf_token], [status])
        hf_token.submit(on_token_set, [hf_token], [status])
        upload.change(on_upload, [upload, session_state], [player, video_state, status])

        # Both capture buttons (under the player, and in the frames section) behave
        # identically — grab the player's current frame into the pool.
        for _btn in (capture_btn_top, capture_btn):
            _btn.click(
                on_capture,
                inputs=[session_state, frames_state, cap_url, cap_time],
                outputs=[gallery, frames_state, status],
                js=CAPTURE_JS,
            )
        auto_btn.click(
            on_auto,
            [session_state, frames_state, video_state, transcript_state, draft_state, hf_token],
            [gallery, frames_state, transcript_box, transcript_state, guide_md, draft_state, status],
        )
        gallery.select(on_select_frame, None, [selected_state, status])
        delete_btn.click(
            on_delete_frame,
            [frames_state, selected_state],
            [gallery, frames_state, selected_state, status],
        )
        clear_btn.click(on_clear, None, [gallery, frames_state, selected_state, status])

        transcribe_btn.click(
            on_transcribe, [session_state, video_state], [transcript_box, transcript_state, status]
        )
        generate_btn.click(
            on_generate,
            [transcript_box, transcript_state, hf_token],
            [guide_md, draft_state, status],
        )
        build_btn.click(
            on_build,
            [session_state, draft_state, frames_state, video_state, caption_chk, hf_token, transcript_state],
            [download, status],
        )

    return demo


if __name__ == "__main__":
    # HuggingFace Spaces sets SPACE_ID and serves the app publicly, so treat it as
    # multi-user and bind to all interfaces (Spaces expects 0.0.0.0:7860).
    on_spaces = bool(os.getenv("SPACE_ID"))
    share = os.getenv("DOCUMAKER_SHARE", "0").lower() in ("1", "true", "yes")

    if on_spaces:
        server_name = "0.0.0.0"
        multiuser = True
    else:
        server_name = os.getenv("DOCUMAKER_SERVER_NAME", "127.0.0.1")
        multiuser = share or server_name not in ("127.0.0.1", "localhost", "::1")

    # In shared/multi-user mode keep each user's token in their own session: do
    # NOT mirror it into the process-global environment.
    config.set_allow_env_token(not multiuser)
    if multiuser:
        print(
            "DocuMaker: shared/multi-user mode — HF tokens are kept per session "
            "(HF_TOKEN env is not set)."
        )

    app = build_ui().queue()
    app.launch(
        theme=gr.themes.Soft(),
        allowed_paths=[str(config.WORK_DIR)],
        share=share and not on_spaces,  # Spaces provides its own URL — no tunnel
        server_name=server_name,
        show_error=True,
        inbrowser=not multiuser,
    )
