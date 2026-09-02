"""Handing a scheduled post to the user's own poster Space, and getting it back.

The thing worth guarding here is not the happy path — it is that a post can never go out
twice, and can never be silently lost, while ownership moves between the local scheduler and
the cloud. Those are the two failures a user cannot recover from.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.routers import distribution
from app.services import cloud_poster

_PAYLOAD = {"text": "In 1518, a woman in Strasbourg started dancing in the street."}


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()


@pytest.fixture()
def cloud_ready(monkeypatch: pytest.MonkeyPatch):
    """A configured outbox whose calls are recorded rather than made."""
    calls: dict[str, list] = {"enqueue": [], "cancel": []}
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(
        cloud_poster,
        "enqueue",
        lambda job_id, channel, payload, due_at: calls["enqueue"].append((job_id, channel, due_at)),
    )
    monkeypatch.setattr(cloud_poster, "cancel", lambda job_id: calls["cancel"].append(job_id) or True)
    return calls


# --- ownership handoff ------------------------------------------------------


def test_a_cloud_job_is_invisible_to_the_local_scheduler(app_db, cloud_ready) -> None:
    """The whole safety argument. list_due_scheduled_jobs selects `status = 'scheduled'`, so a
    job the Space owns cannot also be claimed here — the two can never both fire it."""
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "scheduled_cloud", scheduled_at=_past(), payload=json.dumps(_PAYLOAD)
    )
    assert app_db.list_due_scheduled_jobs() == []
    # ...and the local claim, which is the last gate before firing, refuses it too.
    assert app_db.claim_scheduled_distribution_job(job["id"]) is None


def test_a_scheduled_post_on_a_cloud_channel_is_handed_over(app_db, cloud_ready) -> None:
    body = distribution.SendRequest(
        libraryItemId="lib-1", channels=["mastodon"], text=_PAYLOAD["text"], scheduledAt=_future()
    )
    result = distribution.send(body)
    assert result["jobs"][0]["status"] == "scheduled_cloud"
    assert result["jobs"][0]["cloud_enqueued_at"]
    assert len(cloud_ready["enqueue"]) == 1


def test_an_immediate_post_still_goes_out_locally(app_db, cloud_ready, monkeypatch) -> None:
    """Cloud posting exists for the case where the app is closed. It is not, and should never
    become, the path for a send the user is watching happen."""
    monkeypatch.setattr(distribution, "fire_job", lambda *a, **k: None)
    body = distribution.SendRequest(libraryItemId="lib-1", channels=["mastodon"], text=_PAYLOAD["text"])
    distribution.send(body)
    assert cloud_ready["enqueue"] == []


def test_a_non_cloud_channel_is_left_on_the_local_scheduler(app_db, cloud_ready) -> None:
    body = distribution.SendRequest(
        libraryItemId="lib-1", channels=["discord"], text=_PAYLOAD["text"], scheduledAt=_future()
    )
    assert distribution.send(body)["jobs"][0]["status"] == "scheduled"
    assert cloud_ready["enqueue"] == []


def test_a_failed_handover_keeps_the_post_on_the_local_scheduler(
    app_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken outbox must cost the user autonomy, never the post itself."""
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)

    def boom(job_id, channel, payload, due_at):
        raise cloud_poster.CloudPosterError("outbox unreachable")

    monkeypatch.setattr(cloud_poster, "enqueue", boom)
    body = distribution.SendRequest(
        libraryItemId="lib-1", channels=["mastodon"], text=_PAYLOAD["text"], scheduledAt=_future()
    )
    job = distribution.send(body)["jobs"][0]
    assert job["status"] == "scheduled"
    assert "outbox unreachable" in job["error"]


# --- reading the outcome back -----------------------------------------------


