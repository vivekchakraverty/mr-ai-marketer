"""Engage, Mastodon side — acting as yourself on your own instance.

Sibling of routers/engage.py (Bluesky), and deliberately not merged with it: the
two protocols disagree about almost everything a feed screen touches. Bluesky has
one host, one identity namespace and a viewer state embedded in every post view;
Mastodon has a host per community, ids that only mean something on the server that
issued them, and a viewer state split between the status (favourited, bookmarked)
and a separate relationship lookup (following, muting, blocking).

Three things shape this file:

1. **The gate applies here too.** services/mastodon_gate.py is the single
   enforcement point, and every action another person or server can observe —
   posting, replying, boosting, favouriting, following, blocking — goes through
   it. Reads do not, and neither does your own notification read-marker, because
   nobody but you can see it. The Post Creator gates generation for the same
   reason: on the fediverse, "what am I allowed to do here" is a per-server
   question with a published answer, and the app has no business guessing.

2. **Credentials never travel in a URL.** Every endpoint is a POST carrying the
   instance and access token in the body, including the ones that only read. The
   backend binds to localhost, but a token in a query string still lands in access
   logs and anything that mirrors them, and a body costs nothing to use instead.

3. **Actions are dispatched through allow-lists.** Mastodon's API is uniform
   (POST /statuses/:id/:verb), so one endpoint per noun with a checked verb is less
   code and less surface than a dozen near-identical handlers — but the verb is
   interpolated into a path, so only a name in the table can ever get there.
"""

from __future__ import annotations

import hashlib
import re
import logging
import time
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException

from ..services.genqueue import queue_slot
from pydantic import BaseModel

from ..services import image_prompt
from ..services import mastodon as masto
from ..services import mastodon_gate as gate
from ..services.mastodon import MastodonError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mastodon-engage", tags=["mastodon-engage"])

# Mastodon caps a timeline page at 40 whatever you ask for.
MAX_PAGE = 40

VISIBILITIES = ("public", "unlisted", "private", "direct")

# Only a verb in these tables can be interpolated into an API path. See the module
# docstring — the client names the action, so the client must not be able to
# invent one.
_STATUS_ACTIONS = (
    "favourite",
    "unfavourite",
    "reblog",
    "unreblog",
    "bookmark",
    "unbookmark",
    "mute",
    "unmute",
    "pin",
    "unpin",
)
_ACCOUNT_ACTIONS = ("follow", "unfollow", "mute", "unmute", "block", "unblock")
_TAG_ACTIONS = ("follow", "unfollow")

# verify_credentials answers the same thing for the life of a token, and a feed
# load needs it only to mark which posts are yours. Cached briefly so refreshing a
# timeline is one request against someone else's server instead of two.
_ME_TTL_SECONDS = 300
_me_cache: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class EngageRequest(BaseModel):
    """Base for every call: which server, and whose token."""

    instance: str
    accessToken: str = ""


class FeedRequest(EngageRequest):
    feed: str = "home"  # home | local | public | tag | bookmarks | favourites
    tag: str = ""
    limit: int = 30
    maxId: str = ""


class ThreadRequest(EngageRequest):
    statusId: str


class ComposeRequest(EngageRequest):
    text: str
    visibility: str = "public"
    spoilerText: str = ""
    language: str = ""
    inReplyToId: str = ""
    sensitive: bool = False
    #: A /outputs URL for an image this app generated. Empty posts text only.
    imageUrl: str = ""
    #: Alt text. Much of the fediverse treats missing alt text as rude, and several
    #: instances' rules ask for it outright, so it is a first-class field here.
    imageAlt: str = ""
    # Sent by the composer, stable across retries of the same draft, so a
    # double-submit or a retried timeout cannot publish the same post twice.
    idempotencyKey: str = ""


class StatusActionRequest(EngageRequest):
    statusId: str
    action: str
    visibility: str = ""  # boosts only


class AccountActionRequest(EngageRequest):
    accountId: str
    action: str


class TagActionRequest(EngageRequest):
    tag: str
    action: str


class DeleteRequest(EngageRequest):
    statusId: str


class MarkReadRequest(EngageRequest):
    lastReadId: str


class SuggestedFollowsRequest(EngageRequest):
    niche: str = ""
    # A subject typed in rather than a saved niche. Takes over completely when present.
    query: str = ""
    limit: int = 20


class SearchRequest(EngageRequest):
    query: str
    limit: int = 10


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AccountOut(BaseModel):
    id: str
    acct: str
    displayName: str
    url: str
    avatar: str = ""
    bot: bool = False
    followers: int = 0
    note: str = ""


class SuggestedAccountOut(BaseModel):
    account: AccountOut
    reason: str
    matched: list[str]
    posts: int
    bioMatch: bool


class SuggestedFollowsOut(BaseModel):
    niche: str
    keywords: list[str]
    accounts: list[SuggestedAccountOut]
    note: str = ""


class RelationshipOut(BaseModel):
    accountId: str
    following: bool = False
    followedBy: bool = False
    requested: bool = False
    muting: bool = False
    blocking: bool = False
    blockedBy: bool = False


