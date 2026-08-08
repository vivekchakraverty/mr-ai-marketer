"""Retrieval service for the Marketing Plan tool.

Holds the ~1.1 GB marketing-corpus index and answers `search(query, k) -> passages`, so the
desktop app doesn't have to carry the index on every user's machine.

Why this exists as a Space rather than a download:

* The index is a Chroma collection over ~104k passages. Downloading it costs each user a
  1.1 GB pull and a warm-up before their first plan.
* Chroma needs the index as local files — it cannot query storage over the network — so the
  Space materialises it once at boot and keeps it in memory afterwards.

Two ways to call it, and the difference is the whole point:

* `search(...)` returns the passages themselves. Simple, and the corpus leaves in extracts —
  enough queries reconstruct it.
* `compose(...)` takes the caller's *prompt*, drops the passages into it here, runs the model
  here, and returns only the finished prose. The passages never cross the wire. Someone who
  scrapes this endpoint collects paraphrase, which is roughly what they would get by using
  the product honestly.

`compose` is the one the desktop app uses. `search` stays for self-hosters who own their own
corpus and have nothing to protect from themselves.

Where the money goes, which is worth being precise about:

* **This Space's CPU is billed to whoever owns the Space** — Hugging Face has no mechanism to
  charge a Space's own compute to a visitor's token. On CPU-basic hardware that is free, and
  retrieval is cheap: one short embedding plus a vector lookup.
* **Generation is still billed to the end user.** `compose` runs the model through an
  InferenceClient built from the token *the caller sent with the request*, so the inference
  bill lands on their account exactly as it did when the call happened on their machine.

The trade that buys, stated plainly: the corpus stops leaving, and the caller's Hugging Face
token starts arriving. This Space never logs, stores or reuses that token — it is used for
one request and dropped — but a caller has only this file's word for that. Anyone pointing an
app at a Space they do not own should send a fine-grained token scoped to inference and
nothing else, and the desktop app's Settings says so.

The corpus itself stays private: it lives in a private HF Bucket that only this Space can
read, using a token supplied as a Space secret. Callers get passages, never the index.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path

import gradio as gr

# Set these as Space secrets/variables (Settings -> Variables and secrets).
BUCKET_ID = os.getenv("RAG_BUCKET_ID", "").strip()          # e.g. you/dm-rag-index
DATASET_ID = os.getenv("RAG_DATASET_ID", "").strip()        # fallback if no bucket is used
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()                # needs read access to the above
COLLECTION = os.getenv("RAG_COLLECTION", "dm_rag")
# Shared secret the desktop app sends. Empty leaves the endpoint open to anyone with the URL.
APP_KEY = os.getenv("RAG_APP_KEY", "").strip()
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

def _index_dir() -> Path:
    """Where to materialise the index.

    `/data` is the mount point for a Space's persistent storage and **only exists when that
    storage is attached** — writing there on a Space without it either fails or silently
    lands on ephemeral disk. So prefer it when it is genuinely present and writable, and
    otherwise use the working directory, which always is.

    The upshot: the same image works with or without storage, and attaching storage later
    needs no code change — the sync just starts finding the index already there and skips it.
    """
    explicit = os.getenv("RAG_INDEX_DIR", "").strip()
    if explicit:
        return Path(explicit)

    persistent = Path("/data")
    if persistent.is_dir() and os.access(persistent, os.W_OK):
        return persistent / "rag_index"
    return Path.cwd() / "rag_index"


INDEX_DIR = _index_dir()

MAX_K = 20

_collection = None
_embedder = None
_ready = threading.Event()
_status = "starting"


def _fetch_index() -> None:
    """Materialise the index locally — from a private Bucket, or a private Dataset."""
    global _status
    if (INDEX_DIR / "chroma.sqlite3").exists():
        _status = "index already present on disk"
        return

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if BUCKET_ID:
        from huggingface_hub import HfApi

        _status = f"downloading index from bucket {BUCKET_ID}"
        # sync_bucket, not download_bucket_files: the latter wants an explicit list of
        # (remote, local) pairs, while sync mirrors the whole prefix in one call and skips
        # anything already present — which is what makes persistent storage worth attaching.
        HfApi(token=HF_TOKEN or None).sync_bucket(
            source=f"hf://buckets/{BUCKET_ID}", dest=str(INDEX_DIR)
        )
    elif DATASET_ID:
        from huggingface_hub import snapshot_download

        _status = f"downloading index from dataset {DATASET_ID}"
        snapshot_download(
            repo_id=DATASET_ID, repo_type="dataset",
            token=HF_TOKEN or None, local_dir=str(INDEX_DIR),
        )
    else:
        raise RuntimeError("Set RAG_BUCKET_ID (or RAG_DATASET_ID) in the Space's settings.")


def _boot() -> None:
    """Fetch and open the index on a background thread.

    Gradio has to start serving immediately or the Space is marked unhealthy and restarted,
    which on a cold start would loop forever: the fetch takes minutes and the restart throws
    the progress away. So the UI comes up first and reports status while this runs behind it.
    """
    global _collection, _embedder, _status
    started = time.time()
    try:
        _fetch_index()

        import chromadb
        from sentence_transformers import SentenceTransformer

        _status = "loading embedding model"
        _embedder = SentenceTransformer(EMBED_MODEL)
        _status = "opening collection"
        _collection = chromadb.PersistentClient(path=str(INDEX_DIR)).get_collection(COLLECTION)
        persistent = str(INDEX_DIR).startswith("/data")
        _status = (
            f"ready — {_collection.count():,} passages in {time.time() - started:.0f}s"
            f" ({'persistent storage' if persistent else 'ephemeral disk, re-syncs on cold start'})"
        )
    except Exception as err:  # noqa: BLE001 — surface the reason instead of dying silently
        _status = f"failed: {type(err).__name__}: {err}"
    finally:
        _ready.set()


threading.Thread(target=_boot, name="rag-boot", daemon=True).start()


def _authorised(key: str) -> bool:
    """Gate the corpus behind a shared key.

    Space *visibility* does not do this job. A "protected" Space hides its repo — the files,
    the config — but the running app stays reachable to anyone with the URL, which was
    confirmed by querying it anonymously and getting passages back. If the corpus is not
    freely redistributable, that is the hole that matters.

    A shared key is honest about what it is: it stops anyone who merely finds the URL, and it
    would not stop someone who pulls the key out of a distributed client. That is the right
    trade for this, because the alternative — having each user send their own Hugging Face
    token for verification — means real credentials leaving user machines for a server they
    do not control, which is a worse deal than the problem it solves.
    """
    if not APP_KEY:
        return True  # no key configured: open, and the README says so
    return bool(key) and key == APP_KEY


def search(query: str, k: int = 8, category: str = "", key: str = "") -> dict:
    """Top-k passages for a query.

    Returns a dict rather than bare strings so a caller can tell "the index is still warming
    up" apart from "there is genuinely nothing relevant" — the desktop app falls back to an
    ungrounded plan on the former and should not treat it as an empty corpus.
    """
    if not _authorised(key):
        return {"ok": False, "status": "unauthorised", "passages": []}

    query = (query or "").strip()
    if not query:
        return {"ok": False, "status": "empty query", "passages": []}

    # Bounded wait: a cold Space is loading, not broken. Beyond this the caller is better off
    # generating without grounding than blocking a user any longer.
    if not _ready.wait(timeout=120) or _collection is None:
        return {"ok": False, "status": _status, "passages": []}

    k = max(1, min(int(k or 8), MAX_K))
    # Callers pass one category, or several comma-separated — the plan pipeline asks for
    # e.g. "seo,general" so a section draws on its own material plus the general corpus.
    cats = [c.strip() for c in (category or "").split(",") if c.strip()]
    where = None
    if len(cats) == 1:
        where = {"category": cats[0]}
    elif len(cats) > 1:
        where = {"category": {"$in": cats}}
    try:
        vector = _embedder.encode([query], normalize_embeddings=True).tolist()
        result = _collection.query(query_embeddings=vector, n_results=k, where=where)
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "status": f"query failed: {err}", "passages": []}

    return {
        "ok": True,
        "status": _status,
        "passages": result.get("documents", [[]])[0],
        "distances": [round(d, 6) for d in result.get("distances", [[]])[0]],
    }


# --------------------------------------------------------------------------- compose

# The desktop app builds its own prompts — the plan's structure, its wording and its citation
# rules belong to the app, not here — and leaves a marker where the grounding passages go.
# This Space fills that marker in and runs the model, so the prompt arrives here without the
# corpus in it and leaves as prose.
#
# The marker carries the retrieval parameters with it, base64'd, rather than being paired with
# a separate argument. One self-describing string means no way for the query and the prompt to
# arrive out of step, and no server-side state to keep between calls.
MARKER_RE = re.compile(r"<<RAGREMOTE:([A-Za-z0-9+/=_-]+)>>")

MAX_PROMPT_CHARS = 60000
MAX_OUTPUT_TOKENS = 8000


def _passages_for(marker_payload: str) -> tuple[str, bool]:
    """Decode one marker and return the block of passages to put in its place."""
    try:
        spec = json.loads(base64.urlsafe_b64decode(marker_payload.encode()).decode())
    except Exception:  # noqa: BLE001
        return "(no grounding context available for this run)", False

    query = str(spec.get("q") or "").strip()
    k = max(1, min(int(spec.get("k") or 8), MAX_K))
    category = spec.get("cat") or ""
    if isinstance(category, list):
        category = ",".join(str(c) for c in category)

    found = search(query, k, str(category), APP_KEY)
    passages = found.get("passages") or []
    if not passages:
        return "(no grounding context available for this run)", False
    # "Source N" numbering only — the index carries no title, author or URL, and the app's
    # prompt tells the model to cite this way and never invent a citation.
    return "\n\n".join(f"Source {i + 1}: {p}" for i, p in enumerate(passages)), True


def compose(
    prompt: str,
    token: str = "",
    model: str = "",
    max_tokens: int = 3500,
    temperature: float = 0.4,
    key: str = "",
) -> dict:
    """Ground a prompt and run it, returning only what the model wrote.

    This is the endpoint that protects the corpus. `search` hands back extracts, and enough
    calls to it rebuild the index; this hands back a written answer, and enough calls to it
    rebuild nothing but a style.
    """
    if not _authorised(key):
        return {"ok": False, "status": "unauthorised", "text": "", "grounded": False}
    prompt = prompt or ""
    if not prompt.strip():
        return {"ok": False, "status": "empty prompt", "text": "", "grounded": False}
    if len(prompt) > MAX_PROMPT_CHARS:
        return {"ok": False, "status": "prompt too long", "text": "", "grounded": False}
    if not token.strip():
        return {
            "ok": False,
            "status": "no Hugging Face token supplied — generation is billed to the caller",
            "text": "",
            "grounded": False,
        }
    if not model.strip():
        return {"ok": False, "status": "no model specified", "text": "", "grounded": False}

    grounded = False

    def _fill(match: re.Match) -> str:
        nonlocal grounded
        block, hit = _passages_for(match.group(1))
        grounded = grounded or hit
        return block

    filled = MARKER_RE.sub(_fill, prompt)

    from huggingface_hub import InferenceClient

    try:
        client = InferenceClient(api_key=token.strip())
        response = client.chat_completion(
            model=model.strip(),
            messages=[{"role": "user", "content": filled}],
            max_tokens=max(256, min(int(max_tokens or 3500), MAX_OUTPUT_TOKENS)),
            temperature=float(temperature),
        )
        text = response.choices[0].message.content or ""
    except Exception as err:  # noqa: BLE001
        # Return the provider's message — a 401 or an out-of-credit 402 is the caller's to
        # fix, and burying it would leave them staring at "generation failed".
        return {"ok": False, "status": f"{type(err).__name__}: {err}", "text": "", "grounded": grounded}

    # The token is a local in this function and the client is dropped with it. There is no
    # scrubbing step to write here — the point is that nothing above ever put it anywhere.
    return {"ok": True, "status": _status, "text": text, "grounded": grounded}


def status() -> dict:
    return {"ready": _collection is not None, "status": _status}


with gr.Blocks(title="Marketing corpus retrieval") as demo:
    gr.Markdown(
        "## Marketing corpus retrieval\n"
        "Used by Mr. AI Marketer's Marketing Plan tool.\n\n"
        "**`compose`** is the endpoint the app calls: send a prompt with a grounding marker in "
        "it and your own Hugging Face token, and it comes back written. The passages are put "
        "into the prompt here and never leave — and the generation is billed to the token you "
        "sent, not to whoever runs this Space.\n\n"
        "**`search`** returns the passages themselves. It is here for people running this "
        "against their own corpus; pointing it at someone else's is how a corpus walks out the "
        "door a few extracts at a time."
    )
    with gr.Tab("Compose"):
        prompt_box = gr.Textbox(label="Prompt (may contain a <<RAGREMOTE:…>> marker)", lines=8)
        with gr.Row():
            model_box = gr.Textbox(label="Model", scale=2)
            token_box = gr.Textbox(label="Your HF token", type="password", scale=2)
            tokens_box = gr.Slider(256, MAX_OUTPUT_TOKENS, value=3500, step=100, label="Max tokens")
            temp_box = gr.Slider(0, 1.5, value=0.4, step=0.05, label="Temperature")
            compose_key = gr.Textbox(label="App key", type="password", scale=1)
        compose_out = gr.JSON(label="Result")
        gr.Button("Compose", variant="primary").click(
            compose,
            inputs=[prompt_box, token_box, model_box, tokens_box, temp_box, compose_key],
            outputs=compose_out,
            api_name="compose",
        )

    with gr.Tab("Search"):
        with gr.Row():
            query_box = gr.Textbox(label="Query", scale=4)
            k_box = gr.Slider(1, MAX_K, value=8, step=1, label="Passages")
            category_box = gr.Textbox(label="Category filter (optional)", scale=1)
            key_box = gr.Textbox(label="App key", type="password", scale=1)
        out = gr.JSON(label="Result")
        gr.Button("Search", variant="primary").click(
            search, inputs=[query_box, k_box, category_box, key_box], outputs=out, api_name="search"
        )
        gr.Button("Status").click(status, inputs=None, outputs=out, api_name="status")

demo.queue(max_size=32).launch()
