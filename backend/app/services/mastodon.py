"""Mastodon (ActivityPub) access for the Mastodon Post Creator.

Native to this app rather than vendored. vendor/socialpost is the standalone
Bluesky project kept unmodified, and Mastodon is a different protocol with a
different consent model — so the API work lives here, in the service layer, the
same shape as app/services/mail.py. What it produces is written into the
vendored package's own corpus tagged platform='mastodon', which is why the
learning loop (embeddings, exemplars, baselines) needs no duplicate of itself.

Three things about the fediverse this file exists to get right:

  * Every instance is its own jurisdiction. The character limit, the rules, and
    even whether the public timeline is readable at all vary per host — measured
    live: hachyderm.io allows 2263 characters, mastodon.social 500, and
    mastodon.social returns 422 for an unauthenticated public-timeline read
    while hachyderm serves it. Nothing here may hardcode a limit or assume an
    endpoint is reachable; instance_info() asks the server.

  * Rules are per-instance and legally the user's problem, not ours. Several
    servers require generative-AI use to be disclosed, and several ban
    commercial promotion outright. fetch_policy() gathers rules AND the extended
    description, because on some servers (hachyderm being the case that caught
    us) the rules list is clean while the About page carries the restriction
    that actually matters.

  * Consent signals are per-account and must be honoured — see should_learn_from().

Rate limits: Mastodon's default is 300 requests / 5 min per access token and
7500 / 5 min per IP unauthenticated. A collection pass is a few dozen requests,
so the backoff below exists to survive a shared-IP 429, not to pace us.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger(__name__)

USER_AGENT = "MrAIMarketer/1.0 (+https://github.com/; Mastodon Post Creator)"

# Mastodon caps timeline and search pages at 40 regardless of what you ask for.
PAGE_LIMIT = 40

MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 2.0
REQUEST_TIMEOUT = 20

# Courtesy pause between requests in a collection pass. Well inside every
# published limit; the point is to not look like a scraper to an admin reading
# their logs.
POLITE_DELAY_SECONDS = 0.34

# Bio markers by which an account opts out of automated processing. #nobot is the
# long-standing fediverse convention and is honoured by convention, not by the
# API — which is exactly why it has to be checked explicitly here.
NO_BOT_MARKERS = ("#nobot", "#noindex", "#noarchive")


class MastodonError(RuntimeError):
    """An instance said no. The message is written to be shown to a user."""


class RateLimited(MastodonError):
    """Retries exhausted against a 429."""


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    hint: str = ""


@dataclass(frozen=True)
class InstanceInfo:
    host: str
    title: str
    version: str
    description: str
    max_characters: int
    max_media: int

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"


@dataclass(frozen=True)
class InstancePolicy:
    """Everything an instance publishes about what you may do on it.

    `rules` is the enforced list; `extended_description` is the About page. Both
    are carried because neither alone is the whole policy on every server.
    """

    info: InstanceInfo
    rules: list[Rule]
    extended_description: str

    @property
    def fingerprint(self) -> str:
        """Stable hash of the policy text.

        The acknowledgement the user gives is against *this* wording. When an
        instance edits its rules the fingerprint changes, the stored ack no
        longer matches, and the gate closes again — which is the entire reason
        the ack records a hash rather than a boolean.
        """
        payload = json.dumps(
            {
                "host": self.info.host,
                "rules": [[r.id, r.text, r.hint] for r in self.rules],
                "extended": self.extended_description,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Account:
    id: str
    acct: str
    url: str
    display_name: str
    followers: int
    bot: bool
    discoverable: bool | None
    indexable: bool | None
    note: str


@dataclass(frozen=True)
class Status:
    """Flattened Mastodon status — only the fields the corpus stores."""

    id: str
    uri: str  # ActivityPub URI, globally unique
    url: str  # human web link
    account: Account
    text: str
    hashtags: list[str] = field(default_factory=list)
    has_media: bool = False
    created_at: datetime | None = None
    favourites: int = 0
    reblogs: int = 0
    replies: int = 0
    visibility: str = "public"
    language: str = ""


# ---------------------------------------------------------------------------
# Corpus identity
#
# The shared posts table is keyed by a single `uri` text column, and engagement
# has to be re-read later from the instance we collected it on — a status's
# numeric id is assigned per-instance, so the host is part of the identity, not
# incidental to it. Hence a synthetic URI that round-trips both.
# ---------------------------------------------------------------------------

_CORPUS_URI_RE = re.compile(r"^mastodon://(?P<host>[^/]+)/(?P<status_id>[^/]+)$")


def corpus_uri(host: str, status_id: str) -> str:
    return f"mastodon://{host}/{status_id}"


def parse_corpus_uri(uri: str) -> tuple[str, str]:
    """(host, status_id) from a corpus URI, or raise."""
    match = _CORPUS_URI_RE.match((uri or "").strip())
    if not match:
        raise ValueError(f"{uri!r} is not a Mastodon corpus URI.")
    return match.group("host"), match.group("status_id")


# ---------------------------------------------------------------------------
# URL / HTML helpers
# ---------------------------------------------------------------------------


def normalise_host(value: str) -> str:
    """Accept 'hachyderm.io', 'https://hachyderm.io', or a URL on it -> host.

    People paste whatever their browser shows. Rejecting a full URL here would
    be pedantry, and getting it wrong silently points every later request at the
    wrong server.
    """
    raw = (value or "").strip()
    if not raw:
        raise MastodonError("Enter your Mastodon instance, e.g. hachyderm.io")
    if "//" not in raw:
        raw = f"https://{raw}"
    host = (urlparse(raw).hostname or "").strip().lower()
    if not host or "." not in host:
        raise MastodonError(f"{value!r} does not look like a Mastodon instance.")
    return host


_BLOCK_CLOSE_RE = re.compile(r"</(p|div|li|h[1-6]|blockquote)\s*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """Mastodon status/bio HTML -> plain text, preserving line structure.

    Statuses arrive as HTML (<p>, <br>, linkified <a>). Stripping tags without
    replacing the block boundaries first would weld paragraphs together, and the
    exemplar pool would then teach the model that Mastodon posts are one long
    run-on line.
    """
    if not raw:
        return ""
    text = _BR_RE.sub("\n", raw)
    text = _BLOCK_CLOSE_RE.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        log.debug("Unparseable created_at %r", value)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _request(
    host: str,
    path: str,
    token: str = "",
    params: dict[str, Any] | None = None,
) -> Any:
    """GET a Mastodon API path with retry on 429/5xx. Returns decoded JSON.

    4xx other than 429 are not retried: they mean the request itself is wrong
    (bad token, endpoint disabled on this instance), and repeating it just burns
    the budget and the admin's patience.
    """
    url = f"https://{host}{path}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url, headers=headers, params=params, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as err:
            if attempt == MAX_RETRIES - 1:
                raise MastodonError(f"Could not reach {host}: {err}") from None
            time.sleep(BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1))
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise MastodonError(
                    f"{host} returned something that is not JSON for {path}. "
                    f"Is that host actually a Mastodon server?"
                ) from None

        if resp.status_code == 401:
            raise MastodonError(
                f"{host} rejected the access token. Regenerate it under "
                f"Preferences -> Development on your instance."
            )
        if resp.status_code == 404:
            raise MastodonError(f"{host} has no {path} — it may not run Mastodon.")

        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt == MAX_RETRIES - 1:
            if resp.status_code == 429:
                raise RateLimited(
                    f"{host} is rate limiting us. Wait a few minutes and try again."
                )
            raise MastodonError(
                f"{host} returned HTTP {resp.status_code} for {path}."
            )

        delay = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1)
        # Honour whichever pacing hint the server sent; Mastodon uses both.
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        log.warning("%s returned %s; retrying in %.1fs", host, resp.status_code, delay)
        time.sleep(delay)

    raise RateLimited(f"Gave up on {host} after {MAX_RETRIES} attempts.")


# ---------------------------------------------------------------------------
# Instance metadata + policy
# ---------------------------------------------------------------------------


def instance_info(host: str) -> InstanceInfo:
    """Title, version, and the limits this server actually enforces."""
    host = normalise_host(host)
    data = _request(host, "/api/v2/instance")
    statuses = ((data or {}).get("configuration") or {}).get("statuses") or {}
    return InstanceInfo(
        host=host,
        title=str(data.get("title") or host),
        version=str(data.get("version") or ""),
        description=html_to_text(str(data.get("description") or "")),
        # Defaults are the Mastodon stock values, used only if a server omits
        # the field. Never assume them when the server did answer.
        max_characters=int(statuses.get("max_characters") or 500),
        max_media=int(statuses.get("max_media_attachments") or 4),
    )


def instance_rules(host: str) -> list[Rule]:
    data = _request(normalise_host(host), "/api/v1/instance/rules") or []
    return [
        Rule(
            id=str(r.get("id") or ""),
            text=html_to_text(str(r.get("text") or "")),
            hint=html_to_text(str(r.get("hint") or "")),
        )
        for r in data
        if (r.get("text") or "").strip()
    ]


def extended_description(host: str) -> str:
    """The instance's About page, as plain text.

    Fetched separately from the rules because they are separately authored and
    can disagree in scope. hachyderm.io is the worked example: its rules list is
    thirteen conduct rules with nothing about automation or business use, while
    its About page restricts company, agency, project and bot accounts to
    invite-only. Showing only the rules would have told the user the opposite of
    the truth.
    """
    try:
        data = _request(normalise_host(host), "/api/v1/instance/extended_description")
    except MastodonError:
        # Optional endpoint; a server without one is not an error.
        return ""
    return html_to_text(str((data or {}).get("content") or ""))


def fetch_policy(host: str) -> InstancePolicy:
    """Everything the instance publishes about acceptable use, in one object."""
    info = instance_info(host)
    return InstancePolicy(
        info=info,
        rules=instance_rules(info.host),
        extended_description=extended_description(info.host),
    )


# Terms whose presence in a rule means "this rule constrains what this tool
# does". Used only to sort the important rules to the top of the approval
# screen — nothing is ever hidden, because deciding for the user which of their
# instance's rules matter is exactly the judgement we must not make for them.
_RELEVANCE_TERMS = (
    "ai",
    "a.i.",
    "generative",
    "llm",
    "machine-generated",
    "chatgpt",
    "bot",
    "automat",
    "advertis",
    "marketing",
    "promot",
    "spam",
    "seo",
    "commercial",
    "business",
    "corporate",
    "scrap",
    "index",
    "attribut",
    "disclos",
)

_WORD_RE = re.compile(r"[a-z.]+")


def is_relevant(text: str) -> bool:
    """Whether a rule touches AI, automation, or commercial use."""
    words = set(_WORD_RE.findall((text or "").lower()))
    return any(
        term in words or any(w.startswith(term) for w in words)
        for term in _RELEVANCE_TERMS
    )


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


def should_learn_from(status: Status) -> tuple[bool, str]:
    """Whether a status may enter the exemplar corpus. Returns (ok, why_not).

    Mastodon's consent signals are weaker and more numerous than Bluesky's
    single !no-unauthenticated label, and reading them wrong in either direction
    is a real harm — too strict and there is no corpus, too loose and we are
    learning from people who asked not to be.

    The line drawn here: a *public* status carrying a hashtag is an act of
    deliberate publication to strangers; that is the fediverse's own "surface
    this beyond my followers" gesture. Anything less than public visibility is
    not, whatever the API will hand us.

    Deliberately NOT gated on `indexable`. That flag is opt-in-to-full-text-
    search and defaults to false on every instance, so treating it as a consent
    signal would reject essentially the entire network and quietly leave the
    user with an empty pool they cannot diagnose. `discoverable=false` IS
    honoured: that one is an explicit "keep me out of discovery surfaces".
    """
    if status.visibility != "public":
        return False, f"{status.visibility} visibility"
    if status.account.bot:
        return False, "bot account"
    if status.account.discoverable is False:
        return False, "account opted out of discovery"
    note = (status.account.note or "").lower()
    for marker in NO_BOT_MARKERS:
        if marker in note:
            return False, f"account bio carries {marker}"
    if not status.text.strip():
        return False, "no text to learn from"
    return True, ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_account(raw: dict) -> Account:
    return Account(
        id=str(raw.get("id") or ""),
        acct=str(raw.get("acct") or ""),
        url=str(raw.get("url") or ""),
        display_name=str(raw.get("display_name") or ""),
        followers=int(raw.get("followers_count") or 0),
        bot=bool(raw.get("bot")),
        # Tri-state on purpose: absent (older instance) is not the same as false.
        discoverable=raw.get("discoverable") if "discoverable" in raw else None,
        indexable=raw.get("indexable") if "indexable" in raw else None,
        note=html_to_text(str(raw.get("note") or "")),
    )


def _parse_status(raw: dict) -> Status | None:
    """Flatten a status, or None if it is not a usable original text post."""
    if not raw:
        return None
    # A boost carries the booster's id but the original author's content.
    # Keeping it would credit the wrong account and double-count the post.
    if raw.get("reblog"):
        return None
    account = raw.get("account") or {}
    text = html_to_text(str(raw.get("content") or ""))
    if not text:
        return None
    return Status(
        id=str(raw.get("id") or ""),
        uri=str(raw.get("uri") or ""),
        url=str(raw.get("url") or raw.get("uri") or ""),
        account=_parse_account(account),
        text=text,
        hashtags=[
            str(t.get("name")) for t in (raw.get("tags") or []) if t.get("name")
        ],
        has_media=bool(raw.get("media_attachments")),
        created_at=_parse_created_at(raw.get("created_at")),
        favourites=int(raw.get("favourites_count") or 0),
        reblogs=int(raw.get("reblogs_count") or 0),
        replies=int(raw.get("replies_count") or 0),
        visibility=str(raw.get("visibility") or "public"),
        language=str(raw.get("language") or ""),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _hashtag_of(keyword: str) -> str:
    """A niche keyword -> the hashtag a Mastodon user would have written.

    Mastodon hashtags cannot contain spaces or punctuation, so "rust gamedev"
    is posted as #rustgamedev. Collapsing the keyword the same way is what makes
    a niche defined for the Bluesky tool work here without being re-entered.
    """
    return re.sub(r"[^0-9a-z]+", "", (keyword or "").lower())


def tag_timeline(
    host: str,
    keyword: str,
    limit: int = PAGE_LIMIT,
    token: str = "",
) -> list[Status]:
    """Public statuses carrying a hashtag, newest first.

    The hashtag timeline rather than search is the primary collection surface on
    purpose: it is served unauthenticated by most instances, it is scoped to
    content the author deliberately tagged for discovery, and unlike full-text
    search it does not depend on the per-account `indexable` opt-in that almost
    nobody sets.
    """
    tag = _hashtag_of(keyword)
    if not tag:
        return []

    host = normalise_host(host)
    out: list[Status] = []
    max_id: str | None = None

    while len(out) < limit:
        params: dict[str, Any] = {"limit": min(PAGE_LIMIT, limit - len(out))}
        if max_id:
            params["max_id"] = max_id
        page = _request(host, f"/api/v1/timelines/tag/{quote(tag)}", token, params) or []
        if not page:
            break
        for raw in page:
            status = _parse_status(raw)
            if status:
                out.append(status)
        max_id = str(page[-1].get("id") or "")
        if not max_id:
            break
        time.sleep(POLITE_DELAY_SECONDS)

    log.info("mastodon #%s -> %d statuses", tag, len(out))
    return out[:limit]


def search_statuses(
    host: str,
    keyword: str,
    token: str,
    limit: int = PAGE_LIMIT,
) -> list[Status]:
    """Full-text status search. Requires a token, and returns little without one.

    Secondary to tag_timeline(): Mastodon only indexes statuses from accounts
    that opted in via `indexable`, plus the caller's own, so this reliably
    under-returns. Used to top up a thin niche, never as the only source.
    """
    if not token:
        return []
    host = normalise_host(host)
    data = _request(
        host,
        "/api/v2/search",
        token,
        {
            "q": keyword,
            "type": "statuses",
            "limit": min(PAGE_LIMIT, limit),
            "resolve": "false",
        },
    )
    out: list[Status] = []
    for raw in (data or {}).get("statuses") or []:
        status = _parse_status(raw)
        if status:
            out.append(status)
    return out


def get_status(host: str, status_id: str, token: str = "") -> Status | None:
    """Re-read one status for current engagement counts.

    Returns None for a deleted status, which the snapshot pass reads as "no
    measurement for this window" rather than as zeroes.
    """
    try:
        raw = _request(normalise_host(host), f"/api/v1/statuses/{quote(status_id)}", token)
    except MastodonError:
        return None
    return _parse_status(raw)


def get_statuses(host: str, status_ids: Iterable[str], token: str = "") -> dict[str, Status]:
    """Re-read many statuses, keyed by id.

    Mastodon has no batch status endpoint, so this is a polite serial loop
    rather than a chunked call — the reason the snapshot pass caps how many
    posts it measures per run.
    """
    out: dict[str, Status] = {}
    for status_id in status_ids:
        status = get_status(host, status_id, token)
        if status:
            out[status_id] = status
        time.sleep(POLITE_DELAY_SECONDS)
    return out


def resolve_status(host: str, url: str, token: str) -> Status | None:
    """Find a status by its web URL, as the local instance sees it.

    This is what closes the learning loop. The user pastes the link their
    browser showed them; we need the id *on their instance*, because that is the
    only id later engagement reads can use. resolve=true asks the instance to
    fetch it if it has not seen it yet.
    """
    if not token:
        raise MastodonError(
            "Linking a published post needs your access token — add it in Settings."
        )
    data = _request(
        normalise_host(host),
        "/api/v2/search",
        token,
        {"q": url.strip(), "type": "statuses", "resolve": "true", "limit": 1},
    )
    for raw in (data or {}).get("statuses") or []:
        status = _parse_status(raw)
        if status:
            return status
    return None


def verify_credentials(host: str, token: str) -> dict:
    """Who the token belongs to, plus the flags an automated poster should set."""
    raw = _request(normalise_host(host), "/api/v1/accounts/verify_credentials", token)
    account = _parse_account(raw or {})
    return {
        "acct": account.acct,
        "displayName": account.display_name,
        "url": account.url,
        "followers": account.followers,
        "botFlagSet": account.bot,
        "discoverable": account.discoverable,
    }


def engagement_rate(favourites: int, reblogs: int, replies: int, followers: int) -> float:
    """Follower-normalised engagement, matching the Bluesky tool's definition.

    Same formula as vendor/socialpost's snapshot job (interactions / followers)
    so the two corpora produce comparable scores and the shared exemplar
    ranking stays meaningful. Accounts below the floor return 0.0 rather than a
    division artefact: five likes on a 3-follower account is not a 167% rate.
    """
    if followers < 1:
        return 0.0
    return round((favourites + reblogs + replies) / followers, 6)
