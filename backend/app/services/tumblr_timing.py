"""Watch Tumblr blogs and measure their posts at known ages.

WHY THIS EXISTS. Every timing question asked of the existing Tumblr corpus failed to
reproduce — posting hour scored +0.06 against a 0.30 gate, and tag count and post length
were no better. The cause is not the sample size (6,732 scored posts, more than Mastodon
needed) but its shape: the median post was 55 days old when collected, and its note count is
a lifetime total. Tumblr notes accrue through reblogs for weeks, so that number records how
far a post travelled, not anything decidable when it was published. No statistic recovers a
posting-hour effect from it, and collecting more of the same never will.

So this collects the other kind of data: posts caught while they are new, each measured again
at fixed ages. Notes at 48 hours are comparable between two posts in a way lifetime notes
never are.

RESUMABILITY IS THE WHOLE DESIGN. This runs for weeks against a desktop app that is closed
every evening, updated mid-week, and occasionally killed. So there is no progress state in
memory at all:

  * what to sweep next is a query (`due_blogs`), not a cursor;
  * every write is idempotent — posts are keyed by their Tumblr URI, snapshots by
    (post, window), both INSERT OR IGNORE;
  * a sweep interrupted halfway leaves the blogs it finished marked and the rest due, so
    the next one continues rather than restarting.

Kill it at any moment and the only loss is the request in flight.

WINDOWS ARE NOT APPROXIMATE. A snapshot is only recorded if the post is genuinely near that
age — see TOLERANCES. If the app was closed across a post's 24-hour mark, that window is
recorded as missed rather than filled with a 70-hour reading, because a mislabelled
measurement is worse than a missing one: it would quietly poison the very statistic this
exists to make trustworthy.

CREDENTIALS ARE NEVER STORED. The backend does not persist Tumblr keys — Electron holds them
in safeStorage and hands them over per launch (see `set_credentials`). A watcher that
outlives the app's memory would have to keep a consumer key on disk, and this app's rule is
that it does not.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .. import config
from . import tumblr
from .tumblr import Credentials, TumblrError

log = logging.getLogger(__name__)

#: Ages at which a post is measured, matching the labels the Mastodon collector already
#: writes so the posting-time machinery reads both without special cases.
WINDOWS: tuple[tuple[str, float], ...] = (("1h", 1.0), ("24h", 24.0), ("48h", 48.0))

#: How far past a window's nominal age a reading still counts, in hours. Generous enough to
#: survive an app that is closed overnight, tight enough that the label stays true: a "24h"
#: figure taken at 36 hours is still a young post, one taken at 70 is a different thing.
TOLERANCES: dict[str, float] = {"1h": 3.0, "24h": 12.0, "48h": 24.0}

#: A blog is re-read at most this often. The 48h window only needs a reading once or twice a
#: day, but posts appearing between sweeps are what fills the corpus, so hourly it is.
SWEEP_INTERVAL_HOURS = 1.0

#: Tumblr returns 20 posts per call and allows 1,000 requests an hour per consumer key. One
#: request per blog per sweep keeps a 150-blog watch list at ~3,600/day, 15% of the ceiling,
#: leaving Engage and the generators their share.
POSTS_PER_CALL = 20

#: Posts older than this when first seen are recorded but never measured — their early
#: windows are already gone, and a lifetime total is the thing this exists to avoid.
MAX_AGE_AT_DISCOVERY_HOURS = 6.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tumblr_watch (
    blog            TEXT PRIMARY KEY,
    added_at        TEXT NOT NULL,
    last_swept_at   TEXT,
    last_error      TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tumblr_timing_posts (
    post_uri        TEXT PRIMARY KEY,
    blog            TEXT NOT NULL,
    post_id         TEXT NOT NULL,
    post_url        TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    text            TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    has_media       INTEGER NOT NULL DEFAULT 0,
    -- Set once every window has been resolved, so finished posts drop out of the work
    -- query instead of being re-examined on every sweep for the rest of the collection.
    settled         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS tumblr_timing_posts_open
    ON tumblr_timing_posts (settled, created_at);

CREATE TABLE IF NOT EXISTS tumblr_timing_snapshots (
    post_uri        TEXT NOT NULL,
    window_label    TEXT NOT NULL,
    notes           INTEGER,
    -- How old the post actually was when read, so a later analysis can check the label
    -- rather than trust it.
    age_hours       REAL,
    captured_at     TEXT NOT NULL,
    -- 'ok' or 'missed'. A missed window is recorded rather than left absent: without it
    -- the sweep would reconsider the same overdue post forever.
    outcome         TEXT NOT NULL DEFAULT 'ok',
    PRIMARY KEY (post_uri, window_label)
);
"""


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Credentials — held in memory for the life of the process, never written down
# ---------------------------------------------------------------------------

