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


# --- depth, which a timeline cannot give ------------------------------------


def test_authors_are_discovered_from_the_timeline_then_read_individually(accepted, monkeypatch):
    """The fix for "no usable public posts" on exactly the instances that have the most.

    A public local timeline is read newest-first and is only as deep as the instance is
    quiet — measured, 600 posts covers 21h on hachyderm and 5h on mstdn.social. Every post
    it returns on a busy server is newer than the 24h settle cutoff, so scoring kept
    nothing. The timeline is now used to find WHO posts there; each author's own feed is
    what reaches back.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    account = m.Account(
        id="42", acct="someone", url="https://x/@someone", display_name="Someone",
        followers=500, bot=False, discoverable=True, indexable=None, note="",
    )

    def one_page(host, limit=40, max_id="", local=False, token=""):
        if max_id:
            return []
        # A firehose page: recent, and far too new to score.
        return [
            m.Status(
                id=str(i), uri=f"u{i}", url="", account=account, text="hello there friend",
                created_at=now, favourites=1, reblogs=0, replies=0,
            )
            for i in range(5)
        ]

    asked: list[str] = []

    def author_feed(host, account_id, since, until, token="", max_pages=4):
        asked.append(account_id)
        # Settled posts, spread across the window — what the timeline could not reach.
        return [
            m.Status(
                id=f"a{i}", uri=f"au{i}", url="", account=account, text="an older post",
                created_at=until - timedelta(days=i + 1),
                favourites=i * 3, reblogs=i, replies=0,
            )
            for i in range(6)
        ]

    monkeypatch.setattr(m, "public_timeline", one_page)
    monkeypatch.setattr(m, "author_statuses_in_window", author_feed)

    curve = ptc.collect_mastodon("busy.instance", days=31)

    assert asked == ["42"], "each discovered author's own feed must be read"
    assert curve.scored_posts > 0, "settled posts from author feeds must survive scoring"
    assert curve.scored_authors == 1


def test_an_instance_with_no_eligible_authors_says_so(accepted, monkeypatch):
    """Every author filtered out is a different answer from 'nothing was readable'."""
    account = m.Account(
        id="7", acct="tiny", url="", display_name="", followers=1,  # under the floor
        bot=False, discoverable=True, indexable=None, note="",
    )
    from datetime import datetime, timezone

    monkeypatch.setattr(
        m, "public_timeline",
        lambda host, limit=40, max_id="", local=False, token="": [] if max_id else [
            m.Status(id="1", uri="u", url="", account=account, text="hello there",
                     created_at=datetime.now(timezone.utc), favourites=0, reblogs=0, replies=0)
        ],
    )
    curve = ptc.collect_mastodon("tiny.instance", days=31)
    assert curve.attempted is False
    assert "follower floor" in curve.notes[0] or "opted out" in curve.notes[0]
