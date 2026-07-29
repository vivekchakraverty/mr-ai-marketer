"""Public Bluesky performance analytics and comparable-account cohorts."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/bluesky-analytics", tags=["bluesky-analytics"])

MAX_DISCOVERY = 50
MAX_FEED_POSTS = 50
MAX_POST_AGE_DAYS = 45
SYNC_INTERVAL_SECONDS = 6 * 60 * 60


class AnalyticsStatus(BaseModel):
    configured: bool
    handle: str | None = None
    cohortCount: int = 0
    trackedPosts: int = 0
    lastSyncedAt: str | None = None


class CohortAccount(BaseModel):
    did: str
    handle: str
    displayName: str
    followers: int
    niche: str
    source: str
    isOwner: bool = False


class CohortResponse(BaseModel):
    accounts: list[CohortAccount]
    owner: CohortAccount | None = None


class AddAccountRequest(BaseModel):
    actor: str = Field(min_length=2, max_length=256)
    niche: str = Field(min_length=2, max_length=120)
    source: str = "selected"


class RemoveAccountRequest(BaseModel):
    did: str = Field(min_length=5, max_length=256)


class DiscoverRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=120)
    followerMin: int = Field(default=0, ge=0, le=100_000_000)
    followerMax: int = Field(default=100_000_000, ge=0, le=100_000_000)
    limit: int = Field(default=20, ge=1, le=MAX_DISCOVERY)


class DiscoveredAccount(BaseModel):
    did: str
    handle: str
    displayName: str
    followers: int
    niche: str
    matchedPosts: int
    sampleText: str
    samplePostUri: str


class DiscoveryResponse(BaseModel):
    accounts: list[DiscoveredAccount]


class SyncRequest(BaseModel):
    niche: str = Field(min_length=2, max_length=120)


class SyncResponse(BaseModel):
    ok: bool = True
    accounts: int
    posts: int
    snapshots: int
    syncedAt: str


class DashboardResponse(BaseModel):
    summary: dict[str, Any]
    posts: list[dict[str, Any]]


def _spg() -> tuple[Any, Any]:
    from vendor.socialpost.src import bluesky as spg_bluesky
    from vendor.socialpost.src import config as spg_config

    return spg_bluesky, spg_config


def _configured() -> bool:
    _, config = _spg()
    return config.is_set("BLUESKY_HANDLE") and config.is_set("BLUESKY_APP_PASSWORD")


def _client() -> Any:
    spg_bluesky, _ = _spg()
    return spg_bluesky.get_client()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _call(method: Any, params: dict[str, Any]) -> Any:
    spg_bluesky, _ = _spg()
    return spg_bluesky.with_backoff(method, params)


def _profile_row(profile: Any, niche: str, source: str, is_owner: bool, now: str) -> dict[str, Any]:
    return {
        "did": profile.did,
        "handle": profile.handle,
        "display_name": getattr(profile, "display_name", None) or profile.handle,
        "followers": int(getattr(profile, "followers_count", 0) or 0),
        "niche": niche.strip(),
        "source": source,
        "active": 1,
        "is_owner": 1 if is_owner else 0,
        "created_at": now,
        "updated_at": now,
    }


def _upsert_account(profile: Any, niche: str, source: str, is_owner: bool) -> None:
    now = _iso(_now())
    row = _profile_row(profile, niche, source, is_owner, now)
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO bluesky_analytics_accounts
                (did, handle, display_name, followers, niche, source, active, is_owner, created_at, updated_at)
            VALUES (:did, :handle, :display_name, :followers, :niche, :source, :active, :is_owner, :created_at, :updated_at)
            ON CONFLICT(did) DO UPDATE SET
                handle=excluded.handle,
                display_name=excluded.display_name,
                followers=excluded.followers,
                niche=excluded.niche,
                source=excluded.source,
                active=excluded.active,
                is_owner=excluded.is_owner,
                updated_at=excluded.updated_at
            """,
            row,
        )


