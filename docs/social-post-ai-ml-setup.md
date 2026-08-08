# AI/ML setup — Social Post Creator tools

Reference documentation for the **current** (as-built) AI/ML architecture behind
the Social Post Generator (Bluesky/X/LinkedIn), the Mastodon Post Creator, and
the Hashtag Suggester that sits inside both composers.

Written 2026-07-30 · describes the code as it exists, not proposals.
For the *proposed* fine-tune, see [social-model-finetune-spec.md](./social-model-finetune-spec.md).

---

## 1. TL;DR

**There is no fine-tuning in these tools.** No weights are trained, no LoRA, no
local inference. The stack is:

> **stock hosted instruct model** + **retrieval over an engagement-scored corpus
> we build ourselves** + **a closed measure→rebuild feedback loop**.

That is RAG with a self-curating knowledge base, not a fine-tune and not a bare
prompt template. What "learns" is the **corpus and the exemplar pool**, which
update continuously; the model is frozen and swappable.

(For contrast, three *other* tools in this app do use fine-tuned models on hosted
Spaces: **BrandForge** — `<your-username>/brandforge-space` — plus **Blog
Writer** and **Email Writer**. The social tools deliberately do not.)

### Architecture at a glance

```
        ┌──────────── collect ────────────┐
        │ Bluesky: keyword search +        │  ingest        (6h)
        │          tracked author feeds    │
        │ Mastodon: hashtag timelines      │  mastodon_collect (12h)
        └──────────────┬───────────────────┘
                       ▼   consent filters applied here
                    posts / authors
                       ▼
        ┌──────────── measure ─────────────┐
        │ engagement at 1h / 24h / 48h     │  snapshot      (1h)
        │ rate = (likes+reposts+replies)   │
        │        / followers               │
        └──────────────┬───────────────────┘
                       ▼
        ┌──────────── select ──────────────┐
        │ score = rate × 0.5^(age/14d)     │  refresh_exemplars (24h)
        │ greedy dedupe @ cosine 0.85      │
        │ → top 20 per niche, embedded     │
        └──────────────┬───────────────────┘
                       ▼
        ┌──────────── generate ────────────┐
        │ embed request → retrieve 5       │  on demand
        │ exemplars + 3 KB notes           │
        │ → assemble prompt → stock LLM    │
        └──────────────┬───────────────────┘
                       ▼
        ┌──────────── judge ───────────────┐
        │ user publishes → link pasted →   │  watchdog (weekly)
        │ measured → compared to baseline  │  benchmark (manual)
        │ → underperforming? force relearn │
        └──────────────────────────────────┘
```

---

## 2. Model layer

### 2.1 Generation LLM

All generation flows through **one module**: [`vendor/socialpost/src/llm.py`](../backend/vendor/socialpost/src/llm.py).
Nothing else imports a provider SDK — that containment is what makes the provider
swappable.

| | |
|---|---|
| Providers | `hf` (default in this app) or `gemini`, via `LLM_PROVIDER` |
| HF endpoint | `https://router.huggingface.co/v1` (OpenAI-compatible, plain `requests`) |
| HF default model | `Qwen/Qwen3-Next-80B-A3B-Instruct` |
| Gemini default | `gemini-flash-lite-latest` (an **alias**, deliberately — pinned names rot out of the free tier) |
| Billing | The user's own HF token *is* the billing identity |
| Retries | 4 attempts, base backoff 4s exponential + jitter |
| Prompt fingerprint | `PROMPT_VERSION = "v1"` — bump on material prompt changes so pooled outcomes stay segmentable |

**Model selection was measured, not assumed.** Documented in the module:

- **Avoid thinking models.** `Qwen3-8B` (not labelled "Thinking", but Qwen3 enables
  it by default) burned all 300 tokens of a KB-triage budget and returned *empty*
  text. `-Instruct-2507` releases have it off. Same failure on Gemini flash/pro,
  hence the `-lite` alias.
- **Precision.** Given an Instagram-only article, `Llama-3.3-70B` tagged it
  "Instagram, Facebook, Twitter, TikTok"; Qwen Instruct tagged it "Instagram".
- **Obedience.** `DeepSeek-V3` and `Llama-3.3` wrapped bare-post output in
  quotation marks anyway; Qwen3 did not.