def test_a_sent_cloud_post_is_reconciled_in_the_form_the_learning_loop_parses(
    app_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generation_link keys off `mastodon:<id>`; writing anything else silently breaks the
    Social Post learning loop for every cloud-sent post."""
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "scheduled_cloud", scheduled_at=_past(), payload=json.dumps(_PAYLOAD)
    )
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "outcome", lambda _id: {"status": "sent", "ref": "mastodon:999"})

    distribution._reconcile_cloud_jobs()
    fresh = app_db.get_distribution_job(job["id"])
    assert fresh["status"] == "sent"
    assert fresh["activepieces_run_id"] == "mastodon:999"
    assert fresh["cloud_ref"] == "mastodon:999"


def test_a_job_still_in_flight_is_left_alone(app_db, monkeypatch: pytest.MonkeyPatch) -> None:
    job = app_db.add_distribution_job(
        "lib-1", "bluesky", "scheduled_cloud", scheduled_at=_past(), payload=json.dumps(_PAYLOAD)
    )
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "outcome", lambda _id: None)
    distribution._reconcile_cloud_jobs()
    assert app_db.get_distribution_job(job["id"])["status"] == "scheduled_cloud"


# --- the two ways a post could go out twice ---------------------------------


def test_retrying_a_cloud_post_that_actually_sent_is_refused(
    app_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloud post can fail on the way BACK. Re-firing it through Activepieces — which has no
    idempotency key on the Bluesky path — is how one lost response becomes two posts."""
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "sending", payload=json.dumps(_PAYLOAD)
    )
    app_db.update_distribution_job(
        job["id"], status="failed", error="timed out", cloud_enqueued_at=_past()
    )
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "outcome", lambda _id: {"status": "sent", "ref": "mastodon:42"})

    with pytest.raises(HTTPException) as excinfo:
        distribution.retry_failed_job(job["id"])
    assert excinfo.value.status_code == 409
    # ...and the row is corrected rather than left lying about what happened.
    fresh = app_db.get_distribution_job(job["id"])
    assert fresh["status"] == "sent"
    assert not fresh["error"]