_credentials: Credentials | None = None


def set_credentials(creds: Credentials | None) -> bool:
    """Hand the watcher its Tumblr keys, or clear them. Returns whether it can now run."""
    global _credentials
    _credentials = creds if (creds and creds.complete) else None
    return _credentials is not None


def has_credentials() -> bool:
    return _credentials is not None


# ---------------------------------------------------------------------------
# The watch list
# ---------------------------------------------------------------------------


def watch(blogs: list[str]) -> dict:
    """Add blogs to the watch list. Idempotent — re-adding one keeps its sweep history."""
    added = 0
    with _connect() as conn:
        for raw in blogs:
            try:
                name = tumblr.normalise_blog(raw)
            except TumblrError:
                continue
            if not name:
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO tumblr_watch (blog, added_at) VALUES (?, ?)",
                (name, _iso(_now())),
            )
            added += cursor.rowcount
    return {"added": added, "watching": watch_count()}


def unwatch(blog: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE tumblr_watch SET active = 0 WHERE blog = ?", (blog,))


def watch_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT count(*) FROM tumblr_watch WHERE active = 1").fetchone()[0]


def due_blogs(limit: int = 200) -> list[str]:
    """Blogs ready for another read, oldest first.

    The work queue, computed rather than remembered. A sweep that dies halfway through leaves
    the blogs it already did with a fresh `last_swept_at` and the rest untouched, so the next
    sweep picks up exactly where it stopped with no bookkeeping of its own.
    """
    cutoff = _iso(_now() - timedelta(hours=SWEEP_INTERVAL_HOURS))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT blog FROM tumblr_watch
            WHERE active = 1 AND (last_swept_at IS NULL OR last_swept_at < ?)
            ORDER BY last_swept_at IS NOT NULL, last_swept_at
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    return [row["blog"] for row in rows]


# ---------------------------------------------------------------------------
# One sweep
# ---------------------------------------------------------------------------


@dataclass
class SweepResult:
    blogs_swept: int = 0
    posts_discovered: int = 0
    snapshots_written: int = 0
    windows_missed: int = 0
    blogs_failed: int = 0

    def as_dict(self) -> dict:
        return {
            "blogsSwept": self.blogs_swept,
            "postsDiscovered": self.posts_discovered,
            "snapshotsWritten": self.snapshots_written,
            "windowsMissed": self.windows_missed,
            "blogsFailed": self.blogs_failed,
        }


def sweep(limit: int = 200) -> SweepResult:
    """Read every due blog once: record new posts, measure the ones that have come of age.

    Safe to call again at any time, from anywhere. Everything it does is idempotent, so a
    second sweep started while the first is finishing does no damage — it simply finds less
    to do.
    """
    result = SweepResult()
    if _credentials is None:
        return result

    for blog in due_blogs(limit):
        try:
            posts = tumblr.request(
                _credentials,
                "GET",
                f"/blog/{tumblr.blog_path(blog)}/posts",
                params={"limit": POSTS_PER_CALL, "npf": "true", "reblog_info": "false"},
            )
        except TumblrError as err:
            result.blogs_failed += 1
            _mark_swept(blog, str(err)[:200])
            continue

        entries = (posts or {}).get("posts") or []
        result.posts_discovered += _record_posts(blog, entries)
        written, missed = _record_snapshots(blog, entries)
        result.snapshots_written += written
        result.windows_missed += missed
        _mark_swept(blog, "")
        result.blogs_swept += 1

    return result


def _mark_swept(blog: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE tumblr_watch SET last_swept_at = ?, last_error = ? WHERE blog = ?",
            (_iso(_now()), error, blog),
        )


def _post_uri(blog: str, post_id: str) -> str:
    return f"tumblr://{blog}/{post_id}"


def _record_posts(blog: str, entries: list[dict]) -> int:
    """Store posts young enough to still have their early windows ahead of them."""
    now = _now()
    added = 0
    with _connect() as conn:
        for entry in entries:
            post_id = str(entry.get("id_string") or entry.get("id") or "").strip()
            created = _parse_timestamp(entry)
            if not post_id or created is None:
                continue
            age = (now - created).total_seconds() / 3600
            # Recorded only if caught early. A post first seen at three days old cannot be
            # measured at one hour, and admitting it would put lifetime-shaped rows back
            # into the corpus this exists to replace.
            if age > MAX_AGE_AT_DISCOVERY_HOURS or age < -1:
                continue
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO tumblr_timing_posts
                    (post_uri, blog, post_id, post_url, created_at, first_seen_at,
                     text, tags, has_media)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _post_uri(blog, post_id),
                    blog,
                    post_id,
                    str(entry.get("post_url") or ""),
                    _iso(created),
                    _iso(now),
                    tumblr.npf_text(entry)[:8000],
                    json.dumps([str(t) for t in (entry.get("tags") or [])]),
                    1 if _has_media(entry) else 0,
                ),
            )
            added += cursor.rowcount
    return added


