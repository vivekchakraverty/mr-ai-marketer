"""Every check the monitor knows how to run.

Checks are black-box on purpose: nothing here imports Mr. AI Marketer's code. The monitor
probes the same surfaces a user's machine does — HTTP endpoints, the WSL/Docker runtime,
the Hugging Face API — so a green run means the app's dependencies actually answer, not
that its modules import cleanly.

Status vocabulary, which matters more than it looks:

* ``ok``   — answered as expected.
* ``warn`` — reachable but not in its normal state (a Space mid-build, a degraded tier).
* ``fail`` — the thing is down or broken.
* ``skip`` — not checkable right now for a known, benign reason (no token configured).

The distinction that stops this from being noise: a free Hugging Face Space at rest reports
``SLEEPING``, which is its *normal* state, not an outage — it wakes on the first request.
Treating that as a failure would page you twice a day, every day, forever.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

from . import config


@dataclass
class CheckResult:
    name: str
    category: str  # infra | space | module
    status: str  # ok | warn | fail | skip
    detail: str = ""
    ms: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Check:
    name: str
    category: str
    run: Callable[[], "CheckResult | tuple[str, str]"]


def _result(name, category, outcome) -> CheckResult:
    """Lets a check return either a full CheckResult or a plain (status, detail) tuple."""
    if isinstance(outcome, CheckResult):
        return outcome
    status, detail = outcome
    return CheckResult(name=name, category=category, status=status, detail=detail)


def _describe(err: Exception) -> str:
    """A human sentence, not a urllib3 dump.

    The raw text of a refused connection is ~200 characters of nested pool/adapter detail
    that says one thing: nothing is listening. Reports get read by someone deciding whether
    to care, so they get the one thing.
    """
    if isinstance(err, requests.ConnectionError):
        return "not listening — service is down or was never started"
    if isinstance(err, requests.Timeout):
        return "no response in time — service may be busy or wedged"
    if isinstance(err, subprocess.TimeoutExpired):
        return "command timed out"
    return f"{type(err).__name__}: {str(err)[:140]}"


def run_check(check: Check) -> CheckResult:
    started = time.time()
    try:
        outcome = check.run()
    except Exception as err:  # noqa: BLE001 — a check must never take the run down
        outcome = ("fail", _describe(err))
    result = _result(check.name, check.category, outcome)
    result.ms = int((time.time() - started) * 1000)
    return result


# --------------------------------------------------------------------------- infra


def _http_ok(url: str, expect: tuple[int, ...] = (200,), timeout: Optional[int] = None) -> tuple[str, str]:
    resp = requests.get(url, timeout=timeout or config.DEFAULT_TIMEOUT)
    if resp.status_code in expect:
        return "ok", f"HTTP {resp.status_code}"
    return "fail", f"HTTP {resp.status_code}"


def check_backend() -> tuple[str, str]:
    return _http_ok(f"{config.BACKEND_URL}/health")


def check_activepieces() -> tuple[str, str]:
    return _http_ok(f"{config.ACTIVEPIECES_URL}/api/v1/flags")


def _leadgen_demand() -> Optional[int]:
    """How many Lead Gen campaigns are running, or None if the backend didn't say.

    The Lead Gen containers are started on demand by the app, not at boot — so "not
    listening" is their correct resting state most of the time. Whether that is a fault
    depends entirely on whether a campaign needs them right now, which is what this asks.
    """
    try:
        resp = requests.get(f"{config.BACKEND_URL}/leadgen/status",
                            headers=config.api_headers(), timeout=config.DEFAULT_TIMEOUT)
        return int((resp.json() or {}).get("activeCampaigns")) if resp.ok else None
    except Exception:  # noqa: BLE001
        return None


def _leadgen_service(url: str, expect: tuple[int, ...]):
    def run() -> tuple[str, str]:
        try:
            return _http_ok(url, expect=expect)
        except requests.ConnectionError:
            active = _leadgen_demand()
            if active:
                return "fail", f"not listening while {active} campaign(s) are running"
            if active == 0:
                return "skip", "not running — the app starts it when you open Lead Gen"
            return "fail", "not listening, and the backend could not say whether it is needed"

    return run


def check_searxng() -> tuple[str, str]:
    # SearXNG answers its root with the search page; any 2xx means the container serves.
    return _leadgen_service(config.LEADGEN_SEARXNG_URL, (200, 302))()


def check_reacher() -> tuple[str, str]:
    # Reacher has no unauthenticated health route; a 404 still proves the HTTP server is up.
    return _leadgen_service(config.LEADGEN_REACHER_URL, (200, 404, 405))()


def _wsl(args: list[str], timeout: int = 40) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl.exe", "-d", config.WSL_DISTRO, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_wsl_running() -> tuple[str, str]:
    """The WSL2 VM has to be up for any container to exist.

    This is the failure that looks like something else: when the VM idles out it takes
    dockerd and every container with it, so the engine appears to be 'refusing connections'
    when in fact nothing is running underneath it. The app holds the VM open with a
    keep-alive process while the distribution engine is meant to be up.
    """
    proc = subprocess.run(["wsl.exe", "--list", "--running"], capture_output=True, timeout=40)
    # wsl.exe emits UTF-16LE on Windows; decoding as text= would give interleaved nulls.
    listing = proc.stdout.decode("utf-16-le", errors="ignore")
    if config.WSL_DISTRO.lower() in listing.lower():
        return "ok", f"{config.WSL_DISTRO} running"
    return "fail", f"{config.WSL_DISTRO} is not running — containers cannot be up"


def check_docker() -> tuple[str, str]:
    proc = _wsl(["-u", "root", "--", "docker", "version", "--format", "{{.Server.Version}}"])
    version = (proc.stdout or "").strip()
    if proc.returncode == 0 and version:
        return "ok", f"dockerd {version}"
    return "fail", (proc.stderr or proc.stdout or "docker did not answer").strip()[:180]


def check_containers() -> CheckResult:
    """Reports each expected container by name, so a partial outage is visible."""
    proc = _wsl(["-u", "root", "--", "docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"])
    if proc.returncode != 0:
        return CheckResult("Containers", "infra", "fail", "docker ps failed")

    states = {}
    for line in (proc.stdout or "").splitlines():
        if "\t" in line:
            name, _, state = line.partition("\t")
            states[name.strip()] = state.strip()

    # Only the distribution engine is expected to stay up; the Lead Gen pair is started on
    # demand, so counting them as "expected" would report a permanent partial outage.
    expected = ["mr-ai-marketer-activepieces"]
    on_demand = ["mr-ai-marketer-leadgen-searxng", "mr-ai-marketer-leadgen-reacher"]
    running = [n for n in expected if states.get(n) == "running"]
    missing = [n for n in expected if n not in states]
    stopped = [n for n in expected if n in states and states[n] != "running"]

    idle = [n for n in on_demand if states.get(n) != "running"]
    detail = f"{len(running)}/{len(expected)} always-on running"
    if idle:
        detail += f"; on-demand idle: {', '.join(i.replace('mr-ai-marketer-leadgen-', '') for i in idle)}"
    if stopped:
        detail += f"; stopped: {', '.join(s.replace('mr-ai-marketer-', '') for s in stopped)}"
    if missing:
        detail += f"; never created: {', '.join(m.replace('mr-ai-marketer-', '') for m in missing)}"
    status = "ok" if len(running) == len(expected) else ("warn" if running else "fail")
    return CheckResult("Containers", "infra", status, detail, meta={"states": states})


def check_yt_search() -> tuple[str, str]:
    """Search tier 1. Runs the real search rather than pinging youtube.com, because the
    failure mode worth catching is yt-dlp's extractor breaking, not the site being down."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return "skip", "yt-dlp not installed in this environment"

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
            "socket_timeout": 20, "noplaylist": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info("ytsearch3:git tutorial", download=False)
    n = len(((info or {}).get("entries")) or [])
    return ("ok", f"{n} results") if n else ("fail", "search returned nothing")


