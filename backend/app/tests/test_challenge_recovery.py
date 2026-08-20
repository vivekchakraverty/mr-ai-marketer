"""Escaping the captcha wait once the data is actually readable.

The reported failure: a run parked on "Google needs a manual check", the collector window
showing a normal results page with Keyword Surfer's numbers on it, and five minutes later a
result of `google_challenge` — "The Google verification was not completed in time" — with no
volume, no CPC and no ideas.

Two things caused it, and this covers the second. The wait could only end when the challenge
signal went away, so a page that still looked like a challenge — for any reason, including
the loose text match fixed alongside this — kept the run waiting beside a panel already full
of the numbers it wanted.

Driven entirely with fakes and a monkeypatched clock: the point is that the loop can escape,
and pinning that to Google's mood would make the test worthless.
"""

from __future__ import annotations

import pytest

from app.services import keyword_surfer_collector as collector


@pytest.fixture()
def run():
    return collector.Run(
        id="test-run",
        keywords=["ai plugin for game engines"],
        country=collector.country_by_code("us"),
        delay_ms=collector.MIN_DELAY_MS,
        max_suggestions=25,
        status="running",
    )


class _Page:
    """A results page that Google's own check would be reported on forever."""

    url = "https://www.google.com/search?q=ai+plugin+for+game+engines&sei=abc"

    def goto(self, *_a, **_k):
        return None


@pytest.fixture()
def stubbed(monkeypatch):
    """Challenge always detected; panel data always available."""
    monkeypatch.setattr(collector, "_is_google_challenge", lambda _page: True)
    monkeypatch.setattr(collector, "capture_snapshot", lambda _page: {"markerCount": 3})
    monkeypatch.setattr(
        collector,
        "parse_snapshot",
        lambda _snapshot, keyword: {
            "query": keyword,
            "loaded": True,
            "volume": 1300,
            "cpc": 2.4,
            "cpcDisplay": "$2.40",
            "suggestions": [{"keyword": "godot", "volume": 74000}],
            "countryLabels": ["United States"],
            "diagnostics": {"rootFound": True},
        },
    )
    monkeypatch.setattr(collector, "_save_run", lambda _run: None)
    slept: list[float] = []
    monkeypatch.setattr(collector, "_sleep_cancellable", lambda _run, s: slept.append(s))
    return slept


def test_the_wait_ends_as_soon_as_the_numbers_are_readable(run, stubbed, monkeypatch):
    """The regression. Before, this looped until the five-minute deadline and recorded
    `google_challenge` with nothing in it — while the data sat there the whole time."""
    session = collector.CollectorSession()
    assert session._search(_Page(), run, run.keywords[0]) is True

    result = run.results[-1]
    assert result["status"] == "complete"
    assert result["volume"] == 1300
    assert result["suggestions"], "the ideas were on the page and should have been kept"

    # It must not have sat out the timeout: one second per tick, five minutes of them would
    # be 300 sleeps. Escaping on the first look means far fewer.
    assert len(stubbed) < 5, f"waited {len(stubbed)} ticks before noticing readable data"


def test_a_real_block_still_reports_itself(run, monkeypatch):
    """The other side of it: when there genuinely is nothing to read, the run must still
    say so rather than inventing a result."""
    monkeypatch.setattr(collector, "_is_google_challenge", lambda _page: True)
    monkeypatch.setattr(collector, "capture_snapshot", lambda _page: {})
    monkeypatch.setattr(
        collector,
        "parse_snapshot",
        lambda _s, keyword: {
            "query": keyword,
            "loaded": False,
            "volume": None,
            "suggestions": [],
            "diagnostics": {"rootFound": False},
        },
    )
    monkeypatch.setattr(collector, "_save_run", lambda _run: None)
    monkeypatch.setattr(collector, "_sleep_cancellable", lambda _run, _s: None)
    # Collapse the deadline so the test does not actually wait five minutes.
    monkeypatch.setattr(collector, "_ATTENTION_TIMEOUT_S", 0)

    session = collector.CollectorSession()
    assert session._search(_Page(), run, run.keywords[0]) is False
    assert run.results[-1]["status"] == "google_challenge"
    assert run.results[-1]["volume"] is None
