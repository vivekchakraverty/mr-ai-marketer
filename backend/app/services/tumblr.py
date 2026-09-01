"""Tumblr API v2 access for the Tumblr side of Engage.

Native to this app, the same shape as services/mastodon.py: vendor/socialpost is
the standalone Bluesky project and stays unmodified, so a third network's API work
lives here in the service layer.

Four things about Tumblr this file exists to get right:

  * **It is OAuth 1.0a, and the credentials are four values, not one.** Consumer
    key and secret identify the *application*; token and token secret identify the
    *user*. All four are needed to sign a single request, and every one of them is
    a secret. There is an OAuth2 flow too, but it needs a registered redirect URL
    and a browser round-trip; the four values here can be read straight off
    tumblr.com/oauth/apps and api.tumblr.com/console, which is the only path a
    desktop app can offer without running a callback server.

  * **A user is not a blog.** One Tumblr account owns several blogs, and almost
    every write is addressed to one of them by name. `/user/dashboard` is the
    account's; `/blog/{x}/notifications` is one blog's. So the credentials carry a
    blog identifier, and when the user has not chosen one, primary_blog() picks
    the account's primary.

  * **Posts come in two shapes.** Legacy typed posts (text/photo/quote/link/…)
    and Neue Post Format blocks. Every read here asks for `npf=true` so there is
    one shape to flatten, and `npf_text`/`npf_media` below understand only that
    one. Tumblr's own docs say to prefer NPF; the legacy shapes are still
    tolerated on the way in because `/tagged` ignores the flag for some posts.

  * **The rate limits are per-IP and per-key, not per-token.** 300 requests a
    minute per IP and 1000 an hour per consumer key. A feed load is a handful of
    requests, but the follow-suggestion sweep and the per-author relationship
    lookups can multiply, so both are bounded by their callers and everything
    retries a 429 rather than surfacing it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
from requests_oauthlib import OAuth1

# Tumblr blog descriptions, legacy post bodies and note excerpts are HTML. The
# flattener is protocol-agnostic — it turns block tags into line breaks and drops
# the rest — so it is imported rather than copied.
from .mastodon import html_to_text

# REQUEST_TIMEOUT below is a JSON call's budget. The multipart post is the one request
# whose body is large enough for the socket timeout to matter, and it is sized from the
# payload instead — see upload_budget.
from .upload_budget import upload_timeout

log = logging.getLogger(__name__)

API_ROOT = "https://api.tumblr.com/v2"

USER_AGENT = "MrAIMarketer/1.0 (+https://github.com/; Tumblr Engage)"

REQUEST_TIMEOUT = 25
MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 2.0

# Tumblr caps every paged read at 20 whatever you ask for.
PAGE_LIMIT = 20

# Courtesy pause between the requests of a sweep (suggestions, relationship
# lookups). Well inside 300/minute; the point is to not look like a scraper.
POLITE_DELAY_SECONDS = 0.2


class TumblrError(RuntimeError):
    """Tumblr said no. The message is written to be shown to a user."""


class RateLimited(TumblrError):
    """Retries exhausted against a 429."""


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Credentials:
    """The four OAuth1 values plus the blog to act as.

    `blog` is optional at this layer: the router fills it from the account's
    primary blog when the user has not named one, so nothing below has to care
    which of the two it got.
    """

    consumer_key: str
    consumer_secret: str
    token: str
    token_secret: str
    blog: str = ""

    @property
    def complete(self) -> bool:
        return all(
            v.strip() for v in (self.consumer_key, self.consumer_secret, self.token, self.token_secret)
        )

    @property
    def cache_key(self) -> str:
        """A stable id for this login that is not the login.

        Used as a dict key for the caches below, so a token never ends up in a
        log line or a repr through them.
        """
        digest = hashlib.sha256(f"{self.consumer_key}:{self.token}".encode("utf-8")).hexdigest()
        return digest[:32]


def credentials_from(values: Any) -> Credentials:
    """Build credentials from a request body, trimming every value."""
    return Credentials(
        consumer_key=(getattr(values, "consumerKey", "") or "").strip(),
        consumer_secret=(getattr(values, "consumerSecret", "") or "").strip(),
        token=(getattr(values, "oauthToken", "") or "").strip(),
        token_secret=(getattr(values, "oauthTokenSecret", "") or "").strip(),
        blog=normalise_blog(getattr(values, "blog", "") or ""),
    )


_HOSTNAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


def normalise_blog(raw: str) -> str:
    """Whatever the user typed -> a blog identifier Tumblr accepts.

    Accepts `myblog`, `myblog.tumblr.com`, `https://myblog.tumblr.com/`, and a
    custom domain. A bare name gains `.tumblr.com` because the identifier is
    interpolated into a path and the hostname form is the one Tumblr documents;
    anything already carrying a dot is left alone, since that is either the
    hostname form or a custom domain and guessing between them would break the
    latter.
    """
    value = (raw or "").strip().lower()
    if not value:
        return ""
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0].strip().rstrip(".")
    if not value:
        return ""
    if "." not in value:
        value = f"{value}.tumblr.com"
    if not _HOSTNAME_RE.match(value):
        raise TumblrError(
            f"{raw!r} does not look like a Tumblr blog. Use the short name (myblog) or the "
            "full address (myblog.tumblr.com)."
        )
    return value


def blog_path(identifier: str) -> str:
    """A blog identifier, safe to interpolate into an API path."""
    cleaned = normalise_blog(identifier)
    if not cleaned:
        raise TumblrError("This needs a Tumblr blog name.")
    return quote(cleaned, safe="")


def blog_url(identifier: str) -> str:
    """The public web address of a blog — what /user/follow wants."""
    return f"https://{normalise_blog(identifier)}"


def short_name(identifier: str) -> str:
    """`myblog.tumblr.com` -> `myblog`; a custom domain is returned unchanged."""
    cleaned = normalise_blog(identifier)
    return cleaned[: -len(".tumblr.com")] if cleaned.endswith(".tumblr.com") else cleaned


def avatar_url(identifier: str, size: int = 96) -> str:
    """A blog's avatar.

    The avatar route needs no key and no signature — it redirects to the image —
    so this can be handed straight to an <img> without proxying anything.
    """
    try:
        return f"{API_ROOT}/blog/{blog_path(identifier)}/avatar/{size}"
    except TumblrError:
        return ""


def post_url(blog_name: str, post_id: str) -> str:
    if not blog_name or not post_id:
        return ""
    return f"https://{normalise_blog(blog_name)}/post/{post_id}"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _auth(creds: Credentials) -> OAuth1:
    if not creds.complete:
        raise TumblrError(
            "Tumblr needs all four OAuth values — consumer key, consumer secret, token and "
            "token secret. Add them in Settings."
        )
    return OAuth1(
        creds.consumer_key,
        client_secret=creds.consumer_secret,
        resource_owner_key=creds.token,
        resource_owner_secret=creds.token_secret,
        # Tumblr accepts the signature in the Authorization header, which keeps
        # every OAuth value out of the URL and therefore out of any log.
        signature_type="AUTH_HEADER",
    )


def _detail(payload: Any, status: int) -> str:
    """The most useful sentence Tumblr gave us about a failure."""
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                text = str(first.get("detail") or first.get("title") or "").strip()
                if text:
                    return text
        meta = payload.get("meta")
        if isinstance(meta, dict):
            text = str(meta.get("msg") or "").strip()
            if text:
                return text
    return f"HTTP {status}"


def _friendly(status: int, detail: str) -> str:
    if status in (401, 403):
        return (
            f"Tumblr refused these credentials ({detail}). Check all four OAuth values in "
            "Settings, and that the application they came from has write access."
        )
    if status == 404:
        return f"Tumblr could not find that ({detail})."
    return f"Tumblr said: {detail}"


def request(
    creds: Credentials,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    """One signed call to the Tumblr API, returning its `response` payload.

    Every Tumblr response is `{"meta": {...}, "response": ..., "errors": [...]}`,
    so the envelope is unwrapped here and callers only ever see the part they
    asked for. A 429 is retried with backoff; anything else that failed is raised
    as a TumblrError carrying a sentence written for the user.
    """
    url = f"{API_ROOT}{path}"
    auth = _auth(creds)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    last_error = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(
                method.upper(),
                url,
                auth=auth,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as err:
            last_error = str(err)
            if attempt == MAX_RETRIES - 1:
                raise TumblrError(f"Could not reach Tumblr: {last_error}") from None
            time.sleep(BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.4))
            continue

        if resp.status_code == 429:
            if attempt == MAX_RETRIES - 1:
                raise RateLimited(
                    "Tumblr is rate-limiting this app (300 requests a minute, 1000 an hour). "
                    "Give it a minute and try again."
                )
            # Tumblr sends no Retry-After on the v2 API, so this is exponential
            # backoff with jitter rather than an honoured hint.
            time.sleep(BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.8))
            continue

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if resp.ok:
            if isinstance(payload, dict) and "response" in payload:
                return payload["response"]
            return payload

        raise TumblrError(_friendly(resp.status_code, _detail(payload, resp.status_code)))

    raise TumblrError(f"Could not reach Tumblr: {last_error or 'no response'}")


def get(creds: Credentials, path: str, **params: Any) -> Any:
    clean = {k: v for k, v in params.items() if v not in ("", None)}
    return request(creds, "GET", path, params=clean)


def post_form(creds: Credentials, path: str, **fields: Any) -> Any:
    clean = {k: v for k, v in fields.items() if v not in ("", None)}
    return request(creds, "POST", path, data=clean)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


# /user/info answers the same thing for the life of a token, and every feed load
# needs it only to know which blogs are yours. Cached briefly so a refresh is one
# request rather than two.
_USER_TTL_SECONDS = 300
_user_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def user_info(creds: Credentials, *, refresh: bool = False) -> dict:
    """The account behind these credentials: name, blogs, following count."""
    key = creds.cache_key
    now = time.monotonic()
    if not refresh:
        with _cache_lock:
            hit = _user_cache.get(key)
        if hit and now - hit[0] < _USER_TTL_SECONDS:
            return hit[1]

    payload = get(creds, "/user/info") or {}
    user = payload.get("user") if isinstance(payload, dict) else None
    user = user if isinstance(user, dict) else {}
    with _cache_lock:
        _user_cache[key] = (now, user)
    return user


def own_blogs(user: dict) -> list[dict]:
    blogs = user.get("blogs")
    return [b for b in blogs if isinstance(b, dict)] if isinstance(blogs, list) else []


def primary_blog(user: dict) -> str:
    """The account's primary blog, normalised — the default when none is set."""
    blogs = own_blogs(user)
    chosen = next((b for b in blogs if b.get("primary")), blogs[0] if blogs else None)
    if not chosen:
        return ""
    name = str(chosen.get("name") or "")
    try:
        return normalise_blog(name)
    except TumblrError:
        return ""


