# Fine-tune runbook — Bluesky post model v1

A step-by-step guide to training the model on Vast.ai and getting it serving.
Written for someone who has not run a training job before; every step says what
it does and why, not just what to type.

**Where we are:** the training data is finished and sitting on your machine.
Everything below is about turning it into a model and proving it is better.

---

## The plan in one paragraph

You rent a GPU by the hour, copy 26,000 examples onto it, run a script that
teaches a general-purpose AI to write Bluesky posts the way real high-performing
posts are written, download the ~150MB result, and serve it on Modal exactly like
BrandForge. Then you measure it against the numbers we already recorded, and only
ship it if it actually wins. Total GPU cost is about **$1–3**.

You are NOT training a model from scratch. You are taking a finished model and
nudging it — a technique called **LoRA**, which learns a small "patch" instead of
rewriting all the weights. That is why this costs a dollar and not a fortune.

---

## Step 0 — What you already have

Three files, produced by the pipeline:

```
%APPDATA%\mr-ai-marketer\finetune\train.jsonl   26,048 examples
%APPDATA%\mr-ai-marketer\finetune\val.jsonl      3,264 examples
%APPDATA%\mr-ai-marketer\finetune\test.jsonl     3,257 examples
```

Each line is one lesson: *"given these instructions and this brief, here is the
post a real person wrote that actually performed."*

`train` teaches. `val` is checked during training to catch it going wrong.
`test` is never shown to the model — it is the final exam, and it contains
**only authors the model has never seen**, so it cannot cheat by memorising.

---

## Step 1 — Rent the GPU (~5 minutes)

