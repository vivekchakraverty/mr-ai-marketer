"""Collect Keyword Surfer data in a visible browser the user can reach into.

Ported from the standalone Keyword Surfer Collector. The design decision worth restating,
because it is the one that makes this work at all: the browser is **visible and not
disguised**. Earlier attempts here drove a hidden browser and were served Google's captcha
page every time — measured from a datacenter address and an ordinary home connection alike,
with automation tells masked and a real window on screen. Nothing about hiding harder fixed
it.

So this does the opposite. It opens a window, keeps a dedicated profile so the answer is
remembered, and when Google asks for a check it **stops and waits for the person** instead
of pretending to be one. The extension's rendered UI remains the authority for which rows
belong to a result. For fields its newer UI hides (currently idea CPC), the collector reads
the same records the extension has already loaded into its browser cache; it never calls
the extension's private API itself.

Everything runs on one dedicated thread. Playwright's sync objects belong to the thread
that made them, and the browser has to outlive the request that opened it, so a thread that
owns the browser and takes commands off a queue is the shape that fits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import chromium_launch
from .keyword_surfer import SurferUnavailable, ensure_extension
from .keyword_surfer_js import (
    CAPTURE_JS,
    EXPAND_PAGE_SIZE_JS,
    FOCUS_MODE_JS,
    HEADER_TERMS,
    NEXT_PAGE_JS,
)
from .keyword_surfer_parse import normalize_text, parse_snapshot

log = logging.getLogger(__name__)

# Ported from the collector's config.js. Google's `gl`/`hl` pair; Surfer has its own
# location selector which the user sets once inside the panel, and whose label the parser
# reports back so the two can be seen to agree.
COUNTRIES = [
    {"code": "us", "name": "United States", "language": "en"},
    {"code": "gb", "name": "United Kingdom", "language": "en"},
    {"code": "in", "name": "India", "language": "en"},
    {"code": "ca", "name": "Canada", "language": "en"},
    {"code": "au", "name": "Australia", "language": "en"},
    {"code": "de", "name": "Germany", "language": "de"},
    {"code": "fr", "name": "France", "language": "fr"},
    {"code": "es", "name": "Spain", "language": "es"},
    {"code": "it", "name": "Italy", "language": "it"},
    {"code": "nl", "name": "Netherlands", "language": "nl"},
    {"code": "br", "name": "Brazil", "language": "pt"},
    {"code": "mx", "name": "Mexico", "language": "es"},
    {"code": "jp", "name": "Japan", "language": "ja"},
    {"code": "sg", "name": "Singapore", "language": "en"},
    {"code": "za", "name": "South Africa", "language": "en"},
    {"code": "ae", "name": "United Arab Emirates", "language": "en"},
    {"code": "nz", "name": "New Zealand", "language": "en"},
    {"code": "ie", "name": "Ireland", "language": "en"},
    {"code": "se", "name": "Sweden", "language": "sv"},
    {"code": "pl", "name": "Poland", "language": "pl"},
]

MIN_DELAY_MS = 3_000
MAX_KEYWORDS_PER_RUN = 250
SURFER_STORE_URL = (
    "https://chromewebstore.google.com/detail/keyword-surfer/bafijghppfhdpldihckdcadbcobikaca"
)

_CHALLENGE_URL = re.compile(r"/sorry/|google\.[^/]+/sorry", re.IGNORECASE)
_ATTENTION_TIMEOUT_S = 5 * 60
_DATA_TIMEOUT_S = 30

STATUS_MESSAGES = {
    "extension_not_detected": (
        "Keyword Surfer was not detected on the results page. Close the collector browser "
        "and open it again."
    ),
    "no_data": "The Keyword Surfer panel appeared, but no metrics could be read.",
    "partial": "Suggestions were collected, but the exact-query volume was not visible.",
    "complete": "Collected from the rendered Keyword Surfer panel.",
    "navigation_error": "The search page could not be loaded.",
    "google_challenge": "The Google verification was not completed in time.",
}


def country_by_code(code: str) -> dict:
    lowered = str(code or "").lower()
    return next((c for c in COUNTRIES if c["code"] == lowered), COUNTRIES[0])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def profile_dir() -> Path:
    """A profile of its own, kept between sessions.

    Persistence is the point: a completed Google check, the cookie consent, and Surfer's
    own location setting all live here, so the second run is not asked what the first one
    already answered.
    """
    return config.DATA_DIR / "keyword-surfer" / "profile"


def runs_dir() -> Path:
    return config.DATA_DIR / "keyword-surfer" / "runs"


# --------------------------------------------------------------------------- capture

def capture_snapshot(page) -> dict:
    """Run the capture script in every frame and merge what comes back.

    Surfer renders into the page in some versions and into an extension frame in others,
    so every frame is tried and the most panel-like answer wins, with rows unioned across
    all of them.
    """
    frames = sorted(
        page.frames,
        key=lambda f: 1 if re.search(r"chrome-extension:|surfer", f.url or "", re.IGNORECASE) else 0,
        reverse=True,
    )

    try:
        page_url = page.url or ""
    except Exception:  # noqa: BLE001 - a navigation can replace the page mid-read
        page_url = ""
    capture_args = {
        "headerTerms": HEADER_TERMS,
        "query": _query_from_google_url(page_url),
        "country": _country_code_from_google_url(page_url),
    }

    snapshots = []
    for frame in frames:
        try:
            snapshots.append(frame.evaluate(CAPTURE_JS, capture_args))
        except Exception:  # noqa: BLE001 — a frame can vanish while the extension redraws
            continue

    matching = [s for s in snapshots if s and s.get("rootFound")]
    if not matching:
        if snapshots and snapshots[0]:
            return snapshots[0]
        return {
            "rootFound": False,
            "rootSelector": None,
            "rootText": "",
            "markerCount": 0,
            "countryLabels": [],
            "mainKeywordMetrics": [],
            "cachedKeywordMetrics": [],
            "rows": [],
            "frameUrls": [f.url for f in frames],
            "capturedAt": _now(),
        }

    def score(snapshot: dict) -> int:
        return (
            (20 if re.search(r"chrome-extension:|surfer", snapshot.get("frameUrl") or "", re.IGNORECASE) else 0)
            + (snapshot.get("markerCount") or 0) * 3
            + min(len(snapshot.get("rows") or []), 20)
            + (10 if re.search(r"keyword ideas|search volume|similar keywords", snapshot.get("rootText") or "", re.IGNORECASE) else 0)
        )

    matching.sort(key=score, reverse=True)
    best = dict(matching[0])

    rows = []
    seen = set()
    for snapshot in matching:
        for row in snapshot.get("rows") or []:
            key = "␟".join(row.get("texts") or [])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    root_texts = list(dict.fromkeys([s.get("rootText") for s in matching if s.get("rootText")]))
    inline = next((s.get("mainKeywordMetrics") for s in matching if s.get("mainKeywordMetrics")), [])
    cached_metrics = []
    seen_cached = set()
    for snapshot in snapshots:
        for metric in (snapshot or {}).get("cachedKeywordMetrics") or []:
            key = normalize_text(metric.get("keyword")).casefold()
            if not key or key in seen_cached:
                continue
            seen_cached.add(key)
            cached_metrics.append(metric)
    country_labels = list(dict.fromkeys([c for s in matching for c in (s.get("countryLabels") or [])]))

    best.update(
        {
            "rootText": "\n".join(root_texts)[:20_000],
            "markerCount": sum(s.get("markerCount") or 0 for s in matching),
            "countryLabels": country_labels,
            "mainKeywordMetrics": inline,
            "cachedKeywordMetrics": cached_metrics,
            "rows": rows[:400],
            "frameUrls": [s.get("frameUrl") for s in snapshots if s],
        }
    )
    return best


#: How many pages of ideas to walk before stopping. At the panel's largest page size this
#: is far more than any query produces; it exists so a pager that never reports its last
#: page cannot spin forever.
MAX_IDEA_PAGES = 20


def _in_every_frame(page, script: str):
    """Run a script wherever the panel happens to live, and return the first real answer.

    Keyword Surfer renders into the page in some versions and into an extension frame in
    others — capture_snapshot already deals with that by trying every frame, and anything
    that drives the panel has to do the same.
    """
    for frame in page.frames:
        try:
            result = frame.evaluate(script)
        except Exception:  # noqa: BLE001 — a frame can vanish mid-redraw
            continue
        if result:
            return result
    return None


def capture_paged(page, max_rows: int = 400) -> dict:
    """Snapshot the panel across all of its pages, not just the one on screen.

    The ideas table is paged at five, so reading the DOM once collected five of forty-four
    and reported `complete`. This widens the page size first — one control, one redraw, most
    of the table — and only then walks the pager for whatever is left.

    Merging is by the same row key capture_snapshot dedupes with, so a page that redraws
    without actually advancing adds nothing rather than duplicating everything.
    """
    expanded = _in_every_frame(page, EXPAND_PAGE_SIZE_JS)
    if expanded and expanded.get("changed"):
        log.info("[surfer] ideas per page set to %s (from %s)", expanded["value"], expanded["options"])
        page.wait_for_timeout(700)

    snapshot = capture_snapshot(page)
    rows = list(snapshot.get("rows") or [])
    seen = {"␟".join(r.get("texts") or []) for r in rows}

    for _ in range(MAX_IDEA_PAGES):
        if len(rows) >= max_rows:
            break
        step = _in_every_frame(page, NEXT_PAGE_JS)
        if not step or not step.get("clicked"):
            break
        page.wait_for_timeout(700)

        added = 0
        for row in capture_snapshot(page).get("rows") or []:
            key = "␟".join(row.get("texts") or [])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        # A pager that clicked but changed nothing is at its end, whatever it reported.
        if added == 0:
            break

    if len(rows) != len(snapshot.get("rows") or []):
        log.info("[surfer] collected %d idea rows across the pager", len(rows))
    snapshot["rows"] = rows[:max_rows]
    return snapshot


def _is_google_challenge(page) -> bool:
    """Whether Google is currently asking the person to prove they are one.

    Matching on page text is a trap, and the previous version fell into it: it treated any
    page containing "not a robot" as a challenge. That phrase turns up in ordinary results —
    it is the label on every reCAPTCHA checkbox on the web, so any page *about* captchas
    says it — and once a run believed it was blocked, it sat waiting on a results page that
    was working perfectly, for the full five-minute timeout.

    So the signals here are ones only an actual interstitial has: Google's /sorry/ URL, its
    own wording, or the challenge form itself. A results page can quote any of these in a
    snippet without being one, which is why the text check now also requires the page to be
    short — an interstitial is a few hundred characters and a results page is thousands.
    """
    if _CHALLENGE_URL.search(page.url or ""):
        return True
    try:
        return bool(
            page.evaluate(
                "() => {"
                "  if (document.querySelector('#captcha-form, form#captcha-form,"
                " iframe[src*=\"recaptcha\"]')) return true;"
                "  const t = (document.body?.innerText || '').toLowerCase();"
                "  if (t.length > 1500) return false;"
                "  return t.includes('unusual traffic from your computer network')"
                "      || t.includes('our systems have detected unusual traffic');"
                "}"
            )
        )
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- runs

@dataclass
class Run:
    id: str
    keywords: list[str]
    country: dict
    delay_ms: int
    max_suggestions: int
    status: str = "queued"
    message: str = "Waiting to start…"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    current_index: int = 0
    results: list[dict] = field(default_factory=list)
    cancel_requested: bool = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "keywords": self.keywords,
            "keywordCount": len(self.keywords),
            "currentIndex": self.current_index,
            "completedCount": len(self.results),
            "settings": {
                "country": self.country,
                "delayMs": self.delay_ms,
                "maxSuggestions": self.max_suggestions,
            },
            # Diagnostics are large and only useful when something looks wrong; they stay
            # on disk rather than in every poll response.
            "results": [{k: v for k, v in r.items() if k != "diagnostics"} for r in self.results],
        }


@contextmanager
def _proactor_event_loop_policy():
    """Guarantee Playwright gets a loop that can spawn its driver process.

    Playwright's sync API calls asyncio.new_event_loop(), which honours the *global* policy.
    On Windows a SelectorEventLoop cannot start subprocesses at all — asyncio raises a bare
    NotImplementedError with no message — so if anything in this process has installed the
    selector policy, the collector browser can never open. Reproduced directly: identical
    code launches under the proactor policy and raises NotImplementedError under the
    selector one.

    The Modal SDK installs it, at import time and unconditionally on Windows:

        modal/_utils/async_utils.py:
            if sys.platform == "win32":
                # quick workaround for deadlocks on shutdown
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    Modal is imported lazily, so the policy flips the first time anything Modal-backed runs
    — Brand Studio, or generating an image on your own GPU — and stays flipped for the life
    of the process. That is why the collector opens on a fresh app and stops opening after
    an afternoon's work, with nothing in between that looks related.

    Fixing it here rather than upstream or by import order: Modal is entitled to the loop it
    wants, and load-bearing behaviour that depends on which features someone happened to use
    first is not something to leave standing.

    Restored immediately, and the window is only as long as it takes Playwright to build its
    loop — whatever wanted the selector policy gets it back, and the loop Playwright already
    holds is unaffected by the restore.
    """
    if sys.platform != "win32":
        yield
        return

    previous = asyncio.get_event_loop_policy()
    if isinstance(previous, asyncio.WindowsProactorEventLoopPolicy):
        yield
        return

    log.info("[surfer] temporarily selecting the proactor event loop so Playwright can start")
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


def _release_stale_profile_lock(profile: Path) -> None:
    """Clear the lock a hard-killed browser leaves behind in the profile.

    Chromium writes SingletonLock/Cookie/Socket while a profile is open and removes them on
    a clean exit. Kill the app — or let it crash, or let WSL take the machine down — and
    they survive, after which every later launch fails with "profile is already in use" and
    the collector is simply dead until someone deletes files they have no reason to know
    about. Measured: this happened twice in one afternoon of testing.

    Safe at exactly this moment and nowhere else. We are on the launch path, having already
    established there is no session of our own running; if some other browser really does
    hold the profile it will recreate the lock and the launch fails as before, which is the
    correct outcome. Removing these files does not touch cookies, history or the login this
    profile exists to remember.
    """
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
            log.info("[surfer] cleared a stale %s left by a previous session", name)
        except FileNotFoundError:
            pass
        except OSError as err:
            log.warning("[surfer] could not clear %s: %s", name, err)


def _query_from_google_url(url: str) -> str:
    """The search term out of a Google results URL, or '' if this is not one.

    Only /search pages count. The homepage, an image tab or a consent screen all carry
    other things in the query string and none of them render the panel this reads.
    """
    from urllib.parse import parse_qs, urlparse

    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    if "google." not in (parts.netloc or "") or not (parts.path or "").startswith("/search"):
        return ""
    query = parse_qs(parts.query or "")
    # `tbm` selects a vertical — images, news, shopping. Those are /search too, and Surfer
    # does not publish figures on them, so reading one would file a row whose numbers were
    # never on screen. Absent `tbm` is the web tab, which is the only one that counts.
    if query.get("tbm"):
        return ""
    return normalize_text((query.get("q") or [""])[0])


def _country_code_from_google_url(url: str) -> str:
    """The normalized Google `gl` country code in a web-results URL, if present."""
    from urllib.parse import parse_qs, urlparse

    try:
        return normalize_text((parse_qs(urlparse(url).query or "").get("gl") or [""])[0]).lower()
    except ValueError:
        return ""


def _region_from_google_url(url: str) -> str:
    """The country Google was asked for (`gl`), as the readable name this app uses."""
    code = _country_code_from_google_url(url)
    return country_by_code(code)["name"] if code else ""


def _clean_keywords(values) -> list[str]:
    seen = set()
    cleaned = []
    for value in values or []:
        keyword = normalize_text(value)
        key = keyword.casefold()
        if not keyword or key in seen:
            continue
        seen.add(key)
        cleaned.append(keyword)
    return cleaned


def _csv_escape(value) -> str:
    text = "" if value is None else str(value)
    return f'"{text.replace(chr(34), chr(34) * 2)}"' if re.search(r'[",\r\n]', text) else text


CSV_COLUMNS = [
    "seed_keyword",
    "keyword",
    "type",
    "search_volume",
    "cpc",
    "cpc_display",
    "similarity_percent",
    "status",
    "requested_google_region",
    "surfer_location_detected",
    "collected_at",
]


def _detected_location(result: dict) -> str:
    """The location Surfer reported for these numbers, as one readable string.

    The extension can name more than one (its panel shows a row per country when the user
    has several enabled), so the collector stores a list. Both readers wanted a single
    value and asked for a key nobody writes, which is why `surfer_location_detected` and
    the pill beside each keyword have always been empty.

    This matters more than it looks: a volume is only meaningful for a place, and the whole
    point of recording it is to catch the case where Surfer is reporting somewhere other
    than the region the search was run for.
    """
    labels = result.get("countryLabels") or []
    if isinstance(labels, str):
        return labels
    return ", ".join(str(label).strip() for label in labels if str(label).strip())


def run_as_keyword_data(run: dict) -> list[dict]:
    """A finished run as the rich rows the plan Space's supplied-keyword tier expects.

    Deliberately not the flattened table used for the sheet. The seed keeps its ideas
    nested, each with its own volume, CPC and similarity, because that nesting is what
    lets the SEO stage cluster by similarity and order work by volume — flattening it
    would hand the planner a list of names and the same problem it had before.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for result in run.get("results") or []:
        keyword = str(result.get("query") or "").strip()
        key = keyword.casefold()
        if not keyword or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "keyword": keyword,
                "volume": result.get("volume"),
                "cpc": result.get("cpcDisplay") or result.get("cpc"),
                "source": "keyword_surfer",
                "related": [
                    {
                        "keyword": s.get("keyword"),
                        "volume": s.get("volume"),
                        "cpc": s.get("cpcDisplay") or s.get("cpc"),
                        "similarity": s.get("similarity"),
                    }
                    for s in (result.get("suggestions") or [])
                    if s.get("keyword")
                ],
            }
        )
    return rows