def resolve_blog(creds: Credentials) -> Credentials:
    """Credentials guaranteed to name a blog.

    Called by every endpoint that addresses one, so "which blog?" is answered
    once instead of at each call site — and so a user who never typed a blog name
    still gets a working screen.
    """
    if creds.blog:
        return creds
    chosen = primary_blog(user_info(creds))
    if not chosen:
        raise TumblrError(
            "This Tumblr account has no blog we can post as. Add the blog name in Settings."
        )
    return Credentials(
        consumer_key=creds.consumer_key,
        consumer_secret=creds.consumer_secret,
        token=creds.token,
        token_secret=creds.token_secret,
        blog=chosen,
    )


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


# Tumblr has no batch relationship endpoint — /blog/{x}/info is one request per
# blog and is the only place `followed` and `is_blocked_from_primary` appear. A
# feed page has at most PAGE_LIMIT distinct authors, so a page costs at most that
# many extra requests the first time and none on a refresh within the TTL.
_INFO_TTL_SECONDS = 300
_info_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def blog_info(creds: Credentials, identifier: str, *, refresh: bool = False) -> dict:
    """Public info about a blog, plus your relationship with it.

    `followed` and `is_blocked_from_primary` are only present because the request
    is signed — the same route with an api_key omits them.
    """
    cleaned = normalise_blog(identifier)
    if not cleaned:
        return {}
    key = (creds.cache_key, cleaned)
    now = time.monotonic()
    if not refresh:
        with _cache_lock:
            hit = _info_cache.get(key)
        if hit and now - hit[0] < _INFO_TTL_SECONDS:
            return hit[1]

    payload = get(creds, f"/blog/{blog_path(cleaned)}/info") or {}
    info = payload.get("blog") if isinstance(payload, dict) else None
    info = info if isinstance(info, dict) else {}
    with _cache_lock:
        _info_cache[key] = (now, info)
    return info