def _parse_timestamp(entry: dict) -> datetime | None:
    stamp = entry.get("timestamp")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return datetime.fromtimestamp(float(stamp), timezone.utc)
    return _parse(entry.get("date"))


def _has_media(entry: dict) -> bool:
    for block in entry.get("content") or []:
        if isinstance(block, dict) and block.get("type") in ("image", "video", "audio"):
            return True
    return bool(entry.get("photos"))


def _record_snapshots(blog: str, entries: list[dict]) -> tuple[int, int]:
    """Measure any watched post of this blog that has reached a window it has not recorded.

    Note counts come from the same listing that discovered the post, so measuring costs no
    extra requests — the sweep that keeps the corpus growing is also the one that reads it.
    """
    now = _now()
    notes_by_id = {
        str(e.get("id_string") or e.get("id") or ""): e.get("note_count")
        for e in entries
    }
    written = missed = 0

    with _connect() as conn:
        open_posts = conn.execute(
            "SELECT post_uri, post_id, created_at FROM tumblr_timing_posts "
            "WHERE blog = ? AND settled = 0",
            (blog,),
        ).fetchall()

        for row in open_posts:
            created = _parse(row["created_at"])
            if created is None:
                continue
            age = (now - created).total_seconds() / 3600
            done = {
                r["window_label"]
                for r in conn.execute(
                    "SELECT window_label FROM tumblr_timing_snapshots WHERE post_uri = ?",
                    (row["post_uri"],),
                )
            }

            for label, target in WINDOWS:
                if label in done or age < target:
                    continue
                within = age <= target + TOLERANCES[label]
                notes = notes_by_id.get(row["post_id"])
                if within and notes is None:
                    # The post has aged in but is not in this page of results any more.
                    # Leave the window open; a later sweep may still catch it in tolerance.
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tumblr_timing_snapshots
                        (post_uri, window_label, notes, age_hours, captured_at, outcome)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["post_uri"],
                        label,
                        int(notes) if (within and notes is not None) else None,
                        round(age, 2),
                        _iso(now),
                        "ok" if within else "missed",
                    ),
                )
                if within:
                    written += 1
                else:
                    missed += 1

            # Settled once nothing is left open, so the next sweep skips it entirely.
            resolved = {
                r["window_label"]
                for r in conn.execute(
                    "SELECT window_label FROM tumblr_timing_snapshots WHERE post_uri = ?",
                    (row["post_uri"],),
                )
            }
            if all(label in resolved for label, _ in WINDOWS):
                conn.execute(
                    "UPDATE tumblr_timing_posts SET settled = 1 WHERE post_uri = ?",
                    (row["post_uri"],),
                )

    return written, missed


