---
title: DocuMaker
emoji: 🎬
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 6.18.0
app_file: app.py
python_version: "3.11"
pinned: false
license: mit
short_description: Turn a tutorial video into a step-by-step DOCX guide
---

# DocuMaker

Turn a tutorial/screencast **video** into a polished **step-by-step `.docx` guide**
with screenshots — using open-source tools and **free** HuggingFace models.

> The block above is HuggingFace Spaces config. On GitHub it renders as a small
> table; on Spaces it tells the platform how to run the app.

Pipeline: `video → preview → frames (manual + automatic) → audio transcription
(Whisper) → LLM cleanup & step-structuring → image/step alignment + captions →
DOCX export`.

- **UI:** a local Gradio app.
- **Transcription:** runs **locally** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  (GPU with automatic CPU fallback).
- **Guide writing:** the **HuggingFace Inference API** (uses your HF token).
- **Image captions:** a vision model. It tries an API vision-chat model first, then
  falls back to a **local BLIP** captioner — which is the path most free HF accounts
  end up using, since few enabled providers serve a vision model on the free tier.
- **Frames:** one-click manual snapshots **and** automatic scene-detection
  (PySceneDetect) de-duplicated with perceptual hashing.

### How a frame is chosen for each step

Relevancy is decided by combining accurate signals (no single model "judges" it):

1. **Timestamp alignment** — the frame on screen while that step was narrated
   (Whisper timestamps ↔ step time). The strongest signal for tutorials.
2. **Sharpness** — variance-of-Laplacian, to avoid blurry scene-transition frames.
3. **BLIP caption match** — the BLIP caption is compared to the step's text; a frame
   whose description overlaps the step gets a nudge. This *suggests*, it doesn't decide.
4. **Manual preference** — frames you snapshot yourself win ties.

See `_pick_frame` in [src/guide.py](src/guide.py).

## Requirements

- Python 3.11
- **ffmpeg** on your `PATH` (`ffmpeg -version` should work)
- A HuggingFace token — get one at <https://huggingface.co/settings/tokens>
  (a free **Read** token works). You **paste it into the app's UI**; it is not read
  from the environment. (The headless smoke test reads it from `HF_TOKEN` /
  `HUGGINGFACEHUB_API_TOKEN` instead.)
- A CUDA GPU is optional (Whisper falls back to CPU automatically)

## Setup

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Git Bash:              source .venv/Scripts/activate
pip install -r requirements.txt

