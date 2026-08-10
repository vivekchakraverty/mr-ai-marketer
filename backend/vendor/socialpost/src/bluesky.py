"""Bluesky (AT Protocol) access: auth, keyword search, post/author fetch.

Deliberately polling-only. There is no firehose or Jetstream consumer here and
there must not be one: those need a persistent process, and this system runs on
scheduled GitHub Actions.

Rate limits (as published by Bluesky): the PDS allows 3000 requests / 5 min per
IP, and createSession is limited to 30 / 5 min per account. A scheduled ingest of
a handful of niches is nowhere near either, so the backoff below exists to
survive transient 429/5xx rather than to pace normal operation.
"""

from __future__ import annotations

import functools
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from atproto import Client
from atproto_client.exceptions import (
    AtProtocolError,
    InvokeTimeoutError,
    ModelError,
    NetworkError,
    RequestErrorBase,
)

from .db import require_env

log = logging.getLogger(__name__)

# Bluesky caps searchPosts and getPosts pages at 100 and 25 respectively.
SEARCH_PAGE_LIMIT = 100
GET_POSTS_CHUNK = 25
GET_PROFILES_CHUNK = 25

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0

# Profile label meaning "do not show me to logged-out users". We treat it as the
# detectable form of "this account does not want to be indexed" and skip the
# author entirely. See ingest.py and the data-ethics section of the README.
NO_INDEX_LABELS = {"!no-unauthenticated", "no-unauthenticated"}

_HASHTAG_RE = re.compile(r"#(\w{1,64})")


class RateLimited(Exception):
    """Raised when retries are exhausted against a 429."""


@dataclass(frozen=True)
class BskyPost:
    """Flattened PostView — only the fields this system stores."""

    uri: str
    author_did: str
    author_handle: str
    text: str
    hashtags: list[str]
    has_media: bool
    created_at: datetime | None
    likes: int
    reposts: int
    replies: int


@dataclass(frozen=True)
class BskyAuthor:
    did: str
    handle: str
    follower_count: int
    no_index: bool


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_client() -> Client:
    """Authenticated client, cached per process.

    createSession is the most rate-limited endpoint on the PDS (30 per 5 min per
    account), so a job must log in exactly once. The lru_cache enforces that.
    """
    handle = require_env("BLUESKY_HANDLE")
    password = require_env("BLUESKY_APP_PASSWORD")
    if not password.count("-") >= 3:
        # App passwords look like xxxx-xxxx-xxxx-xxxx. Catching this here beats a
        # confusing 401, and nudges people away from using their real password.
        log.warning(
            "BLUESKY_APP_PASSWORD does not look like an app password. Generate one "
            "at Settings -> Privacy and Security -> App Passwords."
        )
    client = Client()
    client.login(handle, password)
    log.info("Authenticated to Bluesky as %s", handle)
    return client


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def _status_of(err: Exception) -> int | None:
    """Best-effort HTTP status from an atproto error."""
    response = getattr(err, "response", None)
    return getattr(response, "status_code", None)