`python -m src.llm --probe` reports which models a given key can actually
*generate* with — listing and generating are separately gated.

### 2.2 Call budgets

| Call site | Temp | Max tokens | Why |
|---|---|---|---|
| `generate_post` | **0.9** | 600 | The one creative call; the anti-copying rule needs room to diverge from exemplars |
| `summarize_kb` | 0.1 | 300 | Extraction, not writing — creativity here becomes a hallucinated "platform change" |
| Hashtag candidates | 0.3 | 300 | Constrained JSON list |
| Topic suggestions | 0.4 | 900 | Structured JSON, mild synthesis |

### 2.3 Embeddings

[`vendor/socialpost/src/embeddings.py`](../backend/vendor/socialpost/src/embeddings.py)

| | |
|---|---|
| Model | `all-MiniLM-L6-v2` (~90MB, sentence-transformers) |
| Dimensions | 384 — **matches the `vector(384)` column**; changing the model means a migration + full re-embed |
| Device | CPU |
| Normalisation | L2 → cosine similarity **is** a dot product |
| Batch size | 64 |
| Loading | `functools.lru_cache(maxsize=1)`, imported lazily — torch costs seconds and ~1GB RSS, so jobs that never embed don't pay it |

**Not trained. Used only for similarity.** Its uses: exemplar retrieval, exemplar
dedupe, hashtag suitability, topic clustering, niche-relevance sign tests.

### 2.4 Lazy-import discipline

Every router resolves the vendored package inside a function (`_spg()`), never at
module scope, because importing it pulls in torch. This is why backend startup is
fast for users who never open these tools.

---

## 3. The two tools

Siblings sharing a spine. Same DB, same embeddings, same LLM writer, same niche
list, same Library — but separate routers and different platform mechanics.

| | Social Post Generator | Mastodon Post Creator |
|---|---|---|
| Router | [`social_post.py`](../backend/app/routers/social_post.py) | [`mastodon_post.py`](../backend/app/routers/mastodon_post.py) |
| Platform code | `vendor/socialpost` (unmodified) | [`services/mastodon.py`](../backend/app/services/mastodon.py) |
| Generation path | `generation.generate()` — full vendored pipeline (KB, telemetry, consent) | **Bypasses it**: own retrieval + `llm.generate_post()` directly |
| Corpus niche key | `"indie makers"` | `"indie makers · mastodon"` (namespaced) |
| Exemplar pool | 20, with diversity dedupe | 15, **no dedupe pass** (hashtag timelines yield too few candidates for it to pay) |
| Follower floor | 200 | 50 |
| Grounding latency | **~48h cold start** — engagement must accumulate | **None** — hashtag timelines return counts immediately |
| Char limit | 300 fixed (Bluesky) | **Per-instance, read live** (mastodon.example 2263, mastodon.social 500) |
| Rules gate | None | **409 until instance rules accepted** (hash-fingerprinted) |
| Credentials to collect | Bluesky handle + app password | **None** — public/unlisted readable unauthenticated |
| Platform norms | Static table in `llm.py` | Built live from instance facts (`_norms_for`) |

### Why the namespace is load-bearing

Mastodon rows live under `"<niche> · mastodon"`. Without it, the Bluesky
`refresh_exemplars` — which **deactivates a niche's entire pool** and replaces it
— would periodically delete every Mastodon exemplar, and the two tools would
ground each other's drafts in the wrong platform's voice.

---

## 4. Data layer

SQLite at `DATA_DIR/social-post.sqlite3` (Supabase/Postgres also supported). The
sqlite backend mirrors the Postgres RPCs in Python — see
[`backends/sqlite_backend.py`](../backend/vendor/socialpost/src/backends/sqlite_backend.py).

