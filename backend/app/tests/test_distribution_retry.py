"""Sending a failed post again.

History used to be read-only: it showed the engine's error and offered nothing to act on
it, so the only way forward was rebuilding the post by hand. That was defensible while a
failure meant the content was wrong. It stopped being defensible once one turned out to be
a timeout on a 29MB video — a post that needed no editing at all, only sending again.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.routers import distribution
from app.services import mastodon_delivery

_PAYLOAD = {
    "text": "In 1518, a woman in Strasbourg started dancing in the street.",
    "videoUrl": "/outputs/uploads/abc/The Dancing Plague.mp4",
    "videoFileAlt": "",
}


@pytest.fixture()
def failed_job(app_db):
    """One Mastodon video post that died the way the real one did."""
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "sending", payload=json.dumps(_PAYLOAD)
    )
    return app_db.update_distribution_job(
        job["id"],
        status="failed",
        error="Could not reach mastodon.social to upload the image: SSLWantWriteError",
    )


@pytest.fixture(autouse=True)
def _connected(monkeypatch):
    monkeypatch.setattr(mastodon_delivery, "has_credentials", lambda: True)


def test_a_retry_sends_the_same_post_and_clears_the_old_error(
    failed_job, app_db, monkeypatch
) -> None:
    """A stale explanation must not be left sitting next to a post that just succeeded."""
    monkeypatch.setattr(
        mastodon_delivery,
        "publish",
        lambda payload, idempotency_key: {"id": "999", "media_attachments": [{"id": "1"}]},
    )
    result = distribution.retry_failed_job(failed_job["id"])
    assert result["status"] == "sent"
    assert not result["error"]
    assert result["activepieces_run_id"] == "mastodon:999"


def test_a_retry_reuses_the_row_rather_than_filing_a_second_post(
    failed_job, app_db, monkeypatch
) -> None:
    """One row per intent. A copy per attempt would read as several posts having gone out."""
    monkeypatch.setattr(
        mastodon_delivery,
        "publish",
        lambda payload, idempotency_key: {"id": "999", "media_attachments": [{"id": "1"}]},
    )
    distribution.retry_failed_job(failed_job["id"])
    assert len(app_db.list_distribution_jobs()) == 1


def test_the_retry_keeps_the_original_idempotency_key(
    failed_job, monkeypatch
) -> None:
    """The key is the job id, and re-using it is what stops a lost response double-posting."""
    seen: dict[str, str] = {}

    def fake_publish(payload, idempotency_key):
        seen["key"] = idempotency_key
        seen["text"] = payload["text"]
        return {"id": "999", "media_attachments": [{"id": "1"}]}

    monkeypatch.setattr(mastodon_delivery, "publish", fake_publish)
    distribution.retry_failed_job(failed_job["id"])
    assert seen["key"] == failed_job["id"]
    assert seen["text"] == _PAYLOAD["text"]


def test_a_retry_that_fails_again_records_the_new_reason(
    failed_job, monkeypatch
) -> None:
    def boom(payload, idempotency_key):
        raise RuntimeError("still no good")

    monkeypatch.setattr(mastodon_delivery, "publish", boom)
    result = distribution.retry_failed_job(failed_job["id"])
    assert result["status"] == "failed"
    assert "still no good" in result["error"]


def test_only_a_failed_post_can_be_retried(app_db) -> None:
    """A sent post must not be sendable twice by opening its history row."""
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "sent", payload=json.dumps(_PAYLOAD)
    )
    with pytest.raises(HTTPException) as excinfo:
        distribution.retry_failed_job(job["id"])
    assert excinfo.value.status_code == 409


def test_an_unknown_job_is_a_404(app_db) -> None:
    with pytest.raises(HTTPException) as excinfo:
        distribution.retry_failed_job("no-such-job")
    assert excinfo.value.status_code == 404


def test_a_disconnected_mastodon_is_refused_before_the_row_is_touched(
    failed_job, app_db, monkeypatch
) -> None:
    """Firing without a credential would replace a real error with a misleading one."""
    monkeypatch.setattr(mastodon_delivery, "has_credentials", lambda: False)
    with pytest.raises(HTTPException) as excinfo:
        distribution.retry_failed_job(failed_job["id"])
    assert excinfo.value.status_code == 503
    unchanged = app_db.get_distribution_job(failed_job["id"])
    assert unchanged["status"] == "failed"
    assert "SSLWantWriteError" in unchanged["error"]