# ---------------------------------------------------------------------------
# Where the collection stands
# ---------------------------------------------------------------------------


def progress() -> dict:
    """Enough to answer "is this working, and how much longer" without reading the tables.

    The target figures come from the two networks that already have curves: mastodon.social
    produced a usable one from 2,613 posts across 25 accounts, so 2,500 posts from 100 blogs
    is the bar this collection is aiming at.
    """
    with _connect() as conn:
        watching = conn.execute("SELECT count(*) FROM tumblr_watch WHERE active = 1").fetchone()[0]
        swept = conn.execute(
            "SELECT count(*) FROM tumblr_watch WHERE active = 1 AND last_swept_at IS NOT NULL"
        ).fetchone()[0]
        discovered = conn.execute("SELECT count(*) FROM tumblr_timing_posts").fetchone()[0]
        measured = conn.execute(
            "SELECT count(DISTINCT post_uri) FROM tumblr_timing_snapshots "
            "WHERE window_label = '48h' AND outcome = 'ok'"
        ).fetchone()[0]
        blogs_measured = conn.execute(
            "SELECT count(DISTINCT p.blog) FROM tumblr_timing_posts p "
            "JOIN tumblr_timing_snapshots s ON s.post_uri = p.post_uri "
            "WHERE s.window_label = '48h' AND s.outcome = 'ok'"
        ).fetchone()[0]
        missed = conn.execute(
            "SELECT count(*) FROM tumblr_timing_snapshots WHERE outcome = 'missed'"
        ).fetchone()[0]
        earliest = conn.execute("SELECT min(first_seen_at) FROM tumblr_timing_posts").fetchone()[0]

    return {
        "hasCredentials": has_credentials(),
        "watching": watching,
        "everSwept": swept,
        "postsDiscovered": discovered,
        "postsMeasured48h": measured,
        "blogsMeasured": blogs_measured,
        "windowsMissed": missed,
        "collectingSince": earliest,
        "target": {"posts": 2500, "blogs": 100},
    }


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

#: How often the thread wakes. Far shorter than SWEEP_INTERVAL_HOURS on purpose: the tick
#: only asks the database what is due, which is cheap, and a short tick means a blog added
#: mid-session starts being read within minutes rather than at the top of the hour.
_TICK_SECONDS = 300

_scheduler_thread = None


def _scheduler_loop() -> None:
    import time

    while True:
        try:
            # Inert on two counts, both deliberate: an install that has never set up Tumblr
            # should open no sockets and log no failures, and one with keys but no watch
            # list has nothing to read. Checking the watch list is a local count, so this
            # costs nothing on the installs where it does not apply.
            if has_credentials() and watch_count() > 0:
                result = sweep()
                if result.blogs_swept or result.snapshots_written:
                    log.info(
                        "[tumblr-timing] swept %d blogs, %d new posts, %d measurements, %d missed",
                        result.blogs_swept,
                        result.posts_discovered,
                        result.snapshots_written,
                        result.windows_missed,
                    )
        except Exception:  # noqa: BLE001 — a bad tick must not end a three-week collection
            log.exception("[tumblr-timing] sweep tick failed")
        time.sleep(_TICK_SECONDS)