| Table | Role | Notes |
|---|---|---|
| `niches` | name + keywords, active flag | Shared by both tools; seeded once from YAML, then the table wins |
| `posts` | collected corpus | `hashtags` is a JSON array; `niche` denormalised |
| `authors` | `follower_count` | Refreshed every ingest — the denominator of every rate |
| `engagement_snapshots` | measurements | `CHECK (window_label in ('1h','24h','48h'))`, `UNIQUE (post_uri, window_label)`, append-only |
| `exemplars` | the active pool | Carries the 384-d `embedding`; `active` flag, **never deleted** (auditable) |
| `generations` | every draft | `exemplar_ids`, `kb_ids`, `uid`, `posted_uri` (FK) |
| `performance_baselines` | per-niche typical rate | `(scope, scope_key, window_label)` |
| `kb_articles` | platform guidance | `platform_tags`, `decay_weight`, `url_hash` |
| `tracked_authors` | census accounts | Feeds ingest alongside keyword search |
| `job_runs` | job telemetry | **Drives catch-up scheduling** |
| `mastodon_post_meta` | *app* DB | Permalinks/instance/acct — the vendored `posts` table has no column for them |
| `mastodon_rule_acks` | *app* DB | Policy hash per instance |

---

## 5. The learning loop

### 5.1 Cadences

Run by an in-process daemon thread (not cron), started at backend startup.
**Catch-up semantics**: due-ness is measured against the last run recorded in
`job_runs`, not a wall clock — so a laptop asleep for two days resumes with one
run of each job instead of skipping or stampeding. Tick = 300s.

| Job | Every | Tool |
|---|---|---|
| `ingest` | 6h | Bluesky |
| `snapshot` | 1h | Bluesky |
| `refresh_exemplars` | 24h | Bluesky |
| `ingest_kb` | 24h | shared |
| `telemetry` | 6h | shared |
| `cleanup` | 7d | shared |
| `mastodon_snapshot` | 1h | Mastodon |
| `mastodon_collect` | 12h | Mastodon |

Both schedulers are **inert until configured** — the Bluesky one self-skips while
credentials are missing; the Mastodon one does nothing until at least one
instance's rules are accepted, so an untouched install makes **no network
requests to anyone's server**.

### 5.2 Collect

**Bluesky** ([`jobs/ingest.py`](../backend/vendor/socialpost/src/jobs/ingest.py)) — two sources into one URI-deduped dict:

- **Keyword search** — broad, but a *sample*: Bluesky returns what its index feels
  like returning. 50 posts per keyword per run.
- **Tracked author feeds** — narrow, but a *census* of accounts already shown to
  perform in the niche. 50 per author.

Filters: no timestamp → skip; future timestamp beyond 1h skew → skip; **older than
44h → skip**. That last one is subtle and deliberate: the 48h bucket accepts posts
aged 46–50h, so a post ingested at 60h can never be snapshotted, never become an
exemplar, and is just bytes. 44h guarantees every stored post gets a shot, with
cron-drift margin.

**Mastodon** (`_collect_niche`) — hashtag timelines per niche keyword
(`"rust gamedev"` → `#rustgamedev`). Requires ≥24h settle (`MIN_SETTLE_HOURS`) so a
zero isn't confused with "nobody's seen it yet". Engagement is written as the `48h`
snapshot immediately and the pool rebuilt in the same pass — Mastodon has no
algorithmic resurfacing to wait for.

### 5.3 Measure

[`jobs/snapshot.py`](../backend/vendor/socialpost/src/jobs/snapshot.py)

```
engagement_rate = (likes + reposts + replies) / max(follower_count, 1)
```

Follower-normalisation is what lets a 200-follower account outrank a 200k one in
the pool. Without it the system would just learn **"be famous"**, which is not a
transferable style signal.

| Bucket | Target | Tolerance |
|---|---|---|
| `1h` | 1h | ±30 min (early velocity is time-sensitive) |
| `24h` | 24h | ±2h |
| `48h` | 48h | ±2h |

Cap 500 posts/bucket/run (`getPosts` takes 25 URIs → 20 calls/bucket). Follower
counts are read from our `authors` table, not re-fetched — ingest refreshes them
6-hourly and a per-snapshot profile call would triple API usage for a number that
barely moves.

**Deleted or suspended posts write no row.** A zeroed row would look like a real
post that flopped and would drag the niche baseline down. *Absent beats zeroes* —
a rule applied consistently across the codebase.

`--backfill-48h` is an opt-in cold-start bootstrap: for posts aged 50h–7d, record
current counts as an approximate 48h snapshot. Explicitly *an approximation, not a
measurement* — hence never scheduled and age-bounded.

### 5.4 Select

[`jobs/refresh_exemplars.py`](../backend/vendor/socialpost/src/jobs/refresh_exemplars.py)

