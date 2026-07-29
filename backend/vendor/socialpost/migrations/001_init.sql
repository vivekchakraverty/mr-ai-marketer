-- 001_init.sql — full schema for the self-evolving social post generator.
--
-- Apply by pasting into the Supabase SQL editor (Dashboard -> SQL Editor -> New
-- query -> Run). Idempotent: safe to re-run.
--
-- Design notes:
--   * Snapshots are append-only. A UNIQUE (post_uri, window_label) makes an
--     accidental re-capture fail loudly rather than silently rewrite history.
--   * Exemplars are deactivated, never deleted, so a bad refresh can be audited.
--   * All timestamps are timestamptz. Jobs write UTC.

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- niches
--
-- What the system watches. config/niches.yaml seeds this on first run and is
-- then only a template — this table is the runtime source of truth, so niches can
-- be managed from the UI without editing a file in the repo.
--
-- The name is denormalised onto posts, authors, exemplars, generations, and
-- performance_baselines.scope_key. It is therefore NOT safe to rename by hand;
-- use db.rename_niche(), which migrates all five.
-- ---------------------------------------------------------------------------
create table if not exists niches (
    name        text primary key,
    keywords    text[] not null default '{}',
    -- Deactivating stops collection without destroying the corpus. Deleting the
    -- row is a separate, explicit act (see forget/purge).
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists niches_active_idx on niches (active);

-- ---------------------------------------------------------------------------
-- tracked_authors
--
-- Accounts whose whole feed we collect, as opposed to whatever keyword search
-- happens to surface. Search is a SAMPLE — it returns what Bluesky's index feels
-- like returning for a keyword — whereas an author feed is a CENSUS of someone
-- who reliably performs in a niche. Both feed the same pipeline.
--
-- Deliberately no FK to authors(did): an author is tracked before their first
-- post is ingested, so the row would have nothing to reference. The flip side is
-- that forget.py must delete from here explicitly, or a forgotten author would be
-- silently re-collected on the next run.
-- ---------------------------------------------------------------------------
create table if not exists tracked_authors (
    id        bigserial primary key,
    did       text not null,
    handle    text,
    niche     text not null,
    active    boolean not null default true,
    added_at  timestamptz not null default now()
);

create unique index if not exists tracked_authors_did_niche_idx
    on tracked_authors (did, niche);
create index if not exists tracked_authors_niche_active_idx
    on tracked_authors (niche, active);

-- ---------------------------------------------------------------------------
-- authors
-- ---------------------------------------------------------------------------
create table if not exists authors (
    did             text primary key,
    handle          text,
    follower_count  int,
    niche           text,
    last_seen_at    timestamptz not null default now()
);

comment on column authors.follower_count is
    'Snapshot at last ingest. Denominator for engagement_rate; may be stale.';

-- ---------------------------------------------------------------------------
-- posts
-- ---------------------------------------------------------------------------
create table if not exists posts (
    uri          text primary key,
    platform     text not null default 'bluesky',
    author_did   text references authors (did) on delete cascade,
    text         text,
    hashtags     text[] not null default '{}',
    has_media    boolean not null default false,
    created_at   timestamptz,
    niche        text,
    ingested_at  timestamptz not null default now()
);

create index if not exists posts_niche_idx       on posts (niche);
create index if not exists posts_created_at_idx  on posts (created_at desc);
create index if not exists posts_author_did_idx  on posts (author_did);

-- snapshot.py scans for posts inside an age bucket; this index serves that scan.
create index if not exists posts_niche_created_at_idx on posts (niche, created_at desc);

-- ---------------------------------------------------------------------------
-- engagement_snapshots
-- ---------------------------------------------------------------------------
create table if not exists engagement_snapshots (
    id               bigserial primary key,
    post_uri         text not null references posts (uri) on delete cascade,
    captured_at      timestamptz not null default now(),
    window_label     text not null check (window_label in ('1h', '24h', '48h')),
    likes            int not null default 0,
    reposts          int not null default 0,
    replies          int not null default 0,
    engagement_rate  numeric,
    constraint engagement_snapshots_post_window_key unique (post_uri, window_label)
);

create index if not exists engagement_snapshots_post_uri_idx
    on engagement_snapshots (post_uri);
create index if not exists engagement_snapshots_window_captured_idx
    on engagement_snapshots (window_label, captured_at desc);

comment on column engagement_snapshots.engagement_rate is
    'Computed in Python as (likes+reposts+replies)/max(follower_count,1). Not a '
    'generated column: the follower count lives on authors and drifts over time, '
    'so we freeze the value that was true at capture.';

-- ---------------------------------------------------------------------------
-- exemplars
-- ---------------------------------------------------------------------------
create table if not exists exemplars (
    id            bigserial primary key,
    post_uri      text not null references posts (uri) on delete cascade,
    niche         text,
    score         numeric,
    embedding     vector(384),
    active        boolean not null default true,
    refreshed_at  timestamptz not null default now()
);

create index if not exists exemplars_niche_active_idx on exemplars (niche, active);

-- ivfflat needs lists tuned to row count; 100 is fine up to ~100k rows.
-- Cosine ops because embeddings.py L2-normalises, matching MiniLM's training.
create index if not exists exemplars_embedding_idx
    on exemplars using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ---------------------------------------------------------------------------
-- kb_articles
-- ---------------------------------------------------------------------------
create table if not exists kb_articles (
    id             bigserial primary key,
    source         text,
    url            text unique,
    url_hash       text,
    published_at   timestamptz,
    platform_tags  text[] not null default '{}',
    summary        text,
    version        int not null default 1,
    decay_weight   numeric not null default 1.0,
    active         boolean not null default true,
    ingested_at    timestamptz not null default now()
);

create index if not exists kb_articles_url_hash_idx on kb_articles (url_hash);
create index if not exists kb_articles_active_decay_idx
    on kb_articles (active, decay_weight desc);
create index if not exists kb_articles_platform_tags_idx
    on kb_articles using gin (platform_tags);

comment on column kb_articles.summary is
    'LLM-extracted actionable changes only. Items the LLM judged speculative are '
    'never inserted.';

-- ---------------------------------------------------------------------------
-- generations
-- ---------------------------------------------------------------------------
create table if not exists generations (
    id            bigserial primary key,
    created_at    timestamptz not null default now(),
    user_input    text,
    niche         text,
    output_text   text,
    exemplar_ids  bigint[] not null default '{}',
    kb_ids        bigint[] not null default '{}',
    -- Nullable: set by the user in Streamlit after they actually publish.
    -- ON DELETE SET NULL so forget.py can remove a post without losing the
    -- generation record.
    posted_uri    text references posts (uri) on delete set null
);

create index if not exists generations_posted_uri_idx on generations (posted_uri);
create index if not exists generations_niche_idx      on generations (niche);

-- ---------------------------------------------------------------------------
-- performance_baselines
-- ---------------------------------------------------------------------------
create table if not exists performance_baselines (
    id                   bigserial primary key,
    scope                text not null check (scope in ('niche', 'user')),
    scope_key            text not null,
    window_label         text not null check (window_label in ('1h', '24h', '48h')),
    avg_engagement_rate  numeric,
    computed_at          timestamptz not null default now()
);

-- One live baseline per (scope, key, window); refresh_exemplars upserts on this.
create unique index if not exists performance_baselines_scope_key
    on performance_baselines (scope, scope_key, window_label);

-- ---------------------------------------------------------------------------
-- job_runs
-- ---------------------------------------------------------------------------
create table if not exists job_runs (
    id           bigserial primary key,
    job_name     text not null,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    status       text check (status in ('success', 'failure', 'partial')),
    notes        text
);

create index if not exists job_runs_job_started_idx on job_runs (job_name, started_at desc);

-- ---------------------------------------------------------------------------
-- Pooled telemetry (opt-in; inert unless TELEMETRY_ENDPOINT is configured)
--
-- generations gains two columns rather than a new table: `uid` is a
-- non-enumerable id that correlates a generation with its later outcome ACROSS
-- instances without exposing the per-install serial, and `outcome_reported_at`
-- marks a generation whose 48h outcome has already been queued so the collector
-- does not double-report. `add column if not exists` keeps this idempotent on an
-- existing Supabase table; the SQLite backend guards the same add in db.py.
-- ---------------------------------------------------------------------------
alter table generations add column if not exists uid text;
alter table generations add column if not exists outcome_reported_at timestamptz;
create unique index if not exists generations_uid_idx on generations (uid);

-- The local send queue. Records are built locally and drained best-effort by the
-- telemetry job; nothing here blocks generation. payload is the exact JSON the
-- ingest Space will receive, so a human can read a row and know what was sent.
create table if not exists telemetry_outbox (
    id              bigserial primary key,
    kind            text not null check (kind in ('generation', 'outcome', 'delete_request')),
    payload         jsonb not null,
    created_at      timestamptz not null default now(),
    attempts        int not null default 0,
    last_attempt_at timestamptz,
    delivered_at    timestamptz
);

create index if not exists telemetry_outbox_undelivered_idx
    on telemetry_outbox (delivered_at, created_at)
    where delivered_at is null;

-- Consent is append-only for the audit trail: each acceptance is a row, and the
-- most recent one is authoritative. A user can raise or lower the content tier
-- over time and the history shows when. No row at all means "not yet consented".
create table if not exists telemetry_consent (
    id              bigserial primary key,
    consent_version int not null,
    content_opt_in  boolean not null default false,
    identity        text,
    accepted_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- RPC: exemplar retrieval
--
-- The supabase-py client cannot express a pgvector `<=>` ORDER BY, so retrieval
-- lives here. Returns cosine similarity (1 - distance) blended with the stored
-- performance score, so retrieval favours posts that are both on-topic and
-- proven. blend = similarity_weight*similarity + (1-similarity_weight)*norm_score
-- where norm_score is min-max normalised within the niche's active pool.
-- ---------------------------------------------------------------------------
create or replace function match_exemplars (
    query_embedding    vector(384),
    target_niche       text,
    match_count        int default 5,
    similarity_weight  numeric default 0.7
)
returns table (
    id          bigint,
    post_uri    text,
    text        text,
    score       numeric,
    similarity  numeric,
    blended     numeric
)
language sql
stable
as $$
    with pool as (
        select e.id, e.post_uri, p.text, e.score,
               (1 - (e.embedding <=> query_embedding))::numeric as similarity
        from exemplars e
        join posts p on p.uri = e.post_uri
        where e.niche = target_niche
          and e.active
          and e.embedding is not null
    ),
    bounds as (
        select min(score) as lo, max(score) as hi from pool
    )
    select pool.id,
           pool.post_uri,
           pool.text,
           pool.score,
           pool.similarity,
           (similarity_weight * pool.similarity
            + (1 - similarity_weight)
              * case
                    when bounds.hi is null or bounds.hi = bounds.lo then 0.5
                    else (pool.score - bounds.lo) / (bounds.hi - bounds.lo)
                end
           )::numeric as blended
    from pool, bounds
    order by blended desc
    limit match_count;
$$;

-- ---------------------------------------------------------------------------
-- RPC: 48h-snapshot scoring input for refresh_exemplars
--
-- Pulls each post's 48h engagement_rate alongside the post text and age in days,
-- which the job needs to apply recency decay. Doing the join here avoids paging
-- three tables through the REST client.
--
-- min_followers exists because engagement_rate divides by max(follower_count,1):
-- a 3-follower account with 6 replies scores 2.0, while an excellent post from a
-- 40k account scores ~0.01. Without a floor the exemplar pool fills with posts
-- that nobody actually saw. The floor is applied here rather than in
-- engagement_snapshots, which records the truth regardless.
--
-- Postgres overloads functions by signature, so `create or replace` alone would
-- leave an earlier 2-argument version of this function resident alongside the
-- new one and PostgREST could resolve to either. Drop it explicitly.
-- ---------------------------------------------------------------------------
drop function if exists exemplar_candidates(text, int);

create or replace function exemplar_candidates (
    target_niche   text,
    max_age_days   int default 90,
    min_followers  int default 200
)
returns table (
    post_uri         text,
    text             text,
    created_at       timestamptz,
    engagement_rate  numeric,
    follower_count   int,
    age_days         numeric
)
language sql
stable
as $$
    select p.uri,
           p.text,
           p.created_at,
           s.engagement_rate,
           a.follower_count,
           (extract(epoch from (now() - p.created_at)) / 86400.0)::numeric as age_days
    from posts p
    join engagement_snapshots s
      on s.post_uri = p.uri and s.window_label = '48h'
    join authors a
      on a.did = p.author_did
    where p.niche = target_niche
      and p.created_at > now() - (max_age_days || ' days')::interval
      and s.engagement_rate is not null
      and coalesce(a.follower_count, 0) >= min_followers
      and p.text is not null
      and length(trim(p.text)) > 0;
$$;

-- ---------------------------------------------------------------------------
-- RPC: watchdog comparison
--
-- Mean 48h engagement_rate of posts this system generated and the user published,
-- per niche, over a trailing window. Compared against performance_baselines.
-- ---------------------------------------------------------------------------
create or replace function generation_performance (
    lookback_days int default 28
)
returns table (
    niche            text,
    n                bigint,
    avg_engagement   numeric
)
language sql
stable
as $$
    select g.niche,
           count(*) as n,
           avg(s.engagement_rate)::numeric as avg_engagement
    from generations g
    join engagement_snapshots s
      on s.post_uri = g.posted_uri and s.window_label = '48h'
    where g.posted_uri is not null
      and s.captured_at > now() - (lookback_days || ' days')::interval
      and g.niche is not null
    group by g.niche;
$$;
