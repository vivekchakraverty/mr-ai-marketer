"""Describing a post that was just created on Bluesky.

Written after a real posting run returned `404 Post was not found on Bluesky.` for a post
that had gone out. Writing the record and reading it back go to two different services: the
write is authoritative immediately, the read goes through an eventually-consistent index
that had not caught up. Reporting that as a failure invites the person to post again.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from ..routers import engage


class _Me:
    did = "did:plc:someone"
    handle = "someone.bsky.social"
    display_name = "Someone"
    avatar = "https://cdn.example/avatar.jpg"


class _Client:
    """A client whose index catches up after a given number of reads."""

    def __init__(self, ready_after: int) -> None:
        self.me = _Me()
        self.ready_after = ready_after
        self.reads = 0


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nobody should wait real seconds for this."""
    import time

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    monkeypatch.setattr(engage, "_INDEX_WAIT_SECONDS", 0.05)


def _wire(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    def fake_get_feed_post(_c, uri):
        client.reads += 1
        if client.reads <= client.ready_after:
            raise HTTPException(status_code=404, detail="Post was not found on Bluesky.")
        return engage.FeedPost(
            uri=uri, cid="realcid", webUrl="https://bsky.app/x", isPost=True, isOwnPost=True,
            authorDid=_Me.did, authorHandle=_Me.handle, authorName=_Me.display_name,
            text="from the index", createdAt="2026-08-21T00:00:00Z",
            likes=3, reposts=0, replies=0, quotes=0, bookmarks=0,
        )

    monkeypatch.setattr(engage, "_get_feed_post", fake_get_feed_post)


def test_the_index_is_used_when_it_already_has_the_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(ready_after=0)
    _wire(monkeypatch, client)
    post = engage._created_feed_post(client, "at://x/app.bsky.feed.post/1", "sent text")
    assert post.text == "from the index"
    assert post.cid == "realcid"
    assert client.reads == 1


def test_a_slow_index_is_waited_for_rather_than_worked_around(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real answer is better than the reconstructed one when it arrives in time."""
    client = _Client(ready_after=2)
    _wire(monkeypatch, client)
    post = engage._created_feed_post(client, "at://x/app.bsky.feed.post/1", "sent text")
    assert post.text == "from the index"
    assert client.reads == 3


def test_a_post_that_never_indexes_is_still_reported_as_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point. A created post must never come back as an error."""
    client = _Client(ready_after=10_000)
    _wire(monkeypatch, client)
    post = engage._created_feed_post(client, "at://x/app.bsky.feed.post/1", "sent text")
    assert post.text == "sent text"
    assert post.uri == "at://x/app.bsky.feed.post/1"
    assert post.isOwnPost is True
    assert post.authorHandle == _Me.handle
    assert (post.likes, post.reposts, post.replies) == (0, 0, 0)


def test_a_broken_readback_does_not_become_a_posting_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure reading a post back says nothing about whether it was sent."""

    def explode(_c, _uri):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(engage, "_get_feed_post", explode)
    post = engage._created_feed_post(_Client(0), "at://x/app.bsky.feed.post/2", "sent text")
    assert post.text == "sent text"