cp .env.example .env   # optional — tweak model ids / Whisper size
```

`requirements.txt` includes the local **BLIP** captioner (`torch` + `transformers`,
CPU build). The BLIP model (~1 GB) downloads on first use and is cached. Captions
are nice-to-have: without a captioner you still get images + step text, and frame
selection uses timestamp + sharpness only.

To run BLIP on a local **NVIDIA GPU**, reinstall a CUDA build of torch — it's used
automatically when available:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

### HuggingFace token

The **app takes your token from the UI** — paste it into the "🔑 HuggingFace token"
box at the top of the page. When you enter it (local single-user mode), the app
sets it as the process `HF_TOKEN` (in memory only, never written to disk) so every
HuggingFace operation — the guide LLM and model downloads — authenticates with it,
overriding any stale token already in your environment.

**Shared / multi-user deployments:** if you launch with `DOCUMAKER_SHARE=1` or a
non-localhost `DOCUMAKER_SERVER_NAME`, the app switches to per-session tokens and
**does not** touch the global `HF_TOKEN` — so one user's token can never leak to
another. The token is still threaded directly to that user's LLM/caption calls (the
guide LLM clients are created per request, never cached across sessions).

The **headless smoke test** (`scripts/smoke_test.py`) instead reads the token from
`HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` and validates which one actually
authenticates (handy if one is stale).

## Run

```bash
python app.py
```

Open the printed local URL, then:

1. **Paste your HuggingFace token** into the 🔑 box at the top.
2. **Upload & preview** a video.
3. **Capture frames** — scrub the seek bar and click *📸 Capture current frame*,
   and/or click *✨ Auto-extract frames* for scene-based snapshots.
4. **Transcribe audio** (Whisper, local). Edit the transcript if you like.
5. **Generate step-by-step guide** (HF LLM) and review the steps.
6. **Build DOCX** — images are matched to steps and captioned, then download `guide.docx`.

## Quick backend check (no UI)

```bash
python scripts/make_sample.py   # synthesizes work/sample/sample.mp4 (4 scenes + narration)
python scripts/smoke_test.py    # runs the full pipeline and asserts a valid DOCX
```

The smoke test falls back to a naive draft if the HF API is unreachable, so DOCX
assembly is always exercised.

## Deploy to HuggingFace Spaces

This repo is Spaces-ready: the YAML header in this README, `packages.txt` (installs
`ffmpeg`), and a single `requirements.txt` are all the platform needs. The app
auto-detects Spaces (via `SPACE_ID`), binds to `0.0.0.0`, and switches to
**multi-user-safe tokens** — each visitor pastes their own HF token in the UI and it
never touches the shared environment.

1. Create a new **Gradio** Space at <https://huggingface.co/new-space>.
2. Push this repo to it:
   ```bash
   git init && git add -A && git commit -m "DocuMaker"
   git remote add space https://huggingface.co/spaces/<your-username>/<space-name>
   git push space main
   ```
   (Or drag the files into the Space's *Files* tab.)
3. The Space builds and starts automatically. Visitors paste their **own** HF token —
   you don't expose yours or share its quota.

Notes for the free CPU tier: transcription and BLIP run on CPU (slower but fine for a
demo); set `DOCUMAKER_WHISPER_MODEL=base` in the Space *Settings → Variables* for
snappier transcription. You can push to **both** GitHub and the Space (add both as
git remotes).

## Serverless GPU captions (Beam)

The default local captioner, BLIP-base, is a 0.25B model trained on COCO photos.
It has no OCR, so on tutorial screenshots it produces generic text like *"a
computer screen with a website on it"* — near-useless for a step-by-step guide.

[`beam_app.py`](beam_app.py) serves **Qwen2.5-VL-7B-Instruct** on a serverless
GPU instead. It reads on-screen text and understands UI affordances, and it
scales to zero when idle.

```bash
uv tool install beam-client && beam configure default --token <YOUR_TOKEN>
beam deploy beam_app.py:caption
```

Put the printed URL and your Beam token in `.env`:

```
DOCUMAKER_BEAM_CAPTION_URL=https://<your-endpoint>.app.beam.cloud
DOCUMAKER_BEAM_TOKEN=<your-beam-token>
```

Captioning then prefers Beam, falling back to the HF Inference API and then
local BLIP — an unreachable or scaled-to-zero endpoint degrades the output
rather than breaking the run. The whole frame pool is sent in **one** batched
request (see `vision.caption_batch`), so a cold start is paid once per document,
not once per frame.

### Choosing a GPU

`DOCUMAKER_BEAM_GPU` selects the card. Qwen2.5-VL-7B in bf16 is ~16.5GB of
weights, so 24GB is the practical floor.

**No 16GB card is usable on Beam today**, which is why the default is 24GB:
`A4000` deploys but reports *"No compute capacity currently supports A4000"* and
never gets hardware, and `T4`/`V100` are rejected outright with *"This GPU type
is not supported. Please use an A10G or RTX 4090 instead."*

| GPU | VRAM | Notes |
|---|---|---|
| `A10G` | 24GB | **Default.** Ampere, native bf16, explicitly supported by Beam |
| `RTX4090` | 24GB | The other explicitly supported option; Ada, faster |
| `L4` | 24GB | Ada, efficient — check capacity before relying on it |
| `A100_40` | 40GB | Overkill for a 7B VLM |
| `A4000` | 16GB | ⚠️ Deploys, but currently has no capacity |
| `T4` / `V100` | 16GB | ⚠️ Rejected by Beam. `V100` would also fail on CUDA support |

To fit a smaller card, set `DOCUMAKER_BEAM_MODEL=Qwen/Qwen2.5-VL-3B-Instruct`
(~7GB in bf16). `DOCUMAKER_BEAM_MAX_PIXELS` bounds VRAM either way — Qwen2.5-VL
scales visual tokens with input resolution, so an uncapped screenshot can
balloon usage.

### Why torch is pinned

`beam_app.py` pins `torch==2.7.1` deliberately. Beam's hosts run a **CUDA 12.9
driver**; an unpinned `torch` resolves to a cu13 wheel that the driver is too old
to run. The failure is silent and misleading — `torch.cuda.is_available()` just
returns `False`, the model load fails on `device_map="cuda:0"`, and the endpoint
returns a bare `HTTP 500`. If you bump torch, verify the wheel's CUDA version
still matches the host driver.

If the AWQ dependency chain ever breaks, the no-quantization fallback is
`DOCUMAKER_BEAM_MODEL=Qwen/Qwen2.5-VL-3B-Instruct` (~7GB in bf16, still far
better than BLIP). `DOCUMAKER_BEAM_MAX_PIXELS` bounds VRAM — Qwen2.5-VL scales
visual tokens with input resolution, so an uncapped screenshot can balloon usage.

## Configuration

All settings are environment variables (see [.env.example](.env.example)). Highlights:

| Variable | Default | Purpose |
|---|---|---|
| `DOCUMAKER_LLM_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | Text LLM (any HF instruct model) |
| `DOCUMAKER_VLM_MODEL` | `Qwen/Qwen2-VL-7B-Instruct` | API vision model tried before local BLIP |
| `DOCUMAKER_LOCAL_CAPTION_MODEL` | `Salesforce/blip-image-captioning-base` | Local captioner (last-resort fallback) |
| `DOCUMAKER_BEAM_CAPTION_URL` | *(unset)* | Beam GPU captioner; preferred when set |
| `DOCUMAKER_BEAM_TOKEN` | *(unset)* | Beam auth token |
| `DOCUMAKER_BEAM_GPU` | `A10G` | GPU for the Beam endpoint (24GB) |
| `DOCUMAKER_BEAM_MODEL` | `Qwen/Qwen2.5-VL-7B-Instruct` | VLM served on Beam |
| `DOCUMAKER_ENABLE_VISION` | `1` | Set `0` to skip captioning |
| `DOCUMAKER_WHISPER_MODEL` | `small` | `tiny`…`large-v3` |
| `DOCUMAKER_WHISPER_DEVICE` | `auto` | `auto` / `cuda` / `cpu` |
| `DOCUMAKER_SCENE_THRESHOLD` | `27.0` | Lower = more auto-frames |
| `DOCUMAKER_SHARE` | `0` | `1` = Gradio public share link (enables multi-user-safe tokens) |
| `DOCUMAKER_SERVER_NAME` | `127.0.0.1` | Bind address; non-localhost enables multi-user-safe tokens |

