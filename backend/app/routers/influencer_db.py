"""Influencer Database — a browsable, filterable view over the bundled Instagram
influencer catalogue (app/data/influencer_database.xlsx).

The xlsx is a static asset shipped with the app, so it's parsed once into a pandas
DataFrame and every request filters that in memory. Loading is lazy (first request,
not startup) and guarded by a lock: the parse costs a couple of seconds, and the
rest of the app shouldn't wait on a tool the user may never open.

Roughly a third of the catalogue was never successfully enriched (no follower /
post counts). Those rows are kept — they still carry name, handle and contact
details — but flagged `hasStats: false` so the UI can hide them by default, which
is what `withStatsOnly` does.
"""
from __future__ import annotations

import csv
import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..services import hf_assets

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/influencer-db", tags=["influencer-db"])

# The catalogue is fetched from Hugging Face on first use rather than shipped in the build
# (services/hf_assets.py). This path stays as the dev-checkout fallback.
XLSX_PATH = Path(__file__).resolve().parent.parent / "data" / "influencer_database.xlsx"
SHEET_NAME = "Sheet1"

# Niche is blank for rows the enrichment pass never reached; "Uncategorized" is the
# classifier's own "nothing matched" verdict. They mean different things to someone
# browsing, so they stay separate labels.
UNKNOWN_NICHE = "Unknown"

_COLUMN_MAP = {
    "Influencer me": "name",
    "Email": "email",
    "Mobile": "mobile",
    "Youtube Id": "youtubeId",
    "Bio": "bio",
    "Follower_Count": "followers",
    "Following_Count": "following",
    "Posts_Count": "posts",
    "Niche": "niche",
    "Niche_Source": "nicheSource",
    "Normalized_Instagram_Handle": "handle",
    "Full_Name": "fullName",
    "Last_Post_Date": "lastPostDate",
    "Profile_URL": "profileUrl",
    "Is_Private": "isPrivate",
    "Is_Verified": "isVerified",
}

_TEXT_COLUMNS = ["name", "email", "mobile", "youtubeId", "bio", "niche", "nicheSource", "handle", "fullName", "lastPostDate", "profileUrl"]

SORT_OPTIONS = {
    "followers_desc": ("followers", False),
    "followers_asc": ("followers", True),
    "posts_desc": ("posts", False),
    "posts_asc": ("posts", True),
    "lastpost_desc": ("lastPostDate", False),
    "name_asc": ("_name_sort", True),
}

_df: pd.DataFrame | None = None
_load_lock = threading.Lock()


