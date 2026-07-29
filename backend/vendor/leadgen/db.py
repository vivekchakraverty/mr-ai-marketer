"""SQLite storage for the Lead Gen Agent.

One `leadgen.sqlite3` under the resolved data dir (see config.data_dir). Faithful to
OpenOutreach's Campaign / Lead / Deal / ChatMessage / Task model, adapted to this app's
raw-sqlite3 + context-manager style (mirrors app/db.py). Embeddings are stored as float32
BLOBs and rehydrated into numpy for the Gaussian Process qualifier.

Everything returns plain dicts (sqlite3.Row -> dict); the FastAPI router shapes those into
Pydantic responses, exactly like the rest of this app.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from . import config

# Deal-state machine (faithful to OpenOutreach, plus DRAFTED as the review-queue gate).
STATE_NEW = "NEW"
STATE_QUALIFYING = "QUALIFYING"
STATE_QUALIFIED = "QUALIFIED"
STATE_READY_TO_FIND_EMAIL = "READY_TO_FIND_EMAIL"
STATE_FINDING_EMAIL = "FINDING_EMAIL"
STATE_READY_TO_EMAIL = "READY_TO_EMAIL"
STATE_DRAFTED = "DRAFTED"
STATE_EMAILED = "EMAILED"
STATE_REPLIED = "REPLIED"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"

DEAL_STATES = [
    STATE_NEW,
    STATE_QUALIFYING,
    STATE_QUALIFIED,
    STATE_READY_TO_FIND_EMAIL,
    STATE_FINDING_EMAIL,
    STATE_READY_TO_EMAIL,
    STATE_DRAFTED,
    STATE_EMAILED,
    STATE_REPLIED,
    STATE_COMPLETED,
    STATE_FAILED,
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    product_description TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 0,
    auto_send INTEGER NOT NULL DEFAULT 0,
    use_bluesky INTEGER NOT NULL DEFAULT 0,
    daily_cap INTEGER NOT NULL DEFAULT 20,
    active_hours_start INTEGER NOT NULL DEFAULT 9,
    active_hours_end INTEGER NOT NULL DEFAULT 18,
    clauses TEXT,               -- json: the discovery clause pool (families -> values)
    model_blob BLOB,            -- joblib-compressed per-campaign Gaussian Process
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_queries (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    backend TEXT NOT NULL,               -- overpass | searxng
    clause_key TEXT NOT NULL,            -- stable hash of the clause set
    clauses TEXT NOT NULL,               -- json
    status TEXT NOT NULL DEFAULT 'active', -- active | empty | exhausted
    offset INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, clause_key)
);

CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    contact_name TEXT,
    title TEXT,
    domain TEXT,
    website TEXT,
    email TEXT,
    email_source TEXT,                   -- scraped | guessed
    region TEXT,
    source TEXT NOT NULL DEFAULT '',     -- overpass | searxng
    source_url TEXT,
    profile_text TEXT NOT NULL DEFAULT '',
    embedding BLOB,                      -- float32 bytes, 384-dim
    label TEXT,                          -- positive | negative | skipped
    gp_score REAL,
    discovered_by_query_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, domain)
);

CREATE TABLE IF NOT EXISTS deals (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    lead_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'NEW',
    outcome TEXT,
    reason TEXT,
    next_follow_up_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL,
    direction TEXT NOT NULL,             -- out | in
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    message_id TEXT,
    in_reply_to TEXT,
    refs TEXT,                           -- References header
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_drafts (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- opener | follow_up
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    predicted_click_rate REAL,
    ctr_bucket TEXT,
    status TEXT NOT NULL DEFAULT 'draft', -- draft | approved | sent | discarded
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS suppression_list (
    email TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_campaign_label ON leads (campaign_id, label);
CREATE INDEX IF NOT EXISTS idx_deals_campaign_state ON deals (campaign_id, state);
CREATE INDEX IF NOT EXISTS idx_chat_deal ON chat_messages (deal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON outreach_drafts (status, created_at);
"""

