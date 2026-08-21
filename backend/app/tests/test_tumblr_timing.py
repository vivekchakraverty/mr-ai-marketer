"""The Tumblr timing watcher — mostly a test that it can be interrupted.

This collects for weeks against an app that is closed every evening and updated mid-week, so
"resumable" is not a nice property here, it is the feature. The tests that matter are the
ones that kill a sweep and start another.

No network: `tumblr.request` is replaced with a fake blog. What is under test is the
bookkeeping, and pinning that to Tumblr's uptime would make it worthless.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import tumblr_timing as tt
from app.services.tumblr import Credentials


@pytest.fixture()
def store(app_db, monkeypatch):
    tt.init_db()
    monkeypatch.setattr(
        tt, "_credentials", Credentials("ck", "cs", "tok", "tsec", "someblog.tumblr.com")
    )
    return tt


def _post(post_id: str, age_hours: float, notes: int) -> dict:
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {
        "id_string": post_id,
        "timestamp": int(created.timestamp()),
        "post_url": f"https://someblog.tumblr.com/post/{post_id}",
        "note_count": notes,
        "tags": ["art", "wip"],
        "content": [{"type": "text", "text": "a post"}],
    }


def _serve(entries: list[dict]):
    """A fake Tumblr that always answers with the same page of posts."""
    def request(_creds, _method, _path, **_kwargs):
        return {"posts": entries}

    return request


def test_a_blog_is_only_swept_once_an_hour(store, monkeypatch):
    """The work queue is a query, so this is what stops a restart loop hammering Tumblr."""
    monkeypatch.setattr(tt.tumblr, "request", _serve([_post("1", 0.5, 3)]))
    tt.watch(["someblog"])

    assert tt.due_blogs() == ["someblog.tumblr.com"]
    assert tt.sweep().blogs_swept == 1
    # Immediately after, nothing is due — the interval is enforced from stored state, not
    # from a timer that a restart would reset.
    assert tt.due_blogs() == []
    assert tt.sweep().blogs_swept == 0


def test_an_interrupted_sweep_resumes_where_it_stopped(store, monkeypatch):
    """The property the whole design exists for.

    Half the blogs are read, then the process 'dies'. Nothing is remembered in memory, so the
    next sweep must pick up exactly the blogs that were left.
    """
    tt.watch(["one", "two", "three", "four"])

    seen: list[str] = []

    def flaky(_creds, _method, path, **_kwargs):
        seen.append(path)
        if len(seen) == 3:
            raise KeyboardInterrupt("power cut")
        return {"posts": [_post("1", 0.5, 1)]}

    monkeypatch.setattr(tt.tumblr, "request", flaky)
    with pytest.raises(KeyboardInterrupt):
        tt.sweep()

    # Two finished before the interruption; the rest are still owed a read.
    remaining = tt.due_blogs()
    assert len(remaining) == 2

    monkeypatch.setattr(tt.tumblr, "request", _serve([_post("1", 0.5, 1)]))
    assert tt.sweep().blogs_swept == 2
    assert tt.due_blogs() == []


def test_rediscovering_the_same_post_adds_nothing(store, monkeypatch):
    """Sweeps overlap constantly — the same page of posts is read again every hour."""
    entries = [_post("1", 0.5, 3), _post("2", 1.0, 9)]
    monkeypatch.setattr(tt.tumblr, "request", _serve(entries))
    tt.watch(["someblog"])

    assert tt.sweep().posts_discovered == 2
    tt._mark_swept("someblog.tumblr.com", "")
    with tt._connect() as conn:
        conn.execute("UPDATE tumblr_watch SET last_swept_at = NULL")
    assert tt.sweep().posts_discovered == 0, "a second reading must not duplicate posts"


def test_only_posts_caught_young_are_taken(store, monkeypatch):
    """A post found at three days old cannot be measured at one hour.

    Admitting it would put exactly the lifetime-shaped rows back into the corpus that this
    collector exists to replace.
    """
    monkeypatch.setattr(
        tt.tumblr, "request", _serve([_post("fresh", 1.0, 2), _post("stale", 72.0, 900)])
    )
    tt.watch(["someblog"])
    tt.sweep()

    with tt._connect() as conn:
        kept = [r["post_id"] for r in conn.execute("SELECT post_id FROM tumblr_timing_posts")]
    assert kept == ["fresh"]


def test_a_window_is_recorded_at_the_right_age(store, monkeypatch):
    monkeypatch.setattr(tt.tumblr, "request", _serve([_post("1", 0.5, 4)]))
    tt.watch(["someblog"])
    tt.sweep()

    # The post ages past 1h; the same listing now reports more notes.
    monkeypatch.setattr(tt.tumblr, "request", _serve([_post("1", 1.5, 11)]))
    with tt._connect() as conn:
        conn.execute("UPDATE tumblr_watch SET last_swept_at = NULL")
        conn.execute(
            "UPDATE tumblr_timing_posts SET created_at = ?",
            (tt._iso(datetime.now(timezone.utc) - timedelta(hours=1.5)),),
        )
    tt.sweep()

    with tt._connect() as conn:
        row = conn.execute(
            "SELECT window_label, notes, outcome FROM tumblr_timing_snapshots"
        ).fetchone()
    assert row["window_label"] == "1h"
    assert row["notes"] == 11
    assert row["outcome"] == "ok"


def test_a_window_missed_while_the_app_was_closed_is_marked_not_faked(store, monkeypatch):
    """The integrity rule.

    A 24-hour figure read at seventy hours is not a 24-hour figure. Recording it would
    quietly poison the statistic this collector exists to make trustworthy, so the window is
    written down as missed instead — which also stops the sweep reconsidering it forever.
    """
    monkeypatch.setattr(tt.tumblr, "request", _serve([_post("1", 0.5, 4)]))
    tt.watch(["someblog"])
    tt.sweep()

    with tt._connect() as conn:
        conn.execute("UPDATE tumblr_watch SET last_swept_at = NULL")
        conn.execute(
            "UPDATE tumblr_timing_posts SET created_at = ?",
            (tt._iso(datetime.now(timezone.utc) - timedelta(hours=200)),),
        )
    monkeypatch.setattr(tt.tumblr, "request", _serve([_post("1", 200.0, 5000)]))
    tt.sweep()

    with tt._connect() as conn:
        rows = {
            r["window_label"]: (r["notes"], r["outcome"])
            for r in conn.execute(
                "SELECT window_label, notes, outcome FROM tumblr_timing_snapshots"
            )
        }
        settled = conn.execute("SELECT settled FROM tumblr_timing_posts").fetchone()["settled"]

    assert set(rows) == {"1h", "24h", "48h"}
    assert all(outcome == "missed" for _, outcome in rows.values())
    assert all(notes is None for notes, _ in rows.values()), "a missed window stores no figure"
    assert settled == 1, "a fully resolved post drops out of the work queue"


def test_it_does_nothing_without_credentials(store, monkeypatch):
    """Inert until the app hands over keys, which it does per launch and never persists."""
    monkeypatch.setattr(tt, "_credentials", None)
    tt.watch(["someblog"])
    assert tt.sweep().blogs_swept == 0
    assert tt.progress()["hasCredentials"] is False


def test_progress_reports_against_the_target(store):
    p = tt.progress()
    assert p["target"] == {"posts": 2500, "blogs": 100}
    assert p["postsMeasured48h"] == 0


def test_candidate_selection_prefers_originals_over_reach(store, monkeypatch, tmp_path):
    """The rule that keeps reblog farms out of the watch list.

    A blog with an enormous audience and two original posts is worth nothing here: the
    statistic needs five or more of a blog's *own* posts, and it will never produce them.
    """
    import sqlite3

    catalogue = tmp_path / "corpus.sqlite3"
    conn = sqlite3.connect(catalogue)
    conn.execute(
        "CREATE TABLE blogs (name TEXT, posts_total INT, original_seen INT, "
        "audience_proxy_notes REAL, sitemap_lastmod TEXT)"
    )
    recent = tt._iso(datetime.now(timezone.utc) - timedelta(days=2))
    conn.executemany(
        "INSERT INTO blogs VALUES (?,?,?,?,?)",
        [
            ("reblogfarm", 41623, 2, 9744.0, recent),      # huge reach, no originals
            ("realposter", 1340, 149, 5635.0, recent),     # originals and an audience
            ("quietposter", 900, 80, 1.0, recent),         # originals, nobody reads them
            ("goneaway", 5000, 90, 500.0,
             tt._iso(datetime.now(timezone.utc) - timedelta(days=200))),  # long dormant
        ],
    )
    conn.commit()
    conn.close()
    from app.services import tumblr_corpus
    monkeypatch.setattr(tumblr_corpus, "DEFAULT_CORPUS_PATH", catalogue)

    picked = [c["blog"] for c in tt.candidates(50)]
    assert picked == ["realposter"], f"expected only the real poster, got {picked}"


def test_seeding_is_repeatable(store, monkeypatch, tmp_path):
    """Run again in a few weeks and it tops up without disturbing sweep history."""
    import sqlite3

    catalogue = tmp_path / "corpus.sqlite3"
    conn = sqlite3.connect(catalogue)
    conn.execute(
        "CREATE TABLE blogs (name TEXT, posts_total INT, original_seen INT, "
        "audience_proxy_notes REAL, sitemap_lastmod TEXT)"
    )
    recent = tt._iso(datetime.now(timezone.utc) - timedelta(days=2))
    conn.execute("INSERT INTO blogs VALUES (?,?,?,?,?)", ("alpha", 900, 60, 100.0, recent))
    conn.commit()
    conn.close()

    from app.services import tumblr_corpus
    monkeypatch.setattr(tumblr_corpus, "DEFAULT_CORPUS_PATH", catalogue)

    first = tt.seed(50)
    assert first["added"] == 1 and first["watching"] == 1
    second = tt.seed(50)
    assert second["added"] == 0, "re-seeding must not duplicate a watched blog"
    assert second["watching"] == 1