## Troubleshooting

- **Whisper CUDA errors / cuDNN not found:** faster-whisper (CTranslate2) needs the
  NVIDIA CUDA libraries. Either install them —
  `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` — or force CPU with
  `DOCUMAKER_WHISPER_DEVICE=cpu` (slower but always works).
- **LLM call failed / model not available:** free-tier model availability changes.
  Set `DOCUMAKER_LLM_MODEL` to another available instruct model, or pin a provider
  with `DOCUMAKER_LLM_PROVIDER`.
- **No captions in the DOCX:** the API vision model usually isn't served on free HF
  accounts ("not supported by any provider you have enabled"), so DocuMaker uses
  local BLIP (installed via `requirements.txt`). Frame *selection* and images still
  work without it.
- **Video doesn't preview:** the app serves files from `work/` via Gradio's
  `allowed_paths` (URL prefix `/gradio_api/file=`). Make sure the upload completed;
  very large files take a moment.
- **No images in the DOCX:** capture or auto-extract frames before *Build DOCX*.
  Steps with a timestamp but no nearby frame pull a fresh frame from the video.

## Project layout

```
app.py                 Gradio UI + event wiring
src/config.py          env-driven settings
src/video.py           ffmpeg audio extract / duration / frame@timestamp
src/frames.py          scene detection, dedup, manual-capture decode
src/transcribe.py      faster-whisper (CUDA→CPU fallback)
src/llm.py             HF Inference: transcript → structured step JSON
src/vision.py          VLM captioning (HF API) + local BLIP fallback
src/guide.py           align frames↔steps, caption
src/docx_export.py     python-docx assembly
src/web/player.html    custom HTML5 player for seek + snapshot
scripts/make_sample.py synthesize a test clip
scripts/smoke_test.py  headless end-to-end check
```
