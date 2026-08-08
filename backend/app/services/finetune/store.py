"""Staging store for the fine-tune corpus.

A SEPARATE SQLite database from the live corpus, deliberately. Imported posts
must never enter the vendored `posts` / `engagement_snapshots` tables, for three
independent reasons:

  1. `engagement_snapshots.window_label` has a CHECK constraint permitting only
     '1h', '24h', '48h'. Dump posts are ~18 months old, so their current counts
     are *lifetime* engagement — there is no honest label for that in the live
     schema.
  2. `refresh_exemplars` would start selecting 18-month-old posts by strangers
     into the live exemplar pool, silently changing what grounds every draft.
  3. `performance_baselines` would then be computed over two different
     measurement semantics mixed together.

So: one file, one table, no foreign keys into anything live. Deleting
DATA_DIR/finetune/ removes every trace of this pipeline.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from ... import config

log = logging.getLogger(__name__)

FINETUNE_DIR = config.DATA_DIR / "finetune"
DB_PATH = FINETUNE_DIR / "corpus.sqlite3"
MANIFEST_PATH = FINETUNE_DIR / "manifest.json"

# Measurement semantics for the `measured_window` column. Kept explicit rather
# than implied by platform, because the whole point is that these two are NOT
# comparable and downstream code must be able to tell them apart.
WINDOW_LIFETIME = "lifetime"  # bluesky dump, re-hydrated ~18 months after posting
WINDOW_48H = "48h"  # mastodon, copied from our own measured corpus

SCHEMA = """
create table if not exists ft_posts (
    uri                      text primary key,
    platform                 text not null,
    text                     text not null,
    hashtags                 text not null default '[]',
    author_did               text,
    author_handle            text,
    follower_count           integer,
    created_at               text,
    has_media                integer default 0,

    -- populated by rehydrate.py (bluesky) or the mastodon importer
    likes                    integer,
    reposts                  integer,
    replies                  integer,
    lifetime_engagement_rate real,
    measured_window          text,
    measured_at              text,

    -- pipeline bookkeeping
    niche                    text,
    relevance                real,      -- embedding cosine vs the niche string
    status                   text not null default 'candidate',
                                        -- candidate | labelled | gone | rejected
    quality_tier             text,      -- top | mid | low   (stage 3)
    brief                    text,      -- stage 4
    split                    text       -- train | val | test (stage 6)
);

create index if not exists ft_posts_status_idx    on ft_posts (status);
create index if not exists ft_posts_platform_idx  on ft_posts (platform);
create index if not exists ft_posts_niche_idx     on ft_posts (niche);
create index if not exists ft_posts_author_idx    on ft_posts (author_did);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the staging DB, creating it and its schema on first use."""
    FINETUNE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def add_candidates(rows: Iterable[dict]) -> int:
    """Insert filtered dump rows as unlabelled candidates.

    `insert or ignore` on the URI primary key makes a re-run additive rather than
    duplicative, so an interrupted scan can simply be run again.
    """
    payload = [
        (
            r["uri"],
            r.get("platform", "bluesky"),
            r["text"],
            json.dumps(r.get("hashtags") or []),
            r.get("author_did"),
            r.get("author_handle"),
            r.get("created_at"),
            r.get("niche"),
            r.get("relevance"),
        )
        for r in rows
    ]
    if not payload:
        return 0
    with connect() as conn:
        cur = conn.executemany(
            """
            insert or ignore into ft_posts
                (uri, platform, text, hashtags, author_did, author_handle,
                 created_at, niche, relevance)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        return cur.rowcount


def pending_uris(platform: str = "bluesky", limit: int | None = None) -> list[str]:
    """Candidate URIs still awaiting an engagement label."""
    sql = "select uri from ft_posts where status = 'candidate' and platform = ?"
    params: list = [platform]
    if limit:
        sql += " limit ?"
        params.append(int(limit))
    with connect() as conn:
        return [r["uri"] for r in conn.execute(sql, params)]


