"""Storage client factory, config loading, and shared write helpers.

Every job and the Streamlit app go through here. Nothing else in the codebase
constructs a storage client or reads os.environ for credentials.

Two backends, selected by DB_BACKEND:

  supabase  (default)  Hosted Postgres + pgvector. Shared, durable, works with
                       GitHub Actions because Actions runners have no local disk
                       that survives a run.

  sqlite               Fully local. One file on your machine, no accounts, no
                       network, no keys beyond the ones Bluesky and the LLM need.
                       Pair it with `python -m src.scheduler` instead of Actions.
                       See DOCUMENTATION.md -> Local mode.

Callers cannot tell the difference: src/backends/sqlite_backend.py implements the
same client surface. That is the whole point — one code path for the jobs.

The Supabase service-role key bypasses RLS, which is what the jobs need. It must
never reach a browser; the Streamlit app is expected to run somewhere its secrets
are server-side (Community Cloud secrets, HF Spaces secrets).
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import yaml
from dotenv import load_dotenv

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# supabase-py posts the whole batch as one request. Bluesky pages come back in
# 100s and RSS feeds in tens, so this only matters for the big exemplar refresh.
BATCH_SIZE = 500

BACKEND_SUPABASE = "supabase"
BACKEND_SQLITE = "sqlite"
VALID_BACKENDS = (BACKEND_SUPABASE, BACKEND_SQLITE)

DEFAULT_SQLITE_PATH = REPO_ROOT / "data" / "app.db"

# Where this install's settings live. Overridable so the package can be embedded in
# a host app that keeps per-user state elsewhere (the desktop app points this at its
# per-user data directory), instead of writing into the install directory.
ENV_FILE = Path(os.environ.get("SPG_ENV_FILE") or (REPO_ROOT / ".env"))

# override=False (the default): anything the host process already set in the
# environment wins over the file, which is what makes host-supplied config work.
load_dotenv(ENV_FILE)


def backend() -> str:
    """Which storage backend is configured. Defaults to supabase."""
    name = (os.environ.get("DB_BACKEND") or BACKEND_SUPABASE).strip().lower()
    if name not in VALID_BACKENDS:
        raise RuntimeError(
            f"DB_BACKEND={name!r} is not valid. Use one of: {', '.join(VALID_BACKENDS)}."
        )
    return name


def sqlite_path() -> Path:
    """Where the local database file lives."""
    return Path(os.environ.get("SQLITE_PATH") or DEFAULT_SQLITE_PATH)


def utcnow() -> datetime:
    """Timezone-aware UTC now. Jobs must never write naive datetimes."""
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """Serialise a datetime for PostgREST, forcing UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def require_env(name: str) -> str:
    """Read a required env var, failing with a message that names the fix."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            f"Copy .env.example to .env and fill it in, or add {name} to your "
            f"GitHub repository secrets."
        )
    return value


@functools.lru_cache(maxsize=1)
def get_client():
    """Process-wide storage client for the configured backend.

    Returns a supabase.Client, or the SqliteClient shim that implements the same
    surface. Cached because both open a connection; jobs call this from several
    helpers within one run.
    """
    name = backend()

    if name == BACKEND_SQLITE:
        # Imported lazily so Supabase users never load numpy/sqlite machinery,
        # and vice versa — neither backend's dependencies become mandatory.
        from .backends.sqlite_backend import SqliteClient

        path = sqlite_path()
        fresh = not path.exists()
        migration = (MIGRATIONS_DIR / "001_init_sqlite.sql").read_text(encoding="utf-8")
        # The migration is idempotent, so applying it on every connect keeps a
        # local database current without a migration runner. There is no SQL
        # editor to paste into in local mode; this is the equivalent step.
        client = SqliteClient(path, migration_sql=migration)
        if fresh:
            log.info("Created local database at %s", path)
        return client

    from supabase import create_client

    url = require_env("SUPABASE_URL")
    key = require_env("SUPABASE_SERVICE_KEY")
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_niches_yaml() -> dict[str, list[str]]:
    """Return {niche_name: [keyword, ...]} from config/niches.yaml.

    The YAML is a seed template, not the runtime source of truth — see
    load_niches(). Read it directly only when seeding or re-importing.
    """
    path = CONFIG_DIR / "niches.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    niches = (raw or {}).get("niches") or {}
    out: dict[str, list[str]] = {}
    for name, body in niches.items():
        keywords = (body or {}).get("keywords") or []
        if not keywords:
            log.warning("Niche %r has no keywords; skipping.", name)
            continue
        out[str(name)] = [str(k) for k in keywords]
    return out


# Set once the seed check has run in this process, so the extra COUNT(*) does not
# repeat on every niche read within a job.
_seed_checked = False


def ensure_niches_seeded() -> int:
    """Bootstrap the niches table from config/niches.yaml if it has never been used.

    A precondition of every niche read, not one function's side effect. Getting
    this wrong is subtle and nasty: if only load_niches() seeded, then `--list` on
    a fresh install would print nothing while `ingest` saw two niches, and an
    `--add` before any seed would leave the table holding one niche forever with
    the YAML defaults silently unreachable.

    The seed is a write, so it can happen during a --dry-run. That is deliberate:
    the alternative is a dry run on a fresh install reporting "no niches" and
    looking broken. It writes configuration, not job output, and only ever once —
    an empty table means nobody has ever managed niches, whereas a table someone
    has deliberately emptied is not empty, it is deactivated.
    """
    global _seed_checked
    if _seed_checked:
        return 0
    _seed_checked = True

    client = get_client()
    count = client.table("niches").select("*", count="exact").limit(0).execute().count or 0
    if count:
        return 0

    seed = load_niches_yaml()
    if not seed:
        log.warning(
            "No niches configured and config/niches.yaml has none to seed from. "
            "Add one with: python -m src.jobs.niches --add 'my niche' --keywords ..."
        )
        return 0

    now = iso(utcnow())
    upsert(
        "niches",
        [
            {
                "name": name,
                "keywords": keywords,
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
            for name, keywords in seed.items()
        ],
        on_conflict="name",
    )
    log.info(
        "Seeded %d niches from config/niches.yaml (first run): %s",
        len(seed),
        ", ".join(sorted(seed)),
    )
    return len(seed)


def load_niches(active_only: bool = True) -> dict[str, list[str]]:
    """Return {niche_name: [keyword, ...]} — the niches this system watches.

    Reads the `niches` table, so users can manage niches from the UI or the CLI
    rather than editing a file in the repo. config/niches.yaml only seeds the
    table the first time it is found empty; after that the table wins and YAML
    edits need an explicit `python -m src.jobs.niches --import-config`.

    Returns {} when every niche is deactivated. Callers must treat that as "there
    is nothing to do", not as an error — a user is allowed to turn everything off.
    """
    ensure_niches_seeded()
    rows = (
        get_client().table("niches").select("name, keywords, active").execute().data or []
    )

    out: dict[str, list[str]] = {}
    for row in rows:
        if active_only and not row["active"]:
            continue
        keywords = [str(k) for k in (row["keywords"] or [])]
        if not keywords:
            log.warning("Niche %r has no keywords; skipping.", row["name"])
            continue
        out[str(row["name"])] = keywords
    return out


# ---------------------------------------------------------------------------
# Niche management
#
# The niche name is denormalised onto five tables, so these helpers exist to keep
# that consistent. Nothing outside here should write the `niches` table.
# ---------------------------------------------------------------------------

# Tables carrying the niche name in a column literally called `niche`.
# performance_baselines is handled separately: it stores the name in scope_key.
_NICHE_TABLES = ("posts", "authors", "exemplars", "generations")

MAX_NICHE_NAME_LENGTH = 60

# Keywords shorter than this match almost anything. Bluesky search returns noise,
# the corpus fills with irrelevance, and the rate limit pays for it. A warning
# rather than an error — "gpt" and "seo" are short and legitimate.
SHORT_KEYWORD_LENGTH = 4


class NicheError(ValueError):
    """Invalid niche input. Message is intended to be shown to a user verbatim."""


def normalise_niche_name(name: str) -> str:
    """Trim and collapse whitespace. Names are compared exactly everywhere."""
    return " ".join((name or "").split())


def validate_niche(name: str, keywords: Sequence[str]) -> tuple[str, list[str]]:
    """Check a niche is usable. Returns the cleaned (name, keywords).

    Raises NicheError with a message written for the person typing it, since this
    runs behind a UI form as well as a CLI.
    """
    name = normalise_niche_name(name)
    if not name:
        raise NicheError("Give the niche a name.")
    if len(name) > MAX_NICHE_NAME_LENGTH:
        raise NicheError(
            f"Niche names are limited to {MAX_NICHE_NAME_LENGTH} characters."
        )

    cleaned: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        keyword = " ".join((keyword or "").split())
        if not keyword:
            continue
        if keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        cleaned.append(keyword)

    if not cleaned:
        raise NicheError("Add at least one search keyword.")
    return name, cleaned


def weak_keywords(keywords: Sequence[str]) -> list[str]:
    """Keywords likely to return mostly noise. Advisory, not blocking.

    A single short word ("ai", "dev") matches an enormous amount of unrelated
    chatter. Multi-word phrases are fine at any length.
    """
    return [
        k
        for k in keywords
        if len(k.split()) == 1 and len(k) < SHORT_KEYWORD_LENGTH
    ]


def get_niche(name: str) -> dict | None:
    """One niche row, or None."""
    ensure_niches_seeded()
    rows = (
        get_client()
        .table("niches")
        .select("*")
        .eq("name", normalise_niche_name(name))
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def list_niches() -> list[dict]:
    """Every niche, active or not, alphabetical."""
    ensure_niches_seeded()
    rows = get_client().table("niches").select("*").execute().data or []
    return sorted(rows, key=lambda r: r["name"].lower())


def save_niche(name: str, keywords: Sequence[str], active: bool = True) -> dict:
    """Create or update a niche by name. Returns the stored row."""
    name, keywords = validate_niche(name, keywords)
    now = iso(utcnow())
    existing = get_niche(name)
    payload = {
        "name": name,
        "keywords": list(keywords),
        "active": active,
        "created_at": existing["created_at"] if existing else now,
        "updated_at": now,
    }
    upsert("niches", [payload], on_conflict="name")
    return payload


def set_niche_active(name: str, active: bool) -> None:
    """Turn collection for a niche on or off. Data is untouched either way."""
    name = normalise_niche_name(name)
    if get_niche(name) is None:
        raise NicheError(f"No niche called {name!r}.")
    get_client().table("niches").update(
        {"active": active, "updated_at": iso(utcnow())}
    ).eq("name", name).execute()


def niche_data_counts(name: str) -> dict[str, int]:
    """How much data a niche actually has. Used by the UI and before deleting."""
    name = normalise_niche_name(name)
    client = get_client()
    out: dict[str, int] = {}
    for table in _NICHE_TABLES:
        out[table] = (
            client.table(table)
            .select("*", count="exact")
            .eq("niche", name)
            .limit(0)
            .execute()
            .count
            or 0
        )
    return out


def rename_niche(old: str, new: str) -> dict[str, int]:
    """Rename a niche AND migrate every row that references it.

    The name is denormalised onto posts, authors, exemplars, generations, and
    performance_baselines.scope_key. A bare UPDATE of the niches table would leave
    every one of those pointing at a name that no longer exists — the niche would
    appear to lose its entire history. So this moves them all and reports what it
    touched.

    Not a transaction: PostgREST cannot express one across requests. The order
    below is chosen so that an interruption leaves data findable under the OLD
    name (which still resolves) rather than under a name nothing references.
    """
    old = normalise_niche_name(old)
    new = normalise_niche_name(new)
    if old == new:
        raise NicheError("The new name is the same as the old one.")
    if get_niche(old) is None:
        raise NicheError(f"No niche called {old!r}.")
    if get_niche(new) is not None:
        raise NicheError(
            f"A niche called {new!r} already exists. Pick another name, or remove "
            f"that one first."
        )
    new, _ = validate_niche(new, ["placeholder"])  # name-only validation

    client = get_client()
    moved: dict[str, int] = {}

    # Dependent rows first: until the niches row itself moves, `old` is still the
    # name a user would look under.
    for table in _NICHE_TABLES:
        rows = (
            client.table(table)
            .update({"niche": new})
            .eq("niche", old)
            .execute()
            .data
            or []
        )
        moved[table] = len(rows)

    # performance_baselines keys the niche name in scope_key. Its UNIQUE
    # (scope, scope_key, window_label) would collide with any stale baseline
    # already sitting under the new name, so clear that first. Baselines are
    # recomputed nightly, making this the cheapest thing to sacrifice.
    client.table("performance_baselines").delete().eq("scope", "niche").eq(
        "scope_key", new
    ).execute()
    rows = (
        client.table("performance_baselines")
        .update({"scope_key": new})
        .eq("scope", "niche")
        .eq("scope_key", old)
        .execute()
        .data
        or []
    )
    moved["performance_baselines"] = len(rows)

    # The niches row last: now everything already points at `new`.
    existing = get_niche(old)
    save_niche(new, existing["keywords"], active=existing["active"])
    client.table("niches").delete().eq("name", old).execute()

    log.info(
        "Renamed niche %r -> %r; migrated %s",
        old,
        new,
        ", ".join(f"{k}={v}" for k, v in moved.items()),
    )
    return moved


def delete_niche(name: str, purge_data: bool = False) -> dict[str, int]:
    """Remove a niche. By default its collected data survives.

    Deactivating stops collection; deleting the niches row also removes it from
    the UI. Neither destroys posts, because the corpus is the expensive thing and
    a mis-click should not be able to burn it.

    purge_data=True deletes the niche's authors, which cascades to their posts,
    snapshots, and exemplars. Note this is niche-scoped housekeeping, not the
    right-to-be-forgotten path — for a person asking to be removed, use
    src/jobs/forget.py, which is explicit about what it erases.
    """
    name = normalise_niche_name(name)
    if get_niche(name) is None:
        raise NicheError(f"No niche called {name!r}.")

    client = get_client()
    removed: dict[str, int] = {}

    if purge_data:
        dids = [
            r["did"]
            for r in (
                client.table("authors").select("did").eq("niche", name).execute().data
                or []
            )
        ]
        for i in range(0, len(dids), 100):
            client.table("authors").delete().in_("did", dids[i : i + 100]).execute()
        removed["authors"] = len(dids)
        # Posts an author shares with another niche go too; authors.niche records
        # the niche we first saw them in, so this is a blunt instrument. That is
        # why it is opt-in.
        client.table("performance_baselines").delete().eq("scope", "niche").eq(
            "scope_key", name
        ).execute()

    client.table("niches").delete().eq("name", name).execute()
    removed["niches"] = 1
    return removed


# ---------------------------------------------------------------------------
# Tracked authors
#
# Keyword search is a sample of a niche; an author feed is a census of one
# account in it. Tracking the accounts that reliably perform is what turns a
# thin, scattershot corpus into a dense one.
# ---------------------------------------------------------------------------


def list_tracked_authors(niche: str | None = None, active_only: bool = True) -> list[dict]:
    """Authors whose feeds get collected, optionally for one niche."""
    query = get_client().table("tracked_authors").select("*")
    if niche:
        query = query.eq("niche", normalise_niche_name(niche))
    if active_only:
        query = query.eq("active", True)
    rows = query.execute().data or []
    return sorted(rows, key=lambda r: ((r.get("handle") or r["did"]).lower()))


def track_author(did: str, handle: str | None, niche: str) -> dict:
    """Start collecting an author's feed for a niche. Idempotent."""
    niche = normalise_niche_name(niche)
    if not did.startswith("did:"):
        raise NicheError(f"Expected a DID, got {did!r}. Resolve the handle first.")
    if get_niche(niche) is None:
        raise NicheError(f"No niche called {niche!r}.")

    row = {
        "did": did,
        "handle": handle,
        "niche": niche,
        "active": True,
        "added_at": iso(utcnow()),
    }
    upsert("tracked_authors", [row], on_conflict="did,niche")
    return row