# The pipeline needs no separate task queue: every handler is an idempotent "act on the
# oldest deal in state X" operation, so all resumable state already lives in `deals`/`leads`
# (and follow-up timing in deals.next_follow_up_at). The daemon simply runs the handlers in
# priority order each tick.


def _db_path() -> str:
    return str(config.data_dir() / "leadgen.sqlite3")


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # Lightweight forward-migrations for columns added after a DB was first created
        # (CREATE TABLE IF NOT EXISTS won't add them to an existing table).
        _ensure_column(conn, "campaigns", "use_bluesky", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    # Generous busy timeout: the daemon thread and FastAPI request threads each open their
    # own short-lived connections; a write collision should wait for the lock rather than
    # surface "database is locked" to the caller.
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


def create_campaign(
    name: str,
    product_description: str,
    objective: str = "",
    country: str = "",
    daily_cap: int = 20,
    auto_send: bool = False,
    use_bluesky: bool = False,
    active_hours_start: int = 9,
    active_hours_end: int = 18,
) -> dict:
    now = _now()
    row = {
        "id": _new_id(),
        "name": name or "Untitled campaign",
        "product_description": product_description,
        "objective": objective,
        "country": country,
        "active": 0,
        "auto_send": 1 if auto_send else 0,
        "use_bluesky": 1 if use_bluesky else 0,
        "daily_cap": daily_cap,
        "active_hours_start": active_hours_start,
        "active_hours_end": active_hours_end,
        "clauses": None,
        "model_blob": None,
        "created_at": now,
        "updated_at": now,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, name, product_description, objective, country, active, "
            "auto_send, use_bluesky, daily_cap, active_hours_start, active_hours_end, clauses, "
            "model_blob, created_at, updated_at) VALUES (:id, :name, :product_description, "
            ":objective, :country, :active, :auto_send, :use_bluesky, :daily_cap, "
            ":active_hours_start, :active_hours_end, :clauses, :model_blob, :created_at, "
            ":updated_at)",
            row,
        )
    return get_campaign(row["id"])  # type: ignore[return-value]


def get_campaign(campaign_id: str) -> Optional[dict]:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return dict(r) if r else None


def list_campaigns() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def active_campaigns() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM campaigns WHERE active = 1").fetchall()
        return [dict(r) for r in rows]


def update_campaign(campaign_id: str, **fields) -> Optional[dict]:
    if not fields:
        return get_campaign(campaign_id)
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE campaigns SET {set_clause} WHERE id = :id", {**fields, "id": campaign_id}
        )
    return get_campaign(campaign_id)


def save_model_blob(campaign_id: str, blob: bytes) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE campaigns SET model_blob = ?, updated_at = ? WHERE id = ?",
            (blob, _now(), campaign_id),
        )


