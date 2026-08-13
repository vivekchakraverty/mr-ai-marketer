"""Engage - the user's own Bluesky feeds and feed-item actions.

Reuses the Bluesky credentials already stored for the Social Post Generator
(BLUESKY_HANDLE / BLUESKY_APP_PASSWORD) rather than asking the user to connect a
second time. Same account, same login, same cached client.
"""

from __future__ import annotations

import os
import re
from typing import Any

from atproto import AtUri, models
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/engage", tags=["engage"])


def _spg() -> tuple[Any, Any]:
    from vendor.socialpost.src import bluesky as spg_bluesky
    from vendor.socialpost.src import config as spg_config

    return spg_bluesky, spg_config


class SuggestedAccount(BaseModel):
    did: str
    handle: str
    displayName: str
    description: str
    avatar: str | None = None
    followers: int
    # Why this account is being suggested, in the user's words rather than a score.
    reason: str
    # Keywords that matched, so the reason can be checked rather than trusted.
    matched: list[str]
    posts: int
    bioMatch: bool


class SuggestedFollowsResponse(BaseModel):
    niche: str
    keywords: list[str]
    accounts: list[SuggestedAccount]
    note: str = ""


class EngageStatus(BaseModel):
    configured: bool
    handle: str | None = None


class MediaOut(BaseModel):
    """One piece of media on a post, in the shape both feeds share.

    Deliberately the same field names the Mastodon panel already emits
    (routers/mastodon_engage.py::MediaOut) so one frontend component can render
    either network's posts without a per-network adapter. `kind` is the union of
    both vocabularies; `isHls` is the one genuinely Bluesky-specific bit.
    """

    kind: str  # image | video | gifv | audio | link | unknown
    url: str = ""  # full-size image, or the video source
    previewUrl: str = ""  # thumbnail / poster frame
    description: str = ""  # alt text
    # Bluesky serves video as an HLS playlist, which Chromium cannot play from a
    # plain <video src>. The frontend attaches hls.js when this is set.
    isHls: bool = False
    aspectRatio: float | None = None
    # Link cards only.
    title: str = ""
    domain: str = ""


class FeedPost(BaseModel):
    uri: str
    cid: str
    webUrl: str
    isPost: bool
    isOwnPost: bool
    authorDid: str
    authorHandle: str
    authorName: str
    authorAvatar: str | None = None
    text: str
    createdAt: str
    likes: int
    reposts: int
    replies: int
    quotes: int
    bookmarks: int
    reason: str | None = None
    reasonSubject: str | None = None
    isRead: bool | None = None
    viewerLike: str | None = None
    viewerRepost: str | None = None
    viewerBookmarked: bool = False
    viewerThreadMuted: bool = False
    viewerReplyDisabled: bool = False
    authorFollowing: str | None = None
    authorFollowedBy: str | None = None
    authorMuted: bool = False
    authorBlocking: str | None = None
    authorBlockedBy: bool = False
    media: list[MediaOut] = []


class FeedResponse(BaseModel):
    posts: list[FeedPost]


class ActorState(BaseModel):
    authorDid: str
    authorFollowing: str | None = None
    authorFollowedBy: str | None = None
    authorMuted: bool = False
    authorBlocking: str | None = None
    authorBlockedBy: bool = False


class ActionResponse(BaseModel):
    ok: bool = True
    post: FeedPost | None = None
    actor: ActorState | None = None
    createdUri: str | None = None
    createdCid: str | None = None


class ComposeRequest(BaseModel):
    text: str


class TargetRequest(BaseModel):
    uri: str
    cid: str | None = None


class TogglePostRequest(TargetRequest):
    enabled: bool
    recordUri: str | None = None


class TextTargetRequest(TargetRequest):
    text: str


class ActorTargetRequest(BaseModel):
    did: str
    enabled: bool
    recordUri: str | None = None