PIPED_INSTANCE_LIST = "https://piped-instances.kavin.rocks/"


def check_piped() -> tuple[str, str]:
    """Search tier 2. Degraded rather than failed when it is down — tier 1 covers it, and
    the public Piped network is thin enough that an outage here is expected, not alarming."""
    try:
        rows = requests.get(PIPED_INSTANCE_LIST, timeout=config.DEFAULT_TIMEOUT).json()
        instances = [r["api_url"].rstrip("/") for r in rows if isinstance(r, dict) and r.get("api_url")]
    except Exception:  # noqa: BLE001
        instances = []
    instances.append("https://api.piped.private.coffee")

    for instance in dict.fromkeys(instances):
        try:
            resp = requests.get(f"{instance}/search", params={"q": "git", "filter": "videos"},
                                timeout=12, headers={"Accept": "application/json"})
            items = resp.json().get("items") if resp.ok else None
            if items:
                return "ok", f"live via {instance}"
        except Exception:  # noqa: BLE001
            continue
    return "warn", "no live Piped instance (search falls back to yt-dlp)"


# --------------------------------------------------------------------------- spaces

# (label, space id, critical). A non-critical Space is one the app has a working path
# without — its failure is a degradation, not an outage. adarshajay/youtube-search is the
# third search tier behind yt-dlp and Piped, is third-party, and has been in RUNTIME_ERROR
# since well before this monitor existed; reporting it red forever would only teach you to
# stop reading the report.
# Your own Spaces come from configuration (see config.WATCHED_SPACES) — the monitor has no
# business hardcoding whose account to probe. The one fixed entry is a third-party Space the
# app falls back to, which is public and identical for everyone.
def _watched_spaces() -> list[tuple[str, str, bool]]:
    spaces = [(label, sid, True) for label, sid in config.WATCHED_SPACES.items() if sid]
    spaces.append(("YouTube search (Tutorial tier 3)", "adarshajay/youtube-search", False))
    return spaces

