# Spec: dual-mode fine-tuned social post model

Status: **proposed** · Owner: Vivek · Written 2026-07-30

One fine-tuned Qwen3 that writes for **both** Bluesky and Mastodon, with the
platform (and a target performance tier) selected at inference by control tokens.
Served as a GGUF on a Hugging Face Space — the pattern
[`app/brandforge/space.py`](../backend/app/brandforge/space.py) already proves —
and wired into both post routers behind a provider flag so it can be A/B'd
against the current stock-model path using **real measured engagement**.

---

## 1. Hypothesis and success criteria

**Hypothesis.** Platform register (length, hashtag density, casing convention,
formality) is learnable from engagement-labelled corpora, and a single model
conditioned on a platform token will produce more platform-native drafts than a
stock instruct model steered by prompt norms alone.

**Success = all three:**

| # | Criterion | Measured by |
|---|---|---|
| S1 | Modes genuinely diverge | Mechanical divergence checks (§9.1) — length + hashtag-count + CamelCase distributions differ by platform token, in the right direction |
| S2 | No capability regression | Instruction-adherence suite (§9.2) — char limits respected, no invented URLs/stats, source material used, disclosure line kept |
| S3 | Real-world lift | 48h `engagement_rate` of *published* drafts, fine-tune arm vs stock arm (§10) |

**Kill criteria.** If S2 fails, stop — the pipeline depends on instruction
following far more than on style. If S1 passes but S3 is flat after ~40 published
posts per arm, keep the stock path as default and treat the fine-tune as a
stylistic option, not an upgrade.

## 2. Non-goals

- **Not replacing retrieval.** Weights are frozen; the exemplar corpus updates
  hourly. Fine-tune teaches *register*, RAG supplies *current niche grounding*.
  Both stay.
- **Not touching `vendor/socialpost`.** It stays unmodified (the invariant that
  kept the Mastodon tool from forking it). See §11 for how.
- **Not importing Mastodon dumps.** Mastodon training data comes only from our
  own consent-filtered collection (`should_learn_from`). Non-negotiable: the app
  enforces instance policies, and a bulk import would contradict its own gate.
- Not a preference-optimised model in v1. DPO is a v2 option (§8.3).

---

## 3. Pipeline overview

```
 ┌─ Bluesky ────────────────────────────────────────────────┐
 │ HF Roronotalt/bluesky  (~8M, firehose, NO engagement)    │
 │   └─ S1 filter: lang, length, niche keywords, quality    │
 │       └─ S2 re-hydrate  bluesky.get_posts()  ────────┐   │
 └──────────────────────────────────────────────────────│───┘
                                                        │
 ┌─ Mastodon ───────────────────────────────────────────│───┐
 │ our own corpus: posts.niche LIKE '% · mastodon'      │   │
 │ (already engagement-scored + consent-filtered)  ─────┤   │
 └──────────────────────────────────────────────────────│───┘
                                                        ▼
                                      S3  engagement → quality tier
                                                        ▼
                                      S4  backtranslate a brief per post
                                                        ▼
                                      S5  control-tagged instruction pairs
                                                        ▼
                            S6 train (LoRA) → merge → GGUF → Space
                                                        ▼
                                  S7 provider flag in both routers → A/B
```

All staging artefacts live under `DATA_DIR/finetune/` and **never** in the live
corpus tables. See §5.3 for why that matters.

---

## 4. Stage 1 — acquisition and filtering