```
score = engagement_rate × 0.5^(age_days / 14)
```

A 14-day half-life keeps the pool responsive to platform shifts without letting
one good week dominate forever — a 14-day-old post needs twice the rate of a
fresh one to hold rank.

Then **greedy diversity selection**: walk the ranked list, keep a candidate only
if it is `< 0.85` cosine-similar to everything already kept.

> Without this the pool collapses. The highest-engagement posts in a niche are
> often near-identical ("just shipped X!"), and few-shotting on twenty paraphrases
> of one post produces twenty paraphrases back.

Eligibility: ≥200 followers, ≤90 days old, must have a 48h snapshot.

**Not transactional.** The old pool is deactivated, then the new one inserted. If
the insert fails, the niche is left with an *empty* active pool rather than a
stale one — retrieval degrades to KB-only, which is preferred over silently
serving a pool we meant to replace.

### 5.5 Baselines

Mean 48h `engagement_rate` per niche over 30 days, **no decay, no ranking** — it
must describe the *typical* post, unlike exemplar selection which wants the best.

The `MIN_FOLLOWERS` floor is applied here too, and the reason is measured:

> Without the floor this niche baselined at **0.20**, because a 7-follower account
> with 12 replies scores 1.71 and drags the mean up an order of magnitude. No real
> account achieves 0.2, so every generation would read as "below 80% of baseline"
> forever and the watchdog would fire every week regardless of quality. With the
> floor: **~0.009**, which real posts straddle in both directions — the entire
> point of a baseline.

Known limitation, documented in-code: mean is outlier-sensitive even above the
floor; a median would be more robust for niches containing a few very large
accounts.

---

## 6. Retrieval and prompt assembly

### 6.1 Retrieval

[`generation.py`](../backend/vendor/socialpost/src/generation.py)

- Embed the request (plus the fetched source's **title**, if a link was supplied —
  a bare "write about this" carries almost no signal for picking exemplars).
- `match_exemplars` returns the top **5** active exemplars for the niche:

```
blended = 0.7 × cosine_similarity + 0.3 × minmax_normalised(score)
```

Tilted toward similarity because an off-topic exemplar *actively misleads* the
model, whereas a merely-good on-topic one still teaches the voice. A pool with no
score spread contributes a flat 0.5 rather than dividing by zero.

- `retrieve_kb` returns the top **3** KB articles matching `[platform, "general"]`,
  ordered by `decay_weight` desc then `published_at` (needed as a tiebreak: on a
  fresh install every row is exactly 1.0).

Empty pool is **not** an error — generation falls back to platform norms alone and
logs a warning. That fallback is also what makes the benchmark's natural
experiment possible (§8.2).

### 6.2 The prompt

Structure of `_GENERATION_PROMPT`:

1. Role: *"You write social media posts for the `{niche}` community on `{platform}`."*
2. **KB section** — current verified platform guidance, or an explicit "none available".
3. **Exemplar section** — real high-performing posts, followed by the anti-copying rules:

   > Use them ONLY to infer tone, structure, rhythm, and length. You must NOT
   > reuse any distinctive phrase, sentence or opening line · reference their
   > products, names, numbers or events · imitate any one closely enough that its
   > author would recognise it. **Treat them as evidence of what this audience
   > responds to, not as text to remix.**

4. **Platform norms** (§6.3).
5. **Anti-invention rule** — never write a URL, handle, statistic, price, date or
   version number that wasn't supplied, *not even a placeholder like
   "example.com" or "[link]"*.
6. **Source section** (only when a link was given) — the one sanctioned origin of
   facts, with explicit permission to use them, because otherwise rule 5 reads as
   forbidding the very details that make a link-grounded post worth posting.
7. Output contract: one post, bare text, no preamble, no options, no wrapping quotes.

### 6.3 Platform norms

Static table in `llm.py` for **bluesky** (300 hard limit, conversational, hashtags
0–2 and not needed for reach, links unpenalised), **x** (280, hook first line,
links suppress reach, 1–2 hashtags), **linkedin** (2-line fold, short paragraphs,
3–5 hashtags).

