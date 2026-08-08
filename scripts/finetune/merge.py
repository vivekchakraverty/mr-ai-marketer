#!/usr/bin/env python
"""Merge a chosen LoRA checkpoint into the base weights and push it.

Separate from train.py on purpose. train.py merges whatever the model looks like
when training ENDS, which is only the right choice if the last step is also the
best one — and it usually is not. This run's eval_loss bottomed at epoch 0.98
(checkpoint-800) and then sat ~0.03 higher for the rest of epoch 2, so the final
weights are measurably worse than one we already have.

Being able to pick the checkpoint is the whole reason every checkpoint was
pushed to the Hub during training: `save_total_limit` had already deleted 800
locally by the time we knew we wanted it.

    CHECKPOINT=checkpoint-800 python merge.py
"""

from __future__ import annotations

# Must precede trl/transformers/peft — see train.py.
import unsloth  # noqa: F401

import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
OUT_DIR = WORKSPACE / "out"
HF_REPO = os.environ.get("HF_REPO", "").strip()
CHECKPOINT = os.environ.get("CHECKPOINT", "").strip()
MAX_SEQ = int(os.environ.get("MAX_SEQ", 1024))


def log(msg: str) -> None:
    print(f"[merge] {msg}", flush=True)


def token() -> str:
    tok = os.environ.get("HF_TOKEN", "").strip()
    if tok:
        return tok
    from huggingface_hub import get_token

    return (get_token() or "").strip()


def main() -> int:
    if not HF_REPO or not CHECKPOINT:
        sys.exit("Set HF_REPO and CHECKPOINT (e.g. CHECKPOINT=checkpoint-800).")
    tok = token()
    if not tok:
        sys.exit("No Hugging Face token found.")

    local = OUT_DIR / CHECKPOINT
    if not local.exists():
        # Expected: save_total_limit prunes older checkpoints locally, but the
        # Hub kept them all. This is that safety net actually being used.
        log(f"{CHECKPOINT} not on disk — pulling from {HF_REPO}")
        from huggingface_hub import snapshot_download

        snapshot_download(
            HF_REPO, allow_patterns=f"{CHECKPOINT}/*", token=tok,
            local_dir=str(OUT_DIR),
        )
    if not local.exists():
        sys.exit(f"{CHECKPOINT} is neither local nor on the Hub.")

    log(f"loading adapter from {local}")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(local), max_seq_length=MAX_SEQ, load_in_4bit=True,
    )

    log(f"merging to 16-bit and pushing to {HF_REPO}")
    model.push_to_hub_merged(
        HF_REPO, tokenizer, save_method="merged_16bit", token=tok, private=True
    )

    (WORKSPACE / "DONE").write_text(f"merged {CHECKPOINT}\n", encoding="utf-8")
    log(f"DONE — {CHECKPOINT} merged and pushed. Safe to destroy the instance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