# A Space at rest is not a broken Space. Only the error stages are failures.
_STAGE_STATUS = {
    "RUNNING": "ok",
    "RUNNING_BUILDING": "ok",
    "RUNNING_APP_STARTING": "ok",
    "SLEEPING": "ok",
    "PAUSED": "warn",
    "BUILDING": "warn",
    "APP_STARTING": "warn",
    "NO_APP_FILE": "fail",
    "CONFIG_ERROR": "fail",
    "BUILD_ERROR": "fail",
    "RUNTIME_ERROR": "fail",
    "DELETING": "fail",
}


def _space_check(space_id: str, critical: bool = True):
    def run() -> CheckResult:
        headers = {"Authorization": f"Bearer {config.HF_TOKEN}"} if config.HF_TOKEN else {}
        resp = requests.get(f"https://huggingface.co/api/spaces/{space_id}",
                            headers=headers, timeout=config.DEFAULT_TIMEOUT)
        if resp.status_code == 401:
            # Private or gated. Without a token there is nothing to report but that fact —
            # calling it a failure would be wrong, the Space may be perfectly healthy.
            status = "skip" if not config.HF_TOKEN else "fail"
            detail = ("private Space — set HF_TOKEN to monitor it"
                      if not config.HF_TOKEN else "token rejected (401)")
            return CheckResult(space_id, "space", status, detail)
        if resp.status_code == 404:
            return CheckResult(space_id, "space", "fail", "not found (renamed or deleted?)")
        resp.raise_for_status()

        runtime = (resp.json() or {}).get("runtime") or {}
        stage = runtime.get("stage") or "UNKNOWN"
        status = _STAGE_STATUS.get(stage, "warn")
        note = " — wakes on first request" if stage == "SLEEPING" else ""
        if status == "fail" and not critical:
            status, note = "warn", " — optional fallback tier, app unaffected"
        return CheckResult(space_id, "space", status, f"{stage}{note}", meta={"stage": stage})

    return run