def start_scheduler() -> None:
    """Start the watcher in the background. Safe to call once at startup.

    Starting it unconditionally is right even with no credentials: the collection is a
    multi-week affair, and requiring someone to visit a screen to restart it after every app
    update is how a corpus quietly stops growing. The loop decides for itself whether there
    is anything to do.
    """
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    import threading

    init_db()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="tumblr-timing", daemon=True
    )
    _scheduler_thread.start()
    log.info("[tumblr-timing] watcher started (%d blogs watched)", watch_count())


# ---------------------------------------------------------------------------
# Choosing whom to watch
# ---------------------------------------------------------------------------

#: A blog must have been posting this recently to be worth watching. The catalogue's own
#: freshness sets the floor — its sitemap readings are days old by the time anyone reads
#: them — so this is deliberately loose.
CANDIDATE_MAX_AGE_DAYS = 45

#: Originals seen while the collector sampled the blog. This is the number that decides
#: whether a blog contributes anything at all: the statistic needs five or more posts from
#: the same blog, and a blog that mostly reblogs will not produce them in a month. Measured
#: on the catalogue — some blogs have 41,000 posts and two originals.
MIN_ORIGINALS_SEEN = 10

#: Notes its posts typically get. A floor, not a ranking: within-blog percentile needs the
#: blog's posts to differ from each other, and a blog whose every post scores zero gives
#: every post the same percentile and therefore no signal at all.
MIN_AUDIENCE_PROXY = 5.0


def candidates(limit: int = 150) -> list[dict]:
    """Blogs worth watching, best first, from the collector's catalogue.

    Read-only against the collector's own store, which may be running: this app is a
    consumer of that project's output, not a peer.

    Originals are a filter, audience is the ranking, and both halves of that matter.

    Filtering on originals excludes the reblog farms: the biggest audiences in the catalogue
    belong to blogs that reblog almost everything — one has 41,623 posts and two originals —
    and they would fill the list with accounts that never publish anything of their own.

    Ranking on originals does not work, because the count saturates: the collector sampled a
    fixed 300 posts per blog, so every prolific blog reports exactly 300 and the ordering
    among them becomes meaningless. It put a blog with 300 originals and an audience of 6
    above one with 298 and an audience of 427.

    So audience decides the order, once originals have decided eligibility. Audience is also
    the thing the statistic needs: within-blog percentile requires a blog's posts to differ
    from one another, and a blog nobody reblogs cannot supply that.
    """
    from .tumblr_corpus import DEFAULT_CORPUS_PATH

    if not DEFAULT_CORPUS_PATH.exists():
        return []

    cutoff = _iso(_now() - timedelta(days=CANDIDATE_MAX_AGE_DAYS))
    conn = sqlite3.connect(f"file:{DEFAULT_CORPUS_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT name, posts_total, original_seen, audience_proxy_notes, sitemap_lastmod
            FROM blogs
            WHERE sitemap_lastmod > ?
              AND original_seen >= ?
              AND audience_proxy_notes >= ?
            ORDER BY audience_proxy_notes DESC, original_seen DESC
            LIMIT ?
            """,
            (cutoff, MIN_ORIGINALS_SEEN, MIN_AUDIENCE_PROXY, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # An older catalogue without these columns is a reason to select nothing, not to
        # crash the screen asking for candidates.
        return []
    finally:
        conn.close()

    return [
        {
            "blog": row["name"],
            "originalsSeen": row["original_seen"],
            "postsTotal": row["posts_total"],
            "audienceProxy": round(row["audience_proxy_notes"] or 0, 1),
            "lastPosted": row["sitemap_lastmod"],
        }
        for row in rows
    ]


def seed(limit: int = 150) -> dict:
    """Fill the watch list from the catalogue. Safe to re-run — watch() is idempotent.

    Re-running after a few weeks tops the list up with blogs that have become active since,
    without disturbing the sweep history of the ones already there.
    """
    chosen = candidates(limit)
    if not chosen:
        return {"added": 0, "watching": watch_count(), "candidates": 0}
    result = watch([c["blog"] for c in chosen])
    result["candidates"] = len(chosen)
    return result
