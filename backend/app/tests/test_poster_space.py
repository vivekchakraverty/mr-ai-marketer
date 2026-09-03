"""The poster Space's own logic.

The Space ships in this repo but runs somewhere else, so nothing here had covered it — and it
is where every defect in cloud posting has been. Two of them cost real scheduled posts:

  * A pass was skipped whenever the outbox commit was unchanged, which is wrong because a job
    becomes due through the passage of TIME. The enqueue-time pass saw nothing due, recorded
    the commit, and every later tick returned early. The scheduled hour arrived with nothing
    looking at the queue.
  * The service-auth token for Bluesky's video service was bound to the method being called.
    It has to be bound to com.atproto.repo.uploadBlob, because what the video service does on
    the account's behalf is upload a blob.

Neither needs the network to test. Both are guarded here.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SPACE = Path(__file__).resolve().parents[3] / "resources" / "poster-space"


@pytest.fixture(scope="module")
def space(monkeypatch_session=None):
    """Import the Space's modules the way the Space itself does — as top-level names."""
    if not (_SPACE / "main.py").is_file():
        pytest.skip("poster Space source not present")
    sys.path.insert(0, str(_SPACE))
    try:
        import main  # noqa: PLC0415
        import networks  # noqa: PLC0415

        yield main, networks
    finally:
        sys.path.remove(str(_SPACE))


# --- when a job is due ------------------------------------------------------


def test_a_job_whose_time_has_passed_is_due(space) -> None:
    """Not "due this minute" — due at any point in the past. This is what makes a late pass
    harmless: a Space that wakes at 07:00 still sends the 03:00 post."""
    main, _ = space
    past = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    assert main._due({"dueAt": past}) is True


def test_a_future_job_is_not_due(space) -> None:
    main, _ = space
    later = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    assert main._due({"dueAt": later}) is False


def test_a_job_with_no_readable_time_is_treated_as_due(space) -> None:
    """Better out now than stuck in the queue forever."""
    main, _ = space
    assert main._due({}) is True
    assert main._due({"dueAt": "not a timestamp"}) is True


# --- the guard that ate two scheduled posts ---------------------------------


def test_an_unchanged_outbox_is_still_examined_once_something_comes_due(space, monkeypatch) -> None:
    """THE regression. Skipping on the commit alone means a job that becomes due while the
    outbox sits still is never looked at again, and the post simply never goes out."""
    main, _ = space
    monkeypatch.setattr(main.store, "configured", lambda: True)
    monkeypatch.setattr(main.store, "head_sha", lambda: "same-sha")
    monkeypatch.setattr(main.store, "list_queue", lambda: [])

    # A pass has run against this commit, and the soonest job was due in the past.
    monkeypatch.setattr(main, "_last_sha", "same-sha")
    monkeypatch.setattr(main, "_next_due_at", datetime.now(timezone.utc) - timedelta(minutes=1))
    main._state["lastTickAt"] = "2026-09-03T00:00:00Z"

    looked = {"n": 0}

    def counting_queue() -> list[str]:
        looked["n"] += 1
        return []

    monkeypatch.setattr(main.store, "list_queue", counting_queue)
    main.run_pass()
    assert looked["n"] == 1, "a job past its due time must be examined even on an unchanged outbox"


def test_an_unchanged_outbox_with_nothing_due_yet_is_skipped(space, monkeypatch) -> None:
    """The optimisation still has to work, or every idle tick lists the whole queue."""
    main, _ = space
    monkeypatch.setattr(main.store, "configured", lambda: True)
    monkeypatch.setattr(main.store, "head_sha", lambda: "same-sha")
    monkeypatch.setattr(main, "_last_sha", "same-sha")
    monkeypatch.setattr(main, "_next_due_at", datetime.now(timezone.utc) + timedelta(hours=2))
    main._state["lastTickAt"] = "2026-09-03T00:00:00Z"

    looked = {"n": 0}
    monkeypatch.setattr(main.store, "list_queue", lambda: looked.__setitem__("n", looked["n"] + 1) or [])
    main.run_pass()
    assert looked["n"] == 0


# --- the Bluesky video credential -------------------------------------------


def test_the_upload_token_is_bound_to_the_blob_lexicon(space) -> None:
    """Bluesky rejects a token bound to the method being called, verbatim:

        invalid token lexicon method "app.bsky.video.uploadVideo",
        should be com.atproto.repo.uploadBlob
    """
    _, networks = space
    assert networks.UPLOAD_LXM == "com.atproto.repo.uploadBlob"


def test_the_audience_is_the_accounts_own_pds(space) -> None:
    """Not the video service, and not the host we connect through. Everyone reaches Bluesky
    via the bsky.social entryway while each account lives on a specific server behind it."""
    _, networks = space
    session = {
        "didDoc": {
            "service": [
                {
                    "id": "#atproto_pds",
                    "type": "AtprotoPersonalDataServer",
                    "serviceEndpoint": "https://stropharia.us-west.host.bsky.network",
                }
            ]
        }
    }
    aud = networks._pds_did("https://bsky.social", "did:plc:whatever", session)
    assert aud == "did:web:stropharia.us-west.host.bsky.network"


def test_an_unresolvable_pds_is_reported_rather_than_guessed(space, monkeypatch) -> None:
    """Falling back to the entryway host would produce Bluesky's opaque refusal much later."""
    _, networks = space
    monkeypatch.setattr(
        networks.httpx, "Client", lambda **_k: (_ for _ in ()).throw(networks.httpx.HTTPError("no"))
    )
    with pytest.raises(networks.PostError) as excinfo:
        networks._pds_did("https://bsky.social", "did:plc:whatever", {"didDoc": {"service": []}})
    assert "which Bluesky server" in str(excinfo.value)


# --- the record key ---------------------------------------------------------


def test_the_record_key_is_a_valid_tid(space) -> None:
    """Bluesky rejects anything else, verbatim:

        Invalid record key for app.bsky.feed.post:
        Invalid TID string (got "c296d17caec24cc78a54a65334cf79cd")
    """
    import re

    _, networks = space
    for job_id in ("9d584c87-8270-43c5-ad22-05980fd9f7bd", "c296d17c-aec2-4cc7-8a54-a65334cf79cd"):
        tid = networks.tid_for(job_id)
        assert re.fullmatch(r"[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}", tid), tid


def test_the_record_key_is_stable_for_a_job(space) -> None:
    """This is what stops a retry becoming a second post: putRecord at a fixed key overwrites
    its own record. A time-based TID would differ on every attempt."""
    _, networks = space
    assert networks.tid_for("job-a") == networks.tid_for("job-a")
    assert networks.tid_for("job-a") != networks.tid_for("job-b")
