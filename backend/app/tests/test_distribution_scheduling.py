"""Cancellation and scheduler-claim coverage for Distribute jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.routers import distribution


def _scheduled_job(app_db, *, due: bool = True) -> dict:
    offset = timedelta(minutes=-1 if due else 10)
    return app_db.add_distribution_job(
        "library-item",
        "bluesky",
        "scheduled",
        scheduled_at=(datetime.now(timezone.utc) + offset).isoformat(),
        payload='{"text":"hello"}',
    )


def test_cancel_scheduled_job_preserves_history_and_is_idempotent(app_db):
    job = _scheduled_job(app_db)
    assert [item["id"] for item in app_db.list_due_scheduled_jobs()] == [job["id"]]

    cancelled = distribution.cancel_scheduled_job(job["id"])

    assert cancelled["status"] == "cancelled"
    assert cancelled["scheduled_at"] == job["scheduled_at"]
    assert cancelled["payload"] == job["payload"]
    assert app_db.list_due_scheduled_jobs() == []
    assert distribution.cancel_scheduled_job(job["id"])["status"] == "cancelled"


def test_cancel_scheduled_job_reports_missing_and_non_cancellable_jobs(app_db):
    with pytest.raises(distribution.HTTPException) as missing:
        distribution.cancel_scheduled_job("missing-job")
    assert missing.value.status_code == 404

    sent = app_db.add_distribution_job(
        "library-item", "bluesky", "sent", payload='{"text":"already sent"}'
    )
    with pytest.raises(distribution.HTTPException) as conflict:
        distribution.cancel_scheduled_job(sent["id"])
    assert conflict.value.status_code == 409
    assert "no longer scheduled" in conflict.value.detail


def test_scheduler_claim_wins_once_and_blocks_late_cancellation(app_db):
    job = _scheduled_job(app_db)

    claimed = app_db.claim_scheduled_distribution_job(job["id"])

    assert claimed is not None
    assert claimed["status"] == "sending"
    assert app_db.claim_scheduled_distribution_job(job["id"]) is None
    with pytest.raises(distribution.HTTPException) as conflict:
        distribution.cancel_scheduled_job(job["id"])
    assert conflict.value.status_code == 409


def test_cancellation_after_due_snapshot_prevents_publish(app_db, monkeypatch):
    job = _scheduled_job(app_db)
    fired: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(distribution.activepieces_client, "list_flows", lambda: [])

    def cancel_during_preparation(payload: dict) -> dict:
        distribution.cancel_scheduled_job(job["id"])
        return payload

    monkeypatch.setattr(
        distribution, "_materialize_media_payload", cancel_during_preparation
    )
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda job_id, channel, payload: fired.append((job_id, channel, payload)),
    )

    distribution._fire_due_scheduled_jobs()

    assert fired == []
    assert app_db.get_distribution_job(job["id"])["status"] == "cancelled"


def test_preparation_failure_does_not_overwrite_concurrent_cancellation(
    app_db, monkeypatch
):
    job = _scheduled_job(app_db)
    monkeypatch.setattr(distribution.activepieces_client, "list_flows", lambda: [])

    def cancel_then_fail(_payload: dict) -> dict:
        distribution.cancel_scheduled_job(job["id"])
        raise distribution.HTTPException(status_code=400, detail="bad media")

    monkeypatch.setattr(distribution, "_materialize_media_payload", cancel_then_fail)
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("cancelled job was published")
        ),
    )

    distribution._fire_due_scheduled_jobs()

    stored = app_db.get_distribution_job(job["id"])
    assert stored["status"] == "cancelled"
    assert stored["error"] is None


def test_due_job_is_claimed_and_fired_only_once(app_db, monkeypatch):
    job = _scheduled_job(app_db)
    fired: list[str] = []
    monkeypatch.setattr(distribution.activepieces_client, "list_flows", lambda: [])
    monkeypatch.setattr(
        distribution,
        "fire_job",
        lambda job_id, _channel, _payload: fired.append(job_id),
    )

    distribution._fire_due_scheduled_jobs()
    distribution._fire_due_scheduled_jobs()

    assert fired == [job["id"]]
    assert app_db.get_distribution_job(job["id"])["status"] == "sending"