def _account_out(row: Any) -> CohortAccount:
    return CohortAccount(
        did=row["did"],
        handle=row["handle"],
        displayName=row["display_name"],
        followers=int(row["followers"] or 0),
        niche=row["niche"],
        source=row["source"],
        isOwner=bool(row["is_owner"]),
    )


def _post_url(uri: str, handle: str) -> str:
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"


def _post_text(post: Any) -> str:
    return (getattr(getattr(post, "record", None), "text", "") or "").strip()


def _post_created_at(post: Any) -> str | None:
    record = getattr(post, "record", None)
    created = getattr(record, "created_at", None) or getattr(post, "indexed_at", None)
    if isinstance(created, datetime):
        return _iso(created if created.tzinfo else created.replace(tzinfo=timezone.utc))
    if isinstance(created, str):
        parsed = _parse_iso(created)
        return _iso(parsed) if parsed else None
    return None


def _has_media(post: Any) -> bool:
    embed = getattr(post, "embed", None)
    py_type = getattr(embed, "py_type", "") or ""
    return any(marker in py_type for marker in ("images", "video", "gallery", "recordWithMedia"))


def _post_row(post: Any, niche: str) -> dict[str, Any] | None:
    created_at = _post_created_at(post)
    text = _post_text(post)
    author = getattr(post, "author", None)
    if not created_at or not author or not getattr(post, "uri", None):
        return None
    return {
        "uri": post.uri,
        "cid": post.cid,
        "author_did": author.did,
        "author_handle": author.handle,
        "text": text,
        "created_at": created_at,
        "niche": niche,
        "is_reply": 1 if getattr(getattr(post, "record", None), "reply", None) else 0,
        "has_media": 1 if _has_media(post) else 0,
    }


def _counts(post: Any) -> tuple[int, int, int, int]:
    return (
        int(getattr(post, "like_count", 0) or 0),
        int(getattr(post, "repost_count", 0) or 0),
        int(getattr(post, "reply_count", 0) or 0),
        int(getattr(post, "quote_count", 0) or 0),
    )