def untrack_author(did: str, niche: str | None = None) -> int:
    """Stop collecting an author. Returns how many tracking rows were removed.

    Collected posts are left alone — this stops future collection, it is not a
    deletion request. For that, see src/jobs/forget.py.
    """
    query = get_client().table("tracked_authors").delete().eq("did", did)
    if niche:
        query = query.eq("niche", normalise_niche_name(niche))
    return len(query.execute().data or [])


def suggest_authors(niche: str, limit: int = 10) -> list[dict]:
    """Authors worth tracking, ranked from engagement we have already measured.

    Self-bootstrapping on purpose: the accounts that repeatedly clear the bar in
    a niche are visible in our own snapshots, so this needs no third-party
    dataset. Requires at least MIN_SUGGEST_POSTS measured posts per author, since
    one lucky post says nothing about an account.

    Applies the same follower floor as the exemplar pool — without it this would
    recommend tracking 7-follower accounts whose engagement_rate is an artefact
    of dividing by a tiny number.
    """
    niche = normalise_niche_name(niche)
    client = get_client()

    posts = (
        client.table("posts")
        .select("uri, author_did")
        .eq("niche", niche)
        .execute()
        .data
        or []
    )
    if not posts:
        return []

    by_uri = {p["uri"]: p["author_did"] for p in posts if p.get("author_did")}
    rates: dict[str, list[float]] = {}
    uris = list(by_uri)
    for i in range(0, len(uris), 100):
        rows = (
            client.table("engagement_snapshots")
            .select("post_uri, engagement_rate")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        )
        for r in rows:
            if r["engagement_rate"] is None:
                continue
            rates.setdefault(by_uri[r["post_uri"]], []).append(float(r["engagement_rate"]))

    if not rates:
        return []

    authors = {
        a["did"]: a
        for a in (
            client.table("authors")
            .select("did, handle, follower_count")
            .in_("did", list(rates)[:100])
            .execute()
            .data
            or []
        )
    }
    already = {t["did"] for t in list_tracked_authors(niche, active_only=False)}

    out = []
    for did, values in rates.items():
        author = authors.get(did)
        if not author or did in already:
            continue
        if (author.get("follower_count") or 0) < MIN_SUGGEST_FOLLOWERS:
            continue
        if len(values) < MIN_SUGGEST_POSTS:
            continue
        out.append(
            {
                "did": did,
                "handle": author.get("handle"),
                "follower_count": author.get("follower_count"),
                "measured_posts": len(values),
                "mean_engagement_rate": round(sum(values) / len(values), 6),
            }
        )
    out.sort(key=lambda a: a["mean_engagement_rate"], reverse=True)
    return out[:limit]