**Mastodon is built live** in `mastodon_post._norms_for()` from the instance's own
facts, because the vendored table has no Mastodon entry and would fall through to
a generic default. It asserts: the real per-instance char limit; *no engagement
algorithm* (reach comes from human boosts); hashtags are the primary discovery
mechanism, 2–4, **CamelCase for screen readers**; marketing register is disliked
and rule-breaking on many instances; long posts are normal; CWs for sensitive
content. Plus a computed budget that **reserves room for the AI-disclosure line**
so the finished post fits, not just the model's share of it.

---

## 7. The knowledge-base layer

[`jobs/ingest_kb.py`](../backend/vendor/socialpost/src/jobs/ingest_kb.py) → curated RSS → `llm.summarize_kb()` → `kb_articles`.

- Returns the sentinel **`SKIP`** unless the item describes a concrete, actionable
  platform change. Skipped items are never stored: every row competes for space in
  the generation prompt, so the KB must stay small and true. A feed that's 90%
  fluff is fine — the fluff costs one cheap call and is dropped.
- Dedupe by `url_hash` (normalised: trailing slash + query stripped), so an item is
  summarised **at most once ever**.
- Caps: 15 items/feed/run, ignore items >45 days old, 4.5s spacing between LLM
  calls (free-tier Gemini is ~15 req/min).
- `feedparser` has **no timeout of its own**; the global socket default is set to
  20s because an observed feed hung for 6 minutes then returned 0 entries, which
  the job misreported as a dead feed.

**Platform tags come from the article's content, not its source feed.** This was a
measured fix: source-level tagging filed a TikTok-emoji article under `bluesky`
(because the publishing blog covers everything) while an article entirely about
Bluesky missed the `bluesky` tag. Since `platform_tags` is the retrieval filter,
coarse tags mean TikTok trivia gets injected into Bluesky posts as "current
platform guidance".

**Decay** (`cleanup`, weekly): multiply every active article's `decay_weight` by
**0.9**, deactivate below **0.3** — about 11 weeks from 1.0 to retirement. Because
retrieval orders by `decay_weight`, advice ages out of the prompt gradually rather
than falling off a cliff. `engagement_snapshots` are pruned after 90 days.

---

## 8. Evaluation and self-correction

This is the part most RAG systems lack: the outcome is **actually measured**.

### 8.1 The watchdog (automatic, weekly)

[`jobs/watchdog.py`](../backend/vendor/socialpost/src/jobs/watchdog.py)

Per niche, compare mean 48h `engagement_rate` of posts this system generated **and
the user actually published** against that niche's baseline.

| Constant | Value |
|---|---|
| `LOOKBACK_DAYS` | 28 |
| `MIN_DATA_POINTS` | 5 |
| `UNDERPERFORM_RATIO` | 0.8 |

Below 80% of baseline with ≥5 data points → **force an exemplar refresh**. In this
app (no `GH_PAT`) it runs `refresh_exemplars` as a subprocess — subprocess rather
than import, because that job loads sentence-transformers (~1GB) which the
watchdog has no other reason to pay for.

The decision is recorded in `job_runs` **either way** — "a watchdog that only logs
when it fires is indistinguishable from a broken watchdog."

This closed loop is what makes the system self-correcting rather than merely
self-collecting.

### 8.2 The benchmark (manual)

[`jobs/benchmark.py`](../backend/vendor/socialpost/src/jobs/benchmark.py) — `python -m src.jobs.benchmark`

Exploits a **natural experiment already in the data**: every generation records how
many exemplars it used. `n_exemplars == 0` means the draft fell back to platform
norms only — an **ungrounded control**. `n_exemplars > 0` is the **grounded
treatment**. Comparing lift-over-baseline directly tests whether grounding works.

Metrics: `lift = engagement / baseline` (mean + **median**), % beating baseline,
cuts by model / prompt version / niche, and **edit-distance draft→published** with
a `pct_near_verbatim` (<0.1) figure — a quality signal that carries *no engagement
selection bias*.

Every report prints its own limits:

- **Selection bias** — users publish drafts they like, so outcomes sample the
  winners. Fine for comparing groups; not for absolute claims.
- **Cold-start confound** — ungrounded drafts cluster early in a niche's life, so
  grounded-vs-ungrounded is partly mature-vs-new niche.
- **Small samples** — under ~30 pairs is directional at best.

### 8.3 Pooled telemetry (opt-in)

Two record types to a shared HF dataset, joined by `generation_uid`:

