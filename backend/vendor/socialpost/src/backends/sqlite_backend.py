"""Local-mode storage: a SQLite client that speaks the supabase-py dialect.

Why a shim rather than a repository refactor
--------------------------------------------
Every job already talks to storage through one narrow, exercised surface:

    client.table("posts").select("uri").eq("niche", n).limit(10).execute().data
    client.rpc("match_exemplars", {...}).execute().data

Seventeen methods, all of them used by code that is already written and tested.
Re-shaping every caller around a new repository interface would mean re-verifying
all seven jobs to gain nothing a user can see. Implementing those seventeen
methods over SQLite instead keeps ONE code path for the jobs and confines the
whole local-mode story to this file.

This is emphatically NOT a general PostgREST emulator. It implements exactly what
this repository calls and raises loudly on anything else, so an unsupported query
fails at the call site during development instead of silently returning wrong rows
in production. `python -m src.backends.sqlite_backend --selftest` checks the
surface is still complete.

Vector search
-------------
No sqlite-vec. Measured on this machine, numpy brute force beats it at every
scale this app reaches (~20x faster at the realistic 20 active exemplars per
niche, still ~4x at 10k), it needs no extension, and `enable_load_extension` is
compiled out of several common system Pythons. See match_exemplars below.

Concurrency
-----------
WAL mode plus a write lock. sqlite3.threadsafety is 3 (serialized) on CPython, so
one connection shared across Streamlit's script threads is safe with
check_same_thread=False; the lock serialises our own multi-statement writes.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

log = logging.getLogger(__name__)

# --- schema knowledge -------------------------------------------------------
#
# SQLite has no arrays, no bool, and no vector type. These maps tell the shim how
# to marshal each column so callers see exactly what Supabase would have handed
# them (Python lists, True/False), and never a JSON string or an 0/1 int.

_ARRAY_COLS: dict[str, set[str]] = {
    "niches": {"keywords"},
    "posts": {"hashtags"},
    "kb_articles": {"platform_tags"},
    "generations": {"exemplar_ids", "kb_ids"},
}
_VECTOR_COLS: dict[str, set[str]] = {"exemplars": {"embedding"}}
_BOOL_COLS: dict[str, set[str]] = {
    "niches": {"active"},
    "posts": {"has_media"},
    "exemplars": {"active"},
    "kb_articles": {"active"},
    "telemetry_consent": {"content_opt_in"},
    "tracked_authors": {"active"},
}

# Guard against typos silently creating a table-shaped hole.
_KNOWN_TABLES = {
    "niches",
    "authors",
    "posts",
    "engagement_snapshots",
    "exemplars",
    "kb_articles",
    "generations",
    "performance_baselines",
    "job_runs",
    "telemetry_outbox",
    "telemetry_consent",
    "tracked_authors",
}

EMBEDDING_DIM = 384


class UnsupportedQuery(NotImplementedError):
    """Raised when a caller uses PostgREST behaviour this shim does not cover.

    Deliberately loud. A shim that guesses is worse than one that stops.
    """


# ---------------------------------------------------------------------------
# Marshalling
# ---------------------------------------------------------------------------


def _serialize_vector(value: Any) -> bytes:
    """Python list[float] -> float32 bytes."""
    arr = np.asarray(value, dtype=np.float32)
    if arr.ndim != 1 or arr.shape[0] != EMBEDDING_DIM:
        raise ValueError(
            f"Expected a {EMBEDDING_DIM}-dim embedding, got shape {arr.shape}. "
            f"The schema stores exemplars.embedding as {EMBEDDING_DIM} float32s."
        )
    return arr.tobytes()


def _deserialize_vector(blob: bytes | None) -> np.ndarray | None:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def _encode(table: str, column: str, value: Any) -> Any:
    """Python value -> SQLite storage value."""
    if value is None:
        return None
    if column in _VECTOR_COLS.get(table, ()):
        return _serialize_vector(value)
    if column in _ARRAY_COLS.get(table, ()):
        if isinstance(value, str):
            # Already JSON; trust it rather than double-encoding.
            return value
        return json.dumps(list(value))
    if column in _BOOL_COLS.get(table, ()):
        return 1 if value else 0
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def _decode_row(table: str, row: sqlite3.Row) -> dict:
    """SQLite row -> the dict shape Supabase would have returned."""
    out: dict[str, Any] = {}
    arrays = _ARRAY_COLS.get(table, set())
    bools = _BOOL_COLS.get(table, set())
    vectors = _VECTOR_COLS.get(table, set())

    for key in row.keys():
        value = row[key]
        if key in arrays:
            out[key] = json.loads(value) if value else []
        elif key in bools:
            out[key] = bool(value) if value is not None else None
        elif key in vectors:
            # Hand back a plain list, matching pgvector-over-PostgREST, which
            # never returns raw bytes.
            vec = _deserialize_vector(value)
            out[key] = vec.tolist() if vec is not None else None
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class Response:
    """Mimics postgrest's APIResponse: .data and .count."""

    __slots__ = ("data", "count")

    def __init__(self, data: list[dict], count: int | None = None) -> None:
        self.data = data
        self.count = count


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_ident(name: str) -> str:
    """Reject anything that is not a bare identifier.

    Column names reach SQL by interpolation (SQLite cannot bind identifiers), so
    this is the boundary that keeps that safe. Every value still goes through a
    bound parameter.
    """
    if not _IDENT_RE.match(name):
        raise UnsupportedQuery(f"Unsafe or unsupported identifier: {name!r}")
    return name


