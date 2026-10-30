"""Handing a scheduled post to the user's own poster Space, and getting it back.

The thing worth guarding here is not the happy path — it is that a post can never go out
twice, and can never be silently lost, while ownership moves between the local scheduler and
the cloud. Those are the two failures a user cannot recover from.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


def test_deleting_a_queued_cloud_post_removes_outbox_and_history(
    app_db, cloud_ready
) -> None:
    job = app_db.add_distribution_job(
        "lib-1", "bluesky", "scheduled_cloud", scheduled_at=_future(), payload=json.dumps(_PAYLOAD)
    )

    assert distribution.delete_scheduled_job(job["id"]) == {
        "id": job["id"],
        "deleted": True,
    }
    assert cloud_ready["cancel"] == [job["id"]]
    assert app_db.get_distribution_job(job["id"]) is None


def test_deleting_a_cloud_post_already_sending_keeps_its_history(
    app_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = app_db.add_distribution_job(
        "lib-1", "mastodon", "scheduled_cloud", scheduled_at=_future(), payload=json.dumps(_PAYLOAD)
    )
    monkeypatch.setattr(cloud_poster, "cancel", lambda _id: False)

    with pytest.raises(HTTPException) as excinfo:
        distribution.delete_scheduled_job(job["id"])

    assert excinfo.value.status_code == 409
    assert app_db.get_distribution_job(job["id"])["status"] == "scheduled_cloud"


def test_cloud_cancel_deletes_queue_and_media_in_one_claim_safe_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits: list[dict] = []

    class FakeApi:
        def repo_info(self, _repo, *, repo_type):
            assert repo_type == "dataset"
            return SimpleNamespace(sha="examined-head")

        def list_repo_files(self, _repo, *, repo_type):
            assert repo_type == "dataset"
            return ["queue/job-1.json", "media/job-1/photo.png", "queue/another.json"]

        def create_commit(self, **kwargs):
            commits.append(kwargs)

    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "_api", lambda: FakeApi())
    monkeypatch.setattr(cloud_poster, "_setting", lambda _name: "owner/outbox")

    assert cloud_poster.cancel("job-1") is True
    assert len(commits) == 1
    assert commits[0]["parent_commit"] == "examined-head"
    assert [operation.path_in_repo for operation in commits[0]["operations"]] == [
        "queue/job-1.json",
        "media/job-1/photo.png",
    ]


def test_cloud_cancel_refuses_a_job_with_a_finished_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeApi:
        def repo_info(self, _repo, *, repo_type):
            return SimpleNamespace(sha="finished-head")

        def list_repo_files(self, _repo, *, repo_type):
            return ["outcomes/job-1.json"]

    monkeypatch.setattr(cloud_poster, "is_configured", lambda: True)
    monkeypatch.setattr(cloud_poster, "_api", lambda: FakeApi())
    monkeypatch.setattr(cloud_poster, "_setting", lambda _name: "owner/outbox")

    assert cloud_poster.cancel("job-1") is False


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


def test_a_space_handed_over_mid_session_is_still_nudged_when_a_post_is_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handover above is only half the job: the scheduler also has to ASK through it.

    _wake_cloud_if_due gated on config.CLOUD_POSTER_URL, which is the spawn environment and
    frozen at import. Enqueueing already went through the runtime override, so a Space set up
    mid-session took ownership of scheduled posts and then never got woken for them — leaving
    them to whenever something else happened to touch the Space. On free hardware that is the
    difference between a post at 09:00 and a post whenever the app is next opened.
    """
    from app import config

    monkeypatch.setattr(config, "CLOUD_POSTER_URL", "")
    monkeypatch.setattr(cloud_poster, "_runtime", {})
    assert cloud_poster.set_credentials(
        space_id="someone/poster",
        url="https://someone-poster.hf.space",
        key="k",
        outbox="someone/poster-outbox",
        token="hf_test",
    )

    due_soon = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(
        distribution.db, "list_cloud_pending_jobs", lambda: [{"scheduled_at": due_soon}]
    )
    woken = {"n": 0}
    monkeypatch.setattr(cloud_poster, "wake", lambda: woken.__setitem__("n", woken["n"] + 1))

    distribution._wake_cloud_if_due()
    assert woken["n"] == 1, "a Space the app knows about at runtime must still be nudged"