def create_npf_post_with_media(
    creds: Credentials,
    blog: str,
    payload: dict[str, Any],
    filename: str,
    content: bytes,
    identifier: str = "attached-image",
) -> Any:
    """Create an NPF post carrying one uploaded image or video.

    Tumblr takes media inline rather than through a separate upload endpoint like
    Mastodon's: the request is multipart, one part holds the NPF JSON, another holds the
    bytes, and an image block points at that part by `identifier`. So this cannot go
    through `request()`, which sends JSON — hence a function of its own rather than a
    files= branch complicating the shared transport for one caller.

    The caller supplies the whole NPF payload including the image block, because only it
    knows where the picture belongs among the text blocks.

    Not retried. `request()` retries on 429 because its calls are idempotent or cheap to
    repeat; repeating this one publishes a second post.

    The timeout is sized from the payload rather than shared with `request()`. Tumblr takes
    video here, and video_attach lets it up to 100MB — which a 25-second budget could only
    write over a sustained 34Mbit/s uplink. The same mistake on Mastodon failed naming TLS
    rather than the clock. See upload_budget.
    """
    url = f"{API_ROOT}/blog/{blog_path(blog)}/posts"
    # The JSON part must be typed, or Tumblr parses it as a plain string and rejects the
    # whole body with a generic error that says nothing about which part was wrong.
    files = {
        "json": (None, json.dumps(payload), "application/json"),
        identifier: (filename, content),
    }
    budget = upload_timeout(len(content))
    megabytes = len(content) / (1024 * 1024)
    try:
        resp = requests.post(
            url,
            auth=_auth(creds),
            files=files,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=budget,
        )
    except requests.RequestException as err:
        # The size and the budget are the two numbers that say whether a repeat of this is
        # the uplink or us; "Could not reach Tumblr" said neither.
        raise TumblrError(
            f"Could not reach Tumblr to upload {filename} "
            f"({megabytes:.1f}MB, gave up after {budget:.0f}s): {err}"
        ) from None

    try:
        body = resp.json()
    except ValueError:
        raise TumblrError(_friendly(resp.status_code, resp.text[:200])) from None
    if resp.status_code not in (200, 201):
        raise TumblrError(_friendly(resp.status_code, _detail(body, resp.status_code)))
    return body.get("response") if isinstance(body, dict) else body