@router.get("/status", response_model=EngageStatus)
def status() -> EngageStatus:
    _, spg_config = _spg()
    if not (spg_config.is_set("BLUESKY_HANDLE") and spg_config.is_set("BLUESKY_APP_PASSWORD")):
        return EngageStatus(configured=False)
    return EngageStatus(configured=True, handle=os.environ.get("BLUESKY_HANDLE"))


def _client() -> Any:
    spg_bluesky, _ = _spg()
    return spg_bluesky.get_client()


def _as_api_error(err: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(err))


def _clean_text(text: str) -> str:
    value = text.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Text is required.")
    return value


def _me_did(client: Any) -> str | None:
    me = getattr(client, "me", None)
    return getattr(me, "did", None)


def _author_fields(author: Any) -> dict[str, str | bool | None]:
    viewer = getattr(author, "viewer", None)
    return {
        "authorDid": author.did,
        "authorHandle": author.handle,
        "authorName": author.display_name or author.handle,
        "authorAvatar": getattr(author, "avatar", None),
        "authorFollowing": getattr(viewer, "following", None),
        "authorFollowedBy": getattr(viewer, "followed_by", None),
        "authorMuted": bool(getattr(viewer, "muted", False)),
        "authorBlocking": getattr(viewer, "blocking", None),
        "authorBlockedBy": bool(getattr(viewer, "blocked_by", False)),
    }


def _web_post_url(uri: str, handle: str) -> str:
    try:
        parsed = AtUri.from_str(uri)
    except Exception:  # noqa: BLE001
        return f"https://bsky.app/profile/{handle}"
    if parsed.collection != "app.bsky.feed.post" or not parsed.rkey:
        return f"https://bsky.app/profile/{handle}"
    return f"https://bsky.app/profile/{handle}/post/{parsed.rkey}"


def _is_feed_post_record(record: Any) -> bool:
    return getattr(record, "py_type", None) == "app.bsky.feed.post" or hasattr(record, "text")


def _viewer_flags(post: Any) -> dict[str, str | bool | None]:
    viewer = getattr(post, "viewer", None)
    return {
        "viewerLike": getattr(viewer, "like", None),
        "viewerRepost": getattr(viewer, "repost", None),
        "viewerBookmarked": bool(getattr(viewer, "bookmarked", False)),
        "viewerThreadMuted": bool(getattr(viewer, "thread_muted", False)),
        "viewerReplyDisabled": bool(getattr(viewer, "reply_disabled", False)),
    }


def _aspect_ratio(obj: Any) -> float | None:
    """width/height off an atproto aspectRatio, or None if absent or degenerate.

    Sent so the frontend can reserve the right box before the image loads, which
    is what stops a feed from jumping around as thumbnails arrive.
    """
    ratio = getattr(obj, "aspect_ratio", None) or getattr(obj, "aspectRatio", None)
    width = getattr(ratio, "width", 0) or 0
    height = getattr(ratio, "height", 0) or 0
    if width > 0 and height > 0:
        return round(width / height, 4)
    return None


