#!/usr/bin/env python
"""Fine-tune a Bluesky post model. Crash-safe and resumable.

Designed to survive the things that actually go wrong on a rented GPU box:

  dropped SSH        run.sh detaches the process, so the trainer never sees it.
  process crash      run.sh restarts it; this script resumes from the newest
                     local checkpoint automatically.
  power cut / reboot  onstart.sh re-launches on boot, same resume path.
  INSTANCE DESTROYED  checkpoints are pushed to the Hub as they are written, so
                     a brand new box can pick up where the dead one stopped.

That last one is why every checkpoint goes to the Hub rather than only the final
model. A local-only checkpoint is worthless the moment the disk it lives on goes
away, and on rented hardware that is a routine event, not a disaster scenario.

Everything is driven by env vars so the same file works unchanged on any box:

  HF_TOKEN     (required) write-scoped Hugging Face token
  HF_REPO      (required) where the model goes, e.g. you/bluesky-post-qwen7b
  DATA_REPO    (optional) HF dataset holding train/val.jsonl; falls back to
                          local files in DATA_DIR
  BASE_MODEL   (optional) default Qwen/Qwen2.5-7B-Instruct
  EPOCHS, LR, LORA_R, BATCH, MAX_SEQ   (optional) training knobs
"""

from __future__ import annotations

# Must precede any trl/transformers/peft import — unsloth patches those modules
# at import time, and importing them first silently disables the optimisations
# (it warns, then runs slower and uses more memory).
import unsloth  # noqa: F401  (import for side effects)

import glob
import os
import sys
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
OUT_DIR = WORKSPACE / "out"
DATA_DIR = Path(os.environ.get("DATA_DIR", WORKSPACE))
MERGED_DIR = WORKSPACE / "merged"