def _write_post_snapshot(row: dict[str, Any], post: Any, followers: int, captured_at: str) -> None:
    likes, reposts, replies, quotes = _counts(post)
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO bluesky_analytics_posts
                (uri, cid, author_did, author_handle, text, created_at, niche, is_reply, has_media)
            VALUES (:uri, :cid, :author_did, :author_handle, :text, :created_at, :niche, :is_reply, :has_media)
            ON CONFLICT(uri) DO UPDATE SET
                cid=excluded.cid,
                author_handle=excluded.author_handle,
                text=excluded.text,
                niche=excluded.niche,
                is_reply=excluded.is_reply,
                has_media=excluded.has_media
            """,
            row,
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO bluesky_analytics_snapshots
                (post_uri, captured_at, likes, reposts, replies, quotes, followers)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (row["uri"], captured_at, likes, reposts, replies, quotes, max(followers, 0)),
        )
        return cursor.rowcount


def _active_account_rows() -> list[dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bluesky_analytics_accounts WHERE active = 1 ORDER BY is_owner DESC, followers DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def _owner_profile(client: Any) -> Any:
    me = getattr(client, "me", None)
    actor = getattr(me, "did", None) or os.environ.get("BLUESKY_HANDLE")
    return _call(client.app.bsky.actor.get_profile, {"actor": actor})


def _sync_account(client: Any, account: dict[str, Any], captured_at: str) -> tuple[int, int]:
    profile = _call(client.app.bsky.actor.get_profile, {"actor": account["did"]})
    _upsert_account(profile, account["niche"], account["source"], bool(account["is_owner"]))
    followers = int(getattr(profile, "followers_count", 0) or 0)
    response = _call(
        client.app.bsky.feed.get_author_feed,
        {"actor": account["did"], "limit": MAX_FEED_POSTS, "filter": "posts_no_replies"},
    )
    posts = 0
    snapshots = 0
    cutoff = _now() - timedelta(days=MAX_POST_AGE_DAYS)
    for item in getattr(response, "feed", None) or []:
        if getattr(item, "reason", None) is not None:
            continue
        post = getattr(item, "post", None)
        row = _post_row(post, account["niche"]) if post else None
        if not row:
            continue
        created = _parse_iso(row["created_at"])
        if not created or created < cutoff:
            continue
        posts += 1
        snapshots += _write_post_snapshot(row, post, followers, captured_at)
    return posts, snapshots


def _sync_now(niche: str | None = None) -> SyncResponse:
    client = _client()
    rows = _active_account_rows()
    owner = next((row for row in rows if row["is_owner"]), None)
    if owner is None:
        profile = _owner_profile(client)
        owner_niche = (niche or "general").strip()
        _upsert_account(profile, owner_niche, "owner", True)
        rows = _active_account_rows()
    elif niche and niche.strip() != owner["niche"]:
        profile = _owner_profile(client)
        _upsert_account(profile, niche.strip(), "owner", True)
        rows = _active_account_rows()

    captured_at = _iso(_now())
    total_posts = 0
    total_snapshots = 0
    synced_accounts = 0
    for account in rows:
        try:
            posts, snapshots = _sync_account(client, account, captured_at)
            total_posts += posts
            total_snapshots += snapshots
            synced_accounts += 1
        except Exception as err:  # noqa: BLE001
            log.warning("[bluesky-analytics] failed to sync @%s: %s", account["handle"], err)
    return SyncResponse(
        accounts=synced_accounts,
        posts=total_posts,
        snapshots=total_snapshots,
        syncedAt=captured_at,
    )


def _median(values: list[float]) -> float:
    return float(median(values)) if values else 0.0


def _percentile(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    return round(100 * sum(1 for item in values if item <= value) / len(values), 1)


def _dashboard_rows(niche: str, follower_min: int, follower_max: int) -> list[dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, a.display_name, a.followers AS current_followers, a.is_owner,
                   s.captured_at, s.likes, s.reposts, s.replies, s.quotes, s.followers
            FROM bluesky_analytics_posts p
            JOIN bluesky_analytics_accounts a ON a.did = p.author_did AND a.active = 1
            JOIN bluesky_analytics_snapshots s ON s.post_uri = p.uri
            WHERE (? = '' OR p.niche = ?)
            ORDER BY p.created_at DESC
            """,
            (niche, niche),
        ).fetchall()

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row["uri"], []).append(row)

    now = _now()
    output: list[dict[str, Any]] = []
    for post_rows in grouped.values():
        post = post_rows[0]
        created = _parse_iso(post["created_at"])
        target = created + timedelta(hours=24) if created else now
        row = min(
            post_rows,
            key=lambda candidate: abs(
                ((_parse_iso(candidate["captured_at"]) or now) - target).total_seconds()
            ),
        )
        followers = int(row["followers"] or row["current_followers"] or 0)
        if not row["is_owner"] and not (follower_min <= followers <= follower_max):
            continue
        engagement = int(row["likes"] or 0) + int(row["reposts"] or 0) + int(row["replies"] or 0) + int(row["quotes"] or 0)
        age_hours = max(0.0, (now - created).total_seconds() / 3600) if created else 0.0
        snapshot_age = max(
            0.0,
            ((_parse_iso(row["captured_at"]) or now) - created).total_seconds() / 3600,
        ) if created else 0.0
        output.append(
            {
                "uri": row["uri"],
                "webUrl": _post_url(row["uri"], row["author_handle"]),
                "handle": row["author_handle"],
                "displayName": row["display_name"],
                "text": row["text"],
                "createdAt": row["created_at"],
                "capturedAt": row["captured_at"],
                "snapshotAgeHours": round(snapshot_age, 1),
                "comparisonWindow": "24h" if abs(snapshot_age - 24) <= 6 else "latest",
                "followers": followers,
                "likes": int(row["likes"] or 0),
                "reposts": int(row["reposts"] or 0),
                "replies": int(row["replies"] or 0),
                "quotes": int(row["quotes"] or 0),
                "engagement": engagement,
                "engagementRate": round(100 * engagement / max(followers, 1), 3),
                "ageHours": round(age_hours, 1),
                "hasMedia": bool(row["has_media"]),
                "isOwn": bool(row["is_owner"]),
                "niche": row["niche"],
            }
        )
    return output