def _embed_media(embed: Any) -> list[MediaOut]:
    """Media out of a hydrated Bluesky embed view.

    Dispatches on which attributes the view carries rather than on its `$type`
    string. The atproto package has renamed and re-namespaced these view models
    more than once; the shapes themselves have been stable, so duck-typing
    survives upgrades that a string match would not.

    Handles the four embeds that carry viewable media. A bare quote post
    (`record#view` with no media of its own) yields nothing here — the quoted
    post's own text is already shown by the client, and recursing into it would
    put someone else's images in the middle of this post's card.
    """
    if embed is None:
        return []

    # recordWithMedia: a quote post that also carries its own images or video.
    # The media hangs off `.media`; unwrap and treat it as a plain embed.
    nested = getattr(embed, "media", None)
    if nested is not None:
        return _embed_media(nested)

    # images#view
    images = getattr(embed, "images", None)
    if isinstance(images, (list, tuple)):
        return [
            MediaOut(
                kind="image",
                url=str(getattr(img, "fullsize", "") or getattr(img, "thumb", "") or ""),
                previewUrl=str(getattr(img, "thumb", "") or getattr(img, "fullsize", "") or ""),
                description=str(getattr(img, "alt", "") or ""),
                aspectRatio=_aspect_ratio(img),
            )
            for img in images
        ]

    # video#view — `playlist` is an HLS manifest, not a playable file URL.
    playlist = getattr(embed, "playlist", None)
    if playlist:
        return [
            MediaOut(
                kind="video",
                url=str(playlist),
                previewUrl=str(getattr(embed, "thumbnail", "") or ""),
                description=str(getattr(embed, "alt", "") or ""),
                isHls=True,
                aspectRatio=_aspect_ratio(embed),
            )
        ]

    # external#view — a link preview card.
    external = getattr(embed, "external", None)
    if external is not None:
        uri = str(getattr(external, "uri", "") or "")
        domain = ""
        if uri:
            from urllib.parse import urlparse

            domain = (urlparse(uri).hostname or "").removeprefix("www.")
        return [
            MediaOut(
                kind="link",
                url=uri,
                previewUrl=str(getattr(external, "thumb", "") or ""),
                title=str(getattr(external, "title", "") or ""),
                description=str(getattr(external, "description", "") or ""),
                domain=domain,
            )
        ]

    return []


def _post_view_to_feed_post(
    post: Any,
    *,
    reason: str | None = None,
    reason_subject: str | None = None,
    is_read: bool | None = None,
    me_did: str | None = None,
) -> FeedPost:
    record = getattr(post, "record", None)
    author = post.author
    return FeedPost(
        uri=post.uri,
        cid=post.cid,
        webUrl=_web_post_url(post.uri, author.handle),
        isPost=True,
        isOwnPost=bool(me_did and author.did == me_did),
        text=getattr(record, "text", "") or "",
        createdAt=post.indexed_at,
        likes=post.like_count or 0,
        reposts=post.repost_count or 0,
        replies=post.reply_count or 0,
        quotes=post.quote_count or 0,
        bookmarks=post.bookmark_count or 0,
        reason=reason,
        reasonSubject=reason_subject,
        isRead=is_read,
        media=_embed_media(getattr(post, "embed", None)),
        **_viewer_flags(post),
        **_author_fields(author),
    )


def _notification_to_feed_post(notification: Any, me_did: str | None = None) -> FeedPost:
    """A notification as a feed card.

    No media: a notification carries the raw *record*, not a hydrated post view,
    so any embed on it holds blob references rather than CDN URLs. Rendering them
    would mean an extra getPosts round-trip per notification. The card links to
    the post, which is where someone goes to see the picture anyway.
    """
    record = getattr(notification, "record", None)
    author = notification.author
    is_post = _is_feed_post_record(record)
    return FeedPost(
        uri=notification.uri,
        cid=notification.cid,
        webUrl=_web_post_url(notification.uri, author.handle),
        isPost=is_post,
        isOwnPost=bool(me_did and author.did == me_did),
        text=(getattr(record, "text", "") or "") if is_post else "",
        createdAt=notification.indexed_at,
        likes=0,
        reposts=0,
        replies=0,
        quotes=0,
        bookmarks=0,
        reason=notification.reason,
        reasonSubject=getattr(notification, "reason_subject", None),
        isRead=notification.is_read,
        **_author_fields(author),
    )


def _get_post_view(client: Any, uri: str) -> Any:
    resp = client.app.bsky.feed.get_posts({"uris": [uri]})
    posts = resp.posts or []
    if not posts:
        raise HTTPException(status_code=404, detail="Post was not found on Bluesky.")
    return posts[0]


def _get_feed_post(client: Any, uri: str) -> FeedPost:
    return _post_view_to_feed_post(_get_post_view(client, uri), me_did=_me_did(client))


