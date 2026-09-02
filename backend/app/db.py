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
    -- Set when the job was handed to the user's poster Space instead of the local
    -- scheduler. Holds what the Space reported back ("mastodon:<id>", "bluesky:<at-uri>").
    cloud_ref TEXT,
    cloud_enqueued_at TEXT,
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

-- Tracker Studio (the Manage screen). One row per sheet, holding that sheet's
-- rows as a JSON array — deliberately a document store rather than a column per
-- field. The two source workbooks address cells by letter (Daily Performance
-- runs A..W, and the influencer tracker leaves F/J/N/R empty on purpose), so
-- rows are kept keyed by those same letters. Mirroring ~90 spreadsheet columns
-- as SQL columns would buy nothing: nothing here is ever queried by field, the
-- whole sheet is read and written as a unit, and a schema migration would be
-- needed every time a source workbook grew a column.
CREATE TABLE IF NOT EXISTS tracker_docs (
    key TEXT PRIMARY KEY,
    doc TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- The Community section: a Telegram group the user owns, its paid tiers, and who is in
-- them. The bot cannot create the group (Telegram only lets a human do that), so
-- `chat_id` is filled in when the user adds the bot to a group they made and links it.
--
-- Money is recorded in Telegram Stars (XTR). Telegram requires digital goods to be sold in
-- Stars, and the Bot API reports amounts as integers in that currency, so there is no
-- fractional-currency rounding to get wrong here.
CREATE TABLE IF NOT EXISTS community_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    bot_token TEXT NOT NULL DEFAULT '',
    bot_username TEXT NOT NULL DEFAULT '',
    -- The open group: admins add people by hand, everyone in it sees everything posted.
    chat_id TEXT NOT NULL DEFAULT '',
    chat_title TEXT NOT NULL DEFAULT '',
    invite_link TEXT NOT NULL DEFAULT '',
    -- The paid channel. A second chat is not a design preference — a Telegram group shows
    -- every message to every member, with no per-post visibility, so "only paid members see
    -- this post" can only mean "this post is somewhere only paid members can be".
    gated_chat_id TEXT NOT NULL DEFAULT '',
    gated_chat_title TEXT NOT NULL DEFAULT '',
    gated_invite_link TEXT NOT NULL DEFAULT '',
    last_update_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS community_tiers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    stars INTEGER NOT NULL,
    period_days INTEGER NOT NULL DEFAULT 30,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- One row per Telegram user who has interacted. `expires_at` NULL means "never subscribed";
-- a past date means lapsed. Kept rather than deleted so a returning member keeps their
-- history and the join-request handler can tell a renewal from a first-time join.
CREATE TABLE IF NOT EXISTS community_members (
    telegram_id TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    tier_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT,
    in_group INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS community_payments (
    id TEXT PRIMARY KEY,
    telegram_id TEXT NOT NULL,
    tier_id TEXT,
    stars INTEGER NOT NULL,
    charge_id TEXT NOT NULL DEFAULT '',
    is_recurring INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_community_members_status
    ON community_members (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_community_payments_member
    ON community_payments (telegram_id, created_at);

-- Channels the user added themselves from the Activepieces piece catalogue, as opposed
-- to the ten that ship hardcoded with a hand-authored flow in resources/activepieces/flows.
-- input_map is JSON of {piece prop name: value}, where a value is either an Activepieces
-- template binding ("{{trigger.body.text}}") or a literal the user typed once at setup —
-- it is stored rather than derived because it is a user decision, not a fact about the piece.
CREATE TABLE IF NOT EXISTS custom_channels (
    channel TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    piece_name TEXT NOT NULL,
    piece_version TEXT NOT NULL,
    action_name TEXT NOT NULL,
    auth_type TEXT NOT NULL,
    input_map TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Which draft became which Library entry, so a post that actually goes out can be traced
-- back to the generation that wrote it.
--
-- WHY THIS EXISTS. The Social Post generator's learning loop only judges generations that
-- carry a `posted_uri`, and the only way to set one was a /published call the app never
-- made — 94 generations, none linked, so the snapshot job that runs hourly had nothing to
-- measure. Nothing in the two databases connected the pieces: the generation lives in the
-- vendored corpus DB and knows nothing about Library rows, while a distribution job knows
-- its Library row and its published post but not which draft produced the text.
--
-- This is that missing edge, and it is deliberately app-side: the vendored schema is
-- shared with the standalone collector and should not grow a column about Library entries.
-- `posted_uri` here is a local record of what was linked, so the sweep can tell a job it
-- has already handled from one it has not.
CREATE TABLE IF NOT EXISTS generation_links (
    library_item_id TEXT PRIMARY KEY,
    generation_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    niche TEXT NOT NULL,
    created_at TEXT NOT NULL,
    posted_uri TEXT,
    linked_at TEXT,
    -- Mastodon only: which server the draft was written for. A status id means nothing
    -- without the host that issued it, and the corpus key encodes the instance too.
    instance TEXT
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so columns added after
        # a release need widening explicitly or an upgraded install keeps the old shape.
        for column in ("gated_chat_id", "gated_chat_title", "gated_invite_link"):
            _ensure_column(conn, "community_config", column, "TEXT NOT NULL DEFAULT ''")
        # generation_links shipped in 0.7.11 without this; an install upgraded from it
        # keeps the old shape until the column is added.
        _ensure_column(conn, "generation_links", "instance", "TEXT")
        # Cloud posting shipped after distribution_jobs did.
        _ensure_column(conn, "distribution_jobs", "cloud_ref", "TEXT")
        _ensure_column(conn, "distribution_jobs", "cloud_enqueued_at", "TEXT")


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


def update_item(
    item_id: str,
    *,
    content: Optional[str] = None,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    output_path: Optional[str] = None,
):
    """Edit a saved item in place. Returns the updated row, or None if it is gone.

    Only the fields passed are touched, so an autosaving editor can send content alone
    without having to round-trip the title it is not changing.

    `created_at` is deliberately left as it was: it is when the thing was generated, and
    the Library is sorted by it. Bumping it on every keystroke-batch would make an item
    jump to the top of the shelf while its author was still typing into it.

    A file at output_path is not rewritten. That document belongs to the tool that
    produced it, and this column is the note about it — silently editing one to match the
    other would be a guess about which the user meant. Repointing the note *is* allowed,
    which is how a composition attaches its picture to the row its generation already
    filed; the previously named file is left on disk untouched.
    """
    sets: list[str] = []
    values: list[str] = []
    if content is not None:
        sets.append("content = ?")
        values.append(content)
    if title is not None:
        sets.append("title = ?")
        values.append(title)
    if subtitle is not None:
        sets.append("subtitle = ?")
        values.append(subtitle)
    if output_path is not None:
        sets.append("output_path = ?")
        values.append(output_path)
    if not sets:
        return get_item(item_id)

    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE library SET {', '.join(sets)} WHERE id = ?", (*values, item_id)
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM library WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def delete_item(item_id: str) -> None:
    """Remove one Library entry. The file at output_path, if any, is left alone — it is the
    user's document, and deleting the note about it should not delete it."""
    with _connect() as conn:
        conn.execute("DELETE FROM library WHERE id = ?", (item_id,))


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


def cancel_scheduled_distribution_job(job_id: str) -> Optional[dict]:
    """Cancel a job only while the scheduler still considers it pending.

    The status predicate makes cancellation atomic with
    :func:`claim_scheduled_distribution_job`: whichever update wins decides whether the
    post is cancelled or sent.  A plain read followed by ``update_distribution_job``
    would leave a window where the UI reports success while the scheduler publishes the
    same post.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE distribution_jobs SET status = 'cancelled', updated_at = ? "
            "WHERE id = ? AND status = 'scheduled'",
            (now, job_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM distribution_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def claim_scheduled_distribution_job(
    job_id: str, *, payload: Optional[str] = None
) -> Optional[dict]:
    """Atomically move one pending scheduled job into the sending state.

    ``list_due_scheduled_jobs`` is only a snapshot.  The job may be cancelled while its
    media is being prepared, so the scheduler must claim it with a conditional update
    immediately before triggering the external flow.
    """
    now = datetime.now(timezone.utc).isoformat()
    params = {"id": job_id, "updated_at": now}
    sets = ["status = 'sending'", "updated_at = :updated_at"]
    if payload is not None:
        sets.append("payload = :payload")
        params["payload"] = payload
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE distribution_jobs SET {', '.join(sets)} "
            "WHERE id = :id AND status = 'scheduled'",
            params,
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM distribution_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def fail_scheduled_distribution_job(job_id: str, error: str) -> Optional[dict]:
    """Record preparation failure without overwriting a concurrent cancellation."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE distribution_jobs SET status = 'failed', error = ?, updated_at = ? "
            "WHERE id = ? AND status = 'scheduled'",
            (error, now, job_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM distribution_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def update_distribution_job(job_id: str, **fields) -> Optional[dict]:
    if not fields:
        return get_distribution_job(job_id)
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{key} = :{key}" for key in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE distribution_jobs SET {set_clause} WHERE id = :id", {**fields, "id": job_id})
    return get_distribution_job(job_id)


def cancel_cloud_scheduled_distribution_job(job_id: str) -> Optional[dict]:
    """Cancel a job the poster Space owns. Conditional for the same reason its local twin is:
    the Space may claim it between the caller deciding and this running."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE distribution_jobs SET status = 'cancelled', updated_at = ? "
            "WHERE id = ? AND status = 'scheduled_cloud'",
            (now, job_id),
        )
        if cur.rowcount == 0:
            return None
    return get_distribution_job(job_id)


def list_cloud_pending_jobs(limit: int = 100) -> list[dict]:
    """Jobs the poster Space owns and has not reported back on yet."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM distribution_jobs WHERE status = 'scheduled_cloud' "
            "ORDER BY scheduled_at LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


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


# --- Tracker Studio (Manage screen) ---------------------------------------


def get_tracker_docs() -> dict:
    """Every stored sheet, keyed by sheet id. Empty dict on a fresh install."""
    with _connect() as conn:
        rows = conn.execute("SELECT key, doc FROM tracker_docs").fetchall()
    out: dict = {}
    for row in rows:
        try:
            out[row["key"]] = json.loads(row["doc"])
        except json.JSONDecodeError:
            # A corrupt row shouldn't take the whole screen down — the router
            # re-seeds anything missing, so skipping it degrades to defaults.
            continue
    return out


def put_tracker_docs(docs: dict) -> None:
    """Upsert the given sheets. Keys absent from `docs` are left untouched."""
    if not docs:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {"key": key, "doc": json.dumps(value, ensure_ascii=False), "updated_at": now}
        for key, value in docs.items()
    ]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO tracker_docs (key, doc, updated_at) VALUES (:key, :doc, :updated_at) "
            "ON CONFLICT(key) DO UPDATE SET doc = excluded.doc, updated_at = excluded.updated_at",
            rows,
        )


def clear_tracker_docs() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM tracker_docs")


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


# --- custom distribution channels ------------------------------------------


def add_custom_channel(
    channel: str,
    label: str,
    piece_name: str,
    piece_version: str,
    action_name: str,
    auth_type: str,
    input_map: dict,
) -> dict:
    """Records a channel the user built from the piece catalogue.

    Upserts on the channel key so re-adding the same platform edits it in place rather
    than failing — the flow and connection in Activepieces are keyed by that same string,
    so a second row could never have meant a second channel anyway.
    """
    row = {
        "channel": channel,
        "label": label,
        "piece_name": piece_name,
        "piece_version": piece_version,
        "action_name": action_name,
        "auth_type": auth_type,
        "input_map": json.dumps(input_map, ensure_ascii=False),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO custom_channels "
            "(channel, label, piece_name, piece_version, action_name, auth_type, input_map, created_at) "
            "VALUES (:channel, :label, :piece_name, :piece_version, :action_name, :auth_type, :input_map, :created_at) "
            "ON CONFLICT(channel) DO UPDATE SET "
            "label = excluded.label, "
            "piece_name = excluded.piece_name, "
            "piece_version = excluded.piece_version, "
            "action_name = excluded.action_name, "
            "auth_type = excluded.auth_type, "
            "input_map = excluded.input_map",
            row,
        )
    return _decode_custom_channel(row)


def _decode_custom_channel(row: dict) -> dict:
    out = dict(row)
    out["input_map"] = json.loads(out["input_map"]) if isinstance(out["input_map"], str) else out["input_map"]
    return out


def list_custom_channels() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM custom_channels ORDER BY created_at").fetchall()
    return [_decode_custom_channel(dict(row)) for row in rows]


def get_custom_channel(channel: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM custom_channels WHERE channel = ?", (channel,)).fetchone()
    return _decode_custom_channel(dict(row)) if row else None


def delete_custom_channel(channel: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM custom_channels WHERE channel = ?", (channel,))


# --- Community (Telegram group, tiers, members, payments) -------------------


def get_community_config() -> dict:
    """The single config row, created empty on first read so callers never see None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM community_config WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO community_config (id, updated_at) VALUES (1, ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            row = conn.execute("SELECT * FROM community_config WHERE id = 1").fetchone()
        return dict(row)


def update_community_config(**fields) -> dict:
    allowed = {
        "bot_token", "bot_username", "chat_id", "chat_title", "invite_link", "last_update_id",
        "gated_chat_id", "gated_chat_title", "gated_invite_link",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    get_community_config()  # ensure the row exists
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = :{k}" for k in updates)
        with _connect() as conn:
            conn.execute(f"UPDATE community_config SET {sets} WHERE id = 1", updates)
    return get_community_config()


def list_community_tiers(active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM community_tiers"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY stars"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def save_community_tier(name: str, stars: int, description: str = "",
                        period_days: int = 30, tier_id: str | None = None,
                        active: bool = True) -> dict:
    row = {
        "id": tier_id or str(uuid.uuid4()),
        "name": name,
        "description": description,
        "stars": int(stars),
        "period_days": int(period_days),
        "active": 1 if active else 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO community_tiers (id, name, description, stars, period_days, active, created_at) "
            "VALUES (:id, :name, :description, :stars, :period_days, :active, :created_at) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, description = excluded.description, "
            "stars = excluded.stars, period_days = excluded.period_days, active = excluded.active",
            row,
        )
    return row


def delete_community_tier(tier_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM community_tiers WHERE id = ?", (tier_id,))


def upsert_community_member(telegram_id: str, **fields) -> dict:
    """Records or updates a member. Never deletes: a lapsed member's history is what tells
    a renewal apart from a first-time join when they next knock."""
    allowed = {"username", "first_name", "tier_id", "status", "expires_at", "in_group", "joined_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO community_members (telegram_id, updated_at) VALUES (?, ?) "
            "ON CONFLICT(telegram_id) DO NOTHING",
            (str(telegram_id), now),
        )
        if updates:
            updates["telegram_id"] = str(telegram_id)
            updates["updated_at"] = now
            sets = ", ".join(f"{k} = :{k}" for k in updates if k not in ("telegram_id",))
            conn.execute(
                f"UPDATE community_members SET {sets} WHERE telegram_id = :telegram_id", updates
            )
        row = conn.execute(
            "SELECT * FROM community_members WHERE telegram_id = ?", (str(telegram_id),)
        ).fetchone()
    return dict(row)


def get_community_member(telegram_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM community_members WHERE telegram_id = ?", (str(telegram_id),)
        ).fetchone()
    return dict(row) if row else None


def list_community_members(limit: int = 500) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM community_members ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_expired_members() -> list[dict]:
    """Active members whose subscription has run out — the removal queue."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM community_members WHERE status = 'active' "
            "AND expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_community_payment(telegram_id: str, stars: int, tier_id: str | None,
                          charge_id: str = "", is_recurring: bool = False) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "telegram_id": str(telegram_id),
        "tier_id": tier_id,
        "stars": int(stars),
        "charge_id": charge_id,
        "is_recurring": 1 if is_recurring else 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO community_payments (id, telegram_id, tier_id, stars, charge_id, is_recurring, created_at) "
            "VALUES (:id, :telegram_id, :tier_id, :stars, :charge_id, :is_recurring, :created_at)",
            row,
        )
    return row


def community_revenue() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COALESCE(SUM(stars), 0) FROM community_payments").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM community_payments").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM community_members WHERE status = 'active'"
        ).fetchone()[0]
    return {"totalStars": total, "payments": count, "activeMembers": active}


# --- generation links ------------------------------------------------------
# See the generation_links comment in the schema above for why this edge exists.


def record_generation_link(
    library_item_id: str,
    generation_id: int,
    platform: str,
    niche: str,
    instance: str = "",
) -> None:
    """Remember which draft a Library entry came from.

    Written at generation time because that is the only moment both ids are in the same
    place. INSERT OR REPLACE rather than INSERT: regenerating into the same entry is the
    normal way to work, and the newest draft is the one that will be published.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO generation_links "
            "(library_item_id, generation_id, platform, niche, created_at, posted_uri, "
            "linked_at, instance) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
            (
                library_item_id,
                int(generation_id),
                platform,
                niche,
                datetime.now(timezone.utc).isoformat(),
                instance,
            ),
        )


def unlinked_generation_links(platform: str, limit: int = 25) -> list[dict]:
    """Drafts whose published post has not been traced back to them yet.

    Only those with a distribution job that actually went out: a draft nobody sent has
    nothing to link, and would otherwise be re-examined on every sweep forever.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT g.*, j.id AS job_id, j.activepieces_run_id "
            "FROM generation_links g "
            "JOIN distribution_jobs j ON j.library_item_id = g.library_item_id "
            "WHERE g.posted_uri IS NULL AND g.platform = ? "
            "  AND j.channel = ? AND j.status = 'sent' "
            "ORDER BY j.updated_at DESC LIMIT ?",
            (platform, platform, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_generation_linked(library_item_id: str, posted_uri: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE generation_links SET posted_uri = ?, linked_at = ? WHERE library_item_id = ?",
            (posted_uri, datetime.now(timezone.utc).isoformat(), library_item_id),
        )


def delete_superseded_companion_images(
    output_path: str, keep_id: str, subtitles: set[str]
) -> int:
    """Remove auto-filed image rows that a composition has just taken into itself.

    A companion image files its own row the moment it is drawn, because until a post is
    finished that row is the only place it lives. Once the finished post carries the same
    file, that row is a second card for one picture — the exact fragmentation the
    composition save exists to end.

    Matched on the file AND on the subtitles the companion writer uses, never on the file
    alone: a Brand Studio asset picked into a post is still an asset in its own right, and
    deleting it because a post borrowed it would lose something nobody replaced.
    """
    if not output_path or not subtitles:
        return 0
    placeholders = ",".join("?" for _ in subtitles)
    with _connect() as conn:
        cursor = conn.execute(
            f"DELETE FROM library WHERE output_path = ? AND id <> ? "
            f"AND subtitle IN ({placeholders})",
            (output_path, keep_id, *sorted(subtitles)),
        )
        return cursor.rowcount
