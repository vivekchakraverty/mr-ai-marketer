---
title: Marketing Corpus Retrieval
emoji: 📚
colorFrom: yellow
colorTo: red
sdk: gradio
app_file: app.py
pinned: false
---

# Marketing corpus retrieval

Answers `search(query, k) -> passages` over the ~104k-passage marketing corpus behind
Mr. AI Marketer's Marketing Plan tool.

It exists so the desktop app doesn't have to ship a 1.1 GB index to every user. Chroma can
only query local files, so this Space materialises the index once at boot and keeps it in
memory; callers get extracts, never the corpus.

## Configuration

Set these under **Settings → Variables and secrets**:

| Name | Kind | Purpose |
| --- | --- | --- |
| `RAG_BUCKET_ID` | variable | Private HF Bucket holding the index, e.g. `you/dm-rag-index` |
| `RAG_DATASET_ID` | variable | Alternative source if you published to a Dataset instead |
| `HF_TOKEN` | **secret** | Read access to that bucket/dataset |
| `RAG_INDEX_DIR` | variable | Where to materialise it. Defaults to `/data/rag_index` |

## Hardware

CPU basic is enough — retrieval is one short embedding plus a vector lookup.

**Attach persistent storage if you can.** Without it the disk is wiped on every cold start
and the Space re-downloads 1.1 GB before it can answer, which takes minutes. With it, restarts
are seconds.

## Who pays for what

* **This Space's compute is billed to whoever owns it.** Hugging Face has no way to charge a
  Space's own CPU to a visitor's token. On CPU basic that cost is zero.
* **Plan generation is billed to the end user** — that LLM call happens in the desktop app
  using the token they configured, and this Space is not involved in it.

## Cold starts

Free Spaces sleep after inactivity. The first request after a sleep returns
`{"ok": false, "status": "..."}` while the index loads rather than hanging, so the client can
fall back to generating without grounding instead of blocking someone for minutes.
