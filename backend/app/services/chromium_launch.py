"""Launch a Chromium that actually exists on this machine.

Keyword Surfer drives a real browser through Playwright, and in a packaged install there
was none to drive. `playwright` is a Python dependency, so PyInstaller bundles its *driver*
— but the browser binary is a separate download that `playwright install` fetches, and the
build never ran it. Development machines have one from a manual install, which is exactly
why this survived to a release: the failure is invisible anywhere the tool was written.

    Executable doesn't exist at ...\\_internal\\playwright\\driver\\package\\
    .local-browsers\\chromium-1228\\chrome-win64\\chrome.exe

The obvious fix is to ship the browser. It is also ~150MB per platform on installers that
are already 600MB–985MB, with the Linux one close enough to GitHub's 2GiB asset ceiling
that the build has a guard for it. So this takes the cheaper route first: Playwright can
drive a Chromium the machine already has, through its `channel` argument, and every Windows
machine has Edge whether anyone asked for it or not.

Order is deliberate:

  1. Playwright's own Chromium, if present — a pinned version, tested against, and untouched
     by whatever the user's browser updated itself to overnight.
  2. Edge, then Chrome. Both are Chromium; the Surfer extension and the flags behave the
     same on either.

The user's *profile* is never touched on any of these paths — `user_data_dir` is supplied by
the caller and points inside this app's own data directory. Borrowing the browser is not
borrowing the browsing.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: None means "Playwright's own download". The rest are Playwright channel names.
_CHANNELS: tuple[str | None, ...] = (None, "msedge", "chrome")

_MISSING_MARKERS = (
    "executable doesn't exist",
    "please run the following command to download new browsers",
    "looks like playwright was just installed",
)


def _is_missing_browser(err: Exception) -> bool:
    """Whether this failure means "that browser isn't here", not "it failed to start".

    Matched on the message because Playwright raises the same `Error` type for a missing
    binary as for a crash on launch, and retrying the next channel is only right for the
    first. A genuine launch failure must surface as itself rather than being reported as an
    absent browser three channels later.
    """
    text = str(err).lower()
    return any(marker in text for marker in _MISSING_MARKERS)


class NoChromiumAvailable(RuntimeError):
    """Nothing on this machine could be driven. The message is written for a user."""


ADVICE = (
    "Keyword Surfer needs a Chromium-based browser to drive, and none was found. "
    "Installing Microsoft Edge or Google Chrome is the quickest fix — this app will use it "
    "automatically, and will not touch your profile or your browsing."
)


def launch_persistent_context(playwright, **kwargs):
    """`chromium.launch_persistent_context`, against whichever Chromium is available.

    Takes and forwards the caller's arguments unchanged, so the two call sites keep their
    own flags, profile directory and headless choice — the only thing decided here is which
    browser those arguments are handed to.
    """
    first_error: Exception | None = None

    for channel in _CHANNELS:
        try:
            if channel is None:
                return playwright.chromium.launch_persistent_context(**kwargs)
            return playwright.chromium.launch_persistent_context(channel=channel, **kwargs)
        except Exception as err:  # noqa: BLE001 — narrowed immediately below
            if not _is_missing_browser(err):
                # It exists and refused to start. That is a different problem and the
                # caller deserves to hear about it rather than a browser-not-found story.
                raise
            log.info("[chromium] %s unavailable, trying the next", channel or "bundled chromium")
            if first_error is None:
                first_error = err

    raise NoChromiumAvailable(ADVICE) from first_error