def _strong_ref(client: Any, uri: str, cid: str | None = None) -> Any:
    if not cid:
        cid = _get_post_view(client, uri).cid
    return models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)


def _reply_ref(client: Any, uri: str, cid: str | None = None) -> Any:
    parent = _strong_ref(client, uri, cid)
    root = parent
    record = getattr(_get_post_view(client, uri), "record", None)
    reply = getattr(record, "reply", None)
    if reply and getattr(reply, "root", None):
        root = reply.root
    return models.AppBskyFeedPost.ReplyRef(parent=parent, root=root)


def _thread_root_uri(client: Any, uri: str) -> str:
    record = getattr(_get_post_view(client, uri), "record", None)
    reply = getattr(record, "reply", None)
    root = getattr(reply, "root", None)
    return getattr(root, "uri", None) or uri


def _actor_state(profile: Any) -> ActorState:
    viewer = getattr(profile, "viewer", None)
    return ActorState(
        authorDid=profile.did,
        authorFollowing=getattr(viewer, "following", None),
        authorFollowedBy=getattr(viewer, "followed_by", None),
        authorMuted=bool(getattr(viewer, "muted", False)),
        authorBlocking=getattr(viewer, "blocking", None),
        authorBlockedBy=bool(getattr(viewer, "blocked_by", False)),
    )


def _record_rkey(record_uri: str) -> tuple[str, str]:
    parsed = AtUri.from_str(record_uri)
    if not parsed.hostname or not parsed.rkey:
        raise HTTPException(status_code=400, detail="Invalid Bluesky record URI.")
    return parsed.hostname, parsed.rkey


def _custom_terms(query: str) -> list[str]:
    """A typed niche, split into search terms.

    Split on commas and newlines only, never spaces: "rust gamedev" is one subject and
    searching for "rust" and "gamedev" separately returns metallurgy and Unity. Someone
    who wants two subjects separates them with a comma, which is also how the niche
    keyword box has always worked.
    """
    parts = [t.strip() for t in re.split(r"[,\n]", query)]
    return [t for t in parts if t][:6]


def _niche_keywords(niche: str) -> tuple[str, list[str]]:
    """The keywords behind a niche, or every niche's if none is named."""
    from vendor.socialpost.src import db as spg_db

    rows = spg_db.list_niches()
    if niche.strip():
        row = next((r for r in rows if r["name"] == niche.strip()), None)
        if row is None:
            raise HTTPException(status_code=400, detail=f"No niche called {niche!r}.")
        return row["name"], [str(k) for k in (row["keywords"] or [])]
    words: list[str] = []
    for r in rows:
        words.extend(str(k) for k in (r["keywords"] or []))
    return "", words


