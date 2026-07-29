import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL,
    created_at TEXT NOT NULL,
    content TEXT,
    output_path TEXT
);

CREATE TABLE IF NOT EXISTS distribution_jobs (
    id TEXT PRIMARY KEY,
    library_item_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    activepieces_run_id TEXT,
    resume_url TEXT,
    error TEXT,
    scheduled_at TEXT,
    payload TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per email sent through either SMTP path (the Mail Composer in
-- Distribute, or the Lead Gen Agent's outreach) — `id` doubles as the public
-- pixel/click token embedded in the tracking Space's URLs.
CREATE TABLE IF NOT EXISTS mail_messages (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    message_id TEXT,
    to_addrs TEXT NOT NULL,
    cc_addrs TEXT,
    subject TEXT NOT NULL DEFAULT '',
    leadgen_deal_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

-- Opens/clicks synced from the tracking Space, plus bounces detected locally
-- (IMAP DSN polling or a synchronous SMTP rejection). dedupe_key guards all
-- three kinds against being recorded twice (leadgen's inbox poll in particular
-- re-evaluates the same inbox messages on every tick).
CREATE TABLE IF NOT EXISTS mail_events (
    id TEXT PRIMARY KEY,
    mail_message_id TEXT NOT NULL,
    type TEXT NOT NULL,
    url TEXT,
    detail TEXT,
    space_event_id INTEGER,
    dedupe_key TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mail_messages_source ON mail_messages (source, created_at);
CREATE INDEX IF NOT EXISTS idx_mail_messages_message_id ON mail_messages (message_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_events_dedupe ON mail_events (dedupe_key);
CREATE INDEX IF NOT EXISTS idx_mail_events_message ON mail_events (mail_message_id, occurred_at);

-- Bluesky public-performance analytics. Accounts are the selected or discovered
-- comparison cohort; posts and snapshots preserve the public growth curve.
CREATE TABLE IF NOT EXISTS bluesky_analytics_accounts (
    did TEXT PRIMARY KEY,
    handle TEXT NOT NULL,
    display_name TEXT NOT NULL,
    followers INTEGER NOT NULL DEFAULT 0,
    niche TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'selected',
    active INTEGER NOT NULL DEFAULT 1,
    is_owner INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bluesky_analytics_posts (
    uri TEXT PRIMARY KEY,
    cid TEXT NOT NULL,
    author_did TEXT NOT NULL,
    author_handle TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    niche TEXT NOT NULL,
    is_reply INTEGER NOT NULL DEFAULT 0,
    has_media INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bluesky_analytics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_uri TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    likes INTEGER NOT NULL DEFAULT 0,
    reposts INTEGER NOT NULL DEFAULT 0,
    replies INTEGER NOT NULL DEFAULT 0,
    quotes INTEGER NOT NULL DEFAULT 0,
    followers INTEGER NOT NULL DEFAULT 0,
    UNIQUE(post_uri, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_bsky_analytics_accounts_active
    ON bluesky_analytics_accounts (active, niche);
CREATE INDEX IF NOT EXISTS idx_bsky_analytics_posts_niche_created
    ON bluesky_analytics_posts (niche, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bsky_analytics_snapshots_post_captured
    ON bluesky_analytics_snapshots (post_uri, captured_at DESC);

-- The Mastodon Post Creator's rules gate. One row per instance the user has read
-- and accepted the published rules of. `policy_hash` (not a boolean) is the point:
-- it fingerprints the exact rule text that was shown, so when an instance edits
-- its rules the acceptance stops matching and the gate closes again rather than
-- silently carrying consent forward to wording nobody has read.
CREATE TABLE IF NOT EXISTS mastodon_rule_acks (
    instance TEXT PRIMARY KEY,
    policy_hash TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);

-- Mastodon corpus identifiers that the vendored socialpost `posts` table has no
-- column for. Its rows are keyed by a single `uri`, and we key Mastodon posts as
-- mastodon://<host>/<status_id> so engagement can be re-read later — but that
-- leaves nowhere to keep the human-facing permalink, which the provenance panel
-- needs. Kept here rather than by widening the vendored schema, which is
-- deliberately the standalone project's own.
CREATE TABLE IF NOT EXISTS mastodon_post_meta (
    post_uri TEXT PRIMARY KEY,
    instance TEXT NOT NULL,
    status_id TEXT NOT NULL,
    web_url TEXT NOT NULL DEFAULT '',
    account_acct TEXT NOT NULL DEFAULT ''
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    # Generous busy timeout: the distribution scheduler thread and FastAPI request threads
    # each open their own short-lived connections, and a write collision should wait for
    # the lock rather than surface "database is locked" to the caller.
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_item(tool: str, title: str, subtitle: str, content: Optional[str] = None, output_path: Optional[str] = None) -> dict:
    item = {
        "id": str(uuid.uuid4()),
        "tool": tool,
        "title": title or "Untitled",
        "subtitle": subtitle,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content": content,
        "output_path": output_path,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO library (id, tool, title, subtitle, created_at, content, output_path) "
            "VALUES (:id, :tool, :title, :subtitle, :created_at, :content, :output_path)",
            item,
        )
    return item


def list_items(limit: int = 70) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM library ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_item(item_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM library WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def count_items() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM library").fetchone()[0]


def add_distribution_job(
    library_item_id: str,
    channel: str,
    status: str,
    activepieces_run_id: Optional[str] = None,
    resume_url: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    payload: Optional[str] = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "id": str(uuid.uuid4()),
        "library_item_id": library_item_id,
        "channel": channel,
        "status": status,
        "activepieces_run_id": activepieces_run_id,
        "resume_url": resume_url,
        "error": None,
        "scheduled_at": scheduled_at,
        "payload": payload,
        "created_at": now,
        "updated_at": now,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO distribution_jobs (id, library_item_id, channel, status, activepieces_run_id, resume_url, error, scheduled_at, payload, created_at, updated_at) "
            "VALUES (:id, :library_item_id, :channel, :status, :activepieces_run_id, :resume_url, :error, :scheduled_at, :payload, :created_at, :updated_at)",
            job,
        )
    return job


def list_due_scheduled_jobs() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM distribution_jobs WHERE status = 'scheduled' AND scheduled_at <= ?", (now,)
        ).fetchall()
        return [dict(row) for row in rows]


def update_distribution_job(job_id: str, **fields) -> Optional[dict]:
    if not fields:
        return get_distribution_job(job_id)
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{key} = :{key}" for key in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE distribution_jobs SET {set_clause} WHERE id = :id", {**fields, "id": job_id})
    return get_distribution_job(job_id)


def get_distribution_job(job_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_distribution_jobs(status: Optional[str] = None, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM distribution_jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM distribution_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


# --- Mail tracking (opens/clicks/bounces for both SMTP send paths) ---------


def add_mail_message(
    source: str,
    to_addrs: list[str],
    subject: str,
    cc_addrs: Optional[list[str]] = None,
    leadgen_deal_id: Optional[str] = None,
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "source": source,
        "message_id": None,
        "to_addrs": json.dumps(to_addrs),
        "cc_addrs": json.dumps(cc_addrs) if cc_addrs else None,
        "subject": subject or "",
        "leadgen_deal_id": leadgen_deal_id,
        "status": "pending",
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sent_at": None,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mail_messages "
            "(id, source, message_id, to_addrs, cc_addrs, subject, leadgen_deal_id, status, error, created_at, sent_at) "
            "VALUES (:id, :source, :message_id, :to_addrs, :cc_addrs, :subject, :leadgen_deal_id, :status, :error, :created_at, :sent_at)",
            row,
        )
    return row


def update_mail_message(mail_message_id: str, **fields) -> Optional[dict]:
    if not fields:
        return get_mail_message(mail_message_id)
    set_clause = ", ".join(f"{key} = :{key}" for key in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE mail_messages SET {set_clause} WHERE id = :id", {**fields, "id": mail_message_id}
        )
    return get_mail_message(mail_message_id)


def get_mail_message(mail_message_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM mail_messages WHERE id = ?", (mail_message_id,)).fetchone()
        return dict(row) if row else None


def get_mail_message_by_message_id(message_id: str) -> Optional[dict]:
    if not message_id:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM mail_messages WHERE message_id = ?", (message_id,)).fetchone()
        return dict(row) if row else None


def add_mail_event(
    mail_message_id: str,
    event_type: str,
    dedupe_key: str,
    url: Optional[str] = None,
    detail: Optional[str] = None,
    space_event_id: Optional[int] = None,
    occurred_at: Optional[str] = None,
) -> Optional[dict]:
    """Insert one open/click/bounce event. Returns None (and inserts nothing) if
    `dedupe_key` was already recorded — callers use that to count only genuinely
    new events (e.g. the Space-sync loop, or leadgen's inbox poll re-scanning the
    same messages every tick)."""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "mail_message_id": mail_message_id,
        "type": event_type,
        "url": url,
        "detail": detail,
        "space_event_id": space_event_id,
        "dedupe_key": dedupe_key,
        "occurred_at": occurred_at or now,
        "created_at": now,
    }
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO mail_events "
            "(id, mail_message_id, type, url, detail, space_event_id, dedupe_key, occurred_at, created_at) "
            "VALUES (:id, :mail_message_id, :type, :url, :detail, :space_event_id, :dedupe_key, :occurred_at, :created_at)",
            row,
        )
        if cur.rowcount == 0:
            return None
    return row


def max_space_event_id() -> int:
    """The sync cursor: the highest Space-assigned event id already ingested."""
    with _connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(space_event_id), 0) AS m FROM mail_events").fetchone()
        return row["m"] if row else 0


def list_mail_messages(source: Optional[str] = None, limit: int = 100) -> list[dict]:
    query = (
        "SELECT m.*, "
        "SUM(CASE WHEN e.type='open' THEN 1 ELSE 0 END) AS opens, "
        "SUM(CASE WHEN e.type='click' THEN 1 ELSE 0 END) AS clicks, "
        "SUM(CASE WHEN e.type='bounce' THEN 1 ELSE 0 END) AS bounces "
        "FROM mail_messages m LEFT JOIN mail_events e ON e.mail_message_id = m.id "
    )
    params: list = []
    if source:
        query += "WHERE m.source = ? "
        params.append(source)
    query += "GROUP BY m.id ORDER BY COALESCE(m.sent_at, m.created_at) DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


# --- Mastodon Post Creator (rules gate + corpus identifiers) ---------------


def get_mastodon_ack(instance: str) -> Optional[dict]:
    """The stored acceptance for an instance, or None if it has never been given."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM mastodon_rule_acks WHERE instance = ?", (instance,)
        ).fetchone()
        return dict(row) if row else None


def record_mastodon_ack(instance: str, policy_hash: str, policy: dict) -> dict:
    """Record that the user read and accepted this instance's published rules.

    Stores the policy verbatim alongside its hash so there is an auditable record
    of what was actually on screen when they clicked accept — "I agreed to
    something" is not a useful thing to have kept.
    """
    row = {
        "instance": instance,
        "policy_hash": policy_hash,
        "policy_json": json.dumps(policy, ensure_ascii=False),
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mastodon_rule_acks (instance, policy_hash, policy_json, accepted_at) "
            "VALUES (:instance, :policy_hash, :policy_json, :accepted_at) "
            "ON CONFLICT(instance) DO UPDATE SET "
            "policy_hash = excluded.policy_hash, "
            "policy_json = excluded.policy_json, "
            "accepted_at = excluded.accepted_at",
            row,
        )
    return row


def clear_mastodon_ack(instance: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM mastodon_rule_acks WHERE instance = ?", (instance,))


def list_mastodon_acks() -> list[dict]:
    """Every instance whose rules have been accepted.

    This is what the background learning loop iterates: an instance the user has
    never approved must not be touched by a timer any more than by a button, so
    the acceptance table doubles as the loop's allowlist.
    """
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM mastodon_rule_acks ORDER BY instance").fetchall()
        return [dict(row) for row in rows]


def upsert_mastodon_post_meta(rows: list[dict]) -> int:
    """Remember the permalink + handle behind each mastodon:// corpus URI."""
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO mastodon_post_meta (post_uri, instance, status_id, web_url, account_acct) "
            "VALUES (:post_uri, :instance, :status_id, :web_url, :account_acct) "
            "ON CONFLICT(post_uri) DO UPDATE SET "
            "web_url = excluded.web_url, account_acct = excluded.account_acct",
            rows,
        )
    return len(rows)


def get_mastodon_post_meta(post_uris: list[str]) -> dict[str, dict]:
    """Metadata for the given corpus URIs, keyed by URI. Missing ones are absent."""
    if not post_uris:
        return {}
    out: dict[str, dict] = {}
    with _connect() as conn:
        # Chunked because SQLite caps a statement at 999 bound parameters and an
        # exemplar/provenance lookup is otherwise unbounded in principle.
        for i in range(0, len(post_uris), 500):
            chunk = post_uris[i : i + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT * FROM mastodon_post_meta WHERE post_uri IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                out[row["post_uri"]] = dict(row)
    return out


def mail_tracking_stats(source: Optional[str] = None) -> dict:
    query = (
        "SELECT COUNT(DISTINCT m.id) AS sent, "
        "COUNT(DISTINCT CASE WHEN e.type='open' THEN m.id END) AS opened, "
        "COUNT(DISTINCT CASE WHEN e.type='click' THEN m.id END) AS clicked, "
        "COUNT(DISTINCT CASE WHEN e.type='bounce' THEN m.id END) AS bounced "
        "FROM mail_messages m LEFT JOIN mail_events e ON e.mail_message_id = m.id "
    )
    params: list = []
    if source:
        query += "WHERE m.source = ? "
        params.append(source)
    with _connect() as conn:
        row = conn.execute(query, params).fetchone()

    sent = row["sent"] or 0
    opened = row["opened"] or 0
    clicked = row["clicked"] or 0
    bounced = row["bounced"] or 0

    def rate(n: int) -> float:
        return round(n / sent, 4) if sent else 0.0

    return {
        "sent": sent,
        "opened": opened,
        "clicked": clicked,
        "bounced": bounced,
        "openRate": rate(opened),
        "clickRate": rate(clicked),
        "bounceRate": rate(bounced),
    }
