"""Choosing a Chromium when the packaged app ships none.

Driven with a fake Playwright rather than real browsers: which browsers exist is a property
of the machine, and a test that passes only on a developer's laptop is exactly how the
original bug reached a release.
"""

from __future__ import annotations

import pytest

from app.services import chromium_launch


class _FakePlaywright:
    """Answers like Playwright: available channels launch, the rest raise its own wording."""

    def __init__(self, available: set):
        self.available = available
        self.attempts: list = []
        self.chromium = self

    def launch_persistent_context(self, channel=None, **kwargs):
        self.attempts.append(channel)
        if channel in self.available:
            return f"context:{channel}"
        raise RuntimeError(
            "BrowserType.launch_persistent_context: Executable doesn't exist at "
            r"C:\...\chromium-1228\chrome-win64\chrome.exe"
        )


def test_playwrights_own_chromium_is_preferred():
    """A pinned version tested against beats whatever the user's browser updated to."""
    fake = _FakePlaywright({None, "msedge", "chrome"})
    assert chromium_launch.launch_persistent_context(fake) == "context:None"
    assert fake.attempts == [None]


def test_it_falls_back_to_edge_when_nothing_was_bundled():
    """The packaged case: driver present, browser never downloaded."""
    fake = _FakePlaywright({"msedge", "chrome"})
    assert chromium_launch.launch_persistent_context(fake) == "context:msedge"
    assert fake.attempts == [None, "msedge"]


def test_it_reaches_chrome_when_edge_is_absent_too():
    fake = _FakePlaywright({"chrome"})
    assert chromium_launch.launch_persistent_context(fake) == "context:chrome"
    assert fake.attempts == [None, "msedge", "chrome"]


def test_with_nothing_available_it_says_what_to_do():
    fake = _FakePlaywright(set())
    with pytest.raises(chromium_launch.NoChromiumAvailable) as err:
        chromium_launch.launch_persistent_context(fake)
    message = str(err.value)
    assert "Edge" in message and "Chrome" in message
    # The reassurance matters as much as the instruction: this borrows the browser, never
    # the browsing, and a user asked to install a browser deserves to be told that.
    assert "profile" in message.lower()


def test_a_browser_that_exists_but_crashes_is_not_retried():
    """The distinction the whole fallback depends on.

    A crash on launch reported as "no browser found" would send someone installing Chrome
    to fix a problem Chrome does not have — and would try three browsers to say it.
    """

    class _Crashing:
        def __init__(self):
            self.attempts = []
            self.chromium = self

        def launch_persistent_context(self, channel=None, **kwargs):
            self.attempts.append(channel)
            raise RuntimeError("Target page, context or browser has been closed")

    fake = _Crashing()
    with pytest.raises(RuntimeError, match="has been closed"):
        chromium_launch.launch_persistent_context(fake)
    assert fake.attempts == [None], "a real launch failure must surface immediately"


def test_caller_arguments_are_passed_through_untouched():
    """Each call site keeps its own flags, profile and headless choice."""
    seen = {}

    class _Recording:
        def __init__(self):
            self.chromium = self

        def launch_persistent_context(self, channel=None, **kwargs):
            seen.update(kwargs)
            return "ok"

    chromium_launch.launch_persistent_context(
        _Recording(), user_data_dir="/profile", headless=False, args=["--load-extension=x"]
    )
    assert seen["user_data_dir"] == "/profile"
    assert seen["headless"] is False
    assert seen["args"] == ["--load-extension=x"]