@router.get("/suggested-follows", response_model=SuggestedFollowsResponse)
def suggested_follows(niche: str = "", query: str = "", limit: int = 20) -> SuggestedFollowsResponse:
    """People worth following, found from niche keywords or from a subject typed in.

    `query` is a niche the user typed rather than one they saved: it takes over completely
    when present, so the results are about that subject and nothing else. A saved niche is
    a standing interest, and a typed one is a question being asked right now — mixing them
    would answer neither.

    Two passes, because they answer different questions. Searching posts finds who is
    actually writing about the subject right now; searching actors finds who says they are
    about it in their bio. An account that shows up in both is the strongest signal there
    is, and the ranking says so.

    A bio match is a ranking signal rather than a filter. Plenty of people post constantly
    about a subject and have a bio that is a joke and a city name, and dropping them would
    leave a list of accounts that describe themselves well rather than accounts worth
    reading.

    Anyone already followed, muted, blocked, or the user themselves, is left out — a
    suggestion list whose top entry is someone you followed last week teaches you to stop
    reading it.
    """
    if query.strip():
        name, keywords = "", _custom_terms(query)
        if not keywords:
            return SuggestedFollowsResponse(
                niche="", keywords=[], accounts=[], note="Type a subject to search for."
            )
    else:
        name, keywords = _niche_keywords(niche)
        if not keywords:
            return SuggestedFollowsResponse(
                niche=name,
                keywords=[],
                accounts=[],
                note=(
                    "No niche keywords yet. Add a niche in the Bluesky Post Creator, or "
                    "type a subject above."
                ),
            )

    client = _client()
    me = _me_did(client)
    # Bounded: each keyword is two network calls, and a niche with fifteen keywords would
    # otherwise turn one screen into thirty round trips.
    probe = keywords[:6]

    found: dict[str, dict] = {}

    def note(profile: Any, keyword: str, *, from_post: bool) -> None:
        did = getattr(profile, "did", "") or ""
        if not did or did == me:
            return
        viewer = getattr(profile, "viewer", None)
        if getattr(viewer, "following", None) or getattr(viewer, "muted", False) or getattr(
            viewer, "blocking", None
        ):
            return
        entry = found.setdefault(
            did,
            {
                "profile": profile,
                "matched": set(),
                "posts": 0,
                "bio": False,
            },
        )
        entry["matched"].add(keyword)
        if from_post:
            entry["posts"] += 1
        # Prefer whichever view carries a bio; the enrichment pass below replaces both
        # with a detailed profile anyway, but this keeps the fallback sensible if it fails.
        if getattr(profile, "description", None) and not getattr(entry["profile"], "description", None):
            entry["profile"] = profile

    for keyword in probe:
        try:
            posts = client.app.bsky.feed.search_posts({"q": keyword, "limit": 25})
            for post in getattr(posts, "posts", None) or []:
                note(getattr(post, "author", None), keyword, from_post=True)
        except Exception:  # noqa: BLE001 — one keyword failing must not empty the list
            pass
        try:
            actors = client.app.bsky.actor.search_actors({"q": keyword, "limit": 15})
            for actor in getattr(actors, "actors", None) or []:
                note(actor, keyword, from_post=False)
        except Exception:  # noqa: BLE001
            pass

    # Fill in bios and follower counts before scoring.
    #
    # Neither search surface carries both: an author on a search_posts result has did,
    # handle, display_name and avatar and nothing else, while search_actors returns a
    # ProfileView with a description but no follower count. So without this step an
    # account found by its posts could never match on its bio — the whole second half of
    # the idea — and every follower number would read zero.
    #
    # Enriched in two phases because the budget is limited and the ranking is not known
    # until the bios are in. A provisional pass ranks on what is already available, and
    # only the accounts that could plausibly be shown are fetched in full. get_profiles
    # takes 25 actors per call, so this is two requests rather than one per candidate.
    lowered = [k.lower() for k in probe]

    def bio_of(entry: dict) -> str:
        return (getattr(entry["profile"], "description", "") or "").lower()

    def provisional(did: str) -> tuple:
        entry = found[did]
        blurb = bio_of(entry)
        return (
            any(low in blurb for low in lowered),
            entry["posts"],
            len(entry["matched"]),
        )

    # Capped as well as scaled. The multiplier gives the ranking room to reorder once real
    # bios arrive, but each 25 costs a round trip, and a caller asking for a deep pool to
    # page through locally should not turn one screen into eight requests.
    shortlist = sorted(found, key=provisional, reverse=True)[: min(max(limit * 2, 25), 75)]
    for i in range(0, len(shortlist), 25):
        chunk = shortlist[i : i + 25]
        try:
            detailed = client.app.bsky.actor.get_profiles({"actors": chunk})
            for full in getattr(detailed, "profiles", None) or []:
                did = getattr(full, "did", "") or ""
                if did in found:
                    found[did]["profile"] = full
        except Exception:  # noqa: BLE001 — a failed enrich costs detail, not the list
            pass

    # Anything outside the shortlist was never going to be shown, and carries no bio to
    # judge it on, so it is dropped rather than ranked on missing data.
    found = {did: found[did] for did in shortlist}

    out: list[SuggestedAccount] = []
    for did, entry in found.items():
        profile = entry["profile"]
        description = (getattr(profile, "description", "") or "").strip()
        blurb = description.lower()
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
            SuggestedAccount(
                did=did,
                handle=getattr(profile, "handle", "") or "",
                displayName=(getattr(profile, "display_name", "") or "") or (getattr(profile, "handle", "") or ""),
                description=description,
                avatar=getattr(profile, "avatar", None),
                followers=int(getattr(profile, "followers_count", 0) or 0),
                reason=reason,
                matched=matched,
                posts=posts,
                bioMatch=bool(bio_hits),
            )
        )

    # Bio match first, then how much they actually post about it, then how many distinct
    # keywords they hit. Follower count is deliberately last and never a filter: a small
    # account writing about your subject every day is a better follow than a large one
    # that mentioned it once.
    out.sort(key=lambda a: (a.bioMatch, a.posts, len(a.matched), a.followers), reverse=True)
    return SuggestedFollowsResponse(
        niche=name, keywords=probe, accounts=out[: max(1, min(limit, 50))]
    )