def test_cancelling_a_post_the_space_is_already_sending_is_refused(
    app_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reporting success here would leave the user believing they stopped a post they are
    about to see appear."""
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "scheduled_cloud", scheduled_at=_future(), payload=json.dumps(_PAYLOAD)
    )
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "cancel", lambda _id: False)

    with pytest.raises(HTTPException) as excinfo:
        distribution.cancel_scheduled_job(job["id"])
    assert excinfo.value.status_code == 409
    assert app_db.get_distribution_job(job["id"])["status"] == "scheduled_cloud"


def test_cancelling_a_queued_cloud_post_takes_it_out_of_the_outbox_first(
    app_db, cloud_ready
) -> None:
    job = app_db.add_distribution_job(
        "lib-1", "bluesky", "scheduled_cloud", scheduled_at=_future(), payload=json.dumps(_PAYLOAD)
    )
    assert distribution.cancel_scheduled_job(job["id"])["status"] == "cancelled"
    assert cloud_ready["cancel"] == [job["id"]]


# --- the Space's own source has to be findable ------------------------------
#
# activepieces_client learned this the hard way: a fixed walk up from __file__ is right in a
# checkout and wrong once PyInstaller rewrites __file__ under _MEIPASS, and the failure was
# silent — an empty directory globs to nothing and the error surfaces much later looking like
# somebody else's problem. Same shape here, so the same guard.


def test_the_poster_space_source_ships_with_the_app() -> None:
    from app.services import hf_spaces

    source = hf_spaces.poster_source_dir()
    assert (source / "main.py").is_file()
    assert (source / "Dockerfile").is_file()
    # The README's front matter is what tells Hugging Face this is a Docker Space on 7860.
    assert "sdk: docker" in (source / "README.md").read_text(encoding="utf-8")


def test_space_urls_follow_hugging_faces_slug_rules() -> None:
    from app.services import hf_spaces

    # Dots and underscores become hyphens and everything lowercases, so the URL cannot be
    # built by joining the id as typed.
    assert hf_spaces.space_url("Some.One/My_Poster") == "https://some-one-my-poster.hf.space"


# --- keeping the outbox from growing without bound --------------------------
#
# delete_file removes a blob from the tree, not from history, so a posted 50MB video is stored
# forever unless the branch is squashed. The squash is the dangerous part: it rewrites the head
# the Space's claim compare-and-swap is written against.


class _FakeApi:
    def __init__(self, files: list[str]) -> None:
        self.files = files
        self.commits: list[list] = []
        self.squashes = 0

    def list_repo_files(self, *_a, **_k) -> list[str]:
        return self.files

    def create_commit(self, *_a, **kwargs) -> None:
        self.commits.append(kwargs.get("operations", []))

    def super_squash_history(self, *_a, **_k) -> None:
        self.squashes += 1


@pytest.fixture()
def outbox(monkeypatch: pytest.MonkeyPatch):
    """A configured outbox whose file list the test controls."""
    from app import config

    monkeypatch.setattr(config, "CLOUD_POSTER_OUTBOX", "someone/outbox")
    monkeypatch.setattr(config, "CLOUD_POSTER_TOKEN", "hf_test")

    def _make(files: list[str]) -> _FakeApi:
        api = _FakeApi(files)
        monkeypatch.setattr(cloud_poster, "_api", lambda: api)
        return api

    return _make


def test_the_outbox_is_never_squashed_while_a_post_is_pending(outbox, monkeypatch) -> None:
    """The Space claims jobs against the head it just read. Rewriting that head underneath a
    pass in flight is how a post that went out ends up looking unsent."""
    api = outbox(["queue/job-1.json", "media/job-1/clip.mp4"])
    monkeypatch.setattr(cloud_poster, "outcome", lambda _id: None)
    assert cloud_poster.prune()["squashed"] is False
    assert api.squashes == 0


def test_a_claimed_job_also_blocks_the_squash(outbox, monkeypatch) -> None:
    api = outbox(["claims/job-1.json"])
    monkeypatch.setattr(cloud_poster, "outcome", lambda _id: None)
    assert cloud_poster.prune()["squashed"] is False
    assert api.squashes == 0


def test_an_idle_outbox_is_squashed(outbox, monkeypatch) -> None:
    api = outbox(["outcomes/job-1.json"])
    monkeypatch.setattr(cloud_poster, "outcome", lambda _id: {"status": "sent", "at": _past()})
    assert cloud_poster.prune()["squashed"] is True
    assert api.squashes == 1


def test_recent_outcomes_survive_and_old_ones_do_not(outbox, monkeypatch) -> None:
    """The app reads outcomes back to reconcile. Dropping one before that has happened would
    strand the job as scheduled_cloud forever."""
    from datetime import datetime, timedelta, timezone

    api = outbox(["outcomes/fresh.json", "outcomes/ancient.json"])
    ages = {
        "fresh": datetime.now(timezone.utc).isoformat(),
        "ancient": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
    }
    monkeypatch.setattr(cloud_poster, "outcome", lambda job_id: {"status": "sent", "at": ages[job_id]})

    assert cloud_poster.prune()["pruned"] == 1
    deleted = [op.path_in_repo for ops in api.commits for op in ops]
    assert deleted == ["outcomes/ancient.json"]


def test_pruning_an_unconfigured_outbox_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloud_poster, "is_configured", lambda: False)
    assert cloud_poster.prune() == {"pruned": 0, "squashed": False}


def test_the_space_can_be_handed_over_without_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The walkthrough creates the Space minutes AFTER the backend started, so the spawn
    environment cannot carry it. Without a runtime handover the wizard would report success
    and every scheduled post until the next restart would quietly stay local."""
    from app import config

    monkeypatch.setattr(config, "CLOUD_POSTER_OUTBOX", "")
    monkeypatch.setattr(config, "CLOUD_POSTER_TOKEN", "")
    monkeypatch.setattr(cloud_poster, "_runtime", {})
    assert cloud_poster.is_configured() is False

    assert cloud_poster.set_credentials(
        space_id="someone/poster",
        url="https://someone-poster.hf.space",
        key="k",
        outbox="someone/poster-outbox",
        token="hf_test",
    )
    assert cloud_poster.is_configured() is True

    # ...and clearing it puts scheduled posts back on the local scheduler rather than
    # half-configured, which would fail every enqueue instead of falling back.
    assert cloud_poster.set_credentials(outbox="", token="") is False
    assert cloud_poster.is_configured() is False


def test_a_prefilled_bluesky_password_is_resolved_before_it_reaches_bluesky(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walkthrough's form is prefilled from Settings, and a stored secret arrives there as
    SETTINGS_PLACEHOLDER rather than itself. Forwarded unresolved it would be offered to
    Bluesky as the password and rejected — reading as "your app password is wrong" for a
    password the user never typed."""
    from app.routers import cloud_posting, distribution

    sent: dict[str, str] = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"did": "did:plc:abc", "refreshJwt": "refresh-token"}

    def fake_post(url, json=None, timeout=None):
        sent.update(json or {})
        return _Resp()

    monkeypatch.setattr(cloud_posting.requests, "post", fake_post)
    # _PREFILL_SOURCES captured the loader by reference at import, so patching the name on
    # the module would not reach it.
    monkeypatch.setitem(
        distribution._PREFILL_SOURCES,
        "bluesky",
        (
            "Bluesky Post Creator settings",
            lambda: {"identifier": ("me.bsky.social", False), "password": ("real-app-pw", True)},
        ),
    )
    monkeypatch.setattr(cloud_posting.hf_spaces, "push_variable", lambda *a, **k: None)
    monkeypatch.setattr(cloud_posting.hf_spaces, "push_secret", lambda *a, **k: None)

    result = cloud_posting.connect_bluesky(
        cloud_posting.BlueskyConnectRequest(
            hfToken="hf",
            spaceId="someone/poster",
            identifier="me.bsky.social",
            appPassword=distribution.SETTINGS_PLACEHOLDER,
        )
    )
    assert result["connected"] is True
    assert sent["password"] == "real-app-pw"
    assert distribution.SETTINGS_PLACEHOLDER not in sent.values()