def follower_count(creds: Credentials, identifier: str) -> int | None:
    """Exact follower total for a blog the authenticated account owns, else None.

    Tumblr answers this route with HTTP 403 for any blog you do not control, which is
    the whole reason the corpus collector had to invent `audience_proxy_notes` (the
    median note count of a blog's recent originals) as a stand-in. For the user's *own*
    posts there is no need to estimate, so the Post Creator scores them against this.

    `limit=1` because only the total is wanted; the page of followers is discarded.
    None means "no honest number available", never zero — a zero would read as a blog
    nobody follows and would send its engagement rate to infinity.
    """
    cleaned = normalise_blog(identifier)
    if not cleaned:
        return None
    try:
        payload = get(creds, f"/blog/{blog_path(cleaned)}/followers", limit=1) or {}
    except TumblrError:
        # 403 for a blog you don't own is the expected case, not an error worth raising.
        return None
    total = payload.get("total_users") if isinstance(payload, dict) else None
    return int(total) if isinstance(total, (int, float)) else None


def forget_blog(creds: Credentials, identifier: str) -> None:
    """Drop a cached relationship after acting on it.

    Without this, following someone would leave the button reading "Follow" for
    up to five minutes, because the next feed load would answer from the copy
    taken before the change.
    """
    try:
        cleaned = normalise_blog(identifier)
    except TumblrError:
        return
    with _cache_lock:
        _info_cache.pop((creds.cache_key, cleaned), None)


def relationships(creds: Credentials, names: list[str]) -> dict[str, dict]:
    """Relationship state for a page's worth of blogs, best effort.

    Sequential and polite rather than parallel: Tumblr's limit is per-IP, so
    firing twenty signed requests at once is the reliable way to meet a 429 on a
    screen that could just take a moment longer. A blog whose lookup fails is
    simply absent from the result — a feed that renders with an unknown follow
    state is better than a feed that does not render.
    """
    out: dict[str, dict] = {}
    seen: list[str] = []
    for raw in names:
        try:
            cleaned = normalise_blog(raw)
        except TumblrError:
            continue
        if cleaned and cleaned not in seen:
            seen.append(cleaned)

    for i, name in enumerate(seen[:PAGE_LIMIT]):
        try:
            cached = _info_cache.get((creds.cache_key, name))
            if not (cached and time.monotonic() - cached[0] < _INFO_TTL_SECONDS) and i:
                time.sleep(POLITE_DELAY_SECONDS)
            out[name] = blog_info(creds, name)
        except TumblrError as err:
            log.debug("relationship lookup failed for %s: %s", name, err)
    return out