@router.get("/timeline", response_model=FeedResponse)
def timeline(limit: int = 30) -> FeedResponse:
    try:
        client = _client()
        resp = client.app.bsky.feed.get_timeline({"limit": limit})
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None

    me_did = _me_did(client)
    return FeedResponse(posts=[_post_view_to_feed_post(item.post, me_did=me_did) for item in resp.feed])


@router.get("/notifications", response_model=FeedResponse)
def notifications(limit: int = 30) -> FeedResponse:
    try:
        client = _client()
        resp = client.app.bsky.notification.list_notifications({"limit": limit})
        post_uris = [n.uri for n in resp.notifications if _is_feed_post_record(getattr(n, "record", None))]
        post_views = {}
        if post_uris:
            posts_resp = client.app.bsky.feed.get_posts({"uris": post_uris})
            post_views = {p.uri: p for p in posts_resp.posts or []}
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None

    me_did = _me_did(client)
    posts = []
    for n in resp.notifications:
        if n.uri in post_views:
            posts.append(
                _post_view_to_feed_post(
                    post_views[n.uri],
                    reason=n.reason,
                    reason_subject=getattr(n, "reason_subject", None),
                    is_read=n.is_read,
                    me_did=me_did,
                )
            )
        else:
            posts.append(_notification_to_feed_post(n, me_did=me_did))
    return FeedResponse(posts=posts)


