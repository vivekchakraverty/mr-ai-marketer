-- 001_init_sqlite.sql — local-mode schema. Mirror of 001_init.sql for SQLite.
--
-- You do not run this by hand. src/db.py applies it automatically the first time
-- it opens the database file when DB_BACKEND=sqlite. It is idempotent.
--
-- Kept deliberately in lockstep with migrations/001_init.sql: same tables, same
-- columns, same constraints. If you change one, change the other, or the two
-- backends drift and the parity tests will (correctly) start failing.
--
-- Type mapping and why:
--   timestamptz  -> TEXT     ISO-8601 UTC strings from db.iso(). Lexicographic
--                            ordering is correct for this format, so >/< in SQL
--                            work without any date parsing. Verified in tests.
--   text[]       -> TEXT     JSON arrays. Queried with json_each().
--   vector(384)  -> BLOB     Raw float32 bytes, read back with np.frombuffer.
--                            No sqlite-vec: measured, numpy brute force is ~20x
--                            faster at this scale (20 active exemplars/niche) and
--                            avoids an extension that many system Pythons cannot
--                            load at all.
--   numeric      -> REAL     Callers already coerce with float().
--   boolean      -> INTEGER  0/1; the client shim marshals back to True/False.
--   bigserial    -> INTEGER PRIMARY KEY AUTOINCREMENT
--
-- Postgres DEFAULT now() has no SQLite equivalent that produces our exact
-- format, so every default timestamp is supplied by Python instead. That is
-- already how the jobs behave — they pass iso(utcnow()) explicitly.

-- ---------------------------------------------------------------------------
-- niches
--
-- Mirror of the Postgres table. See migrations/001_init.sql for the rationale:
-- config/niches.yaml seeds this once, then this table is the source of truth so
-- niches are manageable from the UI. Renaming must go through db.rename_niche().
-- ---------------------------------------------------------------------------
create table if not exists niches (
    name        text primary key,
    keywords    text not null default '[]',   -- JSON array
    active      integer not null default 1,
    created_at  text not null,
    updated_at  text not null
);

create index if not exists niches_active_idx on niches (active);

-- ---------------------------------------------------------------------------
-- tracked_authors — mirror of the table in 001_init.sql.
-- ---------------------------------------------------------------------------
create table if not exists tracked_authors (
    id        integer primary key autoincrement,
    did       text not null,
    handle    text,
    niche     text not null,
    active    integer not null default 1,
    added_at  text not null
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
    follower_count  integer,
    niche           text,
    last_seen_at    text not null
);

-- ---------------------------------------------------------------------------
-- posts
-- ---------------------------------------------------------------------------
create table if not exists posts (
    uri          text primary key,
    platform     text not null default 'bluesky',
    author_did   text references authors (did) on delete cascade,
    text         text,
    hashtags     text not null default '[]',   -- JSON array
    has_media    integer not null default 0,
    created_at   text,
    niche        text,
    ingested_at  text not null
);

create index if not exists posts_niche_idx             on posts (niche);
create index if not exists posts_created_at_idx        on posts (created_at desc);
create index if not exists posts_author_did_idx        on posts (author_did);
create index if not exists posts_niche_created_at_idx  on posts (niche, created_at desc);

-- ---------------------------------------------------------------------------
-- engagement_snapshots
-- ---------------------------------------------------------------------------
create table if not exists engagement_snapshots (
    id               integer primary key autoincrement,
    post_uri         text not null references posts (uri) on delete cascade,
    captured_at      text not null,
    window_label     text not null check (window_label in ('1h', '24h', '48h')),
    likes            integer not null default 0,
    reposts          integer not null default 0,
    replies          integer not null default 0,
    engagement_rate  real,
    -- Snapshots are append-only. This is the backstop that makes a double
    -- capture fail loudly instead of rewriting history.
    unique (post_uri, window_label)
);

create index if not exists engagement_snapshots_post_uri_idx
    on engagement_snapshots (post_uri);
create index if not exists engagement_snapshots_window_captured_idx
    on engagement_snapshots (window_label, captured_at desc);

