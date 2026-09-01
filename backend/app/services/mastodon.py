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

# REQUEST_TIMEOUT below is a JSON call's budget and far too short for a body of
# attachment size — see upload_budget for what that cost and how the figure is arrived at.
from .upload_budget import upload_timeout

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

    # What the server's own configuration says you may do. Shown verbatim in the
    # Engage terms panel as the "allowed" half of the picture, next to the rules
    # that say what you may not. All defaulted: an instance that omits a field has
    # told us nothing about it, which is different from telling us zero, and the
    # panel skips anything left at its default rather than claiming a limit of 0.
    max_poll_options: int = 0
    poll_max_expiration_days: int = 0
    image_size_limit_mb: int = 0
    video_size_limit_mb: int = 0
    languages: tuple[str, ...] = ()
    translation: bool = False
    thumbnail: str = ""
    contact_email: str = ""

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
        raise MastodonError("Enter your Mastodon instance, e.g. mastodon.social")
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


def _error_detail(resp: requests.Response) -> str:
    """The sentence the instance wrote about why it refused, if it wrote one.

    Mastodon answers a rejected write with {"error": "Text character limit of 500
    exceeded"} or an OAuth-scope complaint. That sentence is the only thing that
    tells a user what to change, so it must survive up to the UI instead of being
    flattened into "HTTP 422".
    """
    try:
        payload = resp.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("error_description", "error"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _not_found_message(host: str, path: str) -> str:
    """A 404 means different things for metadata and for a single object."""
    if path.startswith(("/api/v1/instance", "/api/v2/instance")):
        return f"{host} has no {path} — it may not run Mastodon."
    return f"{host} has nothing at {path} — it may have been deleted."


def _call(
    host: str,
    method: str,
    path: str,
    token: str = "",
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> Any:
    """One Mastodon API call, with retry on 429/5xx. Returns decoded JSON.

    4xx other than 429 are not retried: they mean the request itself is wrong
    (bad token, endpoint disabled on this instance), and repeating it just burns
    the budget and the admin's patience.

    Retrying a write is only safe because of what the writes are. Every POST this
    module makes except status creation sets a state rather than appending
    something — favourite, boost, bookmark, follow — so running it twice lands in
    the same place. Status creation is the exception, and it is exactly the one
    where a retry after a timeout could publish twice, so it carries an
    Idempotency-Key that Mastodon dedupes on.
    """
    url = f"https://{host}{path}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as err:
            if attempt == MAX_RETRIES - 1:
                raise MastodonError(f"Could not reach {host}: {err}") from None
            time.sleep(BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1))
            continue

        if resp.status_code in (200, 201, 202, 204):
            if not (resp.content or b"").strip():
                return {}
            try:
                return resp.json()
            except ValueError:
                raise MastodonError(
                    f"{host} returned something that is not JSON for {path}. "
                    f"Is that host actually a Mastodon server?"
                ) from None

        detail = _error_detail(resp)

        if resp.status_code == 401:
            raise MastodonError(
                detail
                or (
                    f"{host} rejected the access token. Regenerate it under "
                    f"Preferences -> Development on your instance."
                )
            )
        if resp.status_code == 403:
            # Almost always a token missing the scope the call needs.
            raise MastodonError(
                f"{host} refused that: {detail or 'the action is outside your token’s scopes'}. "
                f"A token for posting and following needs read, write and follow scopes."
            )
        if resp.status_code == 404:
            raise MastodonError(detail or _not_found_message(host, path))
        if resp.status_code == 422:
            raise MastodonError(detail or f"{host} rejected that as invalid.")

        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt == MAX_RETRIES - 1:
            if resp.status_code == 429:
                raise RateLimited(
                    f"{host} is rate limiting us. Wait a few minutes and try again."
                )
            raise MastodonError(
                detail or f"{host} returned HTTP {resp.status_code} for {path}."
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


def _request(
    host: str,
    path: str,
    token: str = "",
    params: dict[str, Any] | None = None,
) -> Any:
    """GET a Mastodon API path. The corpus side of this module only ever reads."""
    return _call(host, "GET", path, token, params=params)


# The Engage screen acts as the user rather than harvesting a corpus, so it needs
# the whole verb set and the raw JSON — the flattened Status above deliberately
# drops boosts, viewer state and media, all of which a client has to show. These
# three are the seam: transport, retry and error wording stay here, and
# routers/mastodon_engage.py owns the shape it presents.


def api_get(
    host: str, path: str, token: str = "", params: dict[str, Any] | None = None
) -> Any:
    return _call(normalise_host(host), "GET", path, token, params=params)


def api_post(
    host: str,
    path: str,
    token: str,
    data: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> Any:
    return _call(
        normalise_host(host),
        "POST",
        path,
        token,
        data=data,
        idempotency_key=idempotency_key,
    )


def api_delete(host: str, path: str, token: str) -> Any:
    return _call(normalise_host(host), "DELETE", path, token)


def upload_media(
    host: str, token: str, filename: str, content: bytes, description: str = ""
) -> str:
    """Upload one image or video and return its media id, for attaching to a status.

    A separate call rather than part of _call because this is the one request that is
    multipart rather than JSON, and folding a files= branch into the shared transport
    would complicate every other caller for one endpoint's sake.

    It is also the only request whose body is large enough for the socket timeout to
    matter, so it does not use REQUEST_TIMEOUT — see upload_budget for what a 20-second
    budget does to a 29MB video, and why the error did not look like a timeout.

    Uses v2, which answers 202 for anything the server is still processing. That claim
    used to end "the id is valid immediately either way, so there is nothing to poll",
    which is true of images and false of video. Posting a clip the moment the upload
    returned produced:

        Cannot attach files that have not finished processing. Try again in a moment!

    Video is transcoded server-side and cannot be referenced until that finishes, so a 202
    is now waited on. Images answer 200 and skip the wait entirely.

    Not retried. The shared transport retries writes because they set a state and are
    safe to repeat; an upload appends, and repeating it leaves orphaned attachments on
    the user's account.
    """
    host = normalise_host(host)
    if not token:
        raise MastodonError("Attaching an image needs your access token for this server.")

    files = {"file": (filename, content)}
    data = {"description": description[:1500]} if description.strip() else None
    budget = upload_timeout(len(content))
    megabytes = len(content) / (1024 * 1024)
    try:
        resp = requests.post(
            f"https://{host}/api/v2/media",
            headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
            files=files,
            data=data,
            timeout=budget,
        )
    except requests.RequestException as err:
        # Named rather than "the image": this path carries video too, and the size and the
        # budget are the two numbers that say whether the next failure is the uplink or us.
        raise MastodonError(
            f"Could not reach {host} to upload {filename} "
            f"({megabytes:.1f}MB, gave up after {budget:.0f}s): {err}"
        ) from None

    if resp.status_code not in (200, 202):
        raise MastodonError(_error_detail(resp))
    media_id = str((resp.json() or {}).get("id") or "")
    if not media_id:
        raise MastodonError(f"{host} accepted the image but returned no id for it.")
    if resp.status_code == 202:
        _wait_for_media(host, token, media_id)
    return media_id


#: How long to wait for a server to finish transcoding before giving up. Generous because
#: the alternative is a post that loses its video, and mean because the person is watching a
#: spinner: a minute is longer than any clip small enough to upload should need.
MEDIA_PROCESSING_TIMEOUT = 60
MEDIA_POLL_SECONDS = 2


def _wait_for_media(host: str, token: str, media_id: str) -> None:
    """Block until the server has finished processing an upload.

    `GET /api/v1/media/:id` answers 206 while the file is still being transcoded and 200
    once it can be attached. Polling that is the documented way to know, and the only
    alternative is posting and being refused.
    """
    import time

    deadline = time.monotonic() + MEDIA_PROCESSING_TIMEOUT
    while time.monotonic() < deadline:
        try:
            resp = requests.get(
                f"https://{host}/api/v1/media/{media_id}",
                headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            # A blip mid-transcode is not a failure; the next poll settles it.
            time.sleep(MEDIA_POLL_SECONDS)
            continue
        if resp.status_code == 200:
            return
        if resp.status_code not in (206, 404):
            raise MastodonError(_error_detail(resp))
        time.sleep(MEDIA_POLL_SECONDS)

    raise MastodonError(
        f"{host} is still processing that video after {MEDIA_PROCESSING_TIMEOUT} seconds. "
        f"It may be too long or too large for this server — try a shorter clip."
    )


# ---------------------------------------------------------------------------
# Instance metadata + policy
# ---------------------------------------------------------------------------


def _mb(value: Any) -> int:
    """Bytes -> whole megabytes, 0 when the server did not say."""
    try:
        return int(value) // (1024 * 1024)
    except (TypeError, ValueError):
        return 0


def instance_info(host: str) -> InstanceInfo:
    """Title, version, and the limits this server actually enforces."""
    host = normalise_host(host)
    data = _request(host, "/api/v2/instance")
    configuration = (data or {}).get("configuration") or {}
    statuses = configuration.get("statuses") or {}
    polls = configuration.get("polls") or {}
    media = configuration.get("media_attachments") or {}
    translation = configuration.get("translation") or {}
    contact = (data or {}).get("contact") or {}
    return InstanceInfo(
        host=host,
        title=str(data.get("title") or host),
        version=str(data.get("version") or ""),
        description=html_to_text(str(data.get("description") or "")),
        # Defaults are the Mastodon stock values, used only if a server omits
        # the field. Never assume them when the server did answer.
        max_characters=int(statuses.get("max_characters") or 500),
        max_media=int(statuses.get("max_media_attachments") or 4),
        max_poll_options=int(polls.get("max_options") or 0),
        poll_max_expiration_days=int(polls.get("max_expiration") or 0) // 86400,
        image_size_limit_mb=_mb(media.get("image_size_limit")),
        video_size_limit_mb=_mb(media.get("video_size_limit")),
        languages=tuple(str(lang) for lang in ((data or {}).get("languages") or [])),
        translation=bool(translation.get("enabled")),
        thumbnail=str(((data or {}).get("thumbnail") or {}).get("url") or ""),
        contact_email=str(contact.get("email") or ""),
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


def _mentions(text: str, terms: Iterable[str]) -> bool:
    """Whether any term appears in the text as a word prefix or a phrase.

    Rules are prose, so "automated" has to match "automat" and "content warning"
    has to match as a phrase. Word-prefix matching rather than plain substring
    keeps "ai" out of "said" and "chain".
    """
    lowered = (text or "").lower()
    words = set(_WORD_RE.findall(lowered))
    for term in terms:
        if " " in term or "-" in term:
            if term in lowered:
                return True
        elif term in words or any(w.startswith(term) for w in words):
            return True
    return False


def is_relevant(text: str) -> bool:
    """Whether a rule touches AI, automation, or commercial use."""
    return _mentions(text, _RELEVANCE_TERMS)


# Headings the Engage terms panel groups an instance's rules under, in priority
# order. Grouping only — every rule is shown verbatim under whichever heading it
# lands in, and anything unmatched falls through to the last one, so a
# mis-classified rule is a cosmetic problem and never a hidden one.
RULE_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "AI, bots and automation",
        (
            "ai", "a.i.", "generative", "llm", "machine-generated", "chatgpt",
            "bot", "automat", "scrap", "crawl", "index", "disclos", "attribut",
        ),
    ),
    (
        "Marketing and commercial use",
        (
            "advertis", "marketing", "promot", "spam", "seo", "commercial",
            "business", "corporate", "monetis", "monetiz", "solicit", "affiliate",
            "crypto", "nft",
        ),
    ),
    (
        "Harassment and hate",
        (
            "harass", "hate", "racis", "sexis", "transphob", "homophob", "ableis",
            "slur", "abuse", "doxx", "dox", "threat", "nazi", "fasci", "bigot",
            "brigad", "stalk",
        ),
    ),
    (
        "Sensitive content",
        (
            "nsfw", "sexual", "porn", "nudity", "violen", "gore", "graphic",
            "content warning", "cw", "spoiler", "sensitive", "trigger", "minor",
            "csam", "self-harm",
        ),
    ),
    (
        "Posting etiquette",
        (
            "alt text", "alt-text", "hashtag", "language", "unlisted", "boost",
            "reply", "thread", "mention", "tag", "caption", "accessib",
        ),
    ),
)

FALLBACK_TOPIC = "House rules"


def rule_topic(text: str) -> str:
    """The heading a rule belongs under. First match wins."""
    for topic, terms in RULE_TOPICS:
        if _mentions(text, terms):
            return topic
    return FALLBACK_TOPIC


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


def author_statuses_in_window(
    host: str,
    account_id: str,
    since: datetime,
    until: datetime,
    token: str = "",
    max_pages: int = 4,
) -> list[Status]:
    """One account's own posts inside a time window, newest first.

    THE POINT OF THIS IS DEPTH, WHICH A TIMELINE CANNOT GIVE. A public local timeline is
    read newest-first and is as deep as the instance is quiet: measured, 600 posts covers
    21 hours on hachyderm and 5 on mstdn.social. Anything that has to score *settled*
    posts — which means older than a day — never reaches them on a busy server, however
    many pages it reads. Asking each author for their own posts reaches back by
    construction, and gives the several-posts-per-author that a within-author statistic
    needs at the same time.

    Stops at the window edge rather than paging a fixed number of times, so a prolific
    account costs the same as a quiet one. `max_pages` is the ceiling for an account that
    posts constantly.

    Replies and boosts are excluded: a boost carries someone else's numbers, and a reply
    into a thread is not a post in the sense being measured.
    """
    host = normalise_host(host)
    out: list[Status] = []
    max_id = ""

    for _ in range(max_pages):
        params: dict[str, Any] = {
            "limit": PAGE_LIMIT,
            "exclude_replies": True,
            "exclude_reblogs": True,
        }
        if max_id:
            params["max_id"] = max_id
        try:
            page = _request(host, f"/api/v1/accounts/{quote(account_id)}/statuses", token, params)
        except MastodonError:
            # One unreadable account (suspended, moved, rate-limited) must not cost the
            # whole pass — the caller is sampling many.
            break
        if not page:
            break

        reached_past_window = False
        for raw in page:
            status = _parse_status(raw)
            if status is None or status.created_at is None:
                continue
            if status.created_at < since:
                reached_past_window = True
                continue
            if status.created_at > until:
                continue  # too recent to have settled
            out.append(status)

        max_id = str(page[-1].get("id") or "")
        if reached_past_window or not max_id:
            break
        time.sleep(POLITE_DELAY_SECONDS)

    return out


def account_statuses(
    host: str,
    token: str,
    limit: int = 40,
    exclude_replies: bool = True,
    exclude_reblogs: bool = True,
) -> tuple[Account | None, list[Status]]:
    """The token holder's own posts, newest first, with their engagement counts.

    Returns the account too, because engagement_rate is follower-normalised and the caller
    would otherwise need a second round trip for the follower count.

    Replies and boosts are excluded by default: this exists to answer "how did the things I
    wrote do", and a boost carries someone else's numbers while a reply into a thread is not
    a post in the sense being measured.
    """
    host = normalise_host(host)
    raw_account = _request(host, "/api/v1/accounts/verify_credentials", token)
    if not raw_account:
        return None, []
    account = _parse_account(raw_account)
    account_id = str(raw_account.get("id") or "")
    if not account_id:
        return account, []

    out: list[Status] = []
    max_id: str | None = None
    while len(out) < limit:
        params: dict[str, Any] = {
            "limit": min(PAGE_LIMIT, limit - len(out)),
            "exclude_replies": exclude_replies,
            "exclude_reblogs": exclude_reblogs,
        }
        if max_id:
            params["max_id"] = max_id
        page = _request(host, f"/api/v1/accounts/{account_id}/statuses", token, params) or []
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

    log.info("mastodon own statuses -> %d", len(out))
    return account, out[:limit]


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


# ---------------------------------------------------------------------------
# Hashtag data — trends and per-tag usage
#
# The one genuinely free, no-key, real-hashtag-semantics data source in the whole
# app. Both endpoints are public reads on most instances, so the Hashtag Suggester
# can use them without the user's token. The token is threaded through only so the
# Mastodon composer, which already holds one, can reach instances that gate trends
# behind auth — it is never required.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TagStats:
    """A hashtag with the usage numbers an instance publishes about it.

    `uses` / `accounts` are the totals over the returned history window (~7 days);
    `momentum` is recent-vs-earlier usage within that window (>1 rising, <1
    cooling, 0.0 when there is not enough history to say). `trending` marks a tag
    the instance itself lists on its trends surface right now.
    """

    name: str
    url: str
    uses: int
    accounts: int
    momentum: float
    days: int
    trending: bool = False


def _tag_history_stats(history: list[dict[str, Any]]) -> tuple[int, int, float, int]:
    """(total uses, total accounts, momentum, days) from a tag's daily history.

    Mastodon returns history newest-first as [{day, uses, accounts}, ...] with the
    numbers as strings. Momentum compares the most recent third of the window
    against the oldest third; the newest entry is a partial day, so a straight
    last-vs-first would read every tag as cooling in the morning.
    """
    days: list[tuple[int, int]] = []
    for entry in history or []:
        try:
            days.append((int(entry.get("uses") or 0), int(entry.get("accounts") or 0)))
        except (TypeError, ValueError):
            continue
    if not days:
        return 0, 0, 0.0, 0

    uses = sum(u for u, _ in days)
    accounts = sum(a for _, a in days)

    # History is newest-first; reverse to chronological so "recent" is the tail.
    chrono = [u for u, _ in reversed(days)]
    momentum = 0.0
    if len(chrono) >= 4:
        window = max(1, len(chrono) // 3)
        earlier = sum(chrono[:window]) / window
        recent = sum(chrono[-window:]) / window
        momentum = round(recent / earlier, 3) if earlier > 0 else (2.0 if recent > 0 else 0.0)
    return uses, accounts, momentum, len(days)


def _parse_tag(raw: dict, *, trending: bool = False) -> TagStats | None:
    name = str(raw.get("name") or "").strip().lstrip("#")
    if not name:
        return None
    uses, accounts, momentum, days = _tag_history_stats(raw.get("history") or [])
    return TagStats(
        name=name,
        url=str(raw.get("url") or ""),
        uses=uses,
        accounts=accounts,
        momentum=momentum,
        days=days,
        trending=trending,
    )


def trending_tags(host: str, limit: int = 20, token: str = "") -> list[TagStats]:
    """The hashtags the instance is surfacing as trending, with their usage.

    One request. `/api/v1/trends/tags` is public on a default Mastodon install;
    an instance that has disabled trends answers 404, which the caller treats as
    "this source is unavailable" rather than an error.
    """
    data = _request(
        normalise_host(host),
        "/api/v1/trends/tags",
        token,
        {"limit": min(40, max(1, limit))},
    ) or []
    out: list[TagStats] = []
    for raw in data:
        stats = _parse_tag(raw, trending=True)
        if stats:
            out.append(stats)
    log.info("mastodon trends -> %d tags from %s", len(out), host)
    return out


def trending_statuses(
    host: str, limit: int = 40, offset: int = 0, token: str = ""
) -> list[Status]:
    """Posts the instance is currently surfacing as trending.

    Measured on hachyderm.io: 197 unique statuses over 5 offset pages, of which
    **96% pass should_learn_from** — against 13.5% for a tag timeline. The gap is
    structural rather than lucky: trending posts come from established,
    discoverable accounts, whereas a tag timeline is dominated by accounts that
    set discoverable=false.

    The catch, and why this must never be the only source: it is a
    survivorship-biased sample. Median author followers on that same run was
    3,272. A corpus built only from here has no `low` tier at all, so quality
    conditioning would have nothing to contrast against and the model would just
    learn "write like a popular account".
    """
    data = _request(
        normalise_host(host),
        "/api/v1/trends/statuses",
        token,
        {"limit": min(40, max(1, limit)), "offset": max(0, offset)},
    ) or []
    out: list[Status] = []
    for raw in data:
        status = _parse_status(raw)
        if status:
            out.append(status)
    return out


def public_timeline(
    host: str,
    limit: int = 40,
    max_id: str = "",
    local: bool = False,
    token: str = "",
) -> list[Status]:
    """The federated (or local) public timeline — a broad, unranked sample.

    The counterweight to trending: ordinary posts by ordinary accounts, which is
    what the `mid` and `low` tiers need. Not served everywhere —
    mastodon.social answers 422 for unauthenticated reads of this endpoint while
    hachyderm serves it — so callers must treat a MastodonError here as "this
    source is unavailable on this instance", not as a failure.
    """
    params: dict[str, Any] = {"limit": min(PAGE_LIMIT, max(1, limit))}
    if not local:
        params["remote"] = "false"
    else:
        params["local"] = "true"
    if max_id:
        params["max_id"] = max_id

    data = _request(normalise_host(host), "/api/v1/timelines/public", token, params) or []
    out: list[Status] = []
    for raw in data:
        status = _parse_status(raw)
        if status:
            out.append(status)
    return out


def tag_info(host: str, tag: str, token: str = "") -> TagStats | None:
    """Usage history for one specific hashtag, or None if it is unknown/ungated.

    `/api/v1/tags/{tag}` is how a candidate that is NOT currently trending still
    gets real volume + momentum numbers attached. Some instances gate it behind a
    token; a MastodonError here is swallowed by the caller so a single gated tag
    never sinks the whole suggestion.
    """
    clean = _hashtag_of(tag)
    if not clean:
        return None
    raw = _request(normalise_host(host), f"/api/v1/tags/{quote(clean)}", token)
    return _parse_tag(raw or {}, trending=False)