class MediaOut(BaseModel):
    type: str
    url: str = ""
    previewUrl: str = ""
    description: str = ""


class StatusOut(BaseModel):
    id: str
    uri: str
    url: str
    createdAt: str
    text: str
    spoilerText: str = ""
    sensitive: bool = False
    visibility: str = "public"
    language: str = ""
    account: AccountOut
    media: list[MediaOut] = []
    hashtags: list[str] = []
    favourites: int = 0
    reblogs: int = 0
    replies: int = 0
    favourited: bool = False
    reblogged: bool = False
    bookmarked: bool = False
    muted: bool = False  # this thread is muted
    pinned: bool = False
    inReplyToId: str | None = None
    isOwn: bool = False
    # Whose boost put this in the feed, when it arrived as one.
    boostedBy: str | None = None
    # The official per-status embed. Unlike an instance's web UI, which sets
    # frame-ancestors 'none', this URL is meant to be framed.
    embedUrl: str = ""
    relationship: RelationshipOut | None = None
    # Notification envelope, when the status arrived as one.
    reason: str | None = None
    notificationId: str | None = None
    isRead: bool | None = None


class FeedOut(BaseModel):
    feed: str
    posts: list[StatusOut]
    nextMaxId: str = ""
    # Only meaningful for a tag feed: whether you follow the hashtag itself.
    tagFollowing: bool | None = None
    lastReadId: str = ""


class ThreadOut(BaseModel):
    ancestors: list[StatusOut]
    status: StatusOut
    descendants: list[StatusOut]


class SessionOut(BaseModel):
    instance: str
    configured: bool  # instance + token both present
    hasToken: bool
    reachable: bool
    detail: str = ""
    title: str = ""
    version: str = ""
    maxCharacters: int = 0
    maxMedia: int = 0
    visibilities: list[str] = []
    rulesAccepted: bool = False
    rulesChanged: bool = False
    account: AccountOut | None = None
    # The instance's own web UI, for the embedded browser.
    embedUrl: str = ""


class RuleOut(BaseModel):
    id: str
    text: str
    hint: str = ""


class TopicOut(BaseModel):
    topic: str
    rules: list[RuleOut]


class LimitOut(BaseModel):
    label: str
    value: str


class TermsOut(BaseModel):
    """What a community allows and forbids, in its own words.

    `limits` is the allowed half — hard facts the server publishes about what it
    will accept. `topics` is the forbidden/required half, and it carries the rules
    verbatim, grouped but never paraphrased: a summary of someone's code of
    conduct is exactly the thing this panel must not put in front of a user.
    """

    instance: str
    title: str
    version: str
    description: str = ""
    thumbnail: str = ""
    contactEmail: str = ""
    aboutUrl: str = ""
    policyHash: str
    accepted: bool
    acceptedAt: str | None = None
    changedSinceAccepted: bool = False
    ruleCount: int = 0
    topics: list[TopicOut] = []
    limits: list[LimitOut] = []
    requires: list[str] = []
    extendedDescription: str = ""


class ActionOut(BaseModel):
    ok: bool = True
    post: StatusOut | None = None
    relationship: RelationshipOut | None = None
    tagFollowing: bool | None = None


class SearchOut(BaseModel):
    accounts: list[AccountOut] = []
    statuses: list[StatusOut] = []
    hashtags: list[str] = []


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _as_http(err: MastodonError) -> HTTPException:
    """Instance-side failures are 400s: the message is written for the user."""
    if isinstance(err, gate.PolicyNotAccepted):
        return HTTPException(status_code=409, detail=str(err))
    if isinstance(err, masto.RateLimited):
        return HTTPException(status_code=429, detail=str(err))
    return HTTPException(status_code=400, detail=str(err))


def _host(instance: str) -> str:
    try:
        return masto.normalise_host(instance)
    except MastodonError as err:
        raise _as_http(err) from None


def _token(body: EngageRequest) -> str:
    token = (body.accessToken or "").strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail=(
                "This needs your Mastodon access token. Create one under Preferences → "
                "Development on your instance with read, write and follow scopes, then "
                "paste it into Settings."
            ),
        )
    return token


def _gated(body: EngageRequest) -> masto.InstancePolicy:
    """Rules check + token check, for anything anyone else can observe."""
    _token(body)
    try:
        return gate.require_accepted(body.instance)
    except MastodonError as err:
        raise _as_http(err) from None


def _me(host: str, token: str) -> dict:
    """The token's own account, cached briefly. {} if the instance won't say."""
    key = f"{host}:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"
    hit = _me_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _ME_TTL_SECONDS:
        return hit[1]
    try:
        account = masto.api_get(host, "/api/v1/accounts/verify_credentials", token) or {}
    except MastodonError:
        return {}
    _me_cache[key] = (now, account)
    return account