- **generation** — `model_id`, `prompt_version`, `niche`, `platform`, retrieval
  refs (exemplar URIs + similarity/score, KB `url_hash` + decay weight),
  `similarity_weight`. References, not content, unless content opt-in.
- **outcome** — `engagement_rate_48h`, `baseline_at_measure`,
  `edit_distance_ratio`, queued later once a 48h snapshot lands.

Gated: `generation.generate()` raises `telemetry.ConsentRequired` (surfaced as HTTP
409) until terms are accepted; the UI shows a consent screen rather than erroring.
`PROMPT_VERSION` exists precisely so a prompt tweak is *measurable* rather than a
shot in the dark. Telemetry enqueue is best-effort and can never break generation.

---

## 9. Consent and filtering

Not incidental — these gates shape what the models ever see.

**Bluesky:** authors carrying the `!no-unauthenticated` label are dropped, and
profiles are fetched *before* anything is written so a no-index author's post never
touches the DB even transiently.

**Mastodon** (`should_learn_from`): public visibility only · skip `bot` accounts ·
skip `discoverable=false` · skip bios containing `#nobot` / `#noindex` /
`#noarchive`. Deliberately **not** gated on `indexable`, which defaults false
network-wide and would reject essentially the entire network, leaving an
undiagnosable empty pool. Skip reasons are counted and surfaced in the UI. First
real pass on that instance: 52 scanned → 7 kept, 30 skipped as opted-out.

**The rules gate:** `/generate` *and* `/collect` return 409 until the instance's
live rules are fetched, shown, and accepted. The ack stores a **hash of the rule
text**, so an upstream edit re-closes the gate. Enforced backend-side — the UI is
not trusted. Background jobs **re-check the fingerprint every tick**: a timer must
not be a way around the gate.

---

## 10. Hashtag Suggester

[`services/hashtags.py`](../backend/app/services/hashtags.py) · one endpoint, shared by both composers.

Blends three axes into a 0–1 score:

| Axis | Weight | Source |
|---|---|---|
| Suitability | **0.50** | MiniLM cosine(draft, tag words) + whether the LLM proposed it |
| Trend | 0.28 | Mastodon `/trends/tags` + per-tag week-over-week `history` + Google Trends direction |
| Data | 0.22 | Mastodon weekly `uses`/`accounts` (mid-tail favoured) + corpus performance weight |

Suitability leads because the feature is "hashtags for *this* post" first and a
trend radar second — a hot tag that doesn't fit is noise.

Provider registry with honest degradation: `corpus`, `mastodon_trends`,
`google_trends` (`interest_over_time` only — `related_queries` reliably 429s),
`llm`, `embeddings`. Any can be unavailable; the response reports
`sourcesUsed` / `sourcesUnavailable`, and **a tag with no data reads "unknown",
never zero**. Reach buckets at ≥150 (broad) / ≥15 (balanced) weekly uses.
`recommendedCount` by platform: mastodon 4, linkedin 4, bluesky 2, x 2.

Because only Mastodon exposes free hashtag stats, non-Mastodon platforms get
fediverse numbers as an explicitly-labelled **cross-network proxy**
(`trendInstance` in the response) rather than a silent assumption.

---

## 11. Adjacent: Topic Scout / topic suggestions

[`vendor/socialpost/src/topics.py`](../backend/vendor/socialpost/src/topics.py) — feeds the composer with what to write about.

Spine is **our own corpus**, clustered by embedding similarity (greedy, threshold
0.55, min size 2, max 6 clusters) and ranked `size × (1 + 10 × best_engagement)` —
volume says "being talked about", measured engagement says "landing".

Overlays (all keyword-scoped, all optional): Google Trends interest, Google News
RSS, Wikipedia pageviews, Hacker News.

`NICHE_RELEVANCE_THRESHOLD = 0.0` carries a long measured justification worth
reading before touching: against the niche string, two RSS spam bots outscored a
genuine indie-maker post 4:1, because **short text embeds toward the origin** — so
any threshold above zero filters by *length* far more than by topic. Only a sign
test is defensible; judging spam is left to the LLM, which is good at it.

Global trending feeds (Bluesky `getTrends`, Google Trends country RSS) were tried
and **removed**: against "indie makers" they surfaced Taco Bell and the Pittsburgh
Pirates.