def with_backoff(fn, *args: Any, **kwargs: Any) -> Any:
    """Call `fn`, retrying 429 and 5xx with exponential backoff + jitter.

    Honours Retry-After when the server sends it. 4xx other than 429 are not
    retried — they mean the request itself is wrong and repeating it just burns
    the budget. ModelError is likewise not retried: it means the response did not
    match the SDK's lexicon, which no amount of retrying will change.
    """
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except ModelError:
            # Permanent: the server's payload disagrees with this SDK version.
            raise
        except RequestErrorBase as err:
            status = _status_of(err)
            retryable = status == 429 or (status is not None and status >= 500)
            if not retryable or attempt == MAX_RETRIES - 1:
                if status == 429:
                    raise RateLimited(
                        f"Rate limited by Bluesky after {MAX_RETRIES} attempts."
                    ) from err
                raise

            delay = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1)
            headers = getattr(getattr(err, "response", None), "headers", None) or {}
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            log.warning(
                "Bluesky returned %s; retrying in %.1fs (attempt %d/%d)",
                status,
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
        except (NetworkError, InvokeTimeoutError) as err:
            # Transport-level failures, which carry no response object.
            if attempt == MAX_RETRIES - 1:
                raise
            delay = BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1)
            log.warning(
                "Bluesky call failed (%s); retrying in %.1fs",
                type(err).__name__,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_created_at(value: str | None) -> datetime | None:
    """Parse a record's createdAt.

    These are self-reported by the posting client and are occasionally garbage
    (missing zone, year 1970, year 2100). We only normalise the format here;
    ingest.py rejects implausible values.
    """
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        log.debug("Unparseable createdAt %r", value)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_hashtags(record: Any, text: str) -> list[str]:
    """Hashtags from richtext facets, falling back to a regex over the text.

    Facets are authoritative (that is what the client actually linked), but not
    every client writes tag facets, so the regex catches the rest.
    """
    tags: list[str] = []
    facets = getattr(record, "facets", None) or []
    for facet in facets:
        for feature in getattr(facet, "features", None) or []:
            tag = getattr(feature, "tag", None)
            if tag:
                tags.append(str(tag).lstrip("#"))
    if not tags:
        tags = _HASHTAG_RE.findall(text or "")
    # Case-insensitive dedupe, preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            out.append(tag)
    return out


# Embed py_types that mean "this post renders with visual media attached".
# 'external' is excluded: a link card is not media the author produced, and it
# behaves differently from images in the algorithm.
# 'gallery' was added by Bluesky after this project was first written — see the
# atproto pin in requirements.txt.
MEDIA_EMBED_MARKERS = ("images", "video", "gallery", "recordWithMedia")


def _has_media(post: Any) -> bool:
    """True if the post carries an image, video, or gallery embed.

    A quote-post of something with media counts, since the media renders in the
    timeline and plausibly drives the engagement we are measuring.
    """
    embed = getattr(post, "embed", None)
    if embed is None:
        return False
    py_type = getattr(embed, "py_type", "") or ""
    return any(marker in py_type for marker in MEDIA_EMBED_MARKERS)


def _post_view_to_post(post: Any) -> BskyPost | None:
    """Flatten a PostView, or None if it is not a usable text post."""
    record = getattr(post, "record", None)
    if record is None:
        return None
    text = (getattr(record, "text", "") or "").strip()
    if not text:
        return None  # media-only post: nothing for the model to learn style from

    author = post.author
    return BskyPost(
        uri=post.uri,
        author_did=author.did,
        author_handle=author.handle,
        text=text,
        hashtags=_extract_hashtags(record, text),
        has_media=_has_media(post),
        created_at=_parse_created_at(getattr(record, "created_at", None)),
        likes=post.like_count or 0,
        reposts=post.repost_count or 0,
        replies=post.reply_count or 0,
    )


def _profile_no_index(profile: Any) -> bool:
    """True if the profile carries a no-index / logged-out-hidden label."""
    for label in getattr(profile, "labels", None) or []:
        if (getattr(label, "val", "") or "").lower() in NO_INDEX_LABELS:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def as_phrase_query(keyword: str) -> str:
    """Quote a multi-word keyword so Bluesky matches it as a phrase.

    Bluesky's search treats an unquoted multi-word query as loose tokens, which
    over-matches badly. Measured against the live API, 50 results each:

        shipped it              6% contained the phrase
        "shipped it"           86%
        building in public     12%
        "building in public"   90%

    Unquoted, the keyword "shipped it" pulled in a food-safety recall (lettuce
    "shipped" across the US) and filled a niche corpus with it. Since every
    downstream stage — exemplars, baselines, topic clusters — trusts that a post
    in a niche is *about* that niche, the noise propagates everywhere.

    Left alone: single words (nothing to phrase), and anything already using
    search syntax — existing quotes, or operators like `from:` and `since:` —
    since wrapping those would break the query the caller deliberately wrote.
    """
    k = keyword.strip()
    if " " not in k or '"' in k or re.search(r"\b\w+:", k):
        return k
    return f'"{k}"'


def search_posts(
    keyword: str,
    limit: int = 100,
    sort: str = "latest",
    lang: str | None = "en",
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[BskyPost]:
    """Search posts by keyword, paging until `limit` results.

    sort='latest' is intentional for ingest: 'top' biases toward already-viral
    posts, which would make the exemplar pool a rich-get-richer loop. We want a
    representative sample and let refresh_exemplars.py do the ranking against
    follower-normalised engagement.

    `until` bounds the *newest* result, and is what makes a cold-start backfill
    possible. sort='latest' returns the most recent matches, so on an active
    keyword every result is hours old — raising a max-age ceiling only permits
    older posts through the filter, it never asks for them. Passing until=now-50h
    is what actually reaches the 50h-7d window that snapshot.py's --backfill-48h
    works on. Without it a brand-new niche cannot have exemplars until its own
    posts age past 48h, however high the ceiling is set.
    """
    client = get_client()
    out: list[BskyPost] = []
    cursor: str | None = None

    while len(out) < limit:
        params: dict[str, Any] = {
            "q": as_phrase_query(keyword),
            "limit": min(SEARCH_PAGE_LIMIT, limit - len(out)),
            "sort": sort,
        }
        if lang:
            params["lang"] = lang
        if cursor:
            params["cursor"] = cursor
        if since:
            params["since"] = since.astimezone(timezone.utc).isoformat()
        if until:
            params["until"] = until.astimezone(timezone.utc).isoformat()

        try:
            resp = with_backoff(client.app.bsky.feed.search_posts, params)
        except ModelError as err:
            # Bluesky returned a lexicon type this SDK version cannot decode, so
            # the entire page is lost. Give up on this keyword rather than the
            # niche, and say plainly what fixes it — this recurs whenever Bluesky
            # ships a new embed/record type.
            log.error(
                "Unknown lexicon type in results for %r; skipping the rest of this "
                "keyword. Upgrade the atproto pin in requirements.txt. Detail: %s",
                keyword,
                str(err).split("\n")[0],
            )
            break
        page = resp.posts or []
        for view in page:
            post = _post_view_to_post(view)
            if post:
                out.append(post)

        cursor = resp.cursor
        if not cursor or not page:
            break

    log.info("search %r -> %d posts", keyword, len(out))
    return out[:limit]


def get_author_feed(
    actor: str,
    limit: int = 50,
    include_replies: bool = False,
) -> list[BskyPost]:
    """Every recent post by one account, newest first.

    The counterpart to search_posts(). Search returns whatever Bluesky's index
    surfaces for a keyword — a sample, and a biased one. An author feed is a
    census of somebody who reliably performs in a niche, which is what the
    exemplar pool actually wants to learn from.

    `actor` may be a handle or a DID; DIDs are safer for anything stored, since
    handles change. Replies are excluded by default: they are conversational
    fragments that read as noise in a style corpus.
    """
    client = get_client()
    out: list[BskyPost] = []
    cursor: str | None = None

    while len(out) < limit:
        params: dict[str, Any] = {
            "actor": actor,
            "limit": min(SEARCH_PAGE_LIMIT, limit - len(out)),
            "filter": "posts_with_replies" if include_replies else "posts_no_replies",
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = with_backoff(client.app.bsky.feed.get_author_feed, params)
        except ModelError as err:
            log.error(
                "Unknown lexicon type in %r's feed; skipping the rest. Upgrade the "
                "atproto pin in requirements.txt. Detail: %s",
                actor,
                str(err).split("\n")[0],
            )
            break
        except AtProtocolError:
            # A deactivated, suspended or renamed account should cost this one
            # author, not the whole ingest run.
            log.warning("Could not read the feed for %r; skipping", actor)
            break

        page = resp.feed or []
        for item in page:
            # Reposts surface here with the reposter's feed but the original
            # author's post. Keeping them would credit the wrong account and
            # double-count the post, so take only what this actor wrote.
            if getattr(item, "reason", None) is not None:
                continue
            post = _post_view_to_post(item.post)
            if post:
                out.append(post)

        cursor = resp.cursor
        if not cursor or not page:
            break

    log.info("author feed %r -> %d posts", actor, len(out))
    return out[:limit]


def get_profiles(dids: Iterable[str]) -> dict[str, BskyAuthor]:
    """Fetch profiles (for follower counts + no-index labels), keyed by DID.

    getProfiles takes at most 25 actors per call.
    """
    did_list = list(dict.fromkeys(dids))  # dedupe, preserve order
    client = get_client()
    out: dict[str, BskyAuthor] = {}

    for i in range(0, len(did_list), GET_PROFILES_CHUNK):
        chunk = did_list[i : i + GET_PROFILES_CHUNK]
        try:
            resp = with_backoff(client.app.bsky.actor.get_profiles, {"actors": chunk})
        except AtProtocolError:
            # A single deactivated/deleted account in the chunk fails the whole
            # call. Losing 25 follower counts is not worth failing the job.
            log.warning("getProfiles failed for a chunk of %d; skipping", len(chunk))
            continue
        for profile in resp.profiles or []:
            out[profile.did] = BskyAuthor(
                did=profile.did,
                handle=profile.handle,
                follower_count=profile.followers_count or 0,
                no_index=_profile_no_index(profile),
            )
    return out


def get_posts(uris: Sequence[str]) -> dict[str, BskyPost]:
    """Re-fetch posts by URI for current engagement counts, keyed by URI.

    Deleted posts simply do not come back — the caller sees a missing key, which
    snapshot.py treats as "no snapshot for this bucket" rather than zeroes.
    """
    client = get_client()
    out: dict[str, BskyPost] = {}

    for i in range(0, len(uris), GET_POSTS_CHUNK):
        chunk = list(uris[i : i + GET_POSTS_CHUNK])
        try:
            resp = with_backoff(client.app.bsky.feed.get_posts, {"uris": chunk})
        except AtProtocolError:
            log.warning("getPosts failed for a chunk of %d; skipping", len(chunk))
            continue
        for view in resp.posts or []:
            post = _post_view_to_post(view)
            if post:
                out[post.uri] = post
    return out