def test_nothing_due_soon_leaves_the_space_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The nudge is insurance, not a heartbeat — waking the Space on every 30-second tick
    would be a request a minute against a free Space for no reason."""
    monkeypatch.setattr(cloud_poster, "_runtime", {})
    assert cloud_poster.set_credentials(
        space_id="someone/poster",
        url="https://someone-poster.hf.space",
        key="k",
        outbox="someone/poster-outbox",
        token="hf_test",
    )
    monkeypatch.setattr(
        distribution.db, "list_cloud_pending_jobs", lambda: [{"scheduled_at": _future()}]
    )
    woken = {"n": 0}
    monkeypatch.setattr(cloud_poster, "wake", lambda: woken.__setitem__("n", woken["n"] + 1))

    distribution._wake_cloud_if_due()
    assert woken["n"] == 0


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


# --- paused is not asleep ---------------------------------------------------
#
# The regression these guard cost three days of scheduled posts (2026-10-28). The poster Space
# was PAUSED, which over HTTP is indistinguishable from SLEEPING: both are silence. Every
# recovery mechanism in the app — /tick, _wake_cloud_if_due, the GitHub heartbeat, healthmon's
# wake() — assumes silence means sleeping and is fixed by a request. A paused Space answers an
# instant 503 for ever and is fixed only by restart_space, so the status line reassured the
# user ("asleep or unreachable... anything queued still goes out when it wakes") about a Space
# that was never going to wake.


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


@pytest.fixture()
def configured_space(monkeypatch: pytest.MonkeyPatch):
    """A poster Space whose /status answer and whose Spaces API stage the test decides."""
    monkeypatch.setattr(cloud_poster, "_runtime", {})
    assert cloud_poster.set_credentials(
        space_id="someone/poster",
        url="https://someone-poster.hf.space",
        key="k",
        outbox="someone/poster-outbox",
        token="hf_test",
    )

    def _make(*, status: object, stage: str) -> None:
        from app.services import hf_spaces

        def _get(*_a, **_k):
            if isinstance(status, Exception):
                raise status
            return status

        monkeypatch.setattr("requests.get", _get)
        monkeypatch.setattr(hf_spaces, "stage", lambda *_a, **_k: stage)

    return _make


def test_a_paused_space_is_not_reported_as_asleep(configured_space) -> None:
    """The bug itself. Silence plus a PAUSED stage must produce an answer that says to act."""
    import requests

    configured_space(status=requests.ConnectionError("no route"), stage="PAUSED")
    result = cloud_poster.space_status()

    assert result["reachable"] is False
    assert result["stage"] == "PAUSED"
    assert result["restartable"] is True
    assert "paused" in result["detail"].lower()
    # The reassurance that made the old wording dangerous must be gone. It said "asleep or
    # unreachable" and invited the user to wait; this has to tell them to restart instead.
    assert "asleep or unreachable" not in result["detail"].lower()
    assert "restart" in result["detail"].lower()


def test_the_router_503_is_also_checked_against_the_stage(configured_space) -> None:
    """The other door to the same question, and the one a real paused Space comes through:
    Hugging Face's router answers 503 rather than dropping the connection, which the old code
    reported as the uninformative "Your Space answered HTTP 503"."""
    configured_space(status=_FakeResponse(503), stage="PAUSED")
    result = cloud_poster.space_status()

    assert result["restartable"] is True
    assert "503" not in result["detail"]


def test_a_sleeping_space_is_still_reported_as_asleep(configured_space) -> None:
    """The fix must not turn the ordinary case into an alarm. A sleeping free Space is normal,
    the queue is safe, and there is nothing for the user to do."""
    import requests

    configured_space(status=requests.Timeout("slow"), stage="SLEEPING")
    result = cloud_poster.space_status()

    assert result["reachable"] is False
    assert result.get("restartable") is None
    assert "asleep" in result["detail"].lower()


def test_a_hugging_face_outage_is_not_mistaken_for_a_paused_space(configured_space) -> None:
    """stage() returns "" when it cannot read the API. That must fall back to describing what
    we saw, not invent a verdict — telling someone to restart a healthy Space because
    huggingface.co was down would be worse than saying nothing."""
    import requests

    configured_space(status=requests.ConnectionError("no route"), stage="")
    result = cloud_poster.space_status()

    assert result.get("restartable") is None
    assert "asleep or unreachable" in result["detail"]


def test_a_crashed_space_is_restartable_too(configured_space) -> None:
    configured_space(status=_FakeResponse(503), stage="RUNTIME_ERROR")
    result = cloud_poster.space_status()

    assert result["stage"] == "RUNTIME_ERROR"
    assert result["restartable"] is True


def test_a_healthy_space_never_asks_the_spaces_api(configured_space, monkeypatch) -> None:
    """The stage lookup is a second HTTP call, so it must only happen when the first one has
    already failed — not on every poll of a working Space."""
    from app.services import hf_spaces

    configured_space(status=_FakeResponse(200, {"queued": 2}), stage="PAUSED")
    asked = {"n": 0}
    real = hf_spaces.stage

    def _counting(*a, **k):
        asked["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(hf_spaces, "stage", _counting)

    result = cloud_poster.space_status()
    assert result["reachable"] is True
    assert result["queued"] == 2
    assert asked["n"] == 0


def test_the_stage_lookup_survives_every_way_it_can_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """stage() is only ever used to add detail to a decided answer, so it returns "" rather
    than raising. A Hugging Face outage must not turn a status row into a stack trace."""
    import requests

    from app.services import hf_spaces

    assert hf_spaces.stage("") == ""

    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    assert hf_spaces.stage("someone/poster") == ""

    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(404))
    assert hf_spaces.stage("someone/poster") == ""

    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(200, {}))
    assert hf_spaces.stage("someone/poster") == ""

    monkeypatch.setattr(
        "requests.get", lambda *a, **k: _FakeResponse(200, {"runtime": {"stage": "PAUSED"}})
    )
    assert hf_spaces.stage("someone/poster") == "PAUSED"


# --- restarting -------------------------------------------------------------


def test_restarting_without_a_write_token_is_refused_with_advice() -> None:
    """The token the Space itself holds is fine-grained and scoped to the outbox dataset, so
    it cannot restart anything. Saying so is the difference between a user adding the right
    token and concluding the button is broken."""
    from app.services import hf_spaces

    with pytest.raises(hf_spaces.SpaceProvisionError) as err:
        hf_spaces.restart("", "someone/poster")
    assert "token" in str(err.value).lower()

    with pytest.raises(hf_spaces.SpaceProvisionError):
        hf_spaces.restart("hf_write", "")


def test_the_restart_endpoint_defaults_to_the_active_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings panel sends no space id, so the endpoint has to find it the same way every
    other runtime caller does — through _setting(), never config, which is the spawn
    environment and frozen at import."""
    from app.routers import cloud_posting

    monkeypatch.setattr(cloud_poster, "_runtime", {})
    cloud_poster.set_credentials(
        space_id="someone/poster",
        url="https://someone-poster.hf.space",
        key="k",
        outbox="someone/poster-outbox",
        token="hf_test",
    )
    asked: dict[str, str] = {}
    monkeypatch.setattr(
        cloud_posting.hf_spaces,
        "restart",
        lambda token, space_id: asked.update(token=token, space_id=space_id),
    )

    result = cloud_posting.restart(cloud_posting.RestartRequest(hfToken="hf_write"))
    assert result["restarted"] is True
    assert asked == {"token": "hf_write", "space_id": "someone/poster"}


def test_restarting_with_no_space_set_up_is_a_clear_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config
    from app.routers import cloud_posting

    monkeypatch.setattr(config, "CLOUD_POSTER_SPACE", "")
    monkeypatch.setattr(cloud_poster, "_runtime", {})
    with pytest.raises(HTTPException) as err:
        cloud_posting.restart(cloud_posting.RestartRequest(hfToken="hf_write"))
    assert err.value.status_code == 400


def test_the_restartable_stages_exclude_the_ones_a_restart_cannot_fix() -> None:
    """A build failure needs the code or the config changed. Restarting it just loops, and an
    automatic restarter that loops is worse than one that reports."""
    from app.services import hf_spaces

    assert "PAUSED" in hf_spaces.RESTARTABLE_STAGES
    assert "RUNTIME_ERROR" in hf_spaces.RESTARTABLE_STAGES
    for stage in ("BUILD_ERROR", "CONFIG_ERROR", "NO_APP_FILE", "DELETING", "SLEEPING", "RUNNING"):
        assert stage not in hf_spaces.RESTARTABLE_STAGES