On [vast.ai](https://vast.ai), search for an instance with:

| Requirement | Why |
|---|---|
| **24GB+ VRAM** (RTX 4090, A5000, A6000) | A 7B model in 4-bit plus training overhead fits comfortably in 24GB |
| **50GB+ disk** | The base model download is ~15GB |
| **PyTorch template** | Saves installing CUDA yourself |
| Good upload speed | You download the result at the end |

A 4090 runs about **$0.30–0.40/hour**. The job takes 1–3 hours. Do not rent an
A100 or H100 — you would pay 5× for a job this small to finish slightly sooner.

**Hand me SSH access once it is up** and I can drive the rest. Your credentials
stay on the box; nothing needs to leave it.

---

## Step 2 — Get the data onto the box (~5 minutes)

From your machine:

```bash
scp -P <port> "$env:APPDATA\mr-ai-marketer\finetune\train.jsonl" root@<host>:/workspace/
scp -P <port> "$env:APPDATA\mr-ai-marketer\finetune\val.jsonl"   root@<host>:/workspace/
```

Vast gives you the exact `<host>` and `<port>` on the instance page.

---

## Step 3 — Install the training tool (~5 minutes)

We use **Unsloth** — a wrapper that makes LoRA training roughly 2× faster and
uses less memory than the standard tooling. It is the simplest reliable option
for a single GPU.

```bash
pip install "unsloth[cu121-torch240]" trl peft accelerate bitsandbytes
```

---

## Step 4 — The training script

Save as `/workspace/train.py`. Every setting is explained.

```python
from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig
from datasets import load_dataset
import torch

MODEL = "Qwen/Qwen2.5-7B-Instruct"

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name    = MODEL,
    max_seq_length= 1024,   # our examples are short (a brief + a <300 char post)
    load_in_4bit  = True,   # squeezes a 7B onto a 24GB card
)

model = FastLanguageModel.get_peft_model(
    model,
    r              = 32,    # "how much can it learn" — 32 suits style adaptation
    lora_alpha     = 64,    # conventionally 2x r
    lora_dropout   = 0.05,  # mild regularisation against memorising
    target_modules = ["q_proj","k_proj","v_proj","o_proj",
                      "gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing = "unsloth",
    random_state   = 20260731,
)

data = load_dataset("json", data_files={
    "train": "/workspace/train.jsonl",
    "val":   "/workspace/val.jsonl",
})

def to_text(row):
    return {"text": tokenizer.apply_chat_template(
        row["messages"], tokenize=False, add_generation_prompt=False)}

data = data.map(to_text)

trainer = SFTTrainer(
    model         = model,
    tokenizer     = tokenizer,
    train_dataset = data["train"],
    eval_dataset  = data["val"],
    args = SFTConfig(
        output_dir                  = "/workspace/out",
        num_train_epochs            = 2,      # see note below
        per_device_train_batch_size = 8,
        gradient_accumulation_steps = 4,      # effective batch 32
        learning_rate               = 1e-4,   # deliberately conservative
        warmup_ratio                = 0.03,
        lr_scheduler_type           = "cosine",
        logging_steps               = 25,
        eval_strategy               = "steps",
        eval_steps                  = 200,
        save_steps                  = 200,
        bf16                        = True,
        dataset_text_field          = "text",
        max_seq_length              = 1024,
        seed                        = 20260731,
    ),
)

trainer.train()
model.save_pretrained_merged("/workspace/merged", tokenizer, save_method="merged_16bit")
```

### Why these numbers

- **2 epochs, LR 1e-4.** The single biggest risk is *catastrophic forgetting* —
  training so hard on writing posts that the model forgets how to follow
  instructions. Our whole pipeline depends on it obeying rules the training data
  never demonstrates (never invent a URL; stay under 300 characters). Low and
  short is deliberate. **If you are unsure, do less.**
- **LoRA rank 32.** Enough capacity for register and length discipline. Higher
  ranks mostly buy memorisation, which we actively do not want.
- **4-bit loading.** Lets a 7B train on a cheap 24GB card with no quality loss
  that matters at this scale.

### Watch for this while it runs

The `eval_loss` printed every 200 steps should **fall, then flatten**. If it
starts *rising*, the model is overfitting — stop and use an earlier checkpoint.
That is what `save_steps` exists for.

**Time:** roughly 1–2 hours on a 4090.

---

## Step 5 — Bring the result home (~10 minutes)

`/workspace/merged` is the finished model (~15GB in 16-bit). Two options:

**A. Push to Hugging Face** (simpler — Modal pulls from there directly):

```bash
huggingface-cli login
huggingface-cli upload <your-username>/bluesky-post-qwen7b /workspace/merged
```

**B. Convert to GGUF first** if you want the CPU-Space route like BrandForge:

```bash
python llama.cpp/convert_hf_to_gguf.py /workspace/merged --outfile model.gguf --outtype q5_k_m
```

**For a 7B I recommend option A + Modal GPU.** A 7B on a free CPU Space is slow
enough to hurt the composer's feel, and you already have the Modal path working.

**Then destroy the instance.** Vast bills by the hour whether you use it or not.

---

## Step 6 — Serve it

Mirror `app/brandforge/space.py`: a Modal GPU function behind a small Gradio
Space, exposing one endpoint.

```
/generate_post(system_prompt, brief) -> post text
```

The system prompt must be **byte-identical** to what training used:

```
Platform: bluesky (hard limit 300 characters)
Target performance: top
Media: text-only

<the platform norms block from llm.platform_norms('bluesky')>
```

This matters more than it sounds. The model learned to associate that exact
wording with the behaviour we want. Paraphrasing it at inference throws away part
of what you just paid to teach it.

---

## Step 7 — Prove it is better (the part people skip)

We recorded baselines *before* training precisely so this is answerable:

| | Capability gate | Over-limit rate |
|---|---|---|
| Qwen3-80B (ships today) | **91.7%** | — |
| Qwen3-4B | 75.0% | 62.5% |

Run the same suite against the fine-tune:

```bash
cd "V:/mr ai marketer/backend"
DATA_DIR="$APPDATA/mr-ai-marketer" ./.venv/Scripts/python.exe -m app.services.finetune eval --space <your-space-id>
```

**Ship only if:**

1. **Capability gate ≥ 91.7%** — it must not invent URLs, handles or statistics
   more than what you ship today. This is a hard gate; style gains do not buy
   forgiveness here.
2. **Over-limit rate near zero.** Every training example is a real post under 300
   characters, so this is the clearest thing SFT should fix.
3. **Divergence closer to the corpus than baseline.** Note the baseline *already*
   scored 3/3 on direction — an instruct model naturally writes longer when told
   "top". So the fine-tune must match the corpus gaps more *accurately*
   (corpus: +23 chars, +0.42 hashtags), not merely show some gap.

If it fails the gate, do not ship it. Retrain at 1 epoch or LR 5e-5 — almost
every failure at this stage is over-training.

---

## Step 8 — A/B it in the app

Wire it behind the `socialModelArm` setting (`stock` | `finetune` | `ab`), write
the arm into `generation_arm`, and compare **real 48h engagement** on posts you
actually published. Read at n≥40 per arm, using **median and a bootstrap CI** —
engagement is heavy-tailed and one viral post would otherwise decide it.

That is the only test that truly matters. Everything before it is a proxy.

---

## Honest expectations for v1

**Likely wins:** length discipline (the big one), Bluesky register, hashtag
restraint.

**Unlikely to improve:** factual restraint. The corpus cannot teach "don't invent
a link" because no training example demonstrates declining to. That is inherited
from the base model, which is exactly why the 7B is worth the extra cost.

**Genuinely uncertain:** whether any of it moves real engagement. That is what
Step 8 is for, and a null result there is a legitimate outcome worth knowing.