Source: [`Roronotalt/bluesky`](https://huggingface.co/datasets/Roronotalt/bluesky)
(~8M+ posts, firehose-collected ≈ Nov–Dec 2024).

> **Snapshot it locally.** Sibling datasets have already been pulled or flagged
> (`bluesky-community/one-million-bluesky-posts` was removed;
> `alpindale/two-million-bluesky-posts` carries a legal-issue report). Do not
> depend on a live URL. Record the commit SHA in the manifest (§12).

Verify the actual column names on download rather than assuming; expect roughly
`uri`, `text`, `author`/`did`, `created_at`, `langs`.

Filters, applied in order (cheapest first):

1. `lang == en` (the embedding model and prompts are English-only today).
2. `20 <= len(text) <= 300` — drop one-word replies and anything over the
   Bluesky ceiling (malformed rows).
3. **Drop replies and reposts.** Replies are conversational fragments whose
   engagement reflects the parent, not the post. Reposts aren't original text.
4. **Niche relevance.** Keep posts matching any active niche's keywords, plus an
   embedding-similarity pass against the niche string (reuse
   `embeddings.embed` + the `topics.py` sign-test convention: only actively
   *unlike* content gets cut — see `NICHE_RELEVANCE_THRESHOLD` and its measured
   rationale before choosing a threshold).
5. Spam/bot heuristics: URL-only posts, >4 hashtags, duplicate text hashes,
   authors contributing >200 posts (RSS republishers).

**Target after filtering: 30k–80k posts, not millions.** A smaller
engagement-labelled niche-relevant set beats a huge unlabelled one, and it keeps
Stage 2 and Stage 4 (both API/LLM-bound) affordable.

## 5. Stage 2 — re-hydration

This is what converts text into *labelled* text.

### 5.1 Mechanism

Reuse what exists — no new API client:

```python
from vendor.socialpost.src import bluesky
live = bluesky.get_posts(uris)          # 25 URIs/call, dict keyed by URI
profiles = bluesky.get_profiles(dids)   # → .follower_count
```

`snapshot.py::backfill_48h` is the working precedent for exactly this shape
(fetch → `engagement_rate(likes, reposts, replies, followers)` → write). Model
the new job on it, including its `JobRun` accounting (`.note()`, `.count()`,
`.partial()`).

New job: `backend/app/services/finetune/rehydrate.py` (app-side, not vendored).

Budget: 80k posts ÷ 25 = **3,200 `getPosts` calls**, plus profile calls batched
by DID. Rate-limit politely and checkpoint to disk every N batches so an
interrupted run resumes.

### 5.2 Semantics — read this before writing any label

Dataset posts are ~18 months old. Current counts are **final lifetime
engagement**, which is *not* the same measurement as a 48h snapshot.
`backfill_48h` deliberately bounds itself to posts aged 50h–7d because "beyond
this, drift and deletions distort" — we are far outside that.

Consequences, both deliberate:

- Label the field **`lifetime_engagement_rate`**, never `48h`.
- Deleted / suspended posts return nothing → **drop the row**. Never write a
  zero (the existing "absent beats zeroes" rule: a zero reads as a real post
  that flopped and drags baselines down).

### 5.3 Hard rule: staging tables only

Imported posts **must not** enter `posts` / `engagement_snapshots`. Three
independent reasons:

1. `engagement_snapshots.window_label` has a `CHECK (window_label in ('1h','24h','48h'))`
   — there is no honest label for a lifetime measurement.
2. `refresh_exemplars` would start selecting 18-month-old posts by strangers into
   the live exemplar pool, silently changing what grounds every draft.
3. `performance_baselines` would be computed over a different measurement
   semantic, corrupting the niche baselines.

Staging lives in its own SQLite file, `DATA_DIR/finetune/corpus.sqlite3`:

```sql
create table if not exists ft_posts (
  uri text primary key,
  platform text not null,               -- bluesky | mastodon
  text text not null,
  hashtags text not null default '[]',
  author_did text,
  follower_count integer,
  created_at text,
  likes integer, reposts integer, replies integer,
  lifetime_engagement_rate real,        -- bluesky: re-hydrated
  measured_window text,                 -- 'lifetime' | '48h'
  niche text,
  quality_tier text,                    -- top | mid | low  (stage 3)
  brief text,                           -- stage 4
  split text                            -- train | val | test (stage 6)
);
```

Mastodon rows are copied in from the live corpus (`posts.niche LIKE '% · mastodon'`
joined to its `48h` snapshots) with `measured_window='48h'` — a *read*, never a
write, so the live tables stay untouched.

## 5.4 P0 results — measured 2026-07-30

Ran end to end. Replaces the estimates above with real numbers.

| | Measured |
|---|---|
| Dump size | **94,967,071 rows** (not the ~8M advertised), 8.2GB, one parquet |
| Scan | 60,000 candidates, 22,606 authors, 26 topics, ~18 min |
| Relevance rerank | 60,000 scored, 2,257 rejected (**3.8%** at the 0.0 sign test) |
| Re-hydration | 5,000 → **4,101 labelled, 899 gone (18.0%)**, 139s, 1,833 authors |
| Rate distribution | median 0.0035 · p75 0.016 · p90 0.055 · p99 0.50 · **max 6.0** · mean 0.033 |
| Zero-engagement | 1,204 (**29.4%**) |

Structural checks all clean: no negative rates, no null rates on labelled rows,
no fake zeroes written to `gone` rows, no mislabelled measurement windows.

**Required amendment — apply a follower floor before tiering.** The raw top of
the distribution is entirely small-denominator artefact: `rate=6.0` was 3 likes
on a **1-follower** account. This is the same trap the live corpus already
documents at `_recompute_baseline` (a niche baselining at 0.20 instead of 0.009).
Measured impact of the floor:

| Floor | n | median | p90 | max |
|---|---|---|---|---|
| none | 4,101 | 0.0035 | 0.0548 | **6.000** |
| ≥50 | 3,359 | 0.0034 | 0.0303 | 1.014 |
| **≥200** | **2,449** | 0.0029 | 0.0219 | **1.014** |

At ≥200 the top of the pool becomes genuinely high-performing rather than
arithmetic noise — 210 likes on 212 followers, 229 likes + 25 reposts on 709,
636 likes on 2,351. So Stage 3 must apply `MIN_FOLLOWERS = 200` (matching
`refresh_exemplars`) *before* percentile-ranking into tiers.

**Yield math for planning.** candidates → labelled is 82% (18% deleted);
labelled → above floor is 59.7%. So **~49% of scanned candidates become usable
training rows**, i.e. a target of *N* pairs needs ≈ **2.04 × N** candidates
scanned. The existing 60k pool therefore yields ~29k usable pairs — at the low
end of the §8.2 mixture target, so P1 should scan wider.

**Two implementation notes carried forward:**
- The dump is **author-sorted**, so scanning must stride row groups AND the
  stride must scale with the target, or an early stop confines the sample to the
  first slice of the file. `acquire.scan` now derives stride from
  target ÷ hit-rate.
- The niches' own keywords matched only **798 posts in 95M** (0.0008%) — they are
  built for Bluesky phrase *search*. `acquire.PREFILTER` holds a 26-topic
  vocabulary instead (~2% hit rate). Breadth is deliberate: register is what a
  fine-tune learns, and retrieval supplies topicality at inference.

## 6. Stage 3 — engagement labels and quality tiers

Percentile-rank `lifetime_engagement_rate` **within (platform, niche)** — never
globally. Cross-niche and cross-platform rates aren't comparable, and a global
percentile would just rank niches against each other.

Apply the **`MIN_FOLLOWERS = 200` floor first** — see §5.4 for why (without it the
entire `top` tier is 1-follower accounts with 3 likes).

| Tier | Percentile | Role |
|---|---|---|
| `top` | ≥ p90 | what we generate with at inference |
| `mid` | p40–p90 | keeps register broad, avoids overfitting to viral outliers |
| `low` | < p40 | **kept**, as a negative-tagged contrast |

**Keep the low tier.** Training only on winners discards the contrastive signal;
conditional training on a quality token lets the model learn what *distinguishes*
a high performer, and we simply always ask for `top` at inference. Filtering
low performers out entirely is the more obvious and worse choice.

Followers floor: reuse the existing convention (≥200 Bluesky / ≥50 Mastodon) so
`engagement_rate` isn't a small-denominator artefact.

## 7. Stage 4 — backtranslated briefs

We have posts but no instructions. Training on raw post text would be next-token
completion, which **degrades instruction-following** — the exact capability the
pipeline depends on (respect this instance's char limit, don't invent URLs or
stats, incorporate fetched source material, keep the disclosure line).

So synthesise the missing half: for each post, have an LLM write the *brief a
person would have typed* to get that post. Standard instruction backtranslation.

Run through `llm._call` (already provider-agnostic, already retry-wrapped),
temperature ~0.2, ~80 output tokens. Prompt sketch:

```
Below is a real social media post. Write the one-sentence request its author
would have typed into a post composer to get it — the intent and desired tone,
NOT a summary of the text.

Rules:
- Never quote distinctive phrases from the post.
- Never mention specific numbers, URLs, product names or handles from it.
- One sentence, imperative, under 25 words.

Post:
"""{text}"""
```

Two guards, because a leaky brief teaches the model to expect facts it will not
have at inference:

- Reject briefs sharing a ≥5-gram with the post → regenerate once, then drop.
- Reject briefs containing a URL, `@handle`, or digit run of 3+.

Cost note: this is one LLM call per retained post — the most expensive stage.
Budget accordingly, checkpoint, and consider capping at ~40k.

## 8. Stage 5 — control-tagged pair assembly

### 8.1 Format

Match the **inference** format exactly, or the control tokens won't transfer.
The routers already build per-platform norms (`llm.platform_norms` for Bluesky/X/
LinkedIn; `mastodon_post._norms_for` for Mastodon), so put the control signal in
the system prompt, in natural language, alongside the same norms text:

```
<system>
Platform: mastodon (instance character limit: 2263)
Target performance: top
{the same norms block the router will send at inference}
</system>
<user>
{backtranslated brief}
Niche: {niche}
</user>
<assistant>
{post text}
</assistant>
```

Explicit `<|platform:...|>` codes are the alternative; natural-language
conditioning is preferred here purely because it composes with the norms
machinery that already exists on both paths.

### 8.2 Mixture and balance

Bluesky will outnumber Mastodon by orders of magnitude. Left alone, the Mastodon
mode drowns.

- Cap Bluesky at **~30k** pairs (top+mid+low, roughly 25/50/25).
- Take **all** usable Mastodon pairs.
- **Upsample Mastodon to ≈25–30%** of total training tokens (duplicate with
  varied briefs rather than verbatim, to limit memorisation).
- Blend in **~5% general instruction data** to protect instruction-following.

Record the exact realised mixture in the manifest — if S1 fails, mixture is the
first knob.

### 8.3 v2 option — DPO

Engagement *is* a preference signal. Pair `top` vs `low` posts on similar topics
within a niche (cosine ≥ 0.5) and run DPO after SFT. Arguably better aligned to
"write what performs" than imitating winners. Out of scope for v1; the staging
schema already supports building the pairs.

## 9. Train/eval split

**Split by author and by time — never randomly.**

- **Author-disjoint.** Same-author posts are near-duplicates in style; a random
  split leaks them across train/test and inflates every metric.
- **Temporal holdout.** Reserve the newest ~10% by `created_at` as `test`, to
  measure drift rather than interpolation.
- Sizes: 80 / 10 / 10 (train / val / test), stratified by (platform, niche, tier)
  so every cell is represented.

### 9.1 Offline eval — divergence (tests S1)

The core hypothesis is that the modes differ. Test it mechanically: generate the
same 200 held-out briefs under each platform token and compare distributions.

| Metric | Expectation |
|---|---|
| Median char length | Mastodon **≫** Bluesky |
| Mean hashtag count | Mastodon ≈ 2–4; Bluesky ≈ 0–2 |
| CamelCase-hashtag rate | Mastodon high; Bluesky low |
| Over-limit rate | ~0 for both |

If these don't separate, the control tokens didn't take — revisit §8.2 before
anything else.

### 9.2 Offline eval — capability (tests S2, gating)

A fixed suite of ~40 adversarial briefs asserting:

- char limit respected (per-instance for Mastodon, 300 for Bluesky)
- **no invented URL / handle / statistic / price / date / version** — the
  existing generation prompt's hardest rule
- supplied source material actually used when present
- disclosure line preserved when requested
- output is bare post text (no preamble, no surrounding quotes, no "Option 1:")

Plus LLM-as-judge pairwise vs the stock path on 100 briefs (blind, order
randomised) for a soft quality read. **S2 is a gate, not a score.**

### 9.3 Perplexity

Report held-out PPL on `top`-tier posts per platform as a training-health signal
only. It is not a success criterion — low PPL on social text does not imply good
drafts.

---

## 10. A/B measurement — the part that actually decides it

This app can do something rare: measure the *real outcome*. `attach_posted_uri`
records that a draft was published, and `snapshot` then captures its 1h/24h/48h
engagement like any other post. So the A/B is a genuine outcome test, not a
proxy.

### 10.1 Recording the arm

`generations` has no provider/model column and `vendor/socialpost` stays
unmodified — so store the arm app-side, keyed by generation id. Exact precedent:
`mastodon_post_meta` exists in the app DB because the vendored `posts` table had
no column for permalinks.

In `backend/app/db.py`:

```sql
create table if not exists generation_arm (
  generation_id integer primary key,
  arm text not null,          -- 'stock' | 'finetune'
  provider text not null,     -- 'hf' | 'space'
  model text not null,        -- resolved model / space id
  platform text not null,
  instance text default '',
  created_at text not null
);
```

Both routers write one row per generation, immediately after the vendored
`generations` insert returns its id.

### 10.2 Assignment

Deterministic, not random: `arm = hash(generation_id_seed) % 2` is
unreproducible before the row exists, so assign on the **request** —
`hash(f"{niche}|{user_input}") % 100 < split_pct`. Stable for a retry of the same
brief (so "Try again" doesn't silently switch arms), and the split percentage is
a setting.

Also honour an explicit override so the arm can be forced for manual comparison.

### 10.3 Readout

```sql
select a.arm, a.platform, count(*) n, avg(s.engagement_rate) avg_rate
from generation_arm a
join generations g on g.id = a.generation_id
join engagement_snapshots s
  on s.post_uri = g.posted_uri and s.window_label = '48h'
where g.posted_uri is not null
group by a.arm, a.platform;
```

Surface it as an **Analytics tab**, next to the existing Bluesky/Email tabs.

**Statistical honesty:** engagement is heavy-tailed, so report the **median and
a bootstrap CI**, not just the mean — one viral post will otherwise decide the
experiment. Pre-commit to a minimum of ~40 published posts per arm per platform
before reading anything, and say so in the UI rather than showing a tempting
n=3 number.

---

## 11. Serving and integration

### 11.1 The Space

Mirror BrandForge exactly: LoRA → merge → GGUF (Q4_K_M / Q5_K_M) → Gradio Space,
optionally proxying to a Modal GPU. One endpoint:

```
/generate_post(system_prompt, brief, niche) -> post text
```

`ERROR:`-prefixed responses signal failure, matching
`brandforge/space.py::generate_section`, which already handles client caching,
sleeping-Space wake-up, and stale-client eviction. Copy that shape.

Settings gets a `socialModelSpaceId` field (defaulted, overridable) — same
treatment as `DEFAULT_SPACE_ID`.

### 11.2 Keeping `vendor/socialpost` unmodified

The stock Bluesky path runs `generation.generate()`, which internally calls
`llm.generate_post`. We must swap only the writer.

**Chosen approach — app-level service reusing vendored retrieval.**
`backend/app/services/social_model.py`:

```python
def generate(user_input, niche, platform, source_url="", norms=None, instance=""):
    from vendor.socialpost.src import generation, sources
    fetched   = sources.fetch_url(source_url) if source_url.strip() else None
    query     = f"{user_input} {fetched.title}".strip() if fetched else user_input
    exemplars = generation.retrieve_exemplars(query, niche)   # reused, public
    kb        = generation.retrieve_kb(platform)              # reused, public
    text      = space.generate_post(_system_prompt(platform, instance, norms),
                                    user_input, niche)
    ...
```

`retrieve_exemplars` and `retrieve_kb` are already public, so retrieval is reused
rather than reimplemented. The Mastodon router needs even less work — it
*already* bypasses `generation.generate()` and calls `llm.generate_post` directly,
so it's a one-call swap.

**Known trade-off:** this bypasses the vendored telemetry `enqueue` and consent
gate on the fine-tune arm. Either replicate the enqueue in the service, or
restrict the fine-tune arm to installs with telemetry disabled, and decide
explicitly — don't let it drift into an accidental gap.

*Rejected for v1:* adding a `writer=` injection parameter to
`generation.generate()`. It is cleaner and matches how `main.py` already injects
`draft_writer` into `leadgen_scheduler`, but it modifies vendored code. Revisit
if the fine-tune becomes the default.

### 11.3 The flag

`LLM_PROVIDER` is vendored config with `VALID_PROVIDERS = ('gemini','hf')`;
adding a third value there means touching vendor. Use an **app-level setting**
instead: `socialModelArm` ∈ `stock | finetune | ab`, in `AppSettings` and the
Settings screen, read by both routers.

Wiring checklist:

- `backend/app/services/social_model.py` — new
- `backend/app/services/finetune/{filter,rehydrate,briefs,pairs}.py` — new
- `backend/app/routers/social_post.py` — arm selection + `generation_arm` write
- `backend/app/routers/mastodon_post.py` — same (swap the `llm.generate_post` call)
- `backend/app/db.py` — `generation_arm` table + helpers
- `backend/backend.spec` — **add every new module to `hiddenimports`** by dotted
  path, or the packaged build breaks silently while dev mode works
- `electron/.../state/types.ts`, `store.ts`, `routes/Settings.tsx` — the arm setting
- `electron/.../routes/Analytics.tsx` — A/B readout tab

### 11.4 Rollout

1. Ship the Space + service with `socialModelArm='stock'` (dark).
2. Manual side-by-side on ~20 briefs; run the §9.2 gate.
3. Flip to `ab` at 50/50.
4. Read at n≥40 published per arm per platform.
5. Default to whichever wins; keep the loser reachable by setting.

Rollback is a settings change — no redeploy.

---

## 12. Reproducibility

Write `DATA_DIR/finetune/manifest.json` for every run: dataset commit SHA, filter
counts at each stage, re-hydration date (engagement is time-dependent — the same
URIs re-hydrated later give different labels), realised mixture, split seed, LoRA
hyperparameters, base model revision, eval results. Without the re-hydration date
the labels are unreproducible.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Instruction-following degrades | §9.2 is a hard gate; instruction pairs not raw text; low LR, modest LoRA rank, 1–2 epochs; ~5% general instruction blend |
| Mastodon mode never diverges (too little data) | Upsample to 25–30% of tokens; if it still fails, fall back to two LoRA adapters (accepting GGUF swap friction) |
| Dataset pulled / licence challenged | Snapshot locally + record SHA; treat as a one-off corpus, not a dependency |
| Training on strangers' posts | Bluesky is public-by-design; still respect deletions (absent rows), never republish verbatim, keep the existing anti-copying prompt rules |
| Style drift (2024 data, 2026 norms) | Temporal holdout in `test`; RAG still supplies current grounding |
| Heavy-tailed engagement fools the A/B | Median + bootstrap CI; pre-committed minimum n |
| Packaged build breaks | `backend.spec` hiddenimports for every new module |

## 14. Phasing

| Phase | Deliverable | Gate |
|---|---|---|
| P0 | Staging schema + filter + re-hydrate 5k posts | Label sanity: rate distribution plausible, deletions dropped |
| P1 | Briefs + pairs, full corpus | Leak guards pass on a 100-row manual audit |
| P2 | Train + offline eval | S1 divergence **and** S2 gate |
| P3 | Space + service, dark | Manual side-by-side acceptable |
| P4 | A/B on, Analytics tab | n≥40/arm/platform → S3 verdict |

P0 is independently useful: a re-hydrated engagement-labelled Bluesky corpus
improves the *existing* exemplar pool story even if training never happens.
