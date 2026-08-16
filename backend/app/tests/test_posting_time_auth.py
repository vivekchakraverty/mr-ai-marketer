"""Reading an instance's local timeline as the user, which the big servers now require.

mastodon.social answers every anonymous form of /api/v1/timelines/public with
`422 {"error":"This method requires an authenticated user"}`. That timeline is the only
broad sample of an instance's own accounts, so without a token the biggest instances
were unmeasurable — and said so in a way that read as "this server has no posts".
"""

from __future__ import annotations

import pytest

from app.services import mastodon as m
from app.services import posting_time_corpus as ptc


@pytest.fixture()
def accepted(monkeypatch):
    """Pretend the instance's rules are accepted; the gate has its own tests."""
    from app.services import mastodon_gate as gate

    monkeypatch.setattr(gate, "require_accepted", lambda host: None)


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def spy(host, limit=40, max_id="", local=False, token=""):
        seen.update(host=host, local=local, token=token)
        raise m.MastodonError("stopped after capturing the call")

    monkeypatch.setattr(m, "public_timeline", spy)
    return seen


def test_the_token_reaches_the_timeline_call(accepted, monkeypatch):
    seen = _capture(monkeypatch)
    ptc.collect_mastodon("mastodon.social", days=7, token="tok-123")
    assert seen["token"] == "tok-123"


def test_it_still_asks_only_for_local_accounts(accepted, monkeypatch):
    """Counts are only authoritative on the instance that hosts the account.

    Adding a token must not quietly widen the sample to federated posts, which would
    rank an author by how well they federated rather than how they did.
    """
    seen = _capture(monkeypatch)
    ptc.collect_mastodon("mastodon.social", days=7, token="tok-123")
    assert seen["local"] is True


def test_no_token_is_still_allowed(accepted, monkeypatch):
    """Smaller servers do serve this anonymously and must not require a login."""
    seen = _capture(monkeypatch)
    ptc.collect_mastodon("toot.garden", days=7)
    assert seen["token"] == ""


def test_an_auth_refusal_tells_the_user_what_to_do(accepted, monkeypatch):
    def refuse(host, limit=40, max_id="", local=False, token=""):
        raise m.MastodonError("This method requires an authenticated user")

    monkeypatch.setattr(m, "public_timeline", refuse)
    curve = ptc.collect_mastodon("mastodon.social", days=7)

    assert curve.attempted is False, "nothing was read, so this must never be stored"
    assert "logged-in account" in curve.notes[0]
    assert "connect your" in curve.notes[0].lower()


def test_a_genuine_absence_is_not_blamed_on_the_login(accepted, monkeypatch):
    """A server that simply does not serve the endpoint must not be misreported."""

    def refuse(host, limit=40, max_id="", local=False, token=""):
        raise m.MastodonError("404 Not Found")

    monkeypatch.setattr(m, "public_timeline", refuse)
    curve = ptc.collect_mastodon("some.instance", days=7)

    assert "does not serve a public local timeline" in curve.notes[0]
    assert "logged-in" not in curve.notes[0]


def test_a_token_that_still_gets_refused_does_not_suggest_connecting_again(accepted, monkeypatch):
    """Telling someone to connect an account they just used would be a dead end."""

    def refuse(host, limit=40, max_id="", local=False, token=""):
        raise m.MastodonError("This method requires an authenticated user")

    monkeypatch.setattr(m, "public_timeline", refuse)
    curve = ptc.collect_mastodon("mastodon.social", days=7, token="tok-123")

    assert "logged-in account" not in curve.notes[0]