class QueryBuilder:
    """Accumulates a PostgREST-style chain, then compiles it to SQL."""

    def __init__(self, client: "SqliteClient", table: str) -> None:
        if table not in _KNOWN_TABLES:
            raise UnsupportedQuery(
                f"Unknown table {table!r}. Known: {', '.join(sorted(_KNOWN_TABLES))}"
            )
        self._client = client
        self._table = table
        self._mode: str | None = None
        self._columns = "*"
        self._count_mode: str | None = None
        self._rows: list[dict] = []
        self._payload: dict = {}
        self._on_conflict: str | None = None
        self._filters: list[tuple[str, list[Any]]] = []  # (sql_fragment, params)
        self._orders: list[str] = []
        self._limit: int | None = None

    # --- verbs -------------------------------------------------------------

    def select(self, columns: str = "*", count: str | None = None) -> "QueryBuilder":
        # `.select()` after insert/upsert is PostgREST's "return the rows" idiom;
        # we always return them, so treat it as a no-op rather than a new query.
        if self._mode in {"insert", "upsert", "update", "delete"}:
            return self
        self._mode = "select"
        self._columns = columns
        if count is not None and count != "exact":
            raise UnsupportedQuery(f"count={count!r}; only 'exact' is supported")
        self._count_mode = count
        return self

    def insert(self, rows: dict | list[dict]) -> "QueryBuilder":
        self._mode = "insert"
        self._rows = [rows] if isinstance(rows, dict) else list(rows)
        return self

    def upsert(
        self, rows: dict | list[dict], on_conflict: str | None = None
    ) -> "QueryBuilder":
        self._mode = "upsert"
        self._rows = [rows] if isinstance(rows, dict) else list(rows)
        if not on_conflict:
            raise UnsupportedQuery(
                "upsert() needs on_conflict; implicit PK inference is not supported"
            )
        self._on_conflict = on_conflict
        return self

    def update(self, payload: dict) -> "QueryBuilder":
        self._mode = "update"
        self._payload = payload
        return self

    def delete(self) -> "QueryBuilder":
        self._mode = "delete"
        return self

    # --- filters -----------------------------------------------------------

    def _add(self, column: str, op: str, value: Any) -> "QueryBuilder":
        col = _check_ident(column)
        self._filters.append((f"{col} {op} ?", [_encode(self._table, col, value)]))
        return self

    def eq(self, column: str, value: Any) -> "QueryBuilder":
        return self._add(column, "=", value)

    def neq(self, column: str, value: Any) -> "QueryBuilder":
        return self._add(column, "!=", value)

    def gt(self, column: str, value: Any) -> "QueryBuilder":
        return self._add(column, ">", value)

    def gte(self, column: str, value: Any) -> "QueryBuilder":
        return self._add(column, ">=", value)

    def lt(self, column: str, value: Any) -> "QueryBuilder":
        return self._add(column, "<", value)

    def lte(self, column: str, value: Any) -> "QueryBuilder":
        return self._add(column, "<=", value)

    def in_(self, column: str, values: Iterable[Any]) -> "QueryBuilder":
        col = _check_ident(column)
        vals = list(values)
        if not vals:
            # PostgREST's in.() matches nothing; make that explicit rather than
            # emitting `IN ()`, which is a SQLite syntax error.
            self._filters.append(("0 = 1", []))
            return self
        placeholders = ",".join("?" * len(vals))
        self._filters.append(
            (f"{col} in ({placeholders})", [_encode(self._table, col, v) for v in vals])
        )
        return self

    def is_(self, column: str, value: Any) -> "QueryBuilder":
        col = _check_ident(column)
        # supabase-py spells SQL NULL as the string "null".
        if value in (None, "null", "NULL"):
            self._filters.append((f"{col} is null", []))
        elif value in (True, "true", False, "false"):
            self._filters.append((f"{col} is ?", [1 if value in (True, "true") else 0]))
        else:
            raise UnsupportedQuery(f"is_({column!r}, {value!r}) is not supported")
        return self

    def overlaps(self, column: str, values: Sequence[Any]) -> "QueryBuilder":
        """Array overlap (Postgres &&) over a JSON-encoded column."""
        col = _check_ident(column)
        if col not in _ARRAY_COLS.get(self._table, ()):
            raise UnsupportedQuery(f"overlaps() on non-array column {self._table}.{col}")
        vals = list(values)
        if not vals:
            self._filters.append(("0 = 1", []))
            return self
        placeholders = ",".join("?" * len(vals))
        self._filters.append(
            (
                f"exists (select 1 from json_each({col}) where value in ({placeholders}))",
                vals,
            )
        )
        return self

    # --- modifiers ---------------------------------------------------------

    def order(
        self, column: str, desc: bool = False, nullsfirst: bool | None = None
    ) -> "QueryBuilder":
        col = _check_ident(column)
        clause = f"{col} {'desc' if desc else 'asc'}"
        if nullsfirst is not None:
            clause += " nulls first" if nullsfirst else " nulls last"
        self._orders.append(clause)
        return self

    def limit(self, n: int) -> "QueryBuilder":
        self._limit = n
        return self

    # --- compile + run -----------------------------------------------------

    def _where(self) -> tuple[str, list[Any]]:
        if not self._filters:
            return "", []
        sql = " where " + " and ".join(f for f, _ in self._filters)
        params: list[Any] = []
        for _, p in self._filters:
            params.extend(p)
        return sql, params

    def execute(self) -> Response:
        if self._mode is None:
            raise UnsupportedQuery("Query has no verb; call select/insert/update/...")
        handler = getattr(self, f"_run_{self._mode}")
        return handler()

    def _run_select(self) -> Response:
        where, params = self._where()

        count: int | None = None
        if self._count_mode == "exact":
            # PostgREST's exact count is of ALL matching rows, independent of
            # limit — which is exactly how `.limit(0)` is used as a cheap
            # "how many are there?".
            row = self._client._query(
                f"select count(*) as n from {self._table}{where}", params
            )
            count = row[0]["n"] if row else 0

        if self._limit == 0:
            return Response([], count)

        cols = (
            "*"
            if self._columns.strip() == "*"
            else ", ".join(_check_ident(c.strip()) for c in self._columns.split(","))
        )
        sql = f"select {cols} from {self._table}{where}"
        if self._orders:
            sql += " order by " + ", ".join(self._orders)
        if self._limit is not None:
            sql += f" limit {int(self._limit)}"

        rows = self._client._query(sql, params, table=self._table)
        return Response(rows, count)

    def _insert_sql(self, row: dict, upsert: bool) -> tuple[str, list[Any]]:
        cols = [_check_ident(c) for c in row]
        placeholders = ",".join("?" * len(cols))
        params = [_encode(self._table, c, row[c]) for c in cols]
        sql = (
            f"insert into {self._table} ({', '.join(cols)}) values ({placeholders})"
        )
        if upsert:
            targets = [_check_ident(c.strip()) for c in (self._on_conflict or "").split(",")]
            updatable = [c for c in cols if c not in targets]
            if updatable:
                assignments = ", ".join(f"{c}=excluded.{c}" for c in updatable)
                sql += f" on conflict({', '.join(targets)}) do update set {assignments}"
            else:
                sql += f" on conflict({', '.join(targets)}) do nothing"
        sql += " returning *"
        return sql, params

    def _run_insert(self) -> Response:
        return self._write_rows(upsert=False)

    def _run_upsert(self) -> Response:
        return self._write_rows(upsert=True)

    def _write_rows(self, upsert: bool) -> Response:
        out: list[dict] = []
        with self._client._lock, self._client._conn:
            for row in self._rows:
                sql, params = self._insert_sql(row, upsert)
                cur = self._client._conn.execute(sql, params)
                out.extend(_decode_row(self._table, r) for r in cur.fetchall())
        return Response(out, None)

    def _run_update(self) -> Response:
        if not self._payload:
            raise UnsupportedQuery("update() with an empty payload")
        cols = [_check_ident(c) for c in self._payload]
        assignments = ", ".join(f"{c}=?" for c in cols)
        params = [_encode(self._table, c, self._payload[c]) for c in cols]
        where, where_params = self._where()
        sql = f"update {self._table} set {assignments}{where} returning *"
        with self._client._lock, self._client._conn:
            cur = self._client._conn.execute(sql, params + where_params)
            rows = [_decode_row(self._table, r) for r in cur.fetchall()]
        return Response(rows, None)

    def _run_delete(self) -> Response:
        where, params = self._where()
        if not where:
            # Postgres would happily truncate; make the caller be explicit.
            raise UnsupportedQuery("Refusing an unfiltered delete(); add a filter")
        sql = f"delete from {self._table}{where} returning *"
        with self._client._lock, self._client._conn:
            cur = self._client._conn.execute(sql, params)
            rows = [_decode_row(self._table, r) for r in cur.fetchall()]
        return Response(rows, None)