@router.post("/post", response_model=ActionResponse)
def create_post(body: ComposeRequest) -> ActionResponse:
    try:
        client = _client()
        created = client.send_post(_clean_text(body.text))
        return ActionResponse(createdUri=created.uri, createdCid=created.cid, post=_get_feed_post(client, created.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/reply", response_model=ActionResponse)
def reply(body: TextTargetRequest) -> ActionResponse:
    try:
        client = _client()
        created = client.send_post(_clean_text(body.text), reply_to=_reply_ref(client, body.uri, body.cid))
        return ActionResponse(createdUri=created.uri, createdCid=created.cid, post=_get_feed_post(client, body.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/quote", response_model=ActionResponse)
def quote(body: TextTargetRequest) -> ActionResponse:
    try:
        client = _client()
        embed = models.AppBskyEmbedRecord.Main(record=_strong_ref(client, body.uri, body.cid))
        created = client.send_post(_clean_text(body.text), embed=embed)
        return ActionResponse(createdUri=created.uri, createdCid=created.cid, post=_get_feed_post(client, body.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/like", response_model=ActionResponse)
def like(body: TogglePostRequest) -> ActionResponse:
    try:
        client = _client()
        current = _get_post_view(client, body.uri)
        viewer_like = body.recordUri or getattr(getattr(current, "viewer", None), "like", None)
        if body.enabled and not viewer_like:
            client.like(body.uri, body.cid or current.cid)
        elif not body.enabled and viewer_like:
            client.unlike(viewer_like)
        return ActionResponse(post=_get_feed_post(client, body.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/repost", response_model=ActionResponse)
def repost(body: TogglePostRequest) -> ActionResponse:
    try:
        client = _client()
        current = _get_post_view(client, body.uri)
        viewer_repost = body.recordUri or getattr(getattr(current, "viewer", None), "repost", None)
        if body.enabled and not viewer_repost:
            client.repost(body.uri, body.cid or current.cid)
        elif not body.enabled and viewer_repost:
            client.unrepost(viewer_repost)
        return ActionResponse(post=_get_feed_post(client, body.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/bookmark", response_model=ActionResponse)
def bookmark(body: TogglePostRequest) -> ActionResponse:
    try:
        client = _client()
        current = _get_post_view(client, body.uri)
        if body.enabled:
            client.app.bsky.bookmark.create_bookmark({"uri": body.uri, "cid": body.cid or current.cid})
        else:
            client.app.bsky.bookmark.delete_bookmark({"uri": body.uri})
        return ActionResponse(post=_get_feed_post(client, body.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/thread-mute", response_model=ActionResponse)
def thread_mute(body: TogglePostRequest) -> ActionResponse:
    try:
        client = _client()
        root_uri = _thread_root_uri(client, body.uri)
        if body.enabled:
            client.app.bsky.graph.mute_thread({"root": root_uri})
        else:
            client.app.bsky.graph.unmute_thread({"root": root_uri})
        return ActionResponse(post=_get_feed_post(client, body.uri))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/follow", response_model=ActionResponse)
def follow(body: ActorTargetRequest) -> ActionResponse:
    try:
        client = _client()
        profile = client.get_profile(body.did)
        current_follow = body.recordUri or getattr(getattr(profile, "viewer", None), "following", None)
        if body.enabled and not current_follow:
            client.follow(body.did)
        elif not body.enabled and current_follow:
            client.unfollow(current_follow)
        return ActionResponse(actor=_actor_state(client.get_profile(body.did)))
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/mute", response_model=ActionResponse)
def mute(body: ActorTargetRequest) -> ActionResponse:
    try:
        client = _client()
        if body.enabled:
            client.mute(body.did)
        else:
            client.unmute(body.did)
        return ActionResponse(actor=_actor_state(client.get_profile(body.did)))
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/block", response_model=ActionResponse)
def block(body: ActorTargetRequest) -> ActionResponse:
    try:
        client = _client()
        me = _me_did(client)
        if not me:
            raise HTTPException(status_code=401, detail="Bluesky login is required.")
        profile = client.get_profile(body.did)
        current_block = body.recordUri or getattr(getattr(profile, "viewer", None), "blocking", None)
        if body.enabled and not current_block:
            record = models.AppBskyGraphBlock.Record(created_at=client.get_current_time_iso(), subject=body.did)
            client.app.bsky.graph.block.create(me, record)
        elif not body.enabled and current_block:
            repo, rkey = _record_rkey(current_block)
            client.app.bsky.graph.block.delete(repo, rkey)
        return ActionResponse(actor=_actor_state(client.get_profile(body.did)))
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/delete-post", response_model=ActionResponse)
def delete_post(body: TargetRequest) -> ActionResponse:
    try:
        client = _client()
        current = _get_post_view(client, body.uri)
        if current.author.did != _me_did(client):
            raise HTTPException(status_code=403, detail="Only your own Bluesky posts can be deleted.")
        client.delete_post(body.uri)
        return ActionResponse(ok=True)
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None


@router.post("/notifications/read", response_model=ActionResponse)
def mark_notifications_read() -> ActionResponse:
    try:
        client = _client()
        client.app.bsky.notification.update_seen({"seen_at": client.get_current_time_iso()})
        return ActionResponse(ok=True)
    except Exception as err:  # noqa: BLE001
        raise _as_api_error(err) from None