@router.get("/status", response_model=AnalyticsStatus)
def status() -> AnalyticsStatus:
    with db._connect() as conn:
        cohort = conn.execute(
            "SELECT COUNT(*) AS n FROM bluesky_analytics_accounts WHERE active = 1 AND is_owner = 0"
        ).fetchone()["n"]
        posts = conn.execute("SELECT COUNT(*) AS n FROM bluesky_analytics_posts").fetchone()["n"]
        last = conn.execute("SELECT MAX(captured_at) AS t FROM bluesky_analytics_snapshots").fetchone()["t"]
    return AnalyticsStatus(
        configured=_configured(),
        handle=os.environ.get("BLUESKY_HANDLE") if _configured() else None,
        cohortCount=int(cohort or 0),
        trackedPosts=int(posts or 0),
        lastSyncedAt=last,
    )


@router.get("/cohort", response_model=CohortResponse)
def cohort() -> CohortResponse:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bluesky_analytics_accounts WHERE active = 1 ORDER BY is_owner DESC, followers DESC"
        ).fetchall()
    accounts = [_account_out(row) for row in rows if not row["is_owner"]]
    owner = next((_account_out(row) for row in rows if row["is_owner"]), None)
    return CohortResponse(accounts=accounts, owner=owner)


@router.post("/cohort/accounts", response_model=CohortAccount)
def add_account(body: AddAccountRequest) -> CohortAccount:
    if not _configured():
        raise HTTPException(status_code=400, detail="Connect Bluesky in Settings first.")
    actor = body.actor.strip().lstrip("@")
    if body.source not in {"selected", "discovered"}:
        raise HTTPException(status_code=400, detail="Invalid cohort source.")
    try:
        profile = _call(_client().app.bsky.actor.get_profile, {"actor": actor})
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not find that Bluesky account: {err}") from None
    _upsert_account(profile, body.niche, body.source, False)
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM bluesky_analytics_accounts WHERE did = ?", (profile.did,)).fetchone()
    return _account_out(row)


@router.delete("/cohort/accounts/{did:path}")
def remove_account(did: str) -> dict[str, bool]:
    with db._connect() as conn:
        conn.execute(
            "UPDATE bluesky_analytics_accounts SET active = 0, updated_at = ? WHERE did = ? AND is_owner = 0",
            (_iso(_now()), did),
        )
    return {"ok": True}


