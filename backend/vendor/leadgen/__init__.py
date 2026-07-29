"""Lead Gen Agent — a local, free/open-source AI sales agent.

An OpenOutreach-faithful pipeline (discover -> qualify -> find & verify email ->
draft -> send -> agentic follow-up) built entirely on tools that are either free,
self-hosted, or already vendored in this app:

  * discovery      OpenStreetMap/Overpass (keyless) + self-hosted SearXNG
  * qualification  scikit-learn Gaussian Process + Bayesian active learning over
                   local BAAI/bge-small-en-v1.5 embeddings (sentence-transformers)
  * email lookup   deterministic pattern finder + self-hosted Reacher verification
  * email writing  the app's own fine-tuned Email Writer HF Space (injected)
  * reasoning      HF Inference Providers (billed to the user's HF token) / Ollama
  * storage        SQLite under DATA_DIR (no external database)

Nothing here shells out to a subprocess or requires a paid third-party API. The
daemon runs in-process (see scheduler.py), matching the packaged-backend constraint
the rest of this app already lives under.
"""