BASE_MODEL = os.environ.get("BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
HF_REPO = os.environ.get("HF_REPO", "").strip()
DATA_REPO = os.environ.get("DATA_REPO", "").strip()
def _token() -> str:
    """Env first, then the box's own `huggingface-cli login` cache.

    The Vast image sets HF_HOME to a non-default path, so the cached token is
    not where you would expect — but huggingface_hub.get_token() honours it.
    Reading the existing login means no credential has to be copied anywhere.
    """
    tok = os.environ.get("HF_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from huggingface_hub import get_token

        return (get_token() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


HF_TOKEN = _token()

EPOCHS = float(os.environ.get("EPOCHS", 2))
LR = float(os.environ.get("LR", 1e-4))
LORA_R = int(os.environ.get("LORA_R", 32))
BATCH = int(os.environ.get("BATCH", 8))
ACCUM = int(os.environ.get("ACCUM", 4))
MAX_SEQ = int(os.environ.get("MAX_SEQ", 1024))
SEED = int(os.environ.get("SEED", 20260731))

# Frequent enough that a crash costs minutes, not hours. Each LoRA checkpoint is
# ~150MB (adapter only, not the base model), so pushing them all is cheap.
SAVE_STEPS = int(os.environ.get("SAVE_STEPS", 100))
SAVE_LIMIT = int(os.environ.get("SAVE_LIMIT", 3))

# Smoke-test escape hatch. Set MAX_STEPS to a small number to prove the whole
# path works — model loads, chat template applies, a step runs, a checkpoint
# reaches the Hub — before committing an hour of GPU time to a config typo.
# A smoke run deliberately does NOT merge, push the model, or write DONE.
MAX_STEPS = int(os.environ.get("MAX_STEPS", -1))


def log(msg: str) -> None:
    print(f"[train] {msg}", flush=True)


def require_env() -> None:
    if not HF_TOKEN:
        sys.exit(
            "No Hugging Face token. Either `huggingface-cli login` on this box, "
            f"or set HF_TOKEN in {WORKSPACE}/.env — this script reads "
            "credentials, it never writes them."
        )
    if not HF_REPO:
        sys.exit(f"HF_REPO not set. Add it to {WORKSPACE}/.env.")


def fetch_data() -> tuple[Path, Path]:
    """Local files if present, otherwise pull from the Hub.

    The Hub path is what makes a fresh box self-sufficient: nothing has to be
    copied from your laptop to resume a dead run.
    """
    train, val = DATA_DIR / "train.jsonl", DATA_DIR / "val.jsonl"
    if train.exists() and val.exists():
        log(f"using local data: {train}")
        return train, val

    if not DATA_REPO:
        sys.exit(f"No {train} and no DATA_REPO set — nothing to train on.")

    from huggingface_hub import hf_hub_download

    log(f"pulling data from {DATA_REPO}")
    paths = []
    for name in ("train.jsonl", "val.jsonl"):
        paths.append(
            Path(
                hf_hub_download(
                    DATA_REPO, name, repo_type="dataset",
                    token=HF_TOKEN, local_dir=str(DATA_DIR),
                )
            )
        )
    return paths[0], paths[1]


def newest_checkpoint() -> str | None:
    """Latest local checkpoint, or None. Resumption hinges on this."""
    found = glob.glob(str(OUT_DIR / "checkpoint-*"))
    if not found:
        return None
    return max(found, key=lambda p: int(p.rsplit("-", 1)[-1]))


def pull_checkpoint_from_hub() -> None:
    """If the disk is empty but the Hub has checkpoints, bring the newest back.

    This is the total-loss recovery path: the previous instance is gone, this one
    is brand new, and training should still continue rather than restart.
    """
    if newest_checkpoint():
        return
    try:
        from huggingface_hub import list_repo_files, snapshot_download
    except Exception:  # noqa: BLE001
        return
    try:
        files = list_repo_files(HF_REPO, token=HF_TOKEN)
    except Exception as err:  # noqa: BLE001 — a fresh repo 404s, which is fine
        log(f"no existing hub checkpoints ({str(err)[:60]})")
        return

    ckpts = {f.split("/")[0] for f in files if f.startswith("checkpoint-")}
    if not ckpts:
        log("hub has no checkpoints yet — starting fresh")
        return

    latest = max(ckpts, key=lambda c: int(c.rsplit("-", 1)[-1]))
    log(f"recovering {latest} from the Hub")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        HF_REPO, allow_patterns=f"{latest}/*", token=HF_TOKEN,
        local_dir=str(OUT_DIR),
    )


def main() -> int:
    require_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # If the merged model is already on the Hub, this run is a no-op. Keeps the
    # supervisor loop from retraining after a successful finish.
    done_marker = WORKSPACE / "DONE"
    if done_marker.exists():
        log("DONE marker present — training already finished. Nothing to do.")
        return 0

    train_path, val_path = fetch_data()
    pull_checkpoint_from_hub()

    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

    log(f"loading {BASE_MODEL} in 4-bit")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL, max_seq_length=MAX_SEQ, load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_R * 2,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )

    data = load_dataset(
        "json", data_files={"train": str(train_path), "val": str(val_path)}
    )

    # Unsloth's patched SFTTrainer does not apply chat templates for us the way
    # vanilla TRL does — it demands pre-rendered text — so render it here.
    def to_text(row):
        return {
            "text": tokenizer.apply_chat_template(
                row["messages"], tokenize=False, add_generation_prompt=False
            )
        }

    data = data.map(to_text, remove_columns=data["train"].column_names)
    log(f"train={len(data['train']):,}  val={len(data['val']):,}")

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,   # TRL >=0.20 renamed `tokenizer`
        train_dataset=data["train"],
        eval_dataset=data["val"],
        args=SFTConfig(
            output_dir=str(OUT_DIR),
            num_train_epochs=EPOCHS,
            max_steps=MAX_STEPS,
            per_device_train_batch_size=BATCH,
            gradient_accumulation_steps=ACCUM,
            learning_rate=LR,
            warmup_ratio=0.03,
            lr_scheduler_type="cosine",
            logging_steps=25,
            eval_strategy="steps",
            eval_steps=SAVE_STEPS * 2,
            save_strategy="steps",
            save_steps=SAVE_STEPS,
            save_total_limit=SAVE_LIMIT,
            bf16=True,
            seed=SEED,
            max_length=MAX_SEQ,           # TRL >=0.20 renamed `max_seq_length`
            dataset_text_field="text",
            # TRL 0.23 otherwise resolves a literal "<EOS_TOKEN>" placeholder,
            # which is not in Qwen's vocabulary and hard-fails at construction.
            eos_token=tokenizer.eos_token,
            report_to="none",
            # Every checkpoint goes to the Hub as it is written. This is the
            # difference between "the box died" being an inconvenience and being
            # a total loss.
            push_to_hub=True,
            hub_model_id=HF_REPO,
            hub_token=HF_TOKEN,
            hub_strategy="all_checkpoints",
            hub_private_repo=True,
        ),
    )

    # Train on the POST only, masking the system prompt and brief out of the
    # loss. Without this the model spends capacity learning to generate the
    # instructions we always supply ourselves at inference. TRL's
    # `assistant_only_loss` needs the conversational format Unsloth rejects, so
    # this is the Unsloth-native equivalent; the markers are Qwen's ChatML turns.
    from unsloth.chat_templates import train_on_responses_only

    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|im_start|>user\n",
        response_part="<|im_start|>assistant\n",
    )

    resume = newest_checkpoint()
    log(f"resuming from {resume}" if resume else "starting from scratch")
    trainer.train(resume_from_checkpoint=bool(resume))

    if MAX_STEPS > 0:
        log(f"SMOKE RUN of {MAX_STEPS} steps finished — not merging, not marking DONE.")
        return 0

    log("training finished — merging adapter into base weights")
    model.save_pretrained_merged(str(MERGED_DIR), tokenizer, save_method="merged_16bit")

    log(f"pushing merged model to {HF_REPO}")
    model.push_to_hub_merged(
        HF_REPO, tokenizer, save_method="merged_16bit", token=HF_TOKEN, private=True
    )

    done_marker.write_text("ok\n", encoding="utf-8")
    log("DONE. Safe to destroy the instance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