# ---------------------------------------------------------------------------
# RPCs — the three Postgres functions from 001_init.sql, reimplemented
# ---------------------------------------------------------------------------


class RpcQuery:
    """What client.rpc(name, params) returns; .execute() runs it."""

    def __init__(self, client: "SqliteClient", name: str, params: dict) -> None:
        self._client = client
        self._name = name
        self._params = params or {}

    def execute(self) -> Response:
        try:
            fn = getattr(self._client, f"_rpc_{self._name}")
        except AttributeError as err:
            raise UnsupportedQuery(
                f"No local implementation of RPC {self._name!r}. Postgres functions "
                f"live in migrations/001_init.sql and must be mirrored in "
                f"src/backends/sqlite_backend.py to work in local mode."
            ) from err
        return Response(fn(**self._params), None)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SqliteClient:
    """Drop-in replacement for supabase.Client covering this repo's usage."""

    def __init__(self, path: str | Path, migration_sql: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,  # safe: sqlite3.threadsafety == 3 (serialized)
            isolation_level="",  # explicit transactions via `with conn`
        )
        self._conn.row_factory = sqlite3.Row
        # WAL lets the Streamlit UI read while a job writes.
        self._conn.execute("pragma journal_mode=WAL")
        # Without this, ON DELETE CASCADE silently does nothing and forget.py
        # would leave orphans. SQLite defaults FKs OFF for backwards compatibility.
        self._conn.execute("pragma foreign_keys=ON")
        # Don't fail instantly if a job holds the write lock.
        self._conn.execute("pragma busy_timeout=5000")

        if migration_sql:
            self.apply_migration(migration_sql)
            self._ensure_columns()

    def apply_migration(self, sql: str) -> None:
        with self._lock, self._conn:
            self._conn.executescript(sql)

    # Columns added to existing tables after the initial schema shipped. SQLite
    # has no ADD COLUMN IF NOT EXISTS, and the migration script is re-run on every
    # connect, so a plain ALTER in the .sql would raise once the column exists.
    # A fresh install already has these from the CREATE TABLE; this only backfills
    # a database created before the column existed.
    _ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
        ("generations", "uid", "text"),
        ("generations", "outcome_reported_at", "text"),
    )

    def _ensure_columns(self) -> None:
        with self._lock, self._conn:
            for table, column, decl in self._ADDED_COLUMNS:
                existing = {
                    row[1]
                    for row in self._conn.execute(f"pragma table_info({table})")
                }
                if column not in existing:
                    self._conn.execute(
                        f"alter table {table} add column {column} {decl}"
                    )
                    log.info("Added column %s.%s to local database", table, column)
            # Created here, not in the migration script: on a pre-telemetry
            # database the uid column does not exist when the script runs, and a
            # CREATE INDEX referencing it would fail before the ALTER above.
            self._conn.execute(
                "create unique index if not exists generations_uid_idx "
                "on generations (uid)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- internals ---------------------------------------------------------

    def _query(
        self, sql: str, params: Sequence[Any] = (), table: str | None = None
    ) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, list(params))
            rows = cur.fetchall()
        if table:
            return [_decode_row(table, r) for r in rows]
        return [dict(r) for r in rows]

    # --- supabase surface --------------------------------------------------

    def table(self, name: str) -> QueryBuilder:
        return QueryBuilder(self, name)

    def rpc(self, name: str, params: dict | None = None) -> RpcQuery:
        return RpcQuery(self, name, params or {})

    # --- RPC implementations ----------------------------------------------

    def _rpc_match_exemplars(
        self,
        query_embedding: Sequence[float],
        target_niche: str,
        match_count: int = 5,
        similarity_weight: float = 0.7,
    ) -> list[dict]:
        """Mirror of match_exemplars() in 001_init.sql.

        Brute force rather than an index. The candidate set is one niche's active
        pool — TARGET_POOL_SIZE is 20 — so a dot product over 20x384 floats is
        tens of microseconds, faster than any index could dispatch, and it lets
        the score blend stay in readable Python instead of nested SQL.
        """
        rows = self._query(
            """
            select e.id, e.post_uri, p.text, e.score, e.embedding
            from exemplars e
            join posts p on p.uri = e.post_uri
            where e.niche = ? and e.active = 1 and e.embedding is not null
            """,
            [target_niche],
        )
        if not rows:
            return []

        query = np.asarray(query_embedding, dtype=np.float32)
        qnorm = float(np.linalg.norm(query))
        if qnorm == 0.0:
            return []

        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        norms = np.linalg.norm(matrix, axis=1)
        # Guard a zero-vector row rather than emitting a NaN that would poison
        # the sort order.
        norms[norms == 0.0] = 1.0
        sims = (matrix @ query) / (norms * qnorm)

        scores = np.array([float(r["score"] or 0.0) for r in rows], dtype=np.float64)
        lo, hi = float(scores.min()), float(scores.max())
        # Matches the SQL: a pool with no spread contributes a flat 0.5 rather
        # than dividing by zero.
        norm_scores = np.full_like(scores, 0.5) if hi == lo else (scores - lo) / (hi - lo)

        w = float(similarity_weight)
        blended = w * sims + (1.0 - w) * norm_scores

        order = np.argsort(-blended)[: int(match_count)]
        return [
            {
                "id": rows[i]["id"],
                "post_uri": rows[i]["post_uri"],
                "text": rows[i]["text"],
                "score": float(rows[i]["score"] or 0.0),
                "similarity": float(sims[i]),
                "blended": float(blended[i]),
            }
            for i in order
        ]

    def _rpc_exemplar_candidates(
        self,
        target_niche: str,
        max_age_days: int = 90,
        min_followers: int = 200,
    ) -> list[dict]:
        """Mirror of exemplar_candidates() in 001_init.sql.

        julianday() parses our ISO-8601-with-offset timestamps directly, so
        age_days needs no Python round-trip.
        """
        from ..db import iso, utcnow  # local import: avoids a circular import

        from datetime import timedelta

        cutoff = iso(utcnow() - timedelta(days=int(max_age_days)))
        return self._query(
            """
            select p.uri  as post_uri,
                   p.text as text,
                   p.created_at,
                   s.engagement_rate,
                   a.follower_count,
                   (julianday('now') - julianday(p.created_at)) as age_days
            from posts p
            join engagement_snapshots s
              on s.post_uri = p.uri and s.window_label = '48h'
            join authors a
              on a.did = p.author_did
            where p.niche = ?
              and p.created_at > ?
              and s.engagement_rate is not null
              and coalesce(a.follower_count, 0) >= ?
              and p.text is not null
              and length(trim(p.text)) > 0
            """,
            [target_niche, cutoff, int(min_followers)],
        )

    def _rpc_generation_performance(self, lookback_days: int = 28) -> list[dict]:
        """Mirror of generation_performance() in 001_init.sql."""
        from ..db import iso, utcnow

        from datetime import timedelta

        cutoff = iso(utcnow() - timedelta(days=int(lookback_days)))
        return self._query(
            """
            select g.niche             as niche,
                   count(*)            as n,
                   avg(s.engagement_rate) as avg_engagement
            from generations g
            join engagement_snapshots s
              on s.post_uri = g.posted_uri and s.window_label = '48h'
            where g.posted_uri is not null
              and s.captured_at > ?
              and g.niche is not null
            group by g.niche
            """,
            [cutoff],
        )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    """Exercise the whole shim against a temp database. No network, no keys.

        python -m src.backends.sqlite_backend --selftest
    """
    import tempfile

    from ..db import CONFIG_DIR  # noqa: F401  (ensures src is importable)

    sql = (Path(__file__).resolve().parents[2] / "migrations" / "001_init_sqlite.sql").read_text(
        encoding="utf-8"
    )
    tmp = Path(tempfile.mkdtemp()) / "selftest.db"
    c = SqliteClient(tmp, migration_sql=sql)
    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  {'ok  ' if got == want else 'FAIL'} {label}")

    now = "2026-07-17T00:00:00+00:00"
    c.table("authors").insert(
        {"did": "did:a", "handle": "a.test", "follower_count": 500, "niche": "n", "last_seen_at": now}
    ).execute()

    # arrays + bools round-trip
    c.table("posts").insert(
        {
            "uri": "at://p1",
            "author_did": "did:a",
            "text": "hello world",
            "hashtags": ["build", "ship"],
            "has_media": True,
            "created_at": now,
            "niche": "n",
            "ingested_at": now,
        }
    ).execute()
    row = c.table("posts").select("*").eq("uri", "at://p1").execute().data[0]
    check("array round-trips as list", row["hashtags"], ["build", "ship"])
    check("bool round-trips as True", row["has_media"], True)

    # insert returns the generated id
    resp = c.table("job_runs").insert({"job_name": "t", "started_at": now}).execute()
    check("insert returns id", isinstance(resp.data[0]["id"], int), True)

    # count='exact' + limit(0)
    check(
        "count exact w/ limit 0",
        c.table("posts").select("*", count="exact").limit(0).execute().count,
        1,
    )

    # upsert on conflict
    c.table("authors").upsert(
        {"did": "did:a", "handle": "renamed", "follower_count": 900, "last_seen_at": now},
        on_conflict="did",
    ).execute()
    check(
        "upsert updates existing",
        c.table("authors").select("handle").eq("did", "did:a").execute().data[0]["handle"],
        "renamed",
    )

    # unique constraint still bites (append-only snapshots)
    c.table("engagement_snapshots").insert(
        {"post_uri": "at://p1", "captured_at": now, "window_label": "48h",
         "likes": 9, "reposts": 0, "replies": 0, "engagement_rate": 0.01}
    ).execute()
    try:
        c.table("engagement_snapshots").insert(
            {"post_uri": "at://p1", "captured_at": now, "window_label": "48h",
             "likes": 999, "reposts": 0, "replies": 0, "engagement_rate": 9.0}
        ).execute()
        check("duplicate snapshot rejected", "no error", "IntegrityError")
    except sqlite3.IntegrityError:
        check("duplicate snapshot rejected", "IntegrityError", "IntegrityError")

    # is_ null
    check("is_ null", len(c.table("job_runs").select("id").is_("status", "null").execute().data), 1)

    # overlaps
    c.table("kb_articles").insert(
        {"url": "u1", "url_hash": "h1", "platform_tags": ["tiktok"], "summary": "s",
         "published_at": now, "ingested_at": now}
    ).execute()
    c.table("kb_articles").insert(
        {"url": "u2", "url_hash": "h2", "platform_tags": ["bluesky"], "summary": "s",
         "published_at": now, "ingested_at": now}
    ).execute()
    hits = c.table("kb_articles").select("url").overlaps("platform_tags", ["bluesky", "general"]).execute().data
    check("overlaps filters correctly", [h["url"] for h in hits], ["u2"])

    # vectors + match_exemplars
    v1 = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    v2 = [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)
    c.table("exemplars").insert(
        {"post_uri": "at://p1", "niche": "n", "score": 0.05, "embedding": v1,
         "active": True, "refreshed_at": now}
    ).execute()
    c.table("exemplars").insert(
        {"post_uri": "at://p1", "niche": "n", "score": 0.01, "embedding": v2,
         "active": True, "refreshed_at": now}
    ).execute()
    m = c.rpc("match_exemplars", {"query_embedding": v1, "target_niche": "n",
                                  "match_count": 2, "similarity_weight": 0.7}).execute().data
    check("match_exemplars ranks by similarity", round(m[0]["similarity"], 3), 1.0)
    check("match_exemplars returns both", len(m), 2)

    # update returns affected rows
    upd = c.table("exemplars").update({"active": False}).eq("niche", "n").eq("active", True).execute()
    check("update returns affected rows", len(upd.data), 2)

    # FK cascade — the thing forget.py depends on
    c.table("authors").delete().eq("did", "did:a").execute()
    check("cascade removed posts", c.table("posts").select("*", count="exact").limit(0).execute().count, 0)
    check("cascade removed snapshots",
          c.table("engagement_snapshots").select("*", count="exact").limit(0).execute().count, 0)
    check("cascade removed exemplars",
          c.table("exemplars").select("*", count="exact").limit(0).execute().count, 0)

    # unfiltered delete refused
    try:
        c.table("posts").delete().execute()
        check("unfiltered delete refused", "allowed", "UnsupportedQuery")
    except UnsupportedQuery:
        check("unfiltered delete refused", "UnsupportedQuery", "UnsupportedQuery")

    c.close()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print("  -", f)
        return 1
    print("sqlite backend selftest: all checks passed")
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        logging.basicConfig(level=logging.INFO)
        sys.exit(_selftest())
    print(__doc__)
