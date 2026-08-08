# Crash-safe fine-tuning on a rented GPU

Scripts that keep a training run alive through the things that actually go wrong
on rented hardware, and push the finished model to Hugging Face by themselves.

## What survives what

| What happens | What saves you |
|---|---|
| You close your laptop / SSH drops | Training runs inside **tmux**, not as a child of your shell |
| The trainer crashes (transient) | **run.sh** restarts it; resumes from the newest checkpoint |
| Power cut, box reboots | **onstart.sh** via `@reboot` cron relaunches it automatically |
| **The instance is destroyed** | Every checkpoint is pushed to the **Hub as it is written**; a brand-new box pulls the data and the last checkpoint and carries on |
| Training finishes while you sleep | Model is merged and pushed to HF automatically, then a `DONE` marker stops anything restarting it |

The last row is the one that matters most. A checkpoint that only exists on the
rented disk is worthless the moment that disk goes away — and on spot instances
that is routine, not a disaster. Checkpoints are LoRA adapters (~150MB), so
pushing all of them costs very little.

## Files

| File | Runs where | Does what |
|---|---|---|
| `push_data.py` | your machine | uploads train/val/test to a **private** HF dataset |
| `bootstrap.sh` | the GPU box | installs everything, checks the GPU, installs the reboot hook |
| `train.py` | the GPU box | the actual training; resumable, pushes checkpoints to the Hub |
| `run.sh` | the GPU box | tmux supervisor with restart-on-crash |
| `onstart.sh` | the GPU box | boot hook that resumes an unfinished run |

## Order of operations

**Once, on your machine:**

```bash
python scripts/finetune/push_data.py --repo you/bluesky-post-data
```

**On the rented box:**

```bash
# 1. credentials — you create this file; the scripts only ever read it
cat > /workspace/.env <<'ENV'
HF_TOKEN=hf_xxxxxxxxxxxx        # WRITE scope
HF_REPO=you/bluesky-post-qwen7b
DATA_REPO=you/bluesky-post-data
ENV

# 2. copy these scripts over, then:
bash /workspace/bootstrap.sh
bash /workspace/run.sh
```

Then close the terminal. It keeps going.

**Check on it any time:**

```bash
bash /workspace/run.sh logs        # follow the log
```

You can also just watch the model repo on huggingface.co — new `checkpoint-*`
folders appearing is proof it is alive, without needing to SSH in at all.

**When `DONE` appears, destroy the instance.** Vast bills by the hour whether the
GPU is busy or idle.

## Recovering from a destroyed instance

Rent a new box, put the same `.env` on it, run `bootstrap.sh` and `run.sh`.
`train.py` sees an empty disk, pulls the newest checkpoint back from the Hub, and
resumes. No manual bookkeeping and nothing needed from your laptop.

## Knobs

Defaults are deliberately conservative — the main risk is training so hard the
model forgets how to follow instructions, which would break the app's reliance on
"never invent a URL" and "stay under 300 characters". Override in `.env`:

```
BASE_MODEL=Qwen/Qwen2.5-7B-Instruct
EPOCHS=2        # if unsure, lower rather than higher
LR=1e-4
LORA_R=32
BATCH=8         # drop to 4 if you see CUDA out-of-memory
```

## Watch the loss

`eval_loss` prints periodically. It should **fall, then flatten**. If it starts
climbing, the model is overfitting: stop, and use an earlier checkpoint — that is
what `save_total_limit=3` keeps around.

## A caveat worth reading

The dependency versions in `bootstrap.sh` are **pinned on purpose**. TRL has
renamed `SFTConfig` arguments between releases, and an unpinned install is the
most likely way for this to break months from now on a box you cannot easily
debug. If you deliberately upgrade and training fails on a `TypeError` about an
unexpected keyword, that is the cause.
