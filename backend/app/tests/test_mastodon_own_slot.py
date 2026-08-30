"""A post of the user's own earning a place among the examples the generator writes from.

Ranking divides interactions by followers plus a prior of 292, so a three-follower account
cannot win a slot on merit however well a post did for it. The reserved slot is what stops
the generator only ever being shown strangers — without turning into a bypass of the floors
that keep flops out.
"""

from __future__ import annotations

import pytest

from app.routers import mastodon_post


def _post(uri: str, text: str = "a real sentence with several words in it") -> dict:
    return {"uri": uri, "text": text}


@pytest.fixture()
def own(monkeypatch):
    """Which toots the user published, as generations.posted_uri records them."""
    uris: set[str] = set()
    monkeypatch.setattr(mastodon_post, "_own_post_uris", lambda key: uris)
    return uris


def test_the_users_best_qualifying_post_takes_a_slot(own):
    own.add("mastodon://host/mine")
    scored = [(0.9, _post("a")), (0.5, _post("b")), (0.01, _post("mastodon://host/mine"))]
    chosen = mastodon_post._reserve_own_slot(scored[:2], scored, "personal · mastodon · host")

    assert [p["uri"] for _, p in chosen][0] == "mastodon://host/mine"
    # The weakest earned entry makes way; the pool does not grow.
    assert len(chosen) == 2
    assert "b" not in [p["uri"] for _, p in chosen]


def test_a_post_that_cleared_no_floor_gets_no_slot(own):
    """`scored` has already dropped anything under the interaction and prose floors, so a
    flop is simply not in it — and reserving a slot for one would teach the generator to
    write like a post that did not work."""
    own.add("mastodon://host/flopped")
    scored = [(0.9, _post("a")), (0.5, _post("b"))]
    chosen = mastodon_post._reserve_own_slot(scored[:2], scored, "key")
    assert [p["uri"] for _, p in chosen] == ["a", "b"]


def test_nothing_changes_when_the_user_has_published_nothing(own):
    scored = [(0.9, _post("a")), (0.5, _post("b"))]
    assert mastodon_post._reserve_own_slot(scored[:2], scored, "key") == scored[:2]


def test_a_post_that_already_earned_its_place_is_not_reserved_twice(own):
    own.add("mastodon://host/mine")
    scored = [(0.9, _post("mastodon://host/mine")), (0.5, _post("b"))]
    chosen = mastodon_post._reserve_own_slot(scored[:2], scored, "key")
    assert [p["uri"] for _, p in chosen] == ["mastodon://host/mine", "b"]
    assert len(chosen) == 2


def test_the_best_of_several_own_posts_is_the_one_chosen(own):
    own.update({"mastodon://host/older", "mastodon://host/better"})
    scored = [
        (0.9, _post("a")),
        (0.4, _post("mastodon://host/better")),
        (0.1, _post("mastodon://host/older")),
    ]
    chosen = mastodon_post._reserve_own_slot(scored[:1], scored, "key")
    assert [p["uri"] for _, p in chosen][0] == "mastodon://host/better"


def test_an_empty_pool_is_left_empty(own):
    own.add("mastodon://host/mine")
    assert mastodon_post._reserve_own_slot([], [], "key") == []