# Mirrors refresh_exemplars.MIN_FOLLOWERS; duplicated rather than imported to
# keep db.py free of job imports.
MIN_SUGGEST_FOLLOWERS = 200
# One good post is luck. This is the floor for calling an account consistent.
MIN_SUGGEST_POSTS = 2


@dataclass(frozen=True)
class RssSource:
    name: str
    url: str
    platform_tags: list[str] = field(default_factory=list)


def load_rss_sources() -> list[RssSource]:
    """Return the curated feed list from config/rss_sources.yaml."""
    raw = yaml.safe_load((CONFIG_DIR / "rss_sources.yaml").read_text(encoding="utf-8"))
    sources = (raw or {}).get("sources") or []
    out: list[RssSource] = []
    for item in sources:
        if not item.get("url"):
            log.warning("RSS source %r has no url; skipping.", item.get("name"))
            continue
        out.append(
            RssSource(
                name=str(item.get("name") or item["url"]),
                url=str(item["url"]),
                platform_tags=[str(t) for t in (item.get("platform_tags") or [])],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def chunked(rows: Sequence[Any], size: int = BATCH_SIZE) -> Iterator[Sequence[Any]]:
    """Yield fixed-size slices so a big write does not become one huge request."""
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def upsert(table: str, rows: Sequence[dict], on_conflict: str) -> int:
    """Upsert rows in batches. Returns the number of rows sent.

    `on_conflict` must name a column or unique constraint's columns, otherwise
    PostgREST rejects the request.
    """
    if not rows:
        return 0
    client = get_client()
    sent = 0
    for batch in chunked(rows):
        client.table(table).upsert(list(batch), on_conflict=on_conflict).execute()
        sent += len(batch)
    return sent


def insert(table: str, rows: Sequence[dict]) -> int:
    """Plain insert in batches. Returns the number of rows sent."""
    if not rows:
        return 0
    client = get_client()
    sent = 0
    for batch in chunked(rows):
        client.table(table).insert(list(batch)).execute()
        sent += len(batch)
    return sent


def existing_post_uris(uris: Iterable[str]) -> set[str]:
    """Subset of `uris` already present in posts.

    Used by ingest to skip re-processing. Chunked because the URI list goes into
    the query string and PostgREST/Cloudflare will reject an over-long URL.
    """
    uri_list = list(uris)
    if not uri_list:
        return set()
    client = get_client()
    found: set[str] = set()
    for batch in chunked(uri_list, 100):
        resp = client.table("posts").select("uri").in_("uri", list(batch)).execute()
        found.update(row["uri"] for row in resp.data or [])
    return found


def rpc(name: str, params: dict) -> list[dict]:
    """Call a Postgres function defined in the migration."""
    return get_client().rpc(name, params).execute().data or []


# ---------------------------------------------------------------------------
# job_runs bookkeeping
# ---------------------------------------------------------------------------


class JobRun:
    """Context manager that brackets a job with a job_runs row.

    Usage:
        with JobRun("ingest", dry_run=args.dry_run) as run:
            run.count("posts_upserted", n)
            run.note("skipped 3 no-index authors")

    On a clean exit the status is 'success', or 'partial' if the job called
    `run.partial()` (e.g. one niche failed but others succeeded). On an
    exception the status is 'failure', the traceback summary lands in notes,
    and the exception re-raises so the Actions run goes red.

    A row is written with status NULL at start and finalised at exit, so a job
    that is SIGKILLed (Actions timeout, runner eviction, OOM) leaves NULL behind
    permanently — indistinguishable from "currently running". cleanup.py reaps
    those; see reap_stuck_job_runs().

    In --dry-run mode nothing is written to job_runs either; the summary is
    logged instead. A dry run must not touch the database at all.
    """

    def __init__(self, job_name: str, dry_run: bool = False) -> None:
        self.job_name = job_name
        self.dry_run = dry_run
        self.started_at = utcnow()
        self.row_id: int | None = None
        self._counts: dict[str, int] = {}
        self._notes: list[str] = []
        self._partial = False

    def count(self, key: str, n: int = 1) -> None:
        """Accumulate a named counter reported in job_runs.notes."""
        self._counts[key] = self._counts.get(key, 0) + n

    def note(self, message: str) -> None:
        """Attach a free-text note; also logged immediately."""
        log.info("[%s] %s", self.job_name, message)
        self._notes.append(message)

    def partial(self) -> None:
        """Mark the run as degraded but not failed."""
        self._partial = True

    def summary(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self._counts.items()))
        parts = [p for p in (counts, "; ".join(self._notes)) if p]
        return " | ".join(parts) or "no activity"

    def __enter__(self) -> "JobRun":
        prefix = "DRY RUN " if self.dry_run else ""
        log.info("%s%s starting", prefix, self.job_name)
        if not self.dry_run:
            resp = (
                get_client()
                .table("job_runs")
                .insert(
                    {
                        "job_name": self.job_name,
                        "started_at": iso(self.started_at),
                        "status": None,
                    }
                )
                .execute()
            )
            self.row_id = resp.data[0]["id"]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            status = "failure"
            notes = f"{exc_type.__name__}: {exc} | {self.summary()}"
        elif self._partial:
            status = "partial"
            notes = self.summary()
        else:
            status = "success"
            notes = self.summary()

        log.info("[%s] %s — %s", self.job_name, status, notes)

        if not self.dry_run and self.row_id is not None:
            try:
                get_client().table("job_runs").update(
                    {
                        "finished_at": iso(utcnow()),
                        "status": status,
                        # notes is unbounded text but keep rows sane.
                        "notes": notes[:4000],
                    }
                ).eq("id", self.row_id).execute()
            except Exception:  # noqa: BLE001 — bookkeeping must not mask the real error
                log.exception("Failed to finalise job_runs row %s", self.row_id)

        return False  # never swallow the exception


def reap_stuck_job_runs(older_than_hours: int = 6) -> int:
    """Mark long-abandoned NULL-status job_runs as 'failure'. Returns the count.

    A job killed mid-run (Actions timeout, runner eviction, OOM) never reaches
    JobRun.__exit__, so its row keeps status NULL forever and reads as "still
    running". Nothing here runs for more than ~30 minutes, so anything NULL and
    older than 6 hours is definitively dead.

    Without this, `select * from job_runs where status is null` — the obvious way
    to ask "is anything wrong?" — slowly fills with corpses and stops being useful.
    """
    cutoff = iso(utcnow() - timedelta(hours=older_than_hours))
    client = get_client()
    stuck = (
        client.table("job_runs")
        .select("id")
        .is_("status", "null")
        .lt("started_at", cutoff)
        .execute()
        .data
        or []
    )
    if not stuck:
        return 0
    for row in stuck:
        client.table("job_runs").update(
            {
                "status": "failure",
                "finished_at": iso(utcnow()),
                "notes": (
                    f"Reaped by cleanup: no completion recorded within "
                    f"{older_than_hours}h. The process was killed before it could "
                    f"finalise (Actions timeout, eviction, or OOM)."
                ),
            }
        ).eq("id", row["id"]).execute()
    return len(stuck)


def configure_logging(verbose: bool = False) -> None:
    """Consistent log format across jobs; called from each job's main()."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are chatty at INFO and drown out job output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
