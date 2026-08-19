"""Engage, Tumblr side — acting as yourself on your own blog.

Third sibling of routers/engage.py (Bluesky) and routers/mastodon_engage.py
(Mastodon), and like those two it is its own file rather than a branch inside
theirs: Tumblr disagrees with both about what a feed even contains.

Five things shape this file:

1. **Credentials never travel in a URL.** Every endpoint is a POST carrying the
   four OAuth1 values in the body, including the ones that only read. The backend
   binds to localhost, but a secret in a query string still lands in access logs
   and anything mirroring them. Same rule the Mastodon side follows.

2. **A user is not a blog.** `/user/dashboard` belongs to the account; activity,
   posting, reblogging, blocking and deleting all belong to *a* blog. Every
   endpoint that addresses one resolves it through `services/tumblr.resolve_blog`,
   which falls back to the account's primary blog, so a user who never typed a
   blog name still gets a working screen.

3. **Reblogging is Tumblr's reply, boost and quote at once.** Tumblr's API can
   create posts and reblogs; it cannot create a *reply*. That is not an omission
   here — there is no documented endpoint for it, and the conversation on Tumblr
   happens in reblog commentary anyway. So a reblog with commentary is what the
   composer offers, and replies are readable through `/notes` (mode=conversation)
   without being writable. The panel says so rather than showing a dead button.

4. **Some Bluesky actions have no Tumblr counterpart, and are absent rather than
   faked.** There is no bookmark (Tumblr's likes are the saved list, and they get
   their own feed), no per-account mute (only block), and no read-marker for the
   activity feed — the `unread` flag is set by Tumblr against its own last-read
   time, which the API will not let anyone move.

5. **Relationship state costs a request per blog.** Tumblr has no batch endpoint
   and the post payload never says whether you follow the author, so a feed page
   is followed by a bounded, cached, polite sweep of `/blog/{x}/info`. See
   services/tumblr.relationships.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import image_prompt
from ..services import tumblr
from ..services.tumblr import Credentials, TumblrError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tumblr-engage", tags=["tumblr-engage"])

# Tumblr caps every paged read at 20 whatever you ask for.
MAX_PAGE = tumblr.PAGE_LIMIT

FEEDS = ("dashboard", "notifications", "likes")

# What a post may be created as. Anything else is Tumblr's business, not a value
# the client gets to invent.
POST_STATES = ("published", "queue", "draft", "private")

# Notes are read in one of Tumblr's documented modes; the value reaches a URL.
NOTE_MODES = ("all", "conversation", "likes", "rollup", "reblogs_with_tags")

# Tumblr's activity types, as a sentence. Same job as REASON_VERB on the other two
# panels; the keys are Tumblr's own `type` strings.
REASON_VERB = {
    "like": "liked your post",
    "reply": "replied to your post",
    "follow": "followed you",
    "mention_in_reply": "mentioned you in a reply",
    "mention_in_post": "mentioned you in a post",
    "reblog_naked": "reblogged your post",
    "reblog_with_content": "reblogged your post with a comment",
    "ask": "sent you an ask",
    "answered_ask": "answered your ask",
    "new_group_blog_member": "joined your group blog",
    "post_attribution": "used your post's content",
    "post_flagged": "a post of yours was flagged",
    "post_appeal_accepted": "your appeal was accepted",
    "post_appeal_rejected": "your appeal was rejected",
    "what_you_missed": "posted something you missed",
    "conversational_note": "added to a post you are watching",
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TumblrRequest(BaseModel):
    """Base for every call: the four OAuth1 values, and which blog to act as."""

    consumerKey: str = ""
    consumerSecret: str = ""
    oauthToken: str = ""
    oauthTokenSecret: str = ""
    # Optional — blank means the account's primary blog.
    blog: str = ""


class FeedRequest(TumblrRequest):
    feed: str = "dashboard"
    limit: int = 20
    offset: int = 0
    # Activity feed only: a unix timestamp that begins the page.
    before: int = 0


class ComposeRequest(TumblrRequest):
    text: str
    title: str = ""
    tags: str = ""
    state: str = "published"
    #: A /outputs URL for an image this app generated. Empty posts text only.
    imageUrl: str = ""
    imageAlt: str = ""


class ReblogRequest(TumblrRequest):
    # The post being reblogged, and the blog it belongs to.
    blogName: str
    postId: str
    reblogKey: str
    comment: str = ""
    tags: str = ""
    state: str = "published"


class LikeRequest(TumblrRequest):
    blogName: str
    postId: str
    reblogKey: str
    enabled: bool


class BlogActionRequest(TumblrRequest):
    blogName: str
    enabled: bool


class PostTargetRequest(TumblrRequest):
    blogName: str = ""
    postId: str


class MuteRequest(PostTargetRequest):
    enabled: bool


class NotesRequest(TumblrRequest):
    blogName: str
    postId: str
    mode: str = "conversation"


class SuggestedFollowsRequest(TumblrRequest):
    niche: str = ""
    # A subject typed in rather than a saved niche. Takes over completely when present.
    query: str = ""
    limit: int = 20


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MediaOut(BaseModel):
    """Deliberately the same field names the Bluesky and Mastodon panels emit
    (routers/engage.py::MediaOut), so one PostMedia component renders all three."""

    kind: str  # image | video | audio | link | unknown
    url: str = ""
    previewUrl: str = ""
    description: str = ""
    isHls: bool = False  # never true here; Tumblr serves video as a direct file
    aspectRatio: float | None = None
    title: str = ""
    domain: str = ""


class BlogState(BaseModel):
    """Your relationship with one blog, as far as Tumblr will say."""

    blogName: str
    following: bool = False
    blocked: bool = False


class TumblrPost(BaseModel):
    id: str
    reblogKey: str = ""
    blogName: str  # normalised host form: myblog.tumblr.com
    blogTitle: str = ""
    blogUrl: str = ""
    avatar: str = ""
    postUrl: str = ""
    createdAt: str = ""
    text: str = ""
    tags: list[str] = []
    noteCount: int = 0
    liked: bool = False
    isOwn: bool = False
    # False for an activity row that carries no post we can act on — the Tumblr
    # equivalent of a Bluesky notification whose subject is a follow.
    isPost: bool = True
    isReblog: bool = False
    rebloggedFrom: str = ""
    # Only the author of a post can see or change this.
    muted: bool = False
    state: str = ""
    following: bool = False
    blocked: bool = False
    media: list[MediaOut] = []
    # Activity-feed envelope, when the row arrived as one.
    reason: str | None = None
    reasonText: str = ""
    isRead: bool | None = None


class FeedOut(BaseModel):
    feed: str
    posts: list[TumblrPost]
    # Whichever cursor this feed pages by: an offset for the dashboard and likes,
    # a unix timestamp for the activity feed. Empty when there is no next page.
    nextOffset: int = 0
    nextBefore: int = 0
    note: str = ""


class BlogSummary(BaseModel):
    name: str
    title: str = ""
    url: str = ""
    primary: bool = False
    followers: int = 0


class SessionOut(BaseModel):
    configured: bool  # all four OAuth values present
    reachable: bool = False
    detail: str = ""
    userName: str = ""
    blog: str = ""  # the blog being acted as
    blogTitle: str = ""
    blogUrl: str = ""
    avatar: str = ""
    following: int = 0
    likes: int = 0
    blogs: list[BlogSummary] = []


class ActionOut(BaseModel):
    ok: bool = True
    post: TumblrPost | None = None
    blog: BlogState | None = None
    createdId: str = ""


class NoteOut(BaseModel):
    type: str
    blogName: str
    blogUrl: str = ""
    avatar: str = ""
    createdAt: str = ""
    text: str = ""
    tags: list[str] = []
    postId: str = ""


class NotesOut(BaseModel):
    notes: list[NoteOut] = []
    totalNotes: int = 0
    totalLikes: int = 0
    totalReblogs: int = 0
    note: str = ""


class SuggestedBlog(BaseModel):
    name: str
    title: str = ""
    url: str = ""
    avatar: str = ""
    description: str = ""
    posts: int = 0
    # Why this blog is being suggested, in the user's words rather than a score.
    reason: str
    matched: list[str] = []
    bioMatch: bool = False


class SuggestedFollowsOut(BaseModel):
    niche: str
    keywords: list[str]
    blogs: list[SuggestedBlog]
    note: str = ""


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _as_http(err: TumblrError) -> HTTPException:
    if isinstance(err, tumblr.RateLimited):
        return HTTPException(status_code=429, detail=str(err))
    return HTTPException(status_code=400, detail=str(err))


def _creds(body: TumblrRequest) -> Credentials:
    """The four values, validated. Raises with instructions if any is missing."""
    try:
        creds = tumblr.credentials_from(body)
    except TumblrError as err:
        raise _as_http(err) from None
    if not creds.complete:
        raise HTTPException(
            status_code=400,
            detail=(
                "Tumblr needs all four OAuth values. Register an application at "
                "tumblr.com/oauth/apps for the consumer key and secret, then get a token "
                "and token secret from api.tumblr.com/console, and paste all four into "
                "Settings."
            ),
        )
    return creds


def _acting(body: TumblrRequest) -> Credentials:
    """Credentials that definitely name a blog — for anything addressed to one."""
    try:
        return tumblr.resolve_blog(_creds(body))
    except TumblrError as err:
        raise _as_http(err) from None


def _pick(value: str, allowed: tuple[str, ...], what: str) -> str:
    chosen = (value or allowed[0]).strip().lower()
    if chosen not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{what} must be one of: {', '.join(allowed)}.",
        )
    return chosen


def _post_id(value: str) -> str:
    """A post id, which reaches a URL and a signed form body."""
    cleaned = (value or "").strip()
    if not cleaned.isdigit():
        raise HTTPException(status_code=400, detail="A Tumblr post id is required.")
    return cleaned


def _clean_text(text: str, what: str = "Text") -> str:
    value = (text or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{what} is required.")
    return value


def _tags(raw: str) -> str:
    """A tag box into Tumblr's comma-separated form.

    Split on commas and newlines only. A Tumblr tag is routinely a phrase — the
    site is built on tags like "my art" and "cottagecore aesthetic" — so splitting
    on spaces would shred exactly the tags that carry the reach.
    """
    parts = [t.strip().lstrip("#").strip() for t in re.split(r"[,\n]", raw or "")]
    return ",".join(t for t in parts if t)[:2000]


def _iso(timestamp: Any) -> str:
    try:
        seconds = int(timestamp)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def _media_out(items: list[dict]) -> list[MediaOut]:
    return [MediaOut(**item) for item in items]


def _post_out(
    raw: dict,
    *,
    own_blogs: set[str],
    rels: dict[str, dict] | None = None,
) -> TumblrPost:
    """One Tumblr post as the screen needs it."""
    rels = rels or {}
    name = str(raw.get("blog_name") or (raw.get("blog") or {}).get("name") or "")
    try:
        blog_name = tumblr.normalise_blog(name)
    except TumblrError:
        blog_name = ""
    info = rels.get(blog_name) or {}
    blog_obj = raw.get("blog") if isinstance(raw.get("blog"), dict) else {}

    trail = raw.get("trail") if isinstance(raw.get("trail"), list) else []
    # Who this was reblogged from: the field when Tumblr sets it, otherwise the
    # first item of the reblog trail, which is the chain's original author.
    reblogged_from = str(raw.get("reblogged_from_name") or "")
    if not reblogged_from and trail and isinstance(trail[0], dict):
        reblogged_from = str((trail[0].get("blog") or {}).get("name") or "")

    return TumblrPost(
        id=str(raw.get("id_string") or raw.get("id") or ""),
        reblogKey=str(raw.get("reblog_key") or ""),
        blogName=blog_name,
        blogTitle=str(blog_obj.get("title") or info.get("title") or tumblr.short_name(blog_name)),
        blogUrl=str(blog_obj.get("url") or info.get("url") or (tumblr.blog_url(blog_name) if blog_name else "")),
        avatar=tumblr.avatar_url(blog_name) if blog_name else "",
        postUrl=str(raw.get("post_url") or ""),
        createdAt=_iso(raw.get("timestamp")),
        text=tumblr.npf_text(raw),
        tags=[str(t) for t in (raw.get("tags") or []) if str(t).strip()],
        noteCount=int(raw.get("note_count") or 0),
        liked=bool(raw.get("liked")),
        isOwn=blog_name in own_blogs,
        isPost=True,
        isReblog=bool(trail) or bool(raw.get("reblogged_from_id")),
        rebloggedFrom=reblogged_from,
        muted=bool(raw.get("muted")),
        state=str(raw.get("state") or ""),
        following=bool(info.get("followed")),
        blocked=bool(info.get("is_blocked_from_primary")),
        media=_media_out(tumblr.npf_media(raw)),
    )


def _activity_out(raw: dict, *, my_blog: str, rels: dict[str, dict] | None = None) -> TumblrPost:
    """One activity item as a feed card.

    Deliberately not a post. An activity item carries the actor, a verb and at
    most an excerpt — never a post payload, and never the `reblog_key` that
    liking or reblogging requires. Presenting it as a post would mean either
    fetching one post per row or showing buttons that cannot work, so the row
    offers what it can honestly do: follow or block the actor, and open the post
    that caused it.
    """
    rels = rels or {}
    actor = str(raw.get("from_tumblelog_name") or "")
    try:
        blog_name = tumblr.normalise_blog(actor) if actor else ""
    except TumblrError:
        blog_name = ""
    info = rels.get(blog_name) or {}

    kind = str(raw.get("type") or "")
    # Their post when the activity produced one (a reblog, a reply), otherwise
    # yours — the one they acted on.
    their_post = str(raw.get("post_id") or "")
    my_post = str(raw.get("target_post_id") or "")
    if their_post and blog_name:
        url = tumblr.post_url(blog_name, their_post)
    else:
        url = tumblr.post_url(my_blog, my_post) if my_post else ""

    text = str(raw.get("reply_text") or raw.get("added_text") or "").strip()

    return TumblrPost(
        id=str(raw.get("id") or f"{kind}:{raw.get('timestamp')}:{actor}"),
        blogName=blog_name,
        blogTitle=str(info.get("title") or tumblr.short_name(blog_name)),
        blogUrl=tumblr.blog_url(blog_name) if blog_name else "",
        avatar=tumblr.avatar_url(blog_name) if blog_name else "",
        postUrl=url,
        createdAt=_iso(raw.get("timestamp")),
        text=tumblr.html_to_text(text),
        tags=[str(t) for t in (raw.get("post_tags") or []) if str(t).strip()],
        isPost=False,
        following=bool(info.get("followed")),
        blocked=bool(info.get("is_blocked_from_primary")),
        reason=kind,
        reasonText=REASON_VERB.get(kind, kind.replace("_", " ")),
        isRead=not bool(raw.get("unread")),
    )


def _same_blog(a: str, b: str) -> bool:
    """Two blog identifiers naming the same blog, tolerating an unparseable one."""
    try:
        return bool(a) and tumblr.normalise_blog(a) == b
    except TumblrError:
        return False


def _own_blog_names(user: dict) -> set[str]:
    names: set[str] = set()
    for blog in tumblr.own_blogs(user):
        try:
            names.add(tumblr.normalise_blog(str(blog.get("name") or "")))
        except TumblrError:
            continue
    return names


def _fetch_post(creds: Credentials, blog_name: str, post_id: str) -> dict:
    """One post, freshly. Used after an action so the card shows real state."""
    target = blog_name or creds.blog
    payload = tumblr.get(
        creds,
        f"/blog/{tumblr.blog_path(target)}/posts",
        id=post_id,
        npf="true",
        reblog_info="true",
        notes_info="true",
    ) or {}
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if isinstance(posts, list) and posts and isinstance(posts[0], dict):
        return posts[0]
    raise TumblrError("Tumblr no longer has that post.")


def _refreshed(creds: Credentials, blog_name: str, post_id: str) -> TumblrPost | None:
    """The post after an action, or None if it cannot be read back.

    A failure here means the action may well have worked and only the re-read
    failed, so it is swallowed: the panel refetches feeds anyway, and turning a
    successful like into an error message would be a lie.
    """
    try:
        raw = _fetch_post(creds, blog_name, post_id)
        user = tumblr.user_info(creds)
        rels = tumblr.relationships(creds, [str(raw.get("blog_name") or blog_name)])
        return _post_out(raw, own_blogs=_own_blog_names(user), rels=rels)
    except TumblrError as err:
        log.debug("post re-read failed for %s/%s: %s", blog_name, post_id, err)
        return None


def _blog_state(creds: Credentials, blog_name: str) -> BlogState:
    tumblr.forget_blog(creds, blog_name)
    try:
        info = tumblr.blog_info(creds, blog_name, refresh=True)
    except TumblrError:
        info = {}
    return BlogState(
        blogName=tumblr.normalise_blog(blog_name),
        following=bool(info.get("followed")),
        blocked=bool(info.get("is_blocked_from_primary")),
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionOut)
def session(body: TumblrRequest) -> SessionOut:
    """Who these credentials are, and which blog the screen will act as.

    Never raises for a missing or rejected credential: this is the call the panel
    makes on arrival to decide whether to show a connect prompt or a feed, and a
    500 there would be a blank screen instead of an explanation.
    """
    try:
        creds = tumblr.credentials_from(body)
    except TumblrError as err:
        return SessionOut(configured=False, detail=str(err))
    if not creds.complete:
        return SessionOut(configured=False)

    try:
        user = tumblr.user_info(creds, refresh=True)
    except TumblrError as err:
        return SessionOut(configured=True, reachable=False, detail=str(err))

    blogs = [
        BlogSummary(
            name=str(b.get("name") or ""),
            title=str(b.get("title") or ""),
            url=str(b.get("url") or ""),
            primary=bool(b.get("primary")),
            followers=int(b.get("followers") or 0),
        )
        for b in tumblr.own_blogs(user)
    ]

    acting = creds.blog or tumblr.primary_blog(user)
    chosen = next((b for b in blogs if _same_blog(b.name, acting)), None)

    detail = ""
    if creds.blog and chosen is None:
        # Not fatal — Tumblr will still act as the named blog if the account owns
        # it under another identifier — but silently posting as the wrong blog
        # would be much worse than saying so.
        detail = (
            f"This account's blogs are {', '.join(b.name for b in blogs) or 'none'} — "
            f"{tumblr.short_name(creds.blog)} is not among them. Check the blog name in Settings."
        )

    return SessionOut(
        configured=True,
        reachable=True,
        detail=detail,
        userName=str(user.get("name") or ""),
        blog=acting,
        blogTitle=chosen.title if chosen else "",
        blogUrl=chosen.url if chosen else (tumblr.blog_url(acting) if acting else ""),
        avatar=tumblr.avatar_url(acting) if acting else "",
        following=int(user.get("following") or 0),
        likes=int(user.get("likes") or 0),
        blogs=blogs,
    )


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


@router.post("/feed", response_model=FeedOut)
def feed(body: FeedRequest) -> FeedOut:
    """One page of the dashboard, the activity feed, or your likes.

    `npf=true` throughout: Tumblr returns either legacy typed posts or Neue Post
    Format blocks depending on how each post was made, and asking for one shape
    means one flattener instead of eight.
    """
    which = _pick(body.feed, FEEDS, "Feed")
    creds = _acting(body)
    limit = max(1, min(body.limit or MAX_PAGE, MAX_PAGE))
    offset = max(0, body.offset)

    try:
        user = tumblr.user_info(creds)
        own = _own_blog_names(user)

        if which == "notifications":
            payload = tumblr.get(
                creds,
                f"/blog/{tumblr.blog_path(creds.blog)}/notifications",
                before=body.before or None,
            ) or {}
            items = [n for n in (payload.get("notifications") or []) if isinstance(n, dict)]
            rels = tumblr.relationships(creds, [str(n.get("from_tumblelog_name") or "") for n in items])
            posts = [_activity_out(n, my_blog=creds.blog, rels=rels) for n in items]
            oldest = min((int(n.get("timestamp") or 0) for n in items), default=0)
            return FeedOut(
                feed=which,
                posts=posts,
                nextBefore=oldest if items else 0,
                note=(
                    ""
                    if items
                    else "Nothing in your activity feed yet."
                ),
            )

        if which == "likes":
            payload = tumblr.get(
                creds, "/user/likes", limit=limit, offset=offset, npf="true", reblog_info="true"
            ) or {}
            raw_posts = payload.get("liked_posts")
        else:
            payload = tumblr.get(
                creds,
                "/user/dashboard",
                limit=limit,
                offset=offset,
                npf="true",
                reblog_info="true",
                notes_info="true",
            ) or {}
            raw_posts = payload.get("posts")

        items = [p for p in (raw_posts or []) if isinstance(p, dict)]
        rels = tumblr.relationships(creds, [str(p.get("blog_name") or "") for p in items])
        posts = [_post_out(p, own_blogs=own, rels=rels) for p in items]
    except TumblrError as err:
        raise _as_http(err) from None

    return FeedOut(
        feed=which,
        posts=posts,
        nextOffset=offset + len(posts) if len(posts) >= limit else 0,
    )


@router.post("/notes", response_model=NotesOut)
def notes(body: NotesRequest) -> NotesOut:
    """The conversation on a post — replies and reblogs with commentary.

    This is where Tumblr replies live. The API can read them and cannot write
    them, which is the whole reason this endpoint exists: without it a reply to
    your post would show up in the activity feed as a one-line excerpt and
    nowhere else.
    """
    creds = _creds(body)
    mode = _pick(body.mode, NOTE_MODES, "Mode")
    post_id = _post_id(body.postId)
    try:
        payload = tumblr.get(
            creds,
            f"/blog/{tumblr.blog_path(body.blogName)}/notes",
            id=post_id,
            mode=mode,
        ) or {}
    except TumblrError as err:
        raise _as_http(err) from None

    rows: list[NoteOut] = []
    for raw in payload.get("notes") or []:
        if not isinstance(raw, dict):
            continue
        try:
            name = tumblr.normalise_blog(str(raw.get("blog_name") or ""))
        except TumblrError:
            continue
        rows.append(
            NoteOut(
                type=str(raw.get("type") or ""),
                blogName=name,
                blogUrl=str(raw.get("blog_url") or (tumblr.blog_url(name) if name else "")),
                avatar=tumblr.avatar_url(name) if name else "",
                createdAt=_iso(raw.get("timestamp")),
                text=tumblr.html_to_text(str(raw.get("reply_text") or raw.get("added_text") or "")),
                tags=[str(t) for t in (raw.get("tags") or []) if str(t).strip()],
                postId=str(raw.get("post_id") or ""),
            )
        )

    return NotesOut(
        notes=rows,
        totalNotes=int(payload.get("total_notes") or 0),
        totalLikes=int(payload.get("total_likes") or 0),
        totalReblogs=int(payload.get("total_reblogs") or 0),
        note="" if rows else "No replies or reblogs with comments on this one yet.",
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


#: Name of the multipart part carrying the image, referenced from the NPF block by
#: `identifier`. Any stable string works; it only has to match between the two.
_IMAGE_PART = "attached-image"


def _mime_for(filename: str) -> str:
    """NPF wants a media type. Everything this app generates is PNG; the rest is
    tolerance for an image picked from elsewhere in the Library."""
    lowered = filename.lower()
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".webp"):
        return "image/webp"
    if lowered.endswith(".gif"):
        return "image/gif"
    return "image/png"


@router.post("/compose", response_model=ActionOut)
def compose(body: ComposeRequest) -> ActionOut:
    """A new post on your blog, in Neue Post Format.

    NPF rather than the legacy `/post` route because Tumblr's own docs point new
    work here, and a text block is the same thing a legacy text post becomes
    anyway. `state` is exposed because "queue" and "draft" are load-bearing on
    Tumblr in a way they are not on the other two networks.
    """
    creds = _acting(body)
    text = _clean_text(body.text)
    state = _pick(body.state, POST_STATES, "State")

    content: list[dict] = []
    if body.title.strip():
        content.append({"type": "text", "subtype": "heading1", "text": body.title.strip()})
    content.append({"type": "text", "text": text})

    payload: dict[str, Any] = {"content": content, "state": state}
    tags = _tags(body.tags)
    if tags:
        payload["tags"] = tags

    attachment: tuple[str, bytes] | None = None
    if body.imageUrl.strip():
        try:
            attachment = image_prompt.attachment_bytes(body.imageUrl)
        except image_prompt.ImageRenderError as err:
            raise HTTPException(status_code=400, detail=str(err)) from None
        # The image leads. Tumblr is a visual dashboard — a picture below the caption
        # reads as an afterthought there in a way it does not on the other two — and NPF
        # block order is exactly what the reader sees.
        block: dict[str, Any] = {
            "type": "image",
            "media": [{"type": _mime_for(attachment[0]), "identifier": _IMAGE_PART}],
        }
        if body.imageAlt.strip():
            block["alt_text"] = body.imageAlt.strip()[:1000]
        content.insert(0, block)

    try:
        if attachment is not None:
            created = tumblr.create_npf_post_with_media(
                creds, creds.blog, payload, attachment[0], attachment[1], _IMAGE_PART
            ) or {}
        else:
            created = tumblr.request(
                creds, "POST", f"/blog/{tumblr.blog_path(creds.blog)}/posts", json_body=payload
            ) or {}
    except TumblrError as err:
        raise _as_http(err) from None

    post_id = str(created.get("id") or "") if isinstance(created, dict) else ""
    # A draft or a queued post is not on the dashboard to read back, and asking
    # for it would 404 the happy path.
    fresh = _refreshed(creds, creds.blog, post_id) if post_id and state == "published" else None
    return ActionOut(createdId=post_id, post=fresh)


@router.post("/reblog", response_model=ActionOut)
def reblog(body: ReblogRequest) -> ActionOut:
    """Reblog a post, with or without a comment.

    The legacy `/post/reblog` route rather than the NPF one, and for a concrete
    reason: an NPF reblog must name the parent blog by its UUID, and the UUID is
    not in a `/user/dashboard` payload — the dashboard identifies each post's
    blog by name only. This route takes the post id and reblog key that every
    feed payload does carry.
    """
    creds = _acting(body)
    state = _pick(body.state, POST_STATES, "State")
    post_id = _post_id(body.postId)
    reblog_key = (body.reblogKey or "").strip()
    if not reblog_key:
        raise HTTPException(
            status_code=400,
            detail="Tumblr needs this post's reblog key, and this row does not carry one.",
        )

    try:
        tumblr.post_form(
            creds,
            f"/blog/{tumblr.blog_path(creds.blog)}/post/reblog",
            id=post_id,
            reblog_key=reblog_key,
            comment=body.comment.strip() or None,
            tags=_tags(body.tags) or None,
            state=state,
        )
        return ActionOut(post=_refreshed(creds, body.blogName, post_id))
    except TumblrError as err:
        raise _as_http(err) from None


@router.post("/like", response_model=ActionOut)
def like(body: LikeRequest) -> ActionOut:
    """Like or unlike a post.

    Tumblr's like route is idempotent and returns nothing useful, so the post is
    read back afterwards — the same shape the Bluesky side uses, and the only way
    the button can show what actually happened rather than what was asked for.
    """
    creds = _acting(body)
    post_id = _post_id(body.postId)
    reblog_key = (body.reblogKey or "").strip()
    if not reblog_key:
        raise HTTPException(
            status_code=400,
            detail="Tumblr needs this post's reblog key, and this row does not carry one.",
        )

    try:
        tumblr.post_form(
            creds,
            "/user/like" if body.enabled else "/user/unlike",
            id=post_id,
            reblog_key=reblog_key,
        )
        return ActionOut(post=_refreshed(creds, body.blogName, post_id))
    except TumblrError as err:
        raise _as_http(err) from None


@router.post("/follow", response_model=ActionOut)
def follow(body: BlogActionRequest) -> ActionOut:
    creds = _acting(body)
    try:
        target = tumblr.blog_url(body.blogName)
    except TumblrError as err:
        raise _as_http(err) from None
    try:
        tumblr.post_form(creds, "/user/follow" if body.enabled else "/user/unfollow", url=target)
        return ActionOut(blog=_blog_state(creds, body.blogName))
    except TumblrError as err:
        raise _as_http(err) from None


@router.post("/block", response_model=ActionOut)
def block(body: BlogActionRequest) -> ActionOut:
    """Block or unblock a blog, on behalf of the blog being acted as.

    Tumblr blocks are per-blog, not per-account: blocking from your side blog
    does not block from your main one. That is why this goes through the resolved
    acting blog rather than the account.
    """
    creds = _acting(body)
    try:
        target = tumblr.normalise_blog(body.blogName)
    except TumblrError as err:
        raise _as_http(err) from None
    if not target:
        raise HTTPException(status_code=400, detail="A Tumblr blog name is required.")

    path = f"/blog/{tumblr.blog_path(creds.blog)}/blocks"
    try:
        if body.enabled:
            tumblr.post_form(creds, path, blocked_tumblelog=target)
        else:
            tumblr.request(creds, "DELETE", path, params={"blocked_tumblelog": target})
        return ActionOut(blog=_blog_state(creds, target))
    except TumblrError as err:
        raise _as_http(err) from None


@router.post("/mute", response_model=ActionOut)
def mute(body: MuteRequest) -> ActionOut:
    """Mute or unmute notifications about one of your own posts.

    Tumblr's nearest thing to muting a thread, and it is only available to the
    post's author — which is why the panel offers it on your posts alone.
    """
    creds = _acting(body)
    post_id = _post_id(body.postId)
    path = f"/blog/{tumblr.blog_path(creds.blog)}/posts/{post_id}/mute"
    try:
        tumblr.request(creds, "POST" if body.enabled else "DELETE", path)
        return ActionOut(post=_refreshed(creds, creds.blog, post_id))
    except TumblrError as err:
        raise _as_http(err) from None


@router.post("/delete-post", response_model=ActionOut)
def delete_post(body: PostTargetRequest) -> ActionOut:
    creds = _acting(body)
    post_id = _post_id(body.postId)
    try:
        user = tumblr.user_info(creds)
        target = tumblr.normalise_blog(body.blogName) if body.blogName else creds.blog
        if target not in _own_blog_names(user):
            raise HTTPException(status_code=403, detail="Only your own Tumblr posts can be deleted.")
        tumblr.post_form(creds, f"/blog/{tumblr.blog_path(target)}/post/delete", id=post_id)
        return ActionOut(ok=True)
    except HTTPException:
        raise
    except TumblrError as err:
        raise _as_http(err) from None


# ---------------------------------------------------------------------------
# Suggested follows
# ---------------------------------------------------------------------------


@router.post("/suggested-follows", response_model=SuggestedFollowsOut)
def suggested_follows(body: SuggestedFollowsRequest) -> SuggestedFollowsOut:
    """Blogs worth following, found from niche keywords or a subject typed in.

    Tumblr is a tag-first site, so there is one pass rather than the two the other
    panels run: `/tagged` is both "who writes about this" and, because tags are
    chosen by the author, "who says they are about this". There is no blog search
    in the API to pair it with.

    A blog's bio comes from a second, bounded pass — `/tagged` returns posts, and a
    post payload carries no description — and that pass is also what supplies the
    follow state, so anyone already followed or blocked drops out before the list
    is shown. A suggestion list whose top entry is someone you followed last week
    teaches you to stop reading it.
    """
    creds = _acting(body)

    if body.query.strip():
        # Split on commas and newlines only, never spaces: "cottagecore baking" is one
        # subject, and searching each word separately returns neither.
        name = ""
        keywords = [t.strip() for t in re.split(r"[,\n]", body.query) if t.strip()][:5]
        if not keywords:
            return SuggestedFollowsOut(
                niche="", keywords=[], blogs=[], note="Type a subject to search for."
            )
    else:
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
                blogs=[],
                note=(
                    "No niche keywords yet. Add a niche in the Bluesky Post Creator, or type "
                    "a subject above."
                ),
            )

    # Bounded: each keyword is a request to Tumblr, on a screen someone opened once.
    probe = keywords[:5]
    try:
        own = _own_blog_names(tumblr.user_info(creds))
    except TumblrError as err:
        raise _as_http(err) from None

    found: dict[str, dict] = {}
    for keyword in probe:
        try:
            posts = tumblr.get(creds, "/tagged", tag=keyword, limit=MAX_PAGE, npf="true") or []
        except TumblrError:
            # One dead keyword must not empty the list.
            continue
        for raw in posts if isinstance(posts, list) else []:
            if not isinstance(raw, dict):
                continue
            try:
                blog_name = tumblr.normalise_blog(str(raw.get("blog_name") or ""))
            except TumblrError:
                continue
            if not blog_name or blog_name in own:
                continue
            entry = found.setdefault(blog_name, {"matched": set(), "posts": 0})
            entry["matched"].add(keyword)
            entry["posts"] += 1

    lowered = [k.lower() for k in probe]
    # Ranked before enrichment on what is already known — how much they post about the
    # subject, then across how many keywords — because each bio costs a request and only
    # the blogs that could plausibly be shown are worth one.
    shortlist = sorted(
        found, key=lambda b: (found[b]["posts"], len(found[b]["matched"])), reverse=True
    )[: min(max(body.limit, 10), 30)]

    out: list[SuggestedBlog] = []
    for blog_name in shortlist:
        entry = found[blog_name]
        try:
            info = tumblr.blog_info(creds, blog_name)
        except TumblrError:
            info = {}
        if info.get("followed") or info.get("is_blocked_from_primary"):
            continue

        description = tumblr.html_to_text(str(info.get("description") or "")).strip()
        blurb = description.lower()
        bio_hits = [k for k, low in zip(probe, lowered) if low in blurb]
        matched = sorted(entry["matched"])
        count = entry["posts"]

        if bio_hits and count:
            reason = f"Tags posts {matched[0]}, and says so in their description"
        elif bio_hits:
            reason = f"Description mentions {bio_hits[0]}"
        elif count > 1:
            reason = f"Tagged {matched[0]} {count} times recently"
        else:
            reason = f"Tagged a post {matched[0]}"

        out.append(
            SuggestedBlog(
                name=blog_name,
                title=str(info.get("title") or tumblr.short_name(blog_name)),
                url=str(info.get("url") or tumblr.blog_url(blog_name)),
                avatar=tumblr.avatar_url(blog_name),
                description=description,
                posts=count,
                reason=reason,
                matched=matched,
                bioMatch=bool(bio_hits),
            )
        )

    # Description match first, then how much they actually post about it, then how many
    # distinct keywords they hit. Post count on the blog is deliberately not a factor: a
    # small blog tagging your subject weekly is a better follow than a huge one that
    # tagged it once.
    out.sort(key=lambda b: (b.bioMatch, b.posts, len(b.matched)), reverse=True)
    return SuggestedFollowsOut(
        niche=name, keywords=probe, blogs=out[: max(1, min(body.limit, 50))]
    )