def _id(value: str, what: str = "id") -> str:
    """A path-safe id. Ids come from the client, so they never reach a URL raw."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"A Mastodon {what} is required.")
    return quote(cleaned, safe="")


def _pick(action: str, allowed: tuple[str, ...]) -> str:
    if action not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{action!r} is not something this can do. Expected one of: {', '.join(allowed)}.",
        )
    return action


def _visibility(value: str, default: str = "public") -> str:
    chosen = (value or default).strip().lower()
    if chosen not in VISIBILITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Visibility must be one of: {', '.join(VISIBILITIES)}.",
        )
    return chosen


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def _account_out(raw: dict) -> AccountOut:
    return AccountOut(
        id=str(raw.get("id") or ""),
        acct=str(raw.get("acct") or ""),
        displayName=str(raw.get("display_name") or "") or str(raw.get("acct") or ""),
        url=str(raw.get("url") or ""),
        avatar=str(raw.get("avatar_static") or raw.get("avatar") or ""),
        bot=bool(raw.get("bot")),
        followers=int(raw.get("followers_count") or 0),
        note=masto.html_to_text(str(raw.get("note") or "")),
    )


def _relationship_out(raw: dict) -> RelationshipOut:
    return RelationshipOut(
        accountId=str(raw.get("id") or ""),
        following=bool(raw.get("following")),
        followedBy=bool(raw.get("followed_by")),
        requested=bool(raw.get("requested")),
        muting=bool(raw.get("muting")),
        blocking=bool(raw.get("blocking")),
        blockedBy=bool(raw.get("blocked_by")),
    )


def _media_out(raw: dict) -> MediaOut:
    return MediaOut(
        type=str(raw.get("type") or "unknown"),
        url=str(raw.get("url") or ""),
        previewUrl=str(raw.get("preview_url") or raw.get("url") or ""),
        description=str(raw.get("description") or ""),
    )


def _status_out(
    raw: dict,
    me_id: str = "",
    relationships: dict[str, RelationshipOut] | None = None,
    *,
    reason: str | None = None,
    notification_id: str | None = None,
    is_read: bool | None = None,
) -> StatusOut:
    """One Mastodon status as the screen needs it.

    A boost arrives as a thin wrapper carrying the booster's account around
    someone else's status. The content, the counts and every action target come
    from the inner status — favouriting the wrapper would like the wrong post —
    while the wrapper only contributes "who boosted this into your feed".
    """
    booster: str | None = None
    if raw.get("reblog"):
        booster = str((raw.get("account") or {}).get("acct") or "") or None
        raw = raw["reblog"] or {}

    account_raw = raw.get("account") or {}
    account = _account_out(account_raw)
    url = str(raw.get("url") or raw.get("uri") or "")
    return StatusOut(
        id=str(raw.get("id") or ""),
        uri=str(raw.get("uri") or ""),
        url=url,
        createdAt=str(raw.get("created_at") or ""),
        text=masto.html_to_text(str(raw.get("content") or "")),
        spoilerText=str(raw.get("spoiler_text") or ""),
        sensitive=bool(raw.get("sensitive")),
        visibility=str(raw.get("visibility") or "public"),
        language=str(raw.get("language") or ""),
        account=account,
        media=[_media_out(m) for m in (raw.get("media_attachments") or [])],
        hashtags=[str(t.get("name")) for t in (raw.get("tags") or []) if t.get("name")],
        favourites=int(raw.get("favourites_count") or 0),
        reblogs=int(raw.get("reblogs_count") or 0),
        replies=int(raw.get("replies_count") or 0),
        favourited=bool(raw.get("favourited")),
        reblogged=bool(raw.get("reblogged")),
        bookmarked=bool(raw.get("bookmarked")),
        muted=bool(raw.get("muted")),
        pinned=bool(raw.get("pinned")),
        inReplyToId=str(raw["in_reply_to_id"]) if raw.get("in_reply_to_id") else None,
        isOwn=bool(me_id and account.id == me_id),
        boostedBy=booster,
        embedUrl=f"{url}/embed" if url else "",
        relationship=(relationships or {}).get(account.id),
        reason=reason,
        notificationId=notification_id,
        isRead=is_read,
    )


def _relationships(
    host: str, token: str, account_ids: list[str], me_id: str
) -> dict[str, RelationshipOut]:
    """Follow/mute/block state for the authors in a feed, in one call per 40.

    Mastodon keeps this out of the status payload, so a feed that shows a Follow
    button has to ask separately. Failing softly: the buttons render in their
    default state rather than the whole feed 400ing because one lookup broke.
    """
    wanted = sorted({i for i in account_ids if i and i != me_id})
    if not wanted or not token:
        return {}
    out: dict[str, RelationshipOut] = {}
    for i in range(0, len(wanted), 40):
        chunk = wanted[i : i + 40]
        try:
            rows = (
                masto.api_get(
                    host, "/api/v1/accounts/relationships", token, {"id[]": chunk}
                )
                or []
            )
        except MastodonError as err:
            log.warning("[mastodon-engage] relationship lookup failed: %s", err)
            return out
        for row in rows:
            rel = _relationship_out(row)
            if rel.accountId:
                out[rel.accountId] = rel
    return out


def _decorate(host: str, token: str, raws: list[dict], me_id: str) -> list[StatusOut]:
    """Flatten a page of statuses, with relationship state attached."""
    inners = [(r.get("reblog") or r) for r in raws]
    rels = _relationships(
        host,
        token,
        [str((s.get("account") or {}).get("id") or "") for s in inners],
        me_id,
    )
    return [_status_out(r, me_id, rels) for r in raws]


def _newest_notification_marker(host: str, token: str) -> str:
    """The id of the newest notification the user has already seen.

    Mastodon has no per-notification read flag — read state is one marker per
    timeline, so "is this new" is a comparison against it rather than a field.
    """
    try:
        markers = (
            masto.api_get(host, "/api/v1/markers", token, {"timeline[]": ["notifications"]})
            or {}
        )
    except MastodonError:
        return ""
    return str(((markers.get("notifications") or {}).get("last_read_id") or ""))


def _is_newer(candidate: str, marker: str) -> bool:
    """Whether a notification id is newer than the read marker.

    Mastodon ids are ascending numeric strings, but they are long enough that they
    are handed out as strings — compare numerically when both parse, and treat an
    unparseable pair as unread rather than silently marking it read.
    """
    if not marker:
        return True
    try:
        return int(candidate) > int(marker)
    except (TypeError, ValueError):
        return candidate != marker


# ---------------------------------------------------------------------------
# Session + terms
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionOut)
def session(body: EngageRequest) -> SessionOut:
    """Everything the screen needs before it can render: reachability, limits, gate state.

    Reachability is reported, never raised. An instance being down is a sentence
    the panel should say out loud, not a 502 that reads as the app being broken.
    """
    instance = (body.instance or "").strip()
    token = (body.accessToken or "").strip()
    if not instance:
        return SessionOut(
            instance="",
            configured=False,
            hasToken=bool(token),
            reachable=False,
            detail="Set your Mastodon instance in Settings or the Post Creator first.",
        )

    host = _host(instance)
    out = SessionOut(
        instance=host,
        configured=bool(token),
        hasToken=bool(token),
        reachable=False,
        visibilities=list(VISIBILITIES),
        embedUrl=f"https://{host}/",
    )

    try:
        policy = gate.load_policy(host)
    except MastodonError as err:
        return out.model_copy(update={"detail": str(err)})

    ack = gate.acceptance(policy.info.host)
    updates: dict[str, Any] = {
        "reachable": True,
        "title": policy.info.title,
        "version": policy.info.version,
        "maxCharacters": policy.info.max_characters,
        "maxMedia": policy.info.max_media,
        "rulesAccepted": bool(ack and ack["policy_hash"] == policy.fingerprint),
        "rulesChanged": bool(ack and ack["policy_hash"] != policy.fingerprint),
    }
    if token:
        me = _me(host, token)
        if me:
            updates["account"] = _account_out(me)
        else:
            updates["detail"] = (
                f"{host} did not accept that access token. Regenerate it under "
                f"Preferences → Development and paste it into Settings."
            )
    return out.model_copy(update=updates)


@router.post("/terms", response_model=TermsOut)
def terms(body: EngageRequest) -> TermsOut:
    """The community's live rules and limits, for the panel above the embed.

    Always re-fetched rather than served from the stored copy. The stored copy
    exists to prove what was agreed to, not to save a request — showing cached
    rules would defeat a gate whose job is to reflect what the server says now.
    """
    try:
        policy = gate.load_policy(body.instance)
    except MastodonError as err:
        raise _as_http(err) from None

    info = policy.info
    ack = gate.acceptance(info.host)

    # Group the rules, relevant ones first, without dropping or rewording any.
    grouped: dict[str, list[RuleOut]] = {}
    for rule in policy.rules:
        topic = masto.rule_topic(f"{rule.text} {rule.hint}")
        grouped.setdefault(topic, []).append(
            RuleOut(id=rule.id, text=rule.text, hint=rule.hint)
        )
    order = [name for name, _ in masto.RULE_TOPICS] + [masto.FALLBACK_TOPIC]
    topics = [
        TopicOut(topic=name, rules=grouped[name]) for name in order if name in grouped
    ]

    limits = [LimitOut(label="Post length", value=f"{info.max_characters} characters")]
    if info.max_media:
        limits.append(
            LimitOut(label="Media per post", value=f"up to {info.max_media} attachments")
        )
    if info.image_size_limit_mb:
        limits.append(LimitOut(label="Image size", value=f"{info.image_size_limit_mb} MB max"))
    if info.video_size_limit_mb:
        limits.append(LimitOut(label="Video size", value=f"{info.video_size_limit_mb} MB max"))
    if info.max_poll_options:
        poll = f"up to {info.max_poll_options} options"
        if info.poll_max_expiration_days:
            poll += f", open up to {info.poll_max_expiration_days} days"
        limits.append(LimitOut(label="Polls", value=poll))
    limits.append(
        LimitOut(
            label="Post visibility",
            value="public, unlisted, followers-only or direct — your choice per post",
        )
    )
    if info.translation:
        limits.append(LimitOut(label="Translation", value="offered on posts by this server"))
    if info.languages:
        limits.append(
            LimitOut(label="Server languages", value=", ".join(info.languages[:6]))
        )

    return TermsOut(
        instance=info.host,
        title=info.title,
        version=info.version,
        description=info.description,
        thumbnail=info.thumbnail,
        contactEmail=info.contact_email,
        aboutUrl=f"https://{info.host}/about",
        policyHash=policy.fingerprint,
        accepted=bool(ack and ack["policy_hash"] == policy.fingerprint),
        acceptedAt=ack["accepted_at"] if ack else None,
        changedSinceAccepted=bool(ack and ack["policy_hash"] != policy.fingerprint),
        ruleCount=len(policy.rules),
        topics=topics,
        limits=limits,
        requires=gate.policy_notes(policy),
        extendedDescription=policy.extended_description,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

_FEED_PATHS = {
    "home": "/api/v1/timelines/home",
    "local": "/api/v1/timelines/public",
    "public": "/api/v1/timelines/public",
    "bookmarks": "/api/v1/bookmarks",
    "favourites": "/api/v1/favourites",
}


@router.post("/timeline", response_model=FeedOut)
def timeline(body: FeedRequest) -> FeedOut:
    """One page of a timeline: home, this server's local feed, the federated feed,
    a hashtag, your bookmarks or your favourites."""
    host = _host(body.instance)
    token = (body.accessToken or "").strip()
    feed = (body.feed or "home").strip().lower()
    limit = max(1, min(body.limit or 30, MAX_PAGE))

    params: dict[str, Any] = {"limit": limit}
    if body.maxId.strip():
        params["max_id"] = body.maxId.strip()

    if feed == "tag":
        tag = masto.html_to_text(body.tag).strip().lstrip("#")
        if not tag:
            raise HTTPException(status_code=400, detail="Which hashtag?")
        path = f"/api/v1/timelines/tag/{_id(tag, 'hashtag')}"
    elif feed in _FEED_PATHS:
        path = _FEED_PATHS[feed]
        if feed == "local":
            params["local"] = "true"
        # home, bookmarks and favourites are yours: they do not exist without a token.
        if feed in ("home", "bookmarks", "favourites"):
            _token(body)
    else:
        raise HTTPException(status_code=400, detail=f"No such feed: {feed!r}.")

    try:
        page = masto.api_get(host, path, token, params) or []
    except MastodonError as err:
        raise _as_http(err) from None

    me_id = str(_me(host, token).get("id") or "") if token else ""
    posts = _decorate(host, token, [p for p in page if isinstance(p, dict)], me_id)

    tag_following: bool | None = None
    if feed == "tag" and token:
        try:
            info = masto.api_get(host, f"/api/v1/tags/{_id(body.tag.strip().lstrip('#'), 'hashtag')}", token) or {}
            tag_following = bool(info.get("following"))
        except MastodonError:
            tag_following = None

    # Paginate on the raw page's last id, not the flattened one: for a boost those
    # differ, and paging on the inner id would re-fetch or skip a chunk.
    next_max = ""
    if page and isinstance(page[-1], dict):
        next_max = str(page[-1].get("id") or "")

    return FeedOut(feed=feed, posts=posts, nextMaxId=next_max, tagFollowing=tag_following)


@router.post("/notifications", response_model=FeedOut)
def notifications(body: FeedRequest) -> FeedOut:
    """Mentions, boosts, favourites, follows and polls, newest first."""
    host = _host(body.instance)
    token = _token(body)
    limit = max(1, min(body.limit or 30, MAX_PAGE))

    params: dict[str, Any] = {"limit": limit}
    if body.maxId.strip():
        params["max_id"] = body.maxId.strip()

    try:
        page = masto.api_get(host, "/api/v1/notifications", token, params) or []
    except MastodonError as err:
        raise _as_http(err) from None

    marker = _newest_notification_marker(host, token)
    me_id = str(_me(host, token).get("id") or "")

    # Notifications about a status carry it inline; a follow carries only an
    # account, so it becomes a card with the actor and no post body.
    inner = [(n.get("status") or {}) for n in page if isinstance(n, dict)]
    author_ids = [str((s.get("account") or {}).get("id") or "") for s in inner]
    author_ids += [
        str((n.get("account") or {}).get("id") or "") for n in page if isinstance(n, dict)
    ]
    rels = _relationships(host, token, author_ids, me_id)

    posts: list[StatusOut] = []
    for note in page:
        if not isinstance(note, dict):
            continue
        note_id = str(note.get("id") or "")
        reason = str(note.get("type") or "")
        is_read = not _is_newer(note_id, marker)
        status = note.get("status")
        if status:
            posts.append(
                _status_out(
                    status,
                    me_id,
                    rels,
                    reason=reason,
                    notification_id=note_id,
                    is_read=is_read,
                )
            )
            continue
        actor = note.get("account") or {}
        posts.append(
            StatusOut(
                id="",
                uri="",
                url=str(actor.get("url") or ""),
                createdAt=str(note.get("created_at") or ""),
                text="",
                account=_account_out(actor),
                relationship=rels.get(str(actor.get("id") or "")),
                reason=reason,
                notificationId=note_id,
                isRead=is_read,
            )
        )

    next_max = ""
    if page and isinstance(page[-1], dict):
        next_max = str(page[-1].get("id") or "")
    return FeedOut(
        feed="notifications", posts=posts, nextMaxId=next_max, lastReadId=marker
    )


@router.post("/thread", response_model=ThreadOut)
def thread(body: ThreadRequest) -> ThreadOut:
    """A status with what came before and after it — the conversation, in order."""
    host = _host(body.instance)
    token = (body.accessToken or "").strip()
    status_id = _id(body.statusId, "status id")

    try:
        status = masto.api_get(host, f"/api/v1/statuses/{status_id}", token) or {}
        context = masto.api_get(host, f"/api/v1/statuses/{status_id}/context", token) or {}
    except MastodonError as err:
        raise _as_http(err) from None

    me_id = str(_me(host, token).get("id") or "") if token else ""
    ancestors = [a for a in (context.get("ancestors") or []) if isinstance(a, dict)]
    descendants = [d for d in (context.get("descendants") or []) if isinstance(d, dict)]
    flat = _decorate(host, token, ancestors + [status] + descendants, me_id)
    return ThreadOut(
        ancestors=flat[: len(ancestors)],
        status=flat[len(ancestors)],
        descendants=flat[len(ancestors) + 1 :],
    )


@router.post("/suggested-follows", response_model=SuggestedFollowsOut)
def suggested_follows(body: SuggestedFollowsRequest) -> SuggestedFollowsOut:
    """People worth following on this instance, from the niche keywords already configured.

    Two passes, answering different questions. A hashtag timeline shows who is writing
    about the subject on the fediverse right now; account search shows who says they are
    about it in their profile. Appearing in both is the strongest signal available.

    A bio match ranks an account up rather than filtering others out. Plenty of people post
    about a subject daily and keep a bio that is a pronoun and a city, and excluding them
    would leave a list of accounts that describe themselves well rather than ones worth
    reading.

    Gated like everything else here. Reading other people's posts to decide who to follow
    is exactly the activity an instance's rules speak to, so it waits behind the same
    acceptance the timeline and the composer do.
    """
    policy = _gated(body)
    token = _token(body)

    if body.query.strip():
        # Split on commas and newlines only, never spaces: "rust gamedev" is one subject,
        # and searching "rust" and "gamedev" separately returns metallurgy and Unity.
        name = ""
        keywords = [t.strip() for t in re.split(r"[,\n]", body.query) if t.strip()][:5]
        if not keywords:
            return SuggestedFollowsOut(
                niche="", keywords=[], accounts=[], note="Type a subject to search for."
            )
        return _suggest(policy, token, name, keywords, body.limit)

    from vendor.socialpost.src import db as spg_db

    rows = spg_db.list_niches()
    if body.niche.strip():
        row = next((r for r in rows if r["name"] == body.niche.strip()), None)
        if row is None:
            raise HTTPException(status_code=400, detail=f"No niche called {body.niche!r}.")
        name, keywords = row["name"], [str(k) for k in (row["keywords"] or [])]
    else:
        name = ""
        keywords = [str(k) for r in rows for k in (r["keywords"] or [])]

    if not keywords:
        return SuggestedFollowsOut(
            niche=name,
            keywords=[],
            accounts=[],
            note=(
                "No niche keywords yet. Add a niche in the Mastodon Post Creator, or type "
                "a subject above."
            ),
        )

    return _suggest(policy, token, name, keywords, body.limit)


def _suggest(
    policy: masto.InstancePolicy, token: str, name: str, keywords: list[str], limit: int
) -> SuggestedFollowsOut:
    """The search itself, shared by the saved-niche and typed-subject paths."""
    host = policy.info.host

    me = _me(host, token)
    me_id = str(me.get("id") or "")
    # Bounded: each keyword costs a timeline read and a search, and these are someone
    # else's servers being asked on a screen the user opened once.
    probe = keywords[:5]

    found: dict[str, dict] = {}

    def note(raw: dict, keyword: str, *, from_post: bool) -> None:
        acct_id = str(raw.get("id") or "")
        if not acct_id or acct_id == me_id:
            return
        entry = found.setdefault(acct_id, {"raw": raw, "matched": set(), "posts": 0})
        entry["matched"].add(keyword)
        if from_post:
            entry["posts"] += 1

    def as_raw(account: masto.Account) -> dict:
        """The Account dataclass back into the API's own shape.

        tag_timeline returns parsed Status objects while search returns raw JSON, and
        _account_out speaks JSON. Normalising here keeps one mapper rather than two that
        can disagree. There is no avatar on the dataclass — the corpus never needed one —
        so suggestions sourced from a timeline show the fallback initial.
        """
        return {
            "id": account.id,
            "acct": account.acct,
            "display_name": account.display_name,
            "url": account.url,
            "avatar": "",
            "bot": account.bot,
            "followers_count": account.followers,
            "note": account.note,
        }

    for keyword in probe:
        try:
            for status in masto.tag_timeline(host, keyword, limit=20, token=token):
                if status.account and status.account.id:
                    note(as_raw(status.account), keyword, from_post=True)
        except MastodonError:
            pass
        try:
            data = masto.api_get(
                host, "/api/v2/search", token,
                {"q": keyword, "type": "accounts", "limit": 10, "resolve": "false"},
            ) or {}
            for raw in data.get("accounts") or []:
                note(raw, keyword, from_post=False)
        except MastodonError:
            pass

    # One relationships call for every candidate rather than one each: Mastodon takes
    # repeated id[] parameters, and a per-account round trip would be dozens of requests
    # to someone else's server for a list the user may not even scroll.
    following: set[str] = set()
    ids = list(found)
    for i in range(0, len(ids), 40):
        chunk = ids[i : i + 40]
        try:
            for rel in masto.api_get(
                host, "/api/v1/accounts/relationships", token, {"id[]": chunk}
            ) or []:
                if rel.get("following") or rel.get("blocking") or rel.get("muting"):
                    following.add(str(rel.get("id") or ""))
        except MastodonError:
            pass

    lowered = [k.lower() for k in probe]
    out: list[SuggestedAccountOut] = []
    for acct_id, entry in found.items():
        if acct_id in following:
            continue
        account = _account_out(entry["raw"])
        blurb = f"{account.note} {account.displayName}".lower()
        bio_hits = [k for k, low in zip(probe, lowered) if low in blurb]
        matched = sorted(entry["matched"])
        posts = entry["posts"]

        if bio_hits and posts:
            reason = f"Posts about {matched[0]}, and says so in their bio"
        elif bio_hits:
            reason = f"Bio mentions {bio_hits[0]}"
        elif posts > 1:
            reason = f"Posted about {matched[0]} {posts} times recently"
        else:
            reason = f"Posted about {matched[0]}"

        out.append(
            SuggestedAccountOut(
                account=account,
                reason=reason,
                matched=matched,
                posts=posts,
                bioMatch=bool(bio_hits),
            )
        )

    # Bots last, then bio match, then how much they post about it. A bot that relays a
    # keyword all day is not a person worth following, but it is not spam either, so it
    # sinks rather than disappears.
    out.sort(
        key=lambda a: (not a.account.bot, a.bioMatch, a.posts, len(a.matched), a.account.followers),
        reverse=True,
    )
    return SuggestedFollowsOut(
        niche=name, keywords=probe, accounts=out[: max(1, min(limit, 50))]
    )


@router.post("/search", response_model=SearchOut)
def search(body: SearchRequest) -> SearchOut:
    """Find people, posts and hashtags. Needs a token — Mastodon's search does."""
    host = _host(body.instance)
    token = _token(body)
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search for what?")

    try:
        data = (
            masto.api_get(
                host,
                "/api/v2/search",
                token,
                {
                    "q": query,
                    "limit": max(1, min(body.limit or 10, 20)),
                    # Ask the server to fetch an unseen remote account or post when
                    # the query is a URL or a full @user@host — that is what makes
                    # "paste a link to find it here" work at all.
                    "resolve": "true",
                },
            )
            or {}
        )
    except MastodonError as err:
        raise _as_http(err) from None

    me_id = str(_me(host, token).get("id") or "")
    statuses = [s for s in (data.get("statuses") or []) if isinstance(s, dict)]
    return SearchOut(
        accounts=[_account_out(a) for a in (data.get("accounts") or []) if isinstance(a, dict)],
        statuses=_decorate(host, token, statuses, me_id),
        hashtags=[
            str(t.get("name")) for t in (data.get("hashtags") or []) if t.get("name")
        ],
    )