def write_labels(rows: Iterable[dict]) -> int:
    """Attach engagement measurements to candidates. Marks them `labelled`."""
    now = iso(utcnow())
    payload = [
        (
            r["likes"],
            r["reposts"],
            r["replies"],
            r["lifetime_engagement_rate"],
            r.get("measured_window", WINDOW_LIFETIME),
            now,
            r.get("follower_count"),
            r.get("has_media", 0),
            json.dumps(r.get("hashtags") or []),
            r["uri"],
        )
        for r in rows
    ]
    if not payload:
        return 0
    with connect() as conn:
        cur = conn.executemany(
            """
            update ft_posts
               set likes = ?, reposts = ?, replies = ?,
                   lifetime_engagement_rate = ?, measured_window = ?,
                   measured_at = ?, follower_count = ?, has_media = ?,
                   hashtags = ?, status = 'labelled'
             where uri = ?
            """,
            payload,
        )
        return cur.rowcount


def mark_gone(uris: Iterable[str]) -> int:
    """Record posts the API would not return — deleted, suspended, or private.

    Marked, never labelled with zeroes. A zero row would read as a real post that
    flopped and would poison the quality tiers. Same 'absent beats zeroes' rule
    the live snapshot job follows.
    """
    uri_list = list(uris)
    if not uri_list:
        return 0
    with connect() as conn:
        cur = conn.executemany(
            "update ft_posts set status = 'gone' where uri = ?",
            [(u,) for u in uri_list],
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Reads / reporting
# ---------------------------------------------------------------------------


def counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "select status, platform, count(*) n from ft_posts group by status, platform"
        ).fetchall()
    return {f"{r['platform']}/{r['status']}": r["n"] for r in rows}


def label_stats(platform: str = "bluesky") -> dict:
    """Distribution of the engagement labels — the P0 sanity gate.

    Percentiles rather than just a mean, because engagement is heavy-tailed: a
    mean alone cannot distinguish 'plausible' from 'one viral post and 4,999
    zeroes'.
    """
    with connect() as conn:
        rates = [
            r["lifetime_engagement_rate"]
            for r in conn.execute(
                """
                select lifetime_engagement_rate from ft_posts
                 where status = 'labelled' and platform = ?
                   and lifetime_engagement_rate is not null
                 order by lifetime_engagement_rate
                """,
                (platform,),
            )
        ]
        totals = conn.execute(
            """
            select count(*) n,
                   sum(likes) likes, sum(reposts) reposts, sum(replies) replies,
                   sum(case when likes = 0 and reposts = 0 and replies = 0
                            then 1 else 0 end) silent
              from ft_posts where status = 'labelled' and platform = ?
            """,
            (platform,),
        ).fetchone()

    def pct(p: float) -> float | None:
        if not rates:
            return None
        return round(rates[min(int(len(rates) * p), len(rates) - 1)], 6)

    n = len(rates)
    return {
        "n": n,
        "min": round(rates[0], 6) if rates else None,
        "p25": pct(0.25),
        "median": pct(0.50),
        "p75": pct(0.75),
        "p90": pct(0.90),
        "p99": pct(0.99),
        "max": round(rates[-1], 6) if rates else None,
        "mean": round(sum(rates) / n, 6) if n else None,
        "zero_engagement": totals["silent"] or 0,
        "total_likes": totals["likes"] or 0,
        "total_reposts": totals["reposts"] or 0,
        "total_replies": totals["replies"] or 0,
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def update_manifest(**fields) -> dict:
    """Merge fields into the run manifest.

    The re-hydration date is load-bearing for reproducibility: engagement is
    time-dependent, so the same URIs re-hydrated next month yield different
    labels. Without the date the corpus cannot be reproduced or explained.
    """
    FINETUNE_DIR.mkdir(parents=True, exist_ok=True)
    current: dict = {}
    if MANIFEST_PATH.exists():
        try:
            current = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            log.warning("manifest unreadable; starting a fresh one")
    current.update(fields)
    current["updated_at"] = iso(utcnow())
    MANIFEST_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def dataset_revision(dataset_dir: str | Path) -> str:
    """The HF snapshot commit SHA, read from the local cache layout.

    Recorded in the manifest so the corpus is traceable to an exact dump revision
    — these datasets get amended and pulled.
    """
    path = Path(dataset_dir)
    snapshots = path / "snapshots"
    if snapshots.is_dir():
        entries = [p.name for p in snapshots.iterdir() if p.is_dir()]
        if entries:
            return entries[0]
    return "unknown"
