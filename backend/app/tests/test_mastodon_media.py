"""Uploading media to Mastodon, and waiting for the ones that need waiting on.

These exist because of a failure that only video produces. An image upload answers 200 and
can be attached at once; a video answers 202 while the server transcodes it, and posting in
that gap is refused with "Cannot attach files that have not finished processing." The code
used to assume the id was usable immediately, which was true of everything it had been
tested with.
"""

from __future__ import annotations

import pytest

from ..services import mastodon


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait is real seconds in production and nobody should pay them here."""
    import time

    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _upload(monkeypatch: pytest.MonkeyPatch, post: _Resp, gets: list[_Resp]) -> tuple[str, int]:
    """Run an upload against a scripted server. Returns the id and how often it was polled."""
    calls = {"n": 0}

    def fake_post(*_a, **_k) -> _Resp:
        return post

    def fake_get(*_a, **_k) -> _Resp:
        calls["n"] += 1
        return gets[min(calls["n"] - 1, len(gets) - 1)]

    monkeypatch.setattr(mastodon.requests, "post", fake_post)
    monkeypatch.setattr(mastodon.requests, "get", fake_get)
    media_id = mastodon.upload_media("example.social", "tok", "clip.mp4", b"data")
    return media_id, calls["n"]


def test_an_image_is_ready_at_once_and_is_never_polled(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 means attachable now. Polling it would add a round trip to every image post."""
    media_id, polls = _upload(monkeypatch, _Resp(200, {"id": "77"}), [_Resp(200)])
    assert media_id == "77"
    assert polls == 0


def test_a_video_is_waited_on_until_the_server_says_it_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """202 plus 206s is the transcoding case that produced the original refusal."""
    media_id, polls = _upload(
        monkeypatch,
        _Resp(202, {"id": "88"}),
        [_Resp(206), _Resp(206), _Resp(200)],
    )
    assert media_id == "88"
    assert polls == 3


def test_a_video_that_never_finishes_says_so_in_words_a_person_can_act_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Better than handing back an id that the compose call will be refused for."""
    monkeypatch.setattr(mastodon, "MEDIA_PROCESSING_TIMEOUT", 0.05)
    with pytest.raises(mastodon.MastodonError) as err:
        _upload(monkeypatch, _Resp(202, {"id": "99"}), [_Resp(206)])
    assert "still processing" in str(err.value)
    assert "shorter clip" in str(err.value)


def test_a_blip_while_polling_is_not_treated_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped connection mid-transcode is normal; the next poll settles it."""
    calls = {"n": 0}
    answers = [mastodon.requests.RequestException("reset"), _Resp(206), _Resp(200)]

    def fake_get(*_a, **_k):
        calls["n"] += 1
        answer = answers[min(calls["n"] - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mastodon.requests, "post", lambda *a, **k: _Resp(202, {"id": "12"}))
    monkeypatch.setattr(mastodon.requests, "get", fake_get)
    assert mastodon.upload_media("example.social", "tok", "clip.mp4", b"d") == "12"
    assert calls["n"] == 3


def test_a_real_error_while_polling_is_raised_rather_than_waited_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoked token should not spend the full timeout before being reported."""
    monkeypatch.setattr(mastodon.requests, "post", lambda *a, **k: _Resp(202, {"id": "13"}))
    monkeypatch.setattr(mastodon.requests, "get", lambda *a, **k: _Resp(401, {"error": "nope"}))
    with pytest.raises(mastodon.MastodonError):
        mastodon.upload_media("example.social", "tok", "clip.mp4", b"d")