# ---------------------------------------------------------------------------
# Writes — all gated. See the module docstring.
# ---------------------------------------------------------------------------


@router.post("/compose", response_model=ActionOut, dependencies=[Depends(queue_slot("model"))])
def compose(body: ComposeRequest) -> ActionOut:
    """Post, or reply to someone. The one call that puts new words on the fediverse."""
    policy = _gated(body)
    host = policy.info.host
    token = _token(body)

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="There's nothing to post.")

    # Checked here as well as by the server: this instance's real limit is already
    # in hand, and "1,340 of 500 characters" is a better answer than a 422.
    total = len(text) + len(body.spoilerText or "")
    if total > policy.info.max_characters:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That's {total} characters and {host} allows {policy.info.max_characters}"
                f"{' (the content warning counts too)' if body.spoilerText else ''}."
            ),
        )

    payload: dict[str, Any] = {
        "status": text,
        "visibility": _visibility(body.visibility),
    }
    if body.spoilerText.strip():
        payload["spoiler_text"] = body.spoilerText.strip()
        # A content warning without the sensitive flag hides the text but not any
        # media, which is not what anyone means by adding one.
        payload["sensitive"] = True
    elif body.sensitive:
        payload["sensitive"] = True
    if body.language.strip():
        payload["language"] = body.language.strip()[:8]
    if body.inReplyToId.strip():
        payload["in_reply_to_id"] = body.inReplyToId.strip()

    if body.imageUrl.strip():
        # Uploaded first and separately: Mastodon takes media on its own endpoint and
        # the status then references the id. A failure here must stop the post rather
        # than quietly publish the words without the picture they were written around.
        try:
            filename, content = image_prompt.attachment_bytes(body.imageUrl)
        except image_prompt.ImageRenderError as err:
            # 400, not 502: the picture is unusable before Mastodon is involved.
            raise HTTPException(status_code=400, detail=str(err)) from None
        try:
            media_id = masto.upload_media(
                host, token, filename, content, description=body.imageAlt.strip()
            )
        except MastodonError as err:
            raise HTTPException(status_code=502, detail=str(err)) from None
        payload["media_ids"] = [media_id]

    try:
        created = (
            masto.api_post(
                host,
                "/api/v1/statuses",
                token,
                payload,
                idempotency_key=body.idempotencyKey.strip() or uuid.uuid4().hex,
            )
            or {}
        )
    except MastodonError as err:
        raise _as_http(err) from None

    me_id = str(_me(host, token).get("id") or "")
    log.info("[mastodon-engage] posted %s to %s", created.get("id"), host)
    return ActionOut(post=_status_out(created, me_id))