---

## 12. Degradation matrix

Nothing here hard-fails on a missing sidecar.

| Missing | Behaviour |
|---|---|
| No exemplars (fresh niche) | Generate on platform norms alone + a UI banner; logged |
| No KB articles | Prompt says "no platform-specific guidance available" |
| Trend/overlay source down | Skipped and **reported**, never fabricated |
| Embedding model unavailable | Hashtags fall back to source-confidence priors |
| LLM provider unconfigured | Hashtag suggester drops that source; generation surfaces a Settings nudge |
| Post deleted at snapshot time | **No row written** (absent ≠ zero) |
| Exemplar insert fails mid-refresh | Empty active pool (KB-only retrieval) rather than a stale one |
| Instance unreachable | Reported in `status`, never a 502 that reads as "app broken" |

---

## 13. Constants reference

| Constant | Value | Where |
|---|---|---|
| `SIMILARITY_WEIGHT` | 0.7 | generation.py / mastodon_post.py |
| `N_EXEMPLARS` | 5 | both |
| `N_KB_ARTICLES` | 3 | generation.py |
| `TARGET_POOL_SIZE` | 20 / **15** | refresh_exemplars / mastodon_post |
| `HALF_LIFE_DAYS` | 14.0 | both |
| `DEDUPE_THRESHOLD` | 0.85 | refresh_exemplars (Bluesky only) |
| `MIN_FOLLOWERS` | 200 / **50** | Bluesky / Mastodon |
| `MAX_CANDIDATE_AGE_DAYS` | 90 | refresh_exemplars |
| `BASELINE_WINDOW_DAYS` | 30 | refresh_exemplars |
| `MAX_POST_AGE` | 44h | ingest |
| `MIN_SETTLE_HOURS` | 24 | mastodon_post |
| `MAX_POSTS_PER_BUCKET` | 500 | snapshot |
| `KB_DECAY_FACTOR` / deactivate | 0.9 / 0.3 | cleanup |
| `SNAPSHOT_RETENTION_DAYS` | 90 | cleanup |
| `UNDERPERFORM_RATIO` | 0.8 | watchdog |
| `MIN_DATA_POINTS` | 5 | watchdog |
| `CLUSTER_THRESHOLD` | 0.55 | topics.py |
| `W_SUITABILITY/TREND/DATA` | 0.50 / 0.28 / 0.22 | hashtags.py |

---

## 14. Deliberate design refusals

Documented so they don't get "fixed" back in:

- **No fabricated numbers.** Unknown volume renders as "unknown", not 0. Topic
  Scout refused to port TrendScope's synthesised Reddit score.
- **No demo-data fallback.** Empty results + a source-health panel is the honest
  answer.
- **Absent beats zeroes** for deleted/unavailable posts, everywhere.
- **Sentiment never feeds momentum** (Topic Scout): momentum measures change, tone
  describes coverage; one number rising for both is uninterpretable.
- **Follower floor on baselines**, for the measured 0.20-vs-0.009 reason.
- **Diversity dedupe** on the Bluesky pool — omitting it collapses the pool.
- **Exemplars deactivated, never deleted** — a bad refresh stays auditable.
- **Empty pool preferred over stale pool** on partial refresh failure.
- **KB tags from content, not source feed.**
- **Not gated on Mastodon `indexable`** — would reject the whole network.
- **`vendor/socialpost` stays unmodified** — all Mastodon logic lives in
  `app/services/` + `app/routers/`, which is why the shared learning machinery
  needs no duplicate of itself.

---

## 15. Known gaps

- **Mean, not median, baselines** — outlier-sensitive for niches with a few very
  large accounts (flagged in-code).
- **English-only** — prompts and the embedding model; no language routing.
- **Mastodon pool has no diversity dedupe** — acceptable at pool size 15, but it
  can admit near-duplicates.
- **Selection bias is unfixable** from published-only outcomes; edit-distance is
  the partial mitigation.
- **X and LinkedIn are write-only** — no collection, no measurement, so drafts for
  them are grounded in Bluesky exemplars + norms, never in their own performance
  data.
- **No A/B mechanism** between generation configurations today; `PROMPT_VERSION`
  makes it *analysable* retrospectively but not *assignable*. (The fine-tune spec
  proposes one.)