def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and everything under it. suppression_list is intentionally global
    and untouched (see PRIVACY.md)."""
    with _connect() as conn:
        deal_ids = [
            r["id"] for r in conn.execute("SELECT id FROM deals WHERE campaign_id = ?", (campaign_id,))
        ]
        for did in deal_ids:
            conn.execute("DELETE FROM chat_messages WHERE deal_id = ?", (did,))
            conn.execute("DELETE FROM outreach_drafts WHERE deal_id = ?", (did,))
        conn.execute("DELETE FROM deals WHERE campaign_id = ?", (campaign_id,))
        conn.execute("DELETE FROM leads WHERE campaign_id = ?", (campaign_id,))
        conn.execute("DELETE FROM discovery_queries WHERE campaign_id = ?", (campaign_id,))
        conn.execute("DELETE FROM tasks WHERE campaign_id = ?", (campaign_id,))
        conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


# ---------------------------------------------------------------------------
# Discovery queries
# ---------------------------------------------------------------------------


def upsert_query(campaign_id: str, backend: str, clause_key: str, clauses: dict) -> dict:
    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM discovery_queries WHERE campaign_id = ? AND clause_key = ?",
            (campaign_id, clause_key),
        ).fetchone()
        if existing:
            return dict(existing)
        row = {
            "id": _new_id(),
            "campaign_id": campaign_id,
            "backend": backend,
            "clause_key": clause_key,
            "clauses": json.dumps(clauses),
            "status": "active",
            "offset": 0,
            "last_run_at": None,
            "created_at": _now(),
        }
        conn.execute(
            "INSERT INTO discovery_queries (id, campaign_id, backend, clause_key, clauses, status, "
            "offset, last_run_at, created_at) VALUES (:id, :campaign_id, :backend, :clause_key, "
            ":clauses, :status, :offset, :last_run_at, :created_at)",
            row,
        )
        return row


def mark_query(query_id: str, *, status: str | None = None, offset: int | None = None) -> None:
    fields: dict[str, object] = {"last_run_at": _now()}
    if status is not None:
        fields["status"] = status
    if offset is not None:
        fields["offset"] = offset
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE discovery_queries SET {set_clause} WHERE id = :id", {**fields, "id": query_id}
        )


def recorded_empties(campaign_id: str) -> list[dict]:
    """Clause sets already known to match nobody — used for anti-monotone pruning."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT clauses FROM discovery_queries WHERE campaign_id = ? AND status = 'empty'",
            (campaign_id,),
        ).fetchall()
        return [json.loads(r["clauses"]) for r in rows]


def active_queries(campaign_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM discovery_queries WHERE campaign_id = ? AND status = 'active' "
            "ORDER BY last_run_at IS NOT NULL, last_run_at",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def query_keys(campaign_id: str) -> set[str]:
    """Every clause_key ever enqueued for a campaign (any status) — so the selector never
    re-runs a query it has already tried."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT clause_key FROM discovery_queries WHERE campaign_id = ?", (campaign_id,)
        ).fetchall()
        return {r["clause_key"] for r in rows}


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


def upsert_lead(campaign_id: str, *, domain: str | None, **fields) -> Optional[dict]:
    """Insert a lead, deduped by (campaign, domain). Returns the row, or None if a lead for
    that domain already exists (deduped away)."""
    with _connect() as conn:
        if domain:
            existing = conn.execute(
                "SELECT id FROM leads WHERE campaign_id = ? AND domain = ?", (campaign_id, domain)
            ).fetchone()
            if existing:
                return None
        row = {
            "id": _new_id(),
            "campaign_id": campaign_id,
            "company": fields.get("company", ""),
            "contact_name": fields.get("contact_name"),
            "title": fields.get("title"),
            "domain": domain,
            "website": fields.get("website"),
            "email": fields.get("email"),
            "email_source": fields.get("email_source"),
            "region": fields.get("region"),
            "source": fields.get("source", ""),
            "source_url": fields.get("source_url"),
            "profile_text": fields.get("profile_text", ""),
            "embedding": fields.get("embedding"),
            "label": None,
            "gp_score": None,
            "discovered_by_query_id": fields.get("discovered_by_query_id"),
            "created_at": _now(),
        }
        conn.execute(
            "INSERT INTO leads (id, campaign_id, company, contact_name, title, domain, website, "
            "email, email_source, region, source, source_url, profile_text, embedding, label, "
            "gp_score, discovered_by_query_id, created_at) VALUES (:id, :campaign_id, :company, "
            ":contact_name, :title, :domain, :website, :email, :email_source, :region, :source, "
            ":source_url, :profile_text, :embedding, :label, :gp_score, :discovered_by_query_id, "
            ":created_at)",
            row,
        )
        return row


def get_lead(lead_id: str) -> Optional[dict]:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(r) if r else None


def update_lead(lead_id: str, **fields) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE leads SET {set_clause} WHERE id = :id", {**fields, "id": lead_id})


def domain_email_patterns(campaign_id: str, domain: str) -> list[str]:
    """Local-parts of already-known emails for a domain — lets the email finder prefer a
    pattern that is already confirmed to work for that company."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email FROM leads WHERE campaign_id = ? AND domain = ? AND email IS NOT NULL "
            "AND email_source = 'scraped'",
            (campaign_id, domain),
        ).fetchall()
    return [r["email"].split("@", 1)[0] for r in rows if r["email"] and "@" in r["email"]]