@router.post("/status-action", response_model=ActionOut)
def status_action(body: StatusActionRequest) -> ActionOut:
    """Favourite, boost, bookmark, mute a thread, or pin — and their undos.

    Mastodon answers each of these with the status in its new state, so the screen
    updates from the server's truth rather than guessing at a toggle.
    """
    policy = _gated(body)
    host = policy.info.host
    token = _token(body)
    action = _pick(body.action, _STATUS_ACTIONS)
    status_id = _id(body.statusId, "status id")

    payload: dict[str, Any] | None = None
    if action == "reblog" and body.visibility:
        payload = {"visibility": _visibility(body.visibility)}

    try:
        updated = (
            masto.api_post(host, f"/api/v1/statuses/{status_id}/{action}", token, payload)
            or {}
        )
    except MastodonError as err:
        raise _as_http(err) from None

    # Boosting answers with the wrapper around the original; _status_out unwraps it,
    # which is what the card wants back.
    me_id = str(_me(host, token).get("id") or "")
    return ActionOut(post=_status_out(updated, me_id))


@router.post("/account-action", response_model=ActionOut)
def account_action(body: AccountActionRequest) -> ActionOut:
    """Follow, mute or block an account — and their undos. Returns the relationship."""
    policy = _gated(body)
    host = policy.info.host
    token = _token(body)
    action = _pick(body.action, _ACCOUNT_ACTIONS)
    account_id = _id(body.accountId, "account id")

    try:
        rel = (
            masto.api_post(host, f"/api/v1/accounts/{account_id}/{action}", token) or {}
        )
    except MastodonError as err:
        raise _as_http(err) from None
    return ActionOut(relationship=_relationship_out(rel))


