"""Engage - the user's own Bluesky feeds and feed-item actions.

Reuses the Bluesky credentials already stored for the Social Post Generator
(BLUESKY_HANDLE / BLUESKY_APP_PASSWORD) rather than asking the user to connect a
second time. Same account, same login, same cached client.
"""

from __future__ import annotations

import os
from typing import Any

from atproto import AtUri, models
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/engage", tags=["engage"])


def _spg() -> tuple[Any, Any]:
    from vendor.socialpost.src import bluesky as spg_bluesky
    from vendor.socialpost.src import config as spg_config

    return spg_bluesky, spg_config


class EngageStatus(BaseModel):
    configured: bool
    handle: str | None = None


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
        **_viewer_flags(post),
        **_author_fields(author),
    )


def _notification_to_feed_post(notification: Any, me_did: str | None = None) -> FeedPost:
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