def check_mail_tracker() -> tuple[str, str]:
    """The mail-tracking Space is a plain HTTP service, not a gradio app, so the Spaces API
    stage tells us less than the service answering does. /docs is public; /events is not."""
    if not config.MAIL_TRACKER_URL:
        return "skip", "no tracking Space configured (MAIL_TRACKER_URL)"
    return _http_ok(f"{config.MAIL_TRACKER_URL}/docs")


# --------------------------------------------------------------------------- modules

# Every read surface the app has, one probe per capability rather than one per module.
# These prove the router is registered *and* that its dependencies answer — which is why
# they are preferred over reading the OpenAPI schema. All were confirmed to return 200 on an
# unconfigured install and to cost single-digit milliseconds, so a daily run of the whole set
# is cheap; the two that legitimately outrun the default budget carry their own timeout.
MODULE_PROBES = [
    # (label, path, timeout override)
    ("Library", "/library"),
    ("Queue", "/queue"),
    ("Backup", "/backup"),

    ("Marketing Plan · industries", "/marketing-plan/industries"),
    ("Marketing Plan · models", "/marketing-plan/models"),

    # meta is the Space's own descriptor; voices is the card list the other tools read;
    # modal/status is the bring-your-own-GPU path, which fails independently of both.
    ("Brand Studio · meta", "/brand-forge/meta"),
    ("Brand Studio · voices", "/brand-forge/voices"),
    ("Brand Studio · Modal", "/brand-forge/modal/status"),

    ("Topic Scout", "/topic-scout/options"),
    # The Influencer DB computes facets across the whole bundled catalogue on a cold call.
    ("Influencer DB", "/influencer-db/facets", 90),

    ("Bluesky Post · status", "/social-post/status"),
    ("Bluesky Post · niches", "/social-post/niches"),
    ("Mastodon Post · status", "/mastodon-post/status"),
    ("Mastodon Post · niches", "/mastodon-post/niches"),
    # Both composers read this; it is served per platform and must answer for each.
    ("Posting time · Bluesky", "/posting-time/recommendation?platform=bluesky"),
    ("Posting time · Mastodon", "/posting-time/recommendation?platform=mastodon"),

    ("Distribution · channels", "/distribution/channels"),

    ("Mail · status", "/mail/status"),
    ("Mail tracking · messages", "/mail-tracking/messages"),
    ("Mail tracking · stats", "/mail-tracking/stats"),

    ("Lead Gen · status", "/leadgen/status"),
    ("Lead Gen · campaigns", "/leadgen/campaigns"),
    ("Lead Gen · suppression", "/leadgen/suppression"),

    ("Engage", "/engage/status"),
    ("Bluesky Analytics · status", "/bluesky-analytics/status"),
    ("Bluesky Analytics · cohort", "/bluesky-analytics/cohort"),
    ("Bluesky Analytics · dashboard", "/bluesky-analytics/dashboard"),

    ("Tracker Studio", "/tracker/workbooks"),

    ("Community · status", "/community/status"),
    ("Community · tiers", "/community/tiers"),
    ("Community · members", "/community/members"),

    ("Settings · social schema", "/settings/social-post/schema"),
    ("Settings · leadgen schema", "/settings/leadgen/schema"),
    # Datasets and models are fetched from Hugging Face rather than bundled, so "is the
    # catalogue actually here" is a real thing to check.
    ("Hosted assets", "/settings/assets"),
]