@router.post("/tag-action", response_model=ActionOut)
def tag_action(body: TagActionRequest) -> ActionOut:
    """Follow or unfollow a hashtag.

    Distinctly a Mastodon thing and worth having: with no ranking algorithm, a
    followed hashtag is how anything reaches your home timeline that the people
    you follow did not put there.
    """
    policy = _gated(body)
    host = policy.info.host
    token = _token(body)
    action = _pick(body.action, _TAG_ACTIONS)
    tag = _id((body.tag or "").strip().lstrip("#"), "hashtag")

    try:
        result = masto.api_post(host, f"/api/v1/tags/{tag}/{action}", token) or {}
    except MastodonError as err:
        raise _as_http(err) from None
    return ActionOut(tagFollowing=bool(result.get("following")))


@router.post("/delete-status", response_model=ActionOut)
def delete_status(body: DeleteRequest) -> ActionOut:
    """Delete one of your own posts.

    The ownership check is ours, before the request: the instance would refuse
    anyway, but a wrong id should not be answered with the server's 404 when the
    real problem is that the post belongs to someone else.
    """
    policy = _gated(body)
    host = policy.info.host
    token = _token(body)
    status_id = _id(body.statusId, "status id")

    try:
        current = masto.api_get(host, f"/api/v1/statuses/{status_id}", token) or {}
        me_id = str(_me(host, token).get("id") or "")
        if not me_id or str((current.get("account") or {}).get("id") or "") != me_id:
            raise HTTPException(
                status_code=403, detail="Only your own posts can be deleted from here."
            )
        masto.api_delete(host, f"/api/v1/statuses/{status_id}", token)
    except HTTPException:
        raise
    except MastodonError as err:
        raise _as_http(err) from None
    log.info("[mastodon-engage] deleted %s on %s", body.statusId, host)
    return ActionOut(ok=True)


@router.post("/notifications/read", response_model=ActionOut)
def mark_notifications_read(body: MarkReadRequest) -> ActionOut:
    """Move your notification read marker.

    Not gated, unlike everything else that writes: a marker is private to your own
    account, and nobody on any server can observe it. Mastodon has no per-item read
    flag — one marker per timeline is the whole mechanism.
    """
    host = _host(body.instance)
    token = _token(body)
    last_read = (body.lastReadId or "").strip()
    if not last_read:
        raise HTTPException(status_code=400, detail="Nothing to mark read.")
    try:
        masto.api_post(
            host,
            "/api/v1/markers",
            token,
            {"notifications": {"last_read_id": last_read}},
        )
    except MastodonError as err:
        raise _as_http(err) from None
    return ActionOut(ok=True)