-- ---------------------------------------------------------------------------
-- exemplars
-- ---------------------------------------------------------------------------
create table if not exists exemplars (
    id            integer primary key autoincrement,
    post_uri      text not null references posts (uri) on delete cascade,
    niche         text,
    score         real,
    embedding     blob,          -- float32 x 384
    active        integer not null default 1,
    refreshed_at  text not null
);

create index if not exists exemplars_niche_active_idx on exemplars (niche, active);

-- No ivfflat equivalent and none needed: match_exemplars scans one niche's
-- active pool (~20 rows) with numpy.

-- ---------------------------------------------------------------------------
-- kb_articles
-- ---------------------------------------------------------------------------
create table if not exists kb_articles (
    id             integer primary key autoincrement,
    source         text,
    url            text unique,
    url_hash       text,
    published_at   text,
    platform_tags  text not null default '[]',  -- JSON array
    summary        text,
    version        integer not null default 1,
    decay_weight   real not null default 1.0,
    active         integer not null default 1,
    ingested_at    text not null
);

create index if not exists kb_articles_url_hash_idx on kb_articles (url_hash);
create index if not exists kb_articles_active_decay_idx
    on kb_articles (active, decay_weight desc);

-- ---------------------------------------------------------------------------
-- generations
-- ---------------------------------------------------------------------------
-- uid and outcome_reported_at power pooled telemetry; see 001_init.sql. They are
-- in the CREATE for fresh installs; db.py's _ensure_sqlite_columns() adds them to
-- pre-telemetry databases, because SQLite has no ADD COLUMN IF NOT EXISTS.
create table if not exists generations (
    id                   integer primary key autoincrement,
    created_at           text not null,
    user_input           text,
    niche                text,
    output_text          text,
    exemplar_ids         text not null default '[]',   -- JSON array
    kb_ids               text not null default '[]',   -- JSON array
    posted_uri           text references posts (uri) on delete set null,
    uid                  text,
    outcome_reported_at  text
);

create index if not exists generations_posted_uri_idx on generations (posted_uri);
create index if not exists generations_niche_idx      on generations (niche);
-- The unique index on uid is created by db.py's _ensure_columns AFTER the column
-- is guaranteed to exist — an old database re-running this script would not have
-- the column yet, and CREATE INDEX on a missing column fails.

-- ---------------------------------------------------------------------------
-- performance_baselines
-- ---------------------------------------------------------------------------
create table if not exists performance_baselines (
    id                   integer primary key autoincrement,
    scope                text not null check (scope in ('niche', 'user')),
    scope_key            text not null,
    window_label         text not null check (window_label in ('1h', '24h', '48h')),
    avg_engagement_rate  real,
    computed_at          text not null
);

-- The upsert target for refresh_exemplars' baseline write.
create unique index if not exists performance_baselines_scope_key
    on performance_baselines (scope, scope_key, window_label);

-- ---------------------------------------------------------------------------
-- job_runs
-- ---------------------------------------------------------------------------
create table if not exists job_runs (
    id           integer primary key autoincrement,
    job_name     text not null,
    started_at   text not null,
    finished_at  text,
    status       text check (status in ('success', 'failure', 'partial')),
    notes        text
);

create index if not exists job_runs_job_started_idx on job_runs (job_name, started_at desc);

-- ---------------------------------------------------------------------------
-- Pooled telemetry — mirror of the tables in 001_init.sql.
-- ---------------------------------------------------------------------------
create table if not exists telemetry_outbox (
    id              integer primary key autoincrement,
    kind            text not null check (kind in ('generation', 'outcome', 'delete_request')),
    payload         text not null,                -- JSON string, sent verbatim
    created_at      text not null,
    attempts        integer not null default 0,
    last_attempt_at text,
    delivered_at    text
);

create index if not exists telemetry_outbox_undelivered_idx
    on telemetry_outbox (delivered_at, created_at);

create table if not exists telemetry_consent (
    id              integer primary key autoincrement,
    consent_version integer not null,
    content_opt_in  integer not null default 0,
    identity        text,
    accepted_at     text not null
);