# ---------------------------------------------------------------------------
# Neue Post Format
# ---------------------------------------------------------------------------


def npf_text(post: dict) -> str:
    """The readable text of an NPF post, or of a legacy one as a fallback.

    Only the post's *own* content blocks are read, never its `trail`: the trail is
    everybody else's writing on the way down a reblog chain, and pulling it in
    would put someone else's words under this author's name.
    """
    content = post.get("content")
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "").strip()
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n\n".join(p for p in parts if p)
        if text:
            return text

    # Legacy shapes, in the order they carry a body.
    for field in ("body", "text", "caption", "description", "summary"):
        value = post.get(field)
        if isinstance(value, str) and value.strip():
            return html_to_text(value)
    quote_text = post.get("quote") or post.get("source")
    if isinstance(quote_text, str) and quote_text.strip():
        return html_to_text(quote_text)
    return ""


def _largest(media: Any) -> dict:
    """The biggest variant in an NPF media array."""
    if isinstance(media, dict):
        return media
    if not isinstance(media, list) or not media:
        return {}
    sized = [m for m in media if isinstance(m, dict)]
    if not sized:
        return {}
    return max(sized, key=lambda m: int(m.get("width") or 0) * int(m.get("height") or 0))


def _smallest(media: Any) -> dict:
    if isinstance(media, dict):
        return media
    sized = [m for m in media if isinstance(m, dict)] if isinstance(media, list) else []
    if not sized:
        return {}
    return min(sized, key=lambda m: int(m.get("width") or 0) * int(m.get("height") or 0) or 10**9)


def _ratio(item: dict) -> float | None:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    return round(width / height, 4) if width > 0 and height > 0 else None


def npf_media(post: dict) -> list[dict]:
    """NPF content blocks -> the media shape both other Engage panels already emit.

    Field names match routers/engage.py::MediaOut and mastodon_engage.py's, so the
    frontend renders a Tumblr post through the same PostMedia component with no
    per-network adapter. Tumblr serves video as a direct file, so nothing here is
    HLS.
    """
    content = post.get("content")
    out: list[dict] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")

        if kind == "image":
            full = _largest(block.get("media"))
            thumb = _smallest(block.get("media"))
            url = str(full.get("url") or "")
            if not url:
                continue
            out.append(
                {
                    "kind": "image",
                    "url": url,
                    "previewUrl": str(thumb.get("url") or url),
                    "description": str(block.get("alt_text") or block.get("caption") or ""),
                    "aspectRatio": _ratio(full),
                }
            )
        elif kind == "video":
            media = _largest(block.get("media"))
            url = str(media.get("url") or block.get("url") or "")
            poster = _largest(block.get("poster"))
            if not url:
                continue
            out.append(
                {
                    "kind": "video",
                    "url": url,
                    "previewUrl": str(poster.get("url") or ""),
                    "description": str(block.get("alt_text") or ""),
                    "aspectRatio": _ratio(media) or _ratio(poster),
                }
            )
        elif kind == "audio":
            url = str(block.get("url") or _largest(block.get("media")).get("url") or "")
            if not url:
                continue
            out.append(
                {
                    "kind": "audio",
                    "url": url,
                    "previewUrl": str(_largest(block.get("poster")).get("url") or ""),
                    "description": str(block.get("title") or block.get("artist") or ""),
                }
            )
        elif kind == "link":
            url = str(block.get("url") or "")
            if not url:
                continue
            domain = re.sub(r"^https?://(www\.)?", "", url).split("/", 1)[0]
            out.append(
                {
                    "kind": "link",
                    "url": url,
                    "previewUrl": str(_largest(block.get("poster")).get("url") or ""),
                    "title": str(block.get("title") or ""),
                    "description": str(block.get("description") or ""),
                    "domain": str(block.get("site_name") or domain),
                }
            )

    # A legacy photo post that /tagged returned without NPF still has photos.
    if not out and isinstance(post.get("photos"), list):
        for photo in post["photos"]:
            if not isinstance(photo, dict):
                continue
            original = photo.get("original_size") or {}
            url = str(original.get("url") or "")
            if not url:
                continue
            small = (photo.get("alt_sizes") or [{}])[-1] if isinstance(photo.get("alt_sizes"), list) else {}
            out.append(
                {
                    "kind": "image",
                    "url": url,
                    "previewUrl": str(small.get("url") or url),
                    "description": str(photo.get("caption") or ""),
                    "aspectRatio": _ratio(original),
                }
            )
    return out
