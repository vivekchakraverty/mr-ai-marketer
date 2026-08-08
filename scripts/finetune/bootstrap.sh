#!/usr/bin/env bash
# One command to take a Vast.ai box to "training is running".
#
# Safe to re-run. On a fresh box after a total loss it re-pulls the data from the
# Hub, recovers the newest checkpoint, and carries on — you do not have to
# remember what state the dead instance was in.

set -euo pipefail
WORKSPACE="${WORKSPACE:-/workspace}"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# Vast ML images keep the real environment in a venv, not in the system python.
PY="${PY:-/venv/main/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "=== 1/4 credentials ==="
[ -f "$WORKSPACE/.env" ] && { set -a; . "$WORKSPACE/.env"; set +a; }
if [ -z "${HF_REPO:-}" ]; then
  echo "HF_REPO is not set. Create $WORKSPACE/.env yourself, e.g.:"
  echo "    HF_REPO=your-username/bluesky-post-qwen7b"
  echo "    DATA_REPO=your-username/bluesky-post-data   # optional"
  echo "    HF_TOKEN=hf_...                             # only if not already logged in"
  exit 1
fi
"$PY" - <<'PY'
import sys
from huggingface_hub import HfApi, get_token
tok = get_token()
if not tok:
    sys.exit("  no HF login found — run `huggingface-cli login` or set HF_TOKEN in .env")
print(f"  hugging face: authenticated as {HfApi().whoami()['name']}")
PY
echo "  target repo: $HF_REPO"

echo "=== 2/4 dependencies ==="
# DO NOT blindly pip install. On a Vast "Unsloth Studio" image everything is
# already present and NEWER than any pin we would write; installing our own
# versions would downgrade a working CUDA/torch/unsloth stack and break it.
# So: detect, report, and only fill genuine gaps.
MISSING="$("$PY" - <<'PY'
import importlib.metadata as md
need = ["torch","unsloth","trl","peft","transformers","datasets",
        "huggingface_hub","bitsandbytes","accelerate"]
missing = []
for pkg in need:
    try:
        md.version(pkg)
    except Exception:
        missing.append(pkg)
print(" ".join(missing))
PY
)"
if [ -n "$MISSING" ]; then
  echo "  installing only what is missing: $MISSING"
  # shellcheck disable=SC2086
  "$PY" -m pip install -q $MISSING
else
  echo "  all present — installing nothing"
fi
"$PY" - <<'PY'
import importlib.metadata as md
for pkg in ("torch","unsloth","trl","transformers","peft"):
    try:
        print(f"    {pkg:16} {md.version(pkg)}")
    except Exception:
        print(f"    {pkg:16} MISSING")
PY
command -v tmux >/dev/null 2>&1 || apt-get install -y -qq tmux 2>/dev/null || true

echo "=== 3/4 GPU check ==="
"$PY" - <<'PY'
import torch
if not torch.cuda.is_available():
    raise SystemExit("  no CUDA GPU visible — wrong instance or bad image.")
gb = torch.cuda.get_device_properties(0).total_memory / 1e9
print(f"  {torch.cuda.get_device_name(0)} — {gb:.0f}GB")
if gb < 22:
    print("  WARNING: under 22GB. A 7B LoRA may OOM; lower BATCH in .env.")
PY

echo "=== 4/4 auto-restart on reboot ==="
# Vast containers do not run systemd, so cron is the hook. Without it a power
# cycle leaves the box idle and still billing.
if command -v crontab >/dev/null 2>&1; then
  ( crontab -l 2>/dev/null | grep -v 'finetune-onstart' || true
    echo "@reboot bash $WORKSPACE/onstart.sh # finetune-onstart" ) | crontab -
  echo "  cron @reboot installed"
else
  echo "  no crontab — set onstart.sh as the instance's on-start script in the Vast UI"
fi

echo
echo "Ready.  start:  bash $WORKSPACE/run.sh"
echo "        watch:  bash $WORKSPACE/run.sh logs"