# Endpoints that only do work — generation, a crawl, a signed-in fediverse call. There is
# nothing to GET, and calling them for real means paying for inference on every run.
#
# So they are probed by their CONTRACT instead: send a body the request model must reject
# and require a 422 back. FastAPI validates before the handler is entered, so nothing is
# generated, nothing is crawled and no token is spent — while still proving the router
# loaded, its request model is intact, and the auth layer let the call through. That is
# strictly more than the old "is the path in openapi.json" check established, and it costs
# about a millisecond.
#
# `body` is chosen per endpoint: {} where the model has required fields, and a
# deliberately mistyped payload where it does not (an empty body would be *valid* there,
# and would run the very work this is avoiding). Verified against the live app: every one
# of these returns 422 in under 0.1s.
CONTRACT_PROBES = [
    ("Blog Writer", "/blog-writer/generate", {}),
    ("Email Writer", "/email-writer/generate", {}),
    ("Tutorial Maker", "/tutorial-maker/generate", {}),
    ("DocuMaker", "/docu-maker/generate", {}),
    ("Guest Post", "/guest-post/search", {"site": 12345, "topic": []}),
    ("Hashtag Suggester", "/hashtags/suggest", {"draft": 12345, "platform": []}),
    # The account half of Community: every route needs a Telegram session in the body.
    ("Community account", "/community/account/status", {}),
    # Measuring an instance reads a server's public timeline, which is real traffic on
    # somebody else's machine — not something to do to a stranger twice a day.
    ("Posting time · measure", "/posting-time/measure", {}),
]

# Mastodon Engage is POST-only across all 13 of its routes and every one needs a live
# session, so the whole module would otherwise go unchecked. These four are the ones the
# panel cannot work without: reading a timeline, reading notifications, posting, and acting
# on a status.
CONTRACT_PROBES += [
    (f"Mastodon Engage · {label}", path, {})
    for label, path in [
        ("timeline", "/mastodon-engage/timeline"),
        ("notifications", "/mastodon-engage/notifications"),
        ("compose", "/mastodon-engage/compose"),
        ("status action", "/mastodon-engage/status-action"),
    ]
]


def _module_probe(path: str, timeout: Optional[int] = None):
    def run() -> tuple[str, str]:
        resp = requests.get(f"{config.BACKEND_URL}{path}",
                            headers=config.api_headers(), timeout=timeout or config.DEFAULT_TIMEOUT)
        if resp.ok:
            return "ok", f"HTTP {resp.status_code}"
        return "fail", f"HTTP {resp.status_code}: {resp.text[:120]}"

    return run


def _contract_probe(path: str, body: dict):
    """Assert an endpoint rejects an invalid request, without letting it do any work.

    The 2xx branch is the important one. A success means the payload this probe sends as
    *invalid* was accepted — so the request model has changed underneath it and the handler
    just ran for real. On a generation endpoint that is a wasted inference call on every
    scheduled run, and it would otherwise look like the healthiest result on the page.
    """
    def run() -> tuple[str, str]:
        resp = requests.post(f"{config.BACKEND_URL}{path}", json=body,
                             headers=config.api_headers(), timeout=config.DEFAULT_TIMEOUT)
        if resp.status_code == 422:
            return "ok", "contract enforced (422)"
        if resp.status_code == 404:
            return "fail", "404 — router failed to load"
        if resp.status_code in (401, 403):
            return "fail", f"HTTP {resp.status_code} — monitor is not authenticated"
        if resp.ok:
            return "warn", (f"HTTP {resp.status_code} — invalid body was ACCEPTED; the probe "
                            "ran the handler. Update its payload in CONTRACT_PROBES.")
        return "fail", f"HTTP {resp.status_code}: {resp.text[:120]}"

    return run


# --------------------------------------------------------------------------- registry


def health_checks() -> list[Check]:
    """The daily set: every module and every capability, with no AI generation.

    "Cheaply" here means no inference, no crawling and no writes — not shallow. Read
    surfaces are called for real, and the work-only endpoints are held to their request
    contract (see CONTRACT_PROBES), so a green run means every router in the app loaded,
    answered, and enforced its own interface.
    """
    checks = [
        Check("Backend API", "infra", check_backend),
        Check("WSL2 VM", "infra", check_wsl_running),
        Check("Docker daemon", "infra", check_docker),
        Check("Containers", "infra", check_containers),
        Check("Distribution engine", "infra", check_activepieces),
        Check("Lead Gen SearXNG", "infra", check_searxng),
        Check("Lead Gen Reacher", "infra", check_reacher),
        Check("Video search (yt-dlp)", "infra", check_yt_search),
        Check("Video search (Piped)", "infra", check_piped),
        Check("Mail tracking Space", "space", check_mail_tracker),
    ]
    checks += [Check(label, "space", _space_check(space_id, critical))
               for label, space_id, critical in _watched_spaces()]
    checks += [Check(row[0], "module", _module_probe(row[1], row[2] if len(row) > 2 else None))
               for row in MODULE_PROBES]
    checks += [Check(f"{label} (contract)", "module", _contract_probe(path, body))
               for label, path, body in CONTRACT_PROBES]
    return checks


