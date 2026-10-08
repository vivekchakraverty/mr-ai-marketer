"""Feed reads must not make a successful Bluesky write look like a failure."""

from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from app.routers import engage


class GatewayError(Exception):
    response = NS(status_code=502)

    def __str__(self) -> str:
        return "<html>502 Bad Gateway</html>"


def test_transient_feed_read_retries_without_reposting(monkeypatch):
    attempts = []
    sleeps = []

    def read(params):
        attempts.append(params)
        if len(attempts) < 3:
            raise GatewayError()
        return "loaded"

    monkeypatch.setattr(engage.time, "sleep", sleeps.append)
    assert engage._read_with_retry(read, {"limit": 30}) == "loaded"
    assert attempts == [{"limit": 30}] * 3
    assert sleeps == [0.4, 0.8]


def test_gateway_error_is_short_and_feed_specific():
    feed_error = engage._as_api_error(GatewayError(), feed=True)
    assert feed_error.status_code == 503
    assert "Refresh this feed" in feed_error.detail
    assert "<html>" not in feed_error.detail
    action_error = engage._as_api_error(GatewayError(), posting=True)
    assert "Check your profile" in action_error.detail


def test_notification_hydration_failure_keeps_basic_cards(monkeypatch):
    notifications = [
        NS(
            uri=f"at://did:plc:writer/app.bsky.feed.post/{i}",
            cid=f"cid{i}",
            record=NS(text=f"post {i}"),
            author=NS(did="did:plc:writer", handle="writer.bsky.social", display_name=None),
            indexed_at="2026-10-07T12:00:00Z",
            reason="mention",
            is_read=False,
        )
        for i in range(30)
    ]
    batches = []

    def get_posts(params):
        batches.append(params["uris"])
        raise GatewayError()

    client = NS(
        me=NS(did="did:plc:me"),
        app=NS(bsky=NS(
            notification=NS(list_notifications=lambda _params: NS(notifications=notifications)),
            feed=NS(get_posts=get_posts),
        )),
    )
    monkeypatch.setattr(engage, "_client", lambda: client)
    monkeypatch.setattr(engage.time, "sleep", lambda _seconds: None)

    result = engage.notifications()

    assert len(result.posts) == 30
    assert result.posts[0].text == "post 0"
    assert result.posts[-1].text == "post 29"
    assert [len(batch) for batch in batches] == [25, 25, 25, 5, 5, 5]


def test_notification_list_gateway_error_is_feed_error(monkeypatch):
    def fail(_params):
        raise GatewayError()

    client = NS(app=NS(bsky=NS(notification=NS(list_notifications=fail))))
    monkeypatch.setattr(engage, "_client", lambda: client)
    monkeypatch.setattr(engage.time, "sleep", lambda _seconds: None)

    with pytest.raises(HTTPException) as captured:
        engage.notifications()
    assert captured.value.status_code == 503
    assert "Refresh this feed" in captured.value.detail