def labeled_leads(campaign_id: str) -> list[dict]:
    """Leads with a positive/negative label + an embedding, for GP warm-start/training."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE campaign_id = ? AND label IN ('positive','negative') "
            "AND embedding IS NOT NULL",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def unlabeled_leads(campaign_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE campaign_id = ? AND label IS NULL AND embedding IS NOT NULL",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------


def create_deal(campaign_id: str, lead_id: str, state: str = STATE_NEW) -> dict:
    now = _now()
    row = {
        "id": _new_id(),
        "campaign_id": campaign_id,
        "lead_id": lead_id,
        "state": state,
        "outcome": None,
        "reason": None,
        "next_follow_up_at": None,
        "created_at": now,
        "updated_at": now,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO deals (id, campaign_id, lead_id, state, outcome, reason, next_follow_up_at, "
            "created_at, updated_at) VALUES (:id, :campaign_id, :lead_id, :state, :outcome, :reason, "
            ":next_follow_up_at, :created_at, :updated_at)",
            row,
        )
    return row


def get_deal(deal_id: str) -> Optional[dict]:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
        return dict(r) if r else None


def deal_for_lead(lead_id: str) -> Optional[dict]:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM deals WHERE lead_id = ?", (lead_id,)).fetchone()
        return dict(r) if r else None


def update_deal(deal_id: str, **fields) -> Optional[dict]:
    if not fields:
        return get_deal(deal_id)
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE deals SET {set_clause} WHERE id = :id", {**fields, "id": deal_id})
    return get_deal(deal_id)


def deals_in_state(campaign_id: str, state: str, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM deals WHERE campaign_id = ? AND state = ? ORDER BY updated_at LIMIT ?",
            (campaign_id, state, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def deal_state_counts(campaign_id: str) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM deals WHERE campaign_id = ? GROUP BY state",
            (campaign_id,),
        ).fetchall()
    return {r["state"]: r["n"] for r in rows}


def list_deals_with_leads(campaign_id: str, limit: int = 500) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT d.*, l.company, l.contact_name, l.email, l.domain, l.region, l.profile_text "
            "FROM deals d JOIN leads l ON l.id = d.lead_id WHERE d.campaign_id = ? "
            "ORDER BY d.updated_at DESC LIMIT ?",
            (campaign_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Chat messages (the conversation thread)
# ---------------------------------------------------------------------------


def add_chat_message(
    deal_id: str,
    direction: str,
    subject: str,
    body: str,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    refs: str | None = None,
) -> dict:
    row = {
        "id": _new_id(),
        "deal_id": deal_id,
        "direction": direction,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "refs": refs,
        "created_at": _now(),
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, deal_id, direction, subject, body, message_id, "
            "in_reply_to, refs, created_at) VALUES (:id, :deal_id, :direction, :subject, :body, "
            ":message_id, :in_reply_to, :refs, :created_at)",
            row,
        )
    return row


def thread_for_deal(deal_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE deal_id = ? ORDER BY created_at", (deal_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def deal_id_for_sent_message(message_id: str) -> Optional[str]:
    """The deal a Message-ID we sent belongs to — used to match an inbound reply (whose
    In-Reply-To/References point at it) back to its conversation."""
    if not message_id:
        return None
    with _connect() as conn:
        r = conn.execute(
            "SELECT deal_id FROM chat_messages WHERE direction = 'out' AND message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        return r["deal_id"] if r else None


def inbound_exists(message_id: str) -> bool:
    """Whether an inbound reply with this Message-ID was already recorded (poll dedupe)."""
    if not message_id:
        return False
    with _connect() as conn:
        r = conn.execute(
            "SELECT 1 FROM chat_messages WHERE direction = 'in' AND message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        return r is not None


def due_followups(campaign_id: str, now_iso: str, limit: int = 20) -> list[dict]:
    """Emailed/replied deals whose follow-up clock has come due."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM deals WHERE campaign_id = ? AND state IN ('EMAILED','REPLIED') "
            "AND next_follow_up_at IS NOT NULL AND next_follow_up_at <= ? "
            "ORDER BY next_follow_up_at LIMIT ?",
            (campaign_id, now_iso, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def in_flight_email_count(campaign_id: str) -> int:
    """Deals already consuming today's send capacity (resolved or being resolved) — used to
    gate how many more emails we resolve, mirroring OpenOutreach's find-email gating."""
    with _connect() as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM deals WHERE campaign_id = ? AND state IN "
            "('FINDING_EMAIL','READY_TO_EMAIL','DRAFTED')",
            (campaign_id,),
        ).fetchone()
    return int(r["n"])


