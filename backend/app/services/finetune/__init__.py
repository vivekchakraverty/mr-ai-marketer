"""Fine-tune corpus pipeline for the social post models.

Builds an engagement-labelled training corpus from two sources:

  * Bluesky — the public HF dump (text only, no engagement), re-hydrated against
    the live API to attach real like/repost/reply counts.
  * Mastodon — read from our own already-scored, consent-filtered corpus.

Everything here writes to a SEPARATE staging database
(DATA_DIR/finetune/corpus.sqlite3) and never to the live corpus tables. See
store.py for the three independent reasons that boundary exists.

See docs/social-model-finetune-spec.md for the full design.
"""
