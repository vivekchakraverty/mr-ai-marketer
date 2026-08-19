"""Collecting the searches a person runs in the collector window themselves.

The window is visible and usable, so people search in it. Every one of those searches
renders exactly the panel a scripted run would have read — but only runs were being
recorded, so someone could sit looking at real volumes on screen while the tool reported
nothing at all.

The URL readers are tested directly because they decide *whether* a page is worth reading,
and getting that wrong either misses real searches or records the consent screen.
"""

from __future__ import annotations

import pytest

from app.services import keyword_surfer_collector as collector


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.google.com/search?q=ai+plugin+suite+for+godot", "ai plugin suite for godot"),
        ("https://www.google.co.uk/search?q=leather+bag&gl=gb", "leather bag"),
        ("https://www.google.com/search?q=spaced%20out&num=10", "spaced out"),
    ],
)
def test_a_results_page_yields_its_search_term(url, expected):
    assert collector._query_from_google_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/",                      # the homepage draws no panel
        "https://www.google.com/imghp?q=cats",          # images, not web results
        "https://consent.google.com/m?continue=x",      # the consent wall
        "https://example.com/search?q=hi",              # not Google at all
        "https://www.google.com/search?tbm=isch&q=cat", # still /search, but not web
        "",
    ],
)
def test_anything_else_is_ignored(url):
    """A page with no term, or the wrong kind of page, must not become a result.

    The image tab is the interesting one: it IS /search, so the path alone is not enough
    to decide, and recording it would file a row with figures that were never shown.
    """
    assert collector._query_from_google_url(url) == ""


def test_the_requested_region_is_read_from_the_url_not_assumed():
    """The user's own search carries whatever region they asked Google for.

    Taking the app's country setting instead would label a UK search as US data, which is
    exactly the mislabelling the mismatch warning elsewhere exists to catch.
    """
    assert collector._region_from_google_url("https://www.google.com/search?q=x&gl=gb") == "United Kingdom"
    assert collector._region_from_google_url("https://www.google.com/search?q=x&gl=in") == "India"
    # No gl at all: the caller falls back rather than inventing a country.
    assert collector._region_from_google_url("https://www.google.com/search?q=x") == ""


def test_a_running_run_keeps_ownership_of_the_browser():
    """Two capture paths must never write the same page twice.

    While a scripted run is working through its keywords it is navigating this very
    browser, so the idle watcher has to stand down or every keyword lands in both runs.
    """
    session = collector.CollectorSession()
    session._run = collector.Run(
        id="x",
        keywords=["a"],
        country=collector.country_by_code("us"),
        delay_ms=3000,
        max_suggestions=25,
        status="running",
    )

    class _Boom:
        @property
        def pages(self):
            raise AssertionError("the watcher looked at the browser during a run")

    session._capture_manual_search(_Boom())  # must return before touching the context