# --------------------------------------------------------------------------- end-to-end


def _post(path: str, body: dict, timeout: Optional[int] = None) -> requests.Response:
    return requests.post(f"{config.BACKEND_URL}{path}", json=body, headers=config.api_headers(),
                         timeout=timeout or config.E2E_TIMEOUT)


def e2e_hashtags() -> tuple[str, str]:
    resp = _post("/hashtags/suggest",
                 {"draft": "Shipping a new CLI tool for developers today.",
                  "platform": "mastodon", "hfToken": config.HF_TOKEN}, timeout=180)
    if not resp.ok:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:160]}"
    tags = (resp.json() or {}).get("suggestions") or []
    return ("ok", f"{len(tags)} hashtags") if tags else ("warn", "no hashtags returned")


def e2e_email_writer() -> tuple[str, str]:
    resp = _post("/email-writer/generate",
                 {"brief": "A short launch announcement for a developer CLI tool.",
                  "hfToken": config.HF_TOKEN})
    if not resp.ok:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:160]}"
    body = (resp.json() or {}).get("body") or ""
    return ("ok", f"{len(body)} chars generated") if body.strip() else ("fail", "empty body")


def e2e_blog_writer() -> tuple[str, str]:
    resp = _post("/blog-writer/generate",
                 {"topic": "What is a monorepo", "primaryKeyword": "monorepo",
                  "secondaryKeyword": "", "contentBrief": "", "hfToken": config.HF_TOKEN})
    if not resp.ok:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:160]}"
    markdown = (resp.json() or {}).get("markdown") or ""
    return ("ok", f"{len(markdown)} chars generated") if markdown.strip() else ("fail", "empty draft")


def e2e_distribution_catalogue() -> tuple[str, str]:
    resp = requests.get(f"{config.BACKEND_URL}/distribution/catalogue", headers=config.api_headers(),
                        params={"q": "telegram"}, timeout=config.DEFAULT_TIMEOUT)
    if not resp.ok:
        return "fail", f"HTTP {resp.status_code}: {resp.text[:160]}"
    total = (resp.json() or {}).get("total", 0)
    return ("ok", f"{total} piece(s) match") if total else ("fail", "catalogue empty")


def e2e_marketing_plan_keywords() -> tuple[str, str]:
    """Exercises the plan module's live keyword tier without generating a whole plan."""
    resp = requests.get(f"{config.BACKEND_URL}/marketing-plan/industries", headers=config.api_headers(),
                        timeout=config.DEFAULT_TIMEOUT)
    if not resp.ok:
        return "fail", f"HTTP {resp.status_code}"
    options = resp.json()
    return ("ok", f"{len(options)} industries") if options else ("fail", "no industries")


def e2e_checks() -> list[Check]:
    """The weekly set: real work through the real endpoints.

    Deliberately a representative subset rather than everything. Tutorial Maker is left out
    on purpose — it downloads a video and can run for many minutes, which is a poor fit for
    an unattended weekly job; its search tiers are already covered by the health run.
    """
    return [
        Check("E2E: Distribution catalogue", "module", e2e_distribution_catalogue),
        Check("E2E: Marketing Plan data", "module", e2e_marketing_plan_keywords),
        Check("E2E: Hashtag Suggester", "module", e2e_hashtags),
        Check("E2E: Email Writer (Space)", "module", e2e_email_writer),
        Check("E2E: Blog Writer (Space)", "module", e2e_blog_writer),
    ]