def _load() -> pd.DataFrame:
    """Parse the xlsx into the shape the API serves. Called once, under _load_lock."""
    # read_only keeps openpyxl from materializing styling for ~14.6k x 25 cells.
    path = hf_assets.path_for("influencers")
    raw = pd.read_excel(path, sheet_name=SHEET_NAME, engine_kwargs={"read_only": True})
    df = raw.rename(columns=_COLUMN_MAP)
    df = df[[c for c in _COLUMN_MAP.values() if c in df.columns]].copy()

    for col in _TEXT_COLUMNS:
        df[col] = df[col].fillna("").astype(str).str.strip()

    for col in ("followers", "following", "posts"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ("isPrivate", "isVerified"):
        df[col] = df[col].fillna(0).astype(bool)

    # Mobile is all digits, so pandas reads it as a float column and astype(str) above
    # renders each number as "9321699213.0" — trim the decimal tail it grew in transit.
    df["mobile"] = df["mobile"].str.replace(r"\.0$", "", regex=True)

    df["handle"] = df["handle"].str.lower().str.lstrip("@")
    df["niche"] = df["niche"].replace("", UNKNOWN_NICHE)
    df["hasStats"] = df["followers"].notna()

    # The source sheet has one row per influencer *per subcategory*, so ~530 handles
    # repeat. Keep the most complete row for each: enriched first, then the highest
    # follower count. Handle-less rows (never matched to an Instagram account) can't
    # be deduped this way, so they're all kept.
    df = df.sort_values(["hasStats", "followers"], ascending=[False, False], na_position="last")
    keyed = df["handle"] != ""
    df = pd.concat([df[keyed].drop_duplicates(subset="handle", keep="first"), df[~keyed]], ignore_index=True)

    # One lowercase haystack per row so the free-text filter is a single vectorized
    # `str.contains` rather than four ORed passes.
    df["_search"] = (df["name"] + " " + df["fullName"] + " " + df["handle"] + " " + df["bio"] + " " + df["niche"]).str.lower()
    df["_name_sort"] = df["name"].str.lower()

    logger.info("Influencer database loaded: %d profiles (%d enriched)", len(df), int(df["hasStats"].sum()))
    return df


def _data() -> pd.DataFrame:
    global _df
    if _df is None:
        with _load_lock:
            if _df is None:
                if not XLSX_PATH.exists():
                    raise HTTPException(status_code=500, detail=f"Influencer database file is missing: {XLSX_PATH}")
                try:
                    _df = _load()
                except HTTPException:
                    raise
                except Exception as err:  # noqa: BLE001 — surface a parse failure as a normal API error
                    raise HTTPException(status_code=500, detail=f"Couldn't read the influencer database: {err}") from err
    return _df


def _num(value) -> int | None:
    """NaN/None -> None, everything else -> int (JSON has no NaN)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return int(value)


def _row_to_dict(row) -> dict:
    return {
        "name": row.name_,
        "fullName": row.fullName,
        "handle": row.handle,
        "profileUrl": row.profileUrl or (f"https://www.instagram.com/{row.handle}/" if row.handle else ""),
        "bio": row.bio,
        "email": row.email,
        "mobile": row.mobile,
        "youtubeId": row.youtubeId,
        "followers": _num(row.followers),
        "following": _num(row.following),
        "posts": _num(row.posts),
        "niche": row.niche,
        "nicheSource": row.nicheSource,
        "isVerified": bool(row.isVerified),
        "isPrivate": bool(row.isPrivate),
        "lastPostDate": row.lastPostDate,
        "hasStats": bool(row.hasStats),
    }


def _rows(frame: pd.DataFrame) -> list[dict]:
    # itertuples shadows the real "name" column with the index name, hence name_.
    frame = frame.rename(columns={"name": "name_"})
    return [_row_to_dict(r) for r in frame.itertuples(index=False)]


class Facets(BaseModel):
    total: int
    withStats: int
    niches: list[dict]
    maxFollowers: int
    maxPosts: int


@router.get("/facets", response_model=Facets)
def facets() -> Facets:
    """Filter vocabulary for the UI: every niche with its profile count, plus the
    upper bounds of the follower/post ranges."""
    df = _data()
    counts = df["niche"].value_counts()
    # Real niches first (by size); the two catch-all buckets sink to the bottom
    # regardless of how large they are, since nobody filters *for* them first.
    catch_all = {UNKNOWN_NICHE, "Uncategorized"}
    niches = sorted(
        ({"value": str(k), "count": int(v)} for k, v in counts.items()),
        key=lambda n: (n["value"] in catch_all, -n["count"]),
    )
    return Facets(
        total=len(df),
        withStats=int(df["hasStats"].sum()),
        niches=niches,
        maxFollowers=int(df["followers"].max(skipna=True) or 0),
        maxPosts=int(df["posts"].max(skipna=True) or 0),
    )


class SearchRequest(BaseModel):
    query: str = ""
    niches: list[str] = []
    followerMin: int | None = None
    followerMax: int | None = None
    postsMin: int | None = None
    postsMax: int | None = None
    verifiedOnly: bool = False
    excludePrivate: bool = False
    withContactOnly: bool = False
    withStatsOnly: bool = True
    sort: str = "followers_desc"
    page: int = 1
    pageSize: int = 50


class SearchResponse(BaseModel):
    total: int
    page: int
    pageSize: int
    rows: list[dict]
    totalFollowers: int
    medianFollowers: int


def _filtered(body: SearchRequest) -> pd.DataFrame:
    df = _data()
    mask = pd.Series(True, index=df.index)

    query = body.query.strip().lower()
    if query:
        mask &= df["_search"].str.contains(query, regex=False, na=False)
    if body.niches:
        mask &= df["niche"].isin(body.niches)
    # A range bound on a metric implies the metric must exist, so rows that were
    # never enriched (NaN) drop out on their own — comparisons against NaN are False.
    if body.followerMin is not None:
        mask &= df["followers"] >= body.followerMin
    if body.followerMax is not None:
        mask &= df["followers"] <= body.followerMax
    if body.postsMin is not None:
        mask &= df["posts"] >= body.postsMin
    if body.postsMax is not None:
        mask &= df["posts"] <= body.postsMax
    if body.verifiedOnly:
        mask &= df["isVerified"]
    if body.excludePrivate:
        mask &= ~df["isPrivate"]
    if body.withContactOnly:
        mask &= (df["email"] != "") | (df["mobile"] != "")
    if body.withStatsOnly:
        mask &= df["hasStats"]

    out = df[mask]
    column, ascending = SORT_OPTIONS.get(body.sort, SORT_OPTIONS["followers_desc"])
    return out.sort_values(column, ascending=ascending, na_position="last")


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest) -> SearchResponse:
    matched = _filtered(body)

    page_size = max(1, min(body.pageSize, 200))
    page = max(1, body.page)
    start = (page - 1) * page_size
    window = matched.iloc[start : start + page_size]

    followers = matched["followers"].dropna()
    return SearchResponse(
        total=len(matched),
        page=page,
        pageSize=page_size,
        rows=_rows(window),
        totalFollowers=int(followers.sum()) if len(followers) else 0,
        medianFollowers=int(followers.median()) if len(followers) else 0,
    )


class ExportRequest(SearchRequest):
    pass


class ExportResponse(BaseModel):
    path: str
    filename: str
    count: int


_EXPORT_COLUMNS = [
    "name", "fullName", "handle", "profileUrl", "niche", "followers", "posts", "following",
    "isVerified", "isPrivate", "lastPostDate", "email", "mobile", "youtubeId", "bio",
]


@router.post("/export", response_model=ExportResponse)
def export(body: ExportRequest) -> ExportResponse:
    """Write the current filter's full result set (not just the visible page) to a
    CSV under the outputs dir, and hand back the path for the shell to open."""
    matched = _filtered(body)
    rows = _rows(matched)

    out_dir = config.OUTPUTS_DIR / "influencers"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"influencers-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    path = out_dir / filename

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return ExportResponse(path=str(path), filename=filename, count=len(rows))