@router.post("/discover", response_model=DiscoveryResponse)
def discover(body: DiscoverRequest) -> DiscoveryResponse:
    if not _configured():
        raise HTTPException(status_code=400, detail="Connect Bluesky in Settings first.")
    if body.followerMin > body.followerMax:
        raise HTTPException(status_code=400, detail="Minimum followers must not exceed maximum followers.")
    try:
        client = _client()
        response = _call(
            client.app.bsky.feed.search_posts,
            {"q": body.niche.strip(), "limit": body.limit, "sort": "latest"},
        )
        posts = getattr(response, "posts", None) or []
        dids = list(dict.fromkeys(post.author.did for post in posts if getattr(post, "author", None)))
        profiles_response = _call(client.app.bsky.actor.get_profiles, {"actors": dids}) if dids else None
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Bluesky discovery failed: {err}") from None

    profiles = {profile.did: profile for profile in (getattr(profiles_response, "profiles", None) or [])}
    candidates: dict[str, dict[str, Any]] = {}
    for post in posts:
        author = getattr(post, "author", None)
        profile = profiles.get(getattr(author, "did", None))
        if not profile:
            continue
        followers = int(getattr(profile, "followers_count", 0) or 0)
        if not body.followerMin <= followers <= body.followerMax:
            continue
        candidate = candidates.setdefault(
            profile.did,
            {
                "did": profile.did,
                "handle": profile.handle,
                "displayName": getattr(profile, "display_name", None) or profile.handle,
                "followers": followers,
                "niche": body.niche.strip(),
                "matchedPosts": 0,
                "sampleText": _post_text(post)[:240],
                "samplePostUri": post.uri,
            },
        )
        candidate["matchedPosts"] += 1
    return DiscoveryResponse(
        accounts=sorted(candidates.values(), key=lambda item: (-item["matchedPosts"], -item["followers"]))
    )


@router.post("/sync", response_model=SyncResponse)
def sync(body: SyncRequest) -> SyncResponse:
    if not _configured():
        raise HTTPException(status_code=400, detail="Connect Bluesky in Settings first.")
    try:
        return _sync_now(body.niche.strip())
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Bluesky sync failed: {err}") from None


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    niche: str = "",
    followerMin: int = 0,
    followerMax: int = 100_000_000,
    limit: int = 40,
) -> DashboardResponse:
    if followerMin < 0 or followerMax < followerMin:
        raise HTTPException(status_code=400, detail="Invalid follower range.")
    rows = _dashboard_rows(niche.strip(), followerMin, followerMax)
    mine = [row for row in rows if row["isOwn"]]
    cohort_rows = [row for row in rows if not row["isOwn"]]
    cohort_rates = [row["engagementRate"] for row in cohort_rows]
    for row in rows:
        peers = [
            candidate["engagementRate"]
            for candidate in cohort_rows
            if candidate["niche"] == row["niche"]
            and max(followerMin, int(row["followers"] * 0.5)) <= candidate["followers"] <= min(followerMax, max(1, int(row["followers"] * 2)))
        ]
        peers = peers or cohort_rates
        row["benchmarkMedianRate"] = round(_median(peers), 3)
        row["percentile"] = _percentile(row["engagementRate"], peers)

    mine_rates = [row["engagementRate"] for row in mine]
    summary = {
        "minePosts": len(mine),
        "cohortPosts": len(cohort_rows),
        "cohortAccounts": len({row["handle"] for row in cohort_rows}),
        "mineMedianRate": round(_median(mine_rates), 3),
        "cohortMedianRate": round(_median(cohort_rates), 3),
        "mineMedianEngagement": round(_median([row["engagement"] for row in mine]), 1),
        "cohortMedianEngagement": round(_median([row["engagement"] for row in cohort_rows]), 1),
        "lastSyncedAt": max((row["capturedAt"] for row in rows), default=None),
        "niche": niche.strip(),
        "followerMin": followerMin,
        "followerMax": followerMax,
    }
    rows.sort(key=lambda row: (not row["isOwn"], -row["engagementRate"], row["createdAt"]), reverse=False)
    return DashboardResponse(summary=summary, posts=rows[: max(1, min(limit, 100))])


_scheduler_thread: threading.Thread | None = None


def _scheduler_loop() -> None:
    # The desktop app may be closed for days; sync resumes on the next launch.
    time.sleep(300)
    while True:
        try:
            if _configured() and _active_account_rows():
                _sync_now()
        except Exception:  # noqa: BLE001
            log.exception("[bluesky-analytics] scheduled sync failed")
        time.sleep(SYNC_INTERVAL_SECONDS)


def start_scheduler() -> None:
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    _scheduler_thread = threading.Thread(target=_scheduler_loop, name="bluesky-analytics-scheduler", daemon=True)
    _scheduler_thread.start()