def sent_today(campaign_id: str) -> int:
    """Outgoing messages for a campaign since local midnight — the daily-cap counter,
    mirroring OpenOutreach's Mailbox.sent_today()."""
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    with _connect() as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM chat_messages c JOIN deals d ON d.id = c.deal_id "
            "WHERE d.campaign_id = ? AND c.direction = 'out' AND c.created_at >= ?",
            (campaign_id, start.astimezone(timezone.utc).isoformat()),
        ).fetchone()
    return int(r["n"])


# ---------------------------------------------------------------------------
# Outreach drafts (the review queue)
# ---------------------------------------------------------------------------


def add_draft(
    deal_id: str,
    kind: str,
    subject: str,
    body: str,
    predicted_click_rate: float | None = None,
    ctr_bucket: str | None = None,
    status: str = "draft",
) -> dict:
    now = _now()
    row = {
        "id": _new_id(),
        "deal_id": deal_id,
        "kind": kind,
        "subject": subject,
        "body": body,
        "predicted_click_rate": predicted_click_rate,
        "ctr_bucket": ctr_bucket,
        "status": status,
        "created_at": now,
        "sent_at": None,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO outreach_drafts (id, deal_id, kind, subject, body, predicted_click_rate, "
            "ctr_bucket, status, created_at, sent_at) VALUES (:id, :deal_id, :kind, :subject, :body, "
            ":predicted_click_rate, :ctr_bucket, :status, :created_at, :sent_at)",
            row,
        )
    return row


def get_draft(draft_id: str) -> Optional[dict]:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM outreach_drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(r) if r else None


def update_draft(draft_id: str, **fields) -> Optional[dict]:
    if not fields:
        return get_draft(draft_id)
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE outreach_drafts SET {set_clause} WHERE id = :id", {**fields, "id": draft_id}
        )
    return get_draft(draft_id)


def list_drafts(campaign_id: str, status: str = "draft") -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT dr.*, d.lead_id, l.company, l.email, l.profile_text "
            "FROM outreach_drafts dr JOIN deals d ON d.id = dr.deal_id "
            "JOIN leads l ON l.id = d.lead_id "
            "WHERE d.campaign_id = ? AND dr.status = ? ORDER BY dr.created_at",
            (campaign_id, status),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Suppression list (never purged)
# ---------------------------------------------------------------------------


def is_suppressed(email: str) -> bool:
    with _connect() as conn:
        r = conn.execute(
            "SELECT 1 FROM suppression_list WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return r is not None


def add_suppression(email: str, reason: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO suppression_list (email, reason, added_at) VALUES (?, ?, ?)",
            (email.strip().lower(), reason, _now()),
        )


def list_suppression() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM suppression_list ORDER BY added_at DESC").fetchall()
        return [dict(r) for r in rows]