def run_csv(run: dict) -> str:
    """One row per keyword, seeds and their suggestions interleaved under each seed."""
    rows = [CSV_COLUMNS]
    for result in run.get("results") or []:
        rows.append(
            [
                result.get("query"),
                result.get("query"),
                "seed",
                result.get("volume"),
                result.get("cpc"),
                result.get("cpcDisplay"),
                "",
                result.get("status"),
                result.get("requestedGoogleRegion"),
                _detected_location(result),
                result.get("collectedAt"),
            ]
        )
        for suggestion in result.get("suggestions") or []:
            rows.append(
                [
                    result.get("query"),
                    suggestion.get("keyword"),
                    "suggestion",
                    suggestion.get("volume"),
                    suggestion.get("cpc"),
                    suggestion.get("cpcDisplay"),
                    suggestion.get("similarity"),
                    result.get("status"),
                    result.get("requestedGoogleRegion"),
                    _detected_location(result),
                    result.get("collectedAt"),
                ]
            )
    return "\r\n".join(",".join(_csv_escape(cell) for cell in row) for row in rows) + "\r\n"


# --------------------------------------------------------------------------- session

class CollectorSession:
    """Owns the visible browser and the run loop, on one thread.

    Playwright's sync objects are bound to the thread that created them, and the browser
    has to survive the request that opened it, so requests post commands to this thread
    rather than touching the browser themselves. `_state` is the only thing they read, and
    it is only ever mutated under `_lock`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._run: Run | None = None
        # Searches the user types into the collector window accumulate into this one run;
        # the URL set stops the same results page being recorded on every idle tick.
        self._manual: Run | None = None
        self._seen_manual_urls: set[str] = set()
        #: Search terms a run performed, so the idle watcher leaves those pages alone.
        self._claimed_queries: set[str] = set()
        self._last_watch_log: tuple[str, str] | None = None
        self._watch_started = False
        self._state = {
            "running": False,
            "error": "",
            "profileDirectory": str(profile_dir()),
        }

    # -- state visible to HTTP handlers ------------------------------------------

    def status(self) -> dict:
        with self._lock:
            state = dict(self._state)
        state["extensionInstalled"] = (_extension_dir() / "manifest.json").exists()
        state["storeUrl"] = SURFER_STORE_URL
        return state

    def _set(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def current_run(self) -> dict | None:
        with self._lock:
            return self._run.public() if self._run else None

    # -- commands ----------------------------------------------------------------

    def open_browser(self, target: str = "https://www.google.com/") -> dict:
        """Start the session thread if needed and point a page at `target`."""
        self._ensure_thread()
        self._commands.put(("open", target))
        return self.status()

    def close_browser(self) -> dict:
        if self._thread and self._thread.is_alive():
            self._commands.put(("close", None))
        return self.status()

    def start_run(self, keywords, country_code: str, delay_ms: int, max_suggestions: int) -> dict:
        with self._lock:
            if self._run and self._run.status in ("queued", "running", "needs_attention"):
                raise ValueError("A keyword run is already active. Wait for it to finish or cancel it.")

        cleaned = _clean_keywords(keywords)
        if not cleaned:
            raise ValueError("Enter at least one keyword.")
        if len(cleaned) > MAX_KEYWORDS_PER_RUN:
            raise ValueError(f"A run can contain at most {MAX_KEYWORDS_PER_RUN} keywords.")

        run = Run(
            id=f"{_now()[:10]}-{uuid.uuid4().hex[:8]}",
            keywords=cleaned,
            country=country_by_code(country_code),
            # The floor is not politeness theatre: searches fired back to back are what
            # earns the captcha that stops the whole run.
            delay_ms=max(MIN_DELAY_MS, min(60_000, int(delay_ms or 7_000))),
            max_suggestions=max(1, min(100, int(max_suggestions or 25))),
        )
        with self._lock:
            self._run = run
        _save_run(run)

        self._ensure_thread()
        self._commands.put(("run", run))
        return run.public()

    def cancel_run(self) -> dict | None:
        with self._lock:
            if not self._run:
                return None
            if self._run.status in ("queued", "running", "needs_attention"):
                self._run.cancel_requested = True
                self._run.message = "Cancelling after the current search..."
            return self._run.public()

    # -- the thread --------------------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="keyword-surfer", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright

        try:
            ext = ensure_extension()
        except SurferUnavailable as err:
            self._set(running=False, error=str(err))
            return

        profile = profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        _release_stale_profile_lock(profile)

        try:
            # .start() rather than `with`, so the policy override below wraps only the
            # moment Playwright builds its loop and not the whole session.
            manager = sync_playwright()
            with _proactor_event_loop_policy():
                p = manager.start()
            try:
                # Via chromium_launch, not p.chromium: a packaged install ships no
                # browser of its own and falls back to the machine's Edge/Chrome. The
                # profile below is this app's, on every path.
                context = chromium_launch.launch_persistent_context(
                    p,
                    user_data_dir=str(profile),
                    # Visible, and staying visible. The window is how a person completes
                    # Google's check and sets Surfer's own location - hide it and the
                    # whole approach collapses back to being blocked.
                    headless=False,
                    viewport=None,
                    args=[
                        f"--disable-extensions-except={ext}",
                        f"--load-extension={ext}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--window-size=680,940",
                    ],
                )
                context.add_init_script(FOCUS_MODE_JS)
                self._set(running=True, error="")
                try:
                    self._serve(context)
                finally:
                    self._set(running=False)
                    try:
                        context.close()
                    except Exception:  # noqa: BLE001 - the user may have closed it already
                        pass
            finally:
                # stop() lives on the Playwright object the manager hands back, not on the
                # manager itself — calling it on the manager raises AttributeError inside
                # the teardown, which would mask whatever actually ended the session.
                p.stop()
        except chromium_launch.NoChromiumAvailable as err:
            # Already phrased for a person, and naming the exception class in front of it
            # only buries the one sentence that says what to do about it.
            self._set(running=False, error=str(err))
        except Exception as err:  # noqa: BLE001
            self._set(running=False, error=f"{type(err).__name__}: {err}")

    def _serve(self, context) -> None:
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=45_000)
        except Exception:  # noqa: BLE001 - an offline start is not fatal
            pass

        while True:
            try:
                command, payload = self._commands.get(timeout=1)
            except queue.Empty:
                # The user closing the window is how this session ends; noticing that is
                # what stops the thread idling forever against a dead browser.
                if not context.pages:
                    return
                self._capture_manual_search(context)
                continue

            if command == "close":
                return
            if command == "open":
                page = self._front_page(context)
                try:
                    page.goto(payload, wait_until="domcontentloaded", timeout=45_000)
                except Exception as err:  # noqa: BLE001
                    self._set(error=f"could not open the page: {err}")
            elif command == "run":
                self._execute(context, payload)

    def _front_page(self, context):
        page = next((p for p in context.pages if not p.is_closed()), None)
        if page is None:
            page = context.new_page()
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        return page

    # -- searches the user runs themselves ---------------------------------------

    def _capture_manual_search(self, context) -> None:
        """Record a Google search the user typed into the collector window.

        The window is visible and usable, so people search in it — and every one of those
        searches renders exactly the panel a run would have read. Collecting only during a
        run meant someone could sit looking at real volumes on screen while this tool
        reported nothing, which is a strange thing for a feature whose whole pitch is
        reading the page you could read yourself.

        Every return says why. This ran for minutes against a real results page producing
        no output whatsoever, which made it impossible to tell "not looking" from "looking
        and finding nothing" — the exact ambiguity that hid the original bug. Reading a page
        mid-navigation genuinely does raise, so the failures stay non-fatal; they just stop
        being invisible.
        """
        if not self._watch_started:
            self._watch_started = True
            log.info("[surfer-watch] watching the collector window for searches you run")

        if self._run and self._run.status in ("queued", "running", "needs_attention"):
            return  # a run owns the browser; its own capture is in charge

        try:
            page = next((p for p in context.pages if not p.is_closed()), None)
        except Exception as err:  # noqa: BLE001
            self._watch_log("", f"could not list the browser's pages ({type(err).__name__}: {err})")
            return
        if page is None:
            self._watch_log("", "the browser has no open page to read")
            return
        try:
            url = page.url or ""
        except Exception as err:  # noqa: BLE001
            self._watch_log("", f"could not read the page's address ({type(err).__name__}: {err})")
            return

        keyword = _query_from_google_url(url)
        if keyword and keyword.casefold() in self._claimed_queries:
            # A run searched for this; its own reader owns the page.
            return
        if not keyword:
            self._watch_log(url, "not a Google web-results page — nothing to collect here")
            return
        if url in self._seen_manual_urls:
            return

        try:
            snapshot = capture_snapshot(page)
            parsed = parse_snapshot(snapshot, keyword)
        except Exception as err:  # noqa: BLE001
            # Swallowed, but no longer silently: a page mid-navigation raises from half the
            # calls above, and "nothing happens and nothing is said" is exactly what made
            # the original failure impossible to diagnose.
            self._watch_log(url, f"could not read the page yet ({type(err).__name__}: {err})")
            return

        # Nothing on screen yet. Not marked as seen, so the next tick re-reads the same
        # page once the extension has drawn.
        if not parsed["loaded"] or (parsed["volume"] is None and not parsed["suggestions"]):
            self._watch_log(
                url,
                "waiting for the Keyword Surfer panel — "
                f"panel found={parsed['diagnostics'].get('rootFound')} "
                f"markers={snapshot.get('markerCount')} "
                f"frames={len(snapshot.get('frameUrls') or [])} "
                f"loaded={parsed['loaded']} volume={parsed['volume']} "
                f"ideas={len(parsed['suggestions'])}",
            )
            return

        self._watch_log(
            url,
            f"collected {keyword!r} — volume={parsed['volume']} ideas={len(parsed['suggestions'])}",
        )
        self._seen_manual_urls.add(url)
        run = self._manual_run()
        _append_result(run, {
            **parsed,
            "suggestions": parsed["suggestions"][: run.max_suggestions],
            "status": "complete" if parsed["volume"] is not None else "partial",
            "message": STATUS_MESSAGES["complete" if parsed["volume"] is not None else "partial"],
            # Whatever Google was asked for is in the URL the user navigated to, which is
            # not necessarily this app's country setting — so it is read back rather than
            # assumed.
            "requestedGoogleRegion": _region_from_google_url(url) or run.country["name"],
            "collectedAt": _now(),
            "googleUrl": url,
        })
        run.status = "completed"
        run.keywords = [r.get("query", "") for r in run.results]
        run.message = (
            f"Collected {len(run.results)} search{'' if len(run.results) == 1 else 'es'} "
            f"you ran in the collector window."
        )
        run.finished_at = _now()
        _save_run(run)

    def _watch_log(self, url: str, message: str) -> None:
        """Log a watcher observation once per distinct state, not once per second.

        Without the dedupe this writes sixty identical lines a minute while a page settles,
        which buries the one line that changes.
        """
        key = (url, message)
        if key == self._last_watch_log:
            return
        self._last_watch_log = key
        log.info("[surfer-watch] %s | %s", url[:90], message)

    def _manual_run(self) -> Run:
        """The run that searches-by-hand accumulate into, created on the first one.

        One run per session rather than one per search, so a browsing session reads as a
        single piece of work in the history instead of twenty one-keyword entries.
        """
        with self._lock:
            if self._manual is None or self._manual is not self._run:
                self._manual = Run(
                    id=f"{_now()[:10]}-manual-{uuid.uuid4().hex[:8]}",
                    keywords=[],
                    country=country_by_code("us"),
                    delay_ms=MIN_DELAY_MS,
                    max_suggestions=100,
                    status="running",
                    message="Watching the collector window.",
                )
                self._run = self._manual
            return self._manual

    # -- one run -----------------------------------------------------------------

    def _execute(self, context, run: Run) -> None:
        run.status = "running"
        run.started_at = _now()
        run.message = "Opening the collector browser..."
        _save_run(run)

        try:
            page = self._front_page(context)
            for index, keyword in enumerate(run.keywords):
                if run.cancel_requested:
                    break
                run.current_index = index
                run.message = f"Searching {keyword!r} ({index + 1} of {len(run.keywords)})..."
                _save_run(run)

                if not self._search(page, run, keyword):
                    continue

                if index < len(run.keywords) - 1 and not run.cancel_requested:
                    seconds = round(run.delay_ms / 1000)
                    run.message = f"Cooling down for {seconds} seconds..."
                    _save_run(run)
                    _sleep_cancellable(run, run.delay_ms / 1000)

            done = len(run.results)
            run.status = "cancelled" if run.cancel_requested else "completed"
            if run.cancel_requested:
                run.message = f"Cancelled after {done} of {len(run.keywords)} keywords."
            else:
                run.message = f"Finished {done} keyword{'' if done == 1 else 's'}."
        except Exception as err:  # noqa: BLE001
            run.status = "failed"
            run.message = f"{type(err).__name__}: {err}"
        finally:
            run.finished_at = _now()
            _save_run(run)

    def _search(self, page, run: Run, keyword: str) -> bool:
        from urllib.parse import urlencode

        params = {
            "q": keyword,
            "gl": run.country["code"],
            "hl": run.country["language"],
            "num": "10",
            "pws": "0",  # no personalisation, so two machines see comparable figures
        }
        target = "https://www.google.com/search?" + urlencode(params)
        # Claimed by SEARCH TERM, not by URL. Claiming the URL was the obvious move and does
        # not work: Google appends its own `sei` parameter on the way in, so the page the
        # watcher sees is never the address we asked for. Measured — the watcher captured
        # the run's own page anyway and replaced the finished run with it.
        self._claimed_queries.add(keyword.casefold())

        try:
            page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        except Exception as err:  # noqa: BLE001
            _append_result(run, {
                "query": keyword,
                "status": "navigation_error",
                "message": f"{STATUS_MESSAGES['navigation_error']} {err}",
                "volume": None,
                "cpc": None,
                "suggestions": [],
                "collectedAt": _now(),
            })
            return False

        if _is_google_challenge(page):
            run.status = "needs_attention"
            run.message = (
                "Google needs a manual check. Complete it in the collector browser window - "
                "this run resumes on its own."
            )
            _save_run(run)

            deadline = _monotonic() + _ATTENTION_TIMEOUT_S
            # Remembered, because the check below cannot ask the page again: a page that
            # still reads as a challenge while serving real numbers is the whole failure
            # being fixed, and re-testing it here would throw the escape straight away.
            resolved_by_data = False
            while _monotonic() < deadline and not run.cancel_requested:
                if not _is_google_challenge(page):
                    break
                # Readable data ends the wait too, whatever the page says about robots.
                # What this loop actually needs to know is "can I collect yet?", and asking
                # that directly cannot be fooled by wording — the old condition could sit
                # out the full timeout beside a panel full of the numbers it was waiting for.
                try:
                    probe = parse_snapshot(capture_snapshot(page), keyword)
                    if probe["loaded"] and (probe["volume"] is not None or probe["suggestions"]):
                        log.info("[surfer] data appeared while waiting on the check — carrying on")
                        resolved_by_data = True
                        break
                except Exception:  # noqa: BLE001 — mid-navigation; the next tick retries
                    pass
                _sleep_cancellable(run, 1)

            run.status = "running"
            run.message = f"Reading {keyword!r}..."
            _save_run(run)
            if not resolved_by_data and _is_google_challenge(page):
                _append_result(run, {
                    "query": keyword,
                    "status": "google_challenge",
                    "message": STATUS_MESSAGES["google_challenge"],
                    "volume": None,
                    "cpc": None,
                    "suggestions": [],
                    "collectedAt": _now(),
                })
                return False

        run.message = f"Waiting for Keyword Surfer data for {keyword!r}..."
        _save_run(run)

        # The panel is drawn after the page settles, so this polls rather than waiting a
        # fixed time - a slow network should not be read as "no data".
        parsed = None
        deadline = _monotonic() + _DATA_TIMEOUT_S
        while _monotonic() < deadline and not run.cancel_requested:
            parsed = parse_snapshot(capture_snapshot(page), keyword)
            if parsed["loaded"] and (parsed["volume"] is not None or parsed["suggestions"]):
                break
            _sleep_cancellable(run, 1)
        if parsed is None:
            parsed = parse_snapshot(capture_snapshot(page), keyword)

        # The poll above reads one page, because it is asking "has anything appeared yet".
        # Now that something has, take the whole table — the pager is the difference between
        # five ideas and all of them.
        if parsed["loaded"] and (parsed["volume"] is not None or parsed["suggestions"]):
            try:
                parsed = parse_snapshot(capture_paged(page, run.max_suggestions * 4), keyword)
            except Exception as err:  # noqa: BLE001 — keep the single-page read we already have
                log.info("[surfer] could not page the ideas table, keeping page one: %s", err)

        if not parsed["diagnostics"]["rootFound"]:
            status = "extension_not_detected"
        elif parsed["volume"] is None and not parsed["suggestions"]:
            status = "no_data"
        elif parsed["volume"] is None:
            status = "partial"
        else:
            status = "complete"

        _append_result(run, {
            **parsed,
            "suggestions": parsed["suggestions"][: run.max_suggestions],
            "status": status,
            "message": STATUS_MESSAGES[status],
            "requestedGoogleRegion": run.country["name"],
            "collectedAt": _now(),
            "googleUrl": page.url,
        })
        return True


def _monotonic() -> float:
    return time.monotonic()


def _sleep_cancellable(run: Run, seconds: float) -> None:
    """Sleep in slices so Cancel takes effect during a cooldown rather than after it."""
    remaining = seconds
    while remaining > 0 and not run.cancel_requested:
        step = min(0.25, remaining)
        time.sleep(step)
        remaining -= step


def _append_result(run: Run, result: dict) -> None:
    run.results.append(result)
    _save_run(run)


def _extension_dir() -> Path:
    from .keyword_surfer import extension_dir

    return extension_dir()


def _save_run(run: Run) -> None:
    directory = runs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = run.public()
    payload["results"] = run.results  # the on-disk copy keeps diagnostics
    (directory / f"{run.id}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )


def load_run(run_id: str) -> dict | None:
    # Sanitised because the id reaches here from a URL path and is used as a filename.
    safe = re.sub(r"[^A-Za-z0-9._-]", "", str(run_id or ""))
    if not safe:
        return None
    path = runs_dir() / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_runs(limit: int = 30) -> list[dict]:
    directory = runs_dir()
    if not directory.exists():
        return []
    runs = []
    for path in sorted(directory.glob("*.json"), reverse=True)[:limit]:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.append({
            "id": run.get("id"),
            "status": run.get("status"),
            "createdAt": run.get("createdAt"),
            "finishedAt": run.get("finishedAt"),
            "keywordCount": run.get("keywordCount"),
            "completedCount": run.get("completedCount"),
            "country": (run.get("settings") or {}).get("country"),
        })
    return runs


SESSION = CollectorSession()
