"""Tumblr Post Creator — third sibling of the Bluesky and Mastodon generators.

Same pipeline as both: learn from posts that actually performed in a niche, then write
in that register. Four things differ, each because Tumblr differs.

1. THE CORPUS IS IMPORTED, NOT CRAWLED.
   The Bluesky tool searches keywords and the Mastodon tool reads hashtag timelines,
   both in one request. Neither approach can judge a Tumblr post: "high engagement"
   there needs the author's own baseline, because Tumblr publishes no follower count
   for blogs you do not control, and computing that baseline costs a page of API calls
   per blog. A separate project does that crawl properly — 382,652 blogs from Tumblr's
   sitemaps at 1 request/second, over days — and services/tumblr_corpus.py imports its
   output. `/collect` still exists for live top-ups (see 2), but the corpus is the
   ground truth and `/import` is the primary path.

2. LIVE COLLECTION IS A TOP-UP, AND NEEDS THE USER'S TUMBLR LOGIN.
   `/tagged` is the closest thing Tumblr has to a hashtag timeline, and it is the one
   discovery surface that works per-niche. It is signed with the same OAuth1 credentials
   the Engage tool already holds, so nothing new is stored and the tool degrades to
   import-only when Tumblr is not connected. What `/tagged` cannot give is an audience
   baseline, so a topped-up post is scored against the blog's *observed* notes in the
   corpus when it is known there, and skipped otherwise rather than scored against a
   number this module invented.

3. ENGAGEMENT IS ONE NUMBER, AND THE DENOMINATOR IS A PROXY.
   Tumblr publishes "notes" — likes, reblogs and replies summed, never broken down.
   Ranking divides by `audience_proxy_notes`, the median note count of the blog's recent
   originals, which is what the collector uses in place of a follower count. Both facts
   are visible in the UI rather than dressed up as something they are not.

4. NICHES CAN LEGITIMATELY BE EMPTY HERE.
   Tumblr's corpus is fandom and art: measured on import, five of seven configured
   niches drew almost no matches. A niche below MIN_NICHE_EXEMPLARS borrows from the
   general pool for register and `/status` says so, rather than showing a full pool and
   implying grounding that does not exist.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services import brand_voice, tumblr as tumblr_api, tumblr_corpus
from ..services.genqueue import queue_slot
from ..services.tumblr import TumblrError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tumblr-post", tags=["tumblr-post"])

PLATFORM = "tumblr"

# Matches the Mastodon tool: a narrow discovery surface makes a thin pool of on-topic
# posts worth more than a fat one padded with near-misses.
TARGET_POOL_SIZE = 15
N_EXEMPLARS = 5
SIMILARITY_WEIGHT = 0.7
HALF_LIFE_DAYS = 14.0
MAX_AVOID_TEXTS = 3

# Below this many of its own, a niche borrows from the general Tumblr pool rather than
# generating ungrounded. Same threshold and same reasoning as the Mastodon tool's
# per-instance borrowing: under eight, the "closest" exemplars are just whatever exists.
MIN_NICHE_EXEMPLARS = 8

# The two floors the Mastodon pool learned the hard way, applied here from the start.
# A post nobody engaged with is not evidence a post worked, and a wall of tags is not
# writing to imitate — Tumblr being the most tag-heavy platform of the three makes the
# second matter more here, not less.
MIN_EXEMPLAR_NOTES = 10
MIN_EXEMPLAR_PROSE_WORDS = tumblr_corpus.MIN_PROSE_WORDS

# Age decay is measured in years here, not the other tools' weeks. The corpus reaches
# back to 2020 on purpose: Tumblr posts accumulate notes for years and a 2021 post with
# 3,000 notes is still a better style exemplar than a 2026 post with nine. Halving every
# 14 days as the Bluesky tool does would delete the entire corpus from contention.
HALF_LIFE_YEARS = 3.0


def _spg():
    """Lazy handle on the vendored package (it pulls torch in via embeddings)."""
    from vendor.socialpost.src import db as spg_db
    from vendor.socialpost.src import embeddings, llm

    return spg_db, embeddings, llm


def _corpus_niche(niche: str) -> str:
    return tumblr_corpus.corpus_niche(niche)


def _general_key() -> str:
    return tumblr_corpus.corpus_niche(tumblr_corpus.GENERAL_NICHE)


def _tumblr_niches() -> list[str]:
    """This tool's niches, derived from what is actually in the corpus.

    Deliberately NOT `spg_db.list_niches()`. Tumblr organises itself around different
    topics than Bluesky or Mastodon do — art_design, books_writing, lgbtq_community,
    humor_memes — and the collector classifies into those. Writing them into the shared
    niches table would put twenty categories into the other two generators' dropdowns
    where they hold nothing. Reading them back out of the corpus keys keeps the taxonomy
    where it belongs: on the platform it describes.

    The general pool is excluded — it is a fallback, not a subject.
    """
    spg_db, _, _ = _spg()
    suffix = f" · {PLATFORM}"
    general = _general_key()
    seen = {
        row["niche"]
        for row in (
            spg_db.get_client().table("posts").select("niche").eq("platform", PLATFORM).execute().data
            or []
        )
        if (row.get("niche") or "").endswith(suffix) and row["niche"] != general
    }
    return sorted(key[: -len(suffix)] for key in seen)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TumblrCreds(BaseModel):
    consumerKey: str = ""
    consumerSecret: str = ""
    oauthToken: str = ""
    oauthTokenSecret: str = ""
    blog: str = ""


class ImportRequest(BaseModel):
    #: Optional override; defaults to the collector's own working store.
    corpusPath: str = ""


class ImportResponse(BaseModel):
    corpus: str
    imported: int
    blogs: int
    perNiche: dict[str, int]
    skipped: dict[str, int]
    pools: dict[str, int]


class NicheOut(BaseModel):
    name: str
    keywords: list[str]
    posts: int
    exemplars: int
    #: True when this niche has too few Tumblr posts of its own and is borrowing.
    borrowing: bool


class StatusResponse(BaseModel):
    corpusFound: bool
    corpusPath: str
    posts: int
    exemplars: int
    generalPoolPosts: int
    connected: bool
    niches: list[NicheOut]
    note: str


class CollectRequest(TumblrCreds):
    niche: str
    limit: int = 60


class CollectResponse(BaseModel):
    scanned: int
    stored: int
    skipped: dict[str, int]
    exemplars: int


class GenerateRequest(BaseModel):
    userInput: str
    niche: str
    brandVoiceId: str = ""
    sourceUrl: str = ""
    avoidTexts: list[str] = []


class ExemplarOut(BaseModel):
    text: str
    blog: str
    notes: int
    postUrl: str
    #: True for a post the user published through this tool — see RESERVED_OWN_SLOTS.
    isYours: bool = False


class GenerateResponse(BaseModel):
    text: str
    tags: list[str]
    niche: str
    exemplars: list[ExemplarOut]
    #: Set when the niche had too few Tumblr posts and the general pool was used.
    borrowedFrom: str
    provider: str
    model: str
    #: Pass back to /published to close the learning loop. 0 if the audit row failed.
    generationId: int


class PublishedRequest(TumblrCreds):
    generationId: int
    niche: str
    #: Either a full Tumblr permalink, or the numeric post id with `blog` set.
    postUrl: str = ""
    postId: str = ""


class MeasureResponse(BaseModel):
    measured: int
    rebuilt: list[str]
    note: str


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


#: How many of a niche's own most-used tags stand in for keywords.
NICHE_TAG_SAMPLE = 6


def _niche_tags(key: str, limit: int = NICHE_TAG_SAMPLE) -> list[str]:
    """The tags a niche's corpus posts most often carry.

    These do the job keywords do for the other two generators. Nobody typed them: the
    niches come from the collector's classifier, so their vocabulary has to be read back
    out of the posts themselves. Counted once per post, so one prolific tagger cannot
    define the niche on its own.
    """
    spg_db, _, _ = _spg()
    counts: dict[str, int] = {}
    for post in (
        spg_db.get_client().table("posts").select("hashtags").eq("niche", key).execute().data or []
    ):
        for tag in {
            str(t).strip().lower() for t in (post.get("hashtags") or []) if str(t).strip()
        }:
            counts[tag] = counts.get(tag, 0) + 1
    return [tag for tag, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def _pool_counts() -> dict[str, int]:
    spg_db, _, _ = _spg()
    rows = (
        spg_db.get_client()
        .table("exemplars")
        .select("niche")
        .eq("active", True)
        .execute()
        .data
        or []
    )
    out: dict[str, int] = {}
    for row in rows:
        key = row["niche"] or ""
        if key.endswith(f" · {PLATFORM}"):
            out[key] = out.get(key, 0) + 1
    return out


@router.post("/import", response_model=ImportResponse)
def import_corpus(body: ImportRequest) -> ImportResponse:
    """Pull the standalone collector's corpus in, then build every pool it touched.

    Re-runnable by design: the collector is resumable and still growing, so this upserts
    by URI and a second run contributes only what has appeared since.
    """
    from pathlib import Path

    try:
        result = tumblr_corpus.run(Path(body.corpusPath) if body.corpusPath.strip() else None)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Import failed: {err}") from None

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    per_niche = result.get("perNiche", {})
    for niche in per_niche:
        try:
            _rebuild_pool(niche)
        except Exception:  # noqa: BLE001 — one niche must not stop the rest
            log.exception("[tumblr-post] could not build the pool for %r", niche)

    return ImportResponse(
        corpus=result["corpus"],
        imported=result["imported"],
        blogs=result["blogs"],
        perNiche=per_niche,
        skipped={k: v for k, v in result.items() if k.startswith("skip_")},
        pools=_pool_counts(),
    )


def _creds(body: TumblrCreds):
    creds = tumblr_api.credentials_from(body)
    if not creds.complete:
        raise HTTPException(
            status_code=409,
            detail="Connect Tumblr in Settings first — live collection signs the same "
            "calls the Engage tool does.",
        )
    return creds


@router.post("/collect", response_model=CollectResponse)
def collect(body: CollectRequest) -> CollectResponse:
    """Top a niche up from Tumblr's tag pages.

    A supplement to `/import`, never a replacement: `/tagged` hands back recent posts
    with their note counts but no audience baseline, so a post whose blog the corpus has
    never measured cannot be scored honestly and is skipped rather than guessed at.
    """
    spg_db, _, _ = _spg()
    creds = _creds(body)

    key = _corpus_niche(body.niche)
    if body.niche not in _tumblr_niches():
        raise HTTPException(
            status_code=400,
            detail=(
                f"No Tumblr niche called {body.niche!r}. Tumblr's niches come from the "
                f"collector's own classification — run Import first."
            ),
        )
    # A niche the collector named has no hand-written keywords, so its own most-used tags
    # are what gets queried. That also keeps the top-up on-topic by construction.
    keywords = _niche_tags(key)
    if not keywords:
        raise HTTPException(
            status_code=400, detail=f"{body.niche!r} has no tags to search Tumblr with yet."
        )
    now = spg_db.utcnow()
    scanned = 0
    skipped: dict[str, int] = {}
    keep: dict[str, dict] = {}

    # Blogs the corpus has already measured. This is what makes a live post scorable.
    known = {
        row["did"]: int(row["follower_count"] or 0)
        for row in (
            spg_db.get_client()
            .table("authors")
            .select("did, follower_count")
            .execute()
            .data
            or []
        )
        if str(row["did"] or "").startswith("tumblr:")
    }

    per_keyword = max(5, body.limit // max(len(keywords), 1))
    for keyword in keywords:
        tag = keyword.strip()
        if not tag:
            continue
        try:
            payload = tumblr_api.get(
                creds, "/tagged", tag=tag, limit=min(20, per_keyword), npf="true"
            )
        except TumblrError as err:
            # One bad tag must not cost the whole pass.
            log.warning("[tumblr-post] #%s failed: %s", tag, err)
            skipped["fetch failed"] = skipped.get("fetch failed", 0) + 1
            continue

        for raw in payload or []:
            if not isinstance(raw, dict):
                continue
            scanned += 1
            blog = str(raw.get("blog_name") or "").strip()
            post_id = str(raw.get("id_string") or raw.get("id") or "").strip()
            if not blog or not post_id:
                skipped["unidentifiable"] = skipped.get("unidentifiable", 0) + 1
                continue
            # A reblog's notes belong to the whole chain, not to this author's addition.
            # The corpus collector excludes them and so must this.
            if raw.get("reblogged_from_id") or raw.get("parent_post_id"):
                skipped["reblog"] = skipped.get("reblog", 0) + 1
                continue

            text = tumblr_api.npf_text(raw).strip()
            if len(text) < tumblr_corpus.MIN_TEXT_CHARS:
                skipped["too short"] = skipped.get("too short", 0) + 1
                continue
            if tumblr_corpus._prose_words(text) < MIN_EXEMPLAR_PROSE_WORDS:
                skipped["tags only"] = skipped.get("tags only", 0) + 1
                continue

            did = f"tumblr:{blog}"
            if did not in known:
                # No baseline for this blog, so no honest way to say whether its note
                # count is good. Importing the corpus is what fixes this.
                skipped["blog not measured"] = skipped.get("blog not measured", 0) + 1
                continue

            notes = int(raw.get("note_count") or 0)
            if notes < MIN_EXEMPLAR_NOTES:
                skipped["below note floor"] = skipped.get("below note floor", 0) + 1
                continue

            keep[f"tumblr://{blog}/{post_id}"] = {
                "blog": blog,
                "did": did,
                "text": text,
                "tags": [str(t) for t in (raw.get("tags") or [])],
                "notes": notes,
                "proxy": float(known[did]),
                "created_at": _iso_from_timestamp(raw.get("timestamp")),
                "has_media": int(bool(raw.get("content") or raw.get("photos"))),
            }

    if keep:
        spg_db.upsert(
            "posts",
            [
                {
                    "uri": uri,
                    "platform": PLATFORM,
                    "author_did": item["did"],
                    "text": item["text"],
                    "hashtags": item["tags"],
                    "has_media": item["has_media"],
                    "created_at": item["created_at"],
                    "niche": key,
                    "ingested_at": spg_db.iso(now),
                }
                for uri, item in keep.items()
            ],
            on_conflict="uri",
        )
        spg_db.upsert(
            "engagement_snapshots",
            [
                {
                    "post_uri": uri,
                    "captured_at": spg_db.iso(now),
                    "window_label": "48h",
                    "likes": item["notes"],
                    "reposts": 0,
                    "replies": 0,
                    "engagement_rate": tumblr_corpus.audience_rate(
                        item["notes"], item["proxy"]
                    ),
                }
                for uri, item in keep.items()
            ],
            on_conflict="post_uri,window_label",
        )

    n = _rebuild_pool(body.niche)
    return CollectResponse(scanned=scanned, stored=len(keep), skipped=skipped, exemplars=n)


def _iso_from_timestamp(value) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Exemplar pool
# ---------------------------------------------------------------------------


def _age_days(created_at, now) -> float:
    if not created_at:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((now - parsed).total_seconds() / 86400.0, 0.0)


def _decay(age_days: float) -> float:
    """Halve the score every HALF_LIFE_YEARS. See the constant for why not weeks."""
    return 0.5 ** (age_days / (HALF_LIFE_YEARS * 365.0))


def _rebuild_pool(niche: str) -> int:
    """Replace one niche's Tumblr exemplar pool from everything measured so far.

    Same shape as the Mastodon rebuild — score, deactivate the old pool, insert the new
    one — including both of its eligibility floors, because they were arrived at by
    measuring a live corpus and Tumblr has no reason to be exempt.
    """
    spg_db, embeddings, _ = _spg()
    key = _corpus_niche(niche)
    client = spg_db.get_client()
    now = spg_db.utcnow()

    posts = (
        client.table("posts")
        .select("uri, text, created_at, author_did")
        .eq("niche", key)
        .execute()
        .data
        or []
    )
    if not posts:
        return 0

    by_uri = {p["uri"]: p for p in posts}
    uris = list(by_uri)
    rates: dict[str, tuple[int, float]] = {}
    for i in range(0, len(uris), 100):
        rows = (
            client.table("engagement_snapshots")
            .select("post_uri, likes, engagement_rate")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        )
        for row in rows:
            rates[row["post_uri"]] = (
                int(row["likes"] or 0),
                float(row["engagement_rate"] or 0.0),
            )

    scored: list[tuple[float, dict]] = []
    for uri, (notes, rate) in rates.items():
        post = by_uri.get(uri)
        if not post or not (post.get("text") or "").strip():
            continue
        if notes < MIN_EXEMPLAR_NOTES:
            continue
        if tumblr_corpus._prose_words(post["text"]) < MIN_EXEMPLAR_PROSE_WORDS:
            continue
        scored.append((rate * _decay(_age_days(post.get("created_at"), now)), post))

    if not scored:
        return 0

    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = scored[:TARGET_POOL_SIZE]
    chosen = _reserve_own_slot(chosen, scored, key)
    vectors = embeddings.embed([p["text"] for _, p in chosen])

    client.table("exemplars").update({"active": False}).eq("niche", key).eq(
        "active", True
    ).execute()
    spg_db.insert(
        "exemplars",
        [
            {
                "post_uri": post["uri"],
                "niche": key,
                "score": round(score, 6),
                "embedding": vectors[i].tolist(),
                "active": True,
                "refreshed_at": spg_db.iso(now),
            }
            for i, (score, post) in enumerate(chosen)
        ],
    )
    return len(chosen)


#: Pool slots held for the user's own published posts, when they have any that qualify.
#
# The corpus this ranks against was pre-filtered by the collector to viral posts — the top
# of `art_design` sits at 9,505, 33,207 and 140,856 notes — so a post of the user's doing
# perfectly well by its own blog's standards will essentially never out-score it. Without
# a reservation the self-learning loop measures faithfully and then changes nothing.
#
# One slot, not more: this is meant to keep the user's own voice represented in what the
# model is shown, not to let a modest post crowd out the evidence of what actually works
# on Tumblr. The other fourteen stay strictly earned.
RESERVED_OWN_SLOTS = 1


def _own_post_uris(key: str) -> set[str]:
    """Posts the user told us they published, in this niche.

    `generations.posted_uri` is the record of that, written by /published — the same
    marker the Mastodon snapshot job uses to decide what is the user's own.
    """
    spg_db, _, _ = _spg()
    return {
        g["posted_uri"]
        for g in (
            spg_db.get_client()
            .table("generations")
            .select("posted_uri, niche")
            .eq("niche", key)
            .execute()
            .data
            or []
        )
        if (g.get("posted_uri") or "").startswith("tumblr://")
    }


def _reserve_own_slot(
    chosen: list[tuple[float, dict]], scored: list[tuple[float, dict]], key: str
) -> list[tuple[float, dict]]:
    """Ensure the user's best qualifying post holds a slot, displacing the weakest entry.

    Deliberately NOT a bypass of the eligibility floors. `scored` has already dropped
    anything under MIN_EXEMPLAR_NOTES or without real prose, and a post that nobody
    engaged with is not evidence of anything — reserving a slot for a flop would teach the
    generator to write like a post that did not work, which is the exact failure the floors
    exist to prevent. "Guaranteed" here means "does not have to out-score a viral corpus",
    not "gets in having earned nothing".

    A no-op when the user has no qualifying posts in this niche, or when their posts
    already made the pool on merit.
    """
    if not chosen:
        return chosen
    own_uris = _own_post_uris(key)
    if not own_uris:
        return chosen

    already = sum(1 for _, post in chosen if post["uri"] in own_uris)
    if already >= RESERVED_OWN_SLOTS:
        return chosen

    best_own = next((pair for pair in scored if pair[1]["uri"] in own_uris), None)
    if best_own is None:
        # They have published here, but nothing that clears the floors yet.
        return chosen

    # Drop the weakest earned entry rather than growing the pool: TARGET_POOL_SIZE is a
    # budget on what rides in the prompt, not a target to overshoot.
    kept = [pair for pair in chosen if pair[1]["uri"] != best_own[1]["uri"]]
    kept = kept[: TARGET_POOL_SIZE - RESERVED_OWN_SLOTS]
    log.info(
        "[tumblr-post] %s: reserving a pool slot for the user's own %s (score %.4f vs "
        "pool best %.4f)",
        key,
        best_own[1]["uri"],
        best_own[0],
        chosen[0][0],
    )
    return kept + [best_own]


def _pool_rows(keys: list[str]) -> list[dict]:
    """Active exemplars under any of these corpus keys, with their post text."""
    spg_db, _, _ = _spg()
    client = spg_db.get_client()
    rows = (
        # `id` is needed by /generate, which records which exemplars produced a draft.
        client.table("exemplars")
        .select("id, post_uri, niche, score, embedding")
        .in_("niche", keys)
        .eq("active", True)
        .execute()
        .data
        or []
    )
    if not rows:
        return []
    uris = [r["post_uri"] for r in rows]
    posts: dict[str, dict] = {}
    for i in range(0, len(uris), 100):
        for post in (
            client.table("posts")
            .select("uri, text, author_did")
            .in_("uri", uris[i : i + 100])
            .execute()
            .data
            or []
        ):
            posts[post["uri"]] = post
    notes: dict[str, int] = {}
    for i in range(0, len(uris), 100):
        for snap in (
            client.table("engagement_snapshots")
            .select("post_uri, likes")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        ):
            notes[snap["post_uri"]] = int(snap["likes"] or 0)

    out: list[dict] = []
    for row in rows:
        post = posts.get(row["post_uri"])
        if not post:
            continue
        out.append(
            {
                **row,
                "text": post["text"],
                "blog": str(post.get("author_did") or "").removeprefix("tumblr:"),
                "notes": notes.get(row["post_uri"], 0),
            }
        )
    return out


def _retrieve_exemplars(niche: str, query: str, n: int = N_EXEMPLARS) -> tuple[list[dict], str]:
    """Top-n exemplars by blended similarity and score, plus what was borrowed from.

    Reimplemented over the client's public surface rather than the vendored
    match_exemplars RPC for the same reason the Mastodon tool does it: that RPC is
    niche-scoped only and cannot express "and only this platform's posts".
    """
    import numpy as np

    _, embeddings, _ = _spg()

    key = _corpus_niche(niche)
    rows = _pool_rows([key])
    borrowed = ""
    if len(rows) < MIN_NICHE_EXEMPLARS:
        general = _general_key()
        if general != key:
            extra = _pool_rows([general])
            if extra:
                rows = rows + extra
                borrowed = tumblr_corpus.GENERAL_NICHE
    if not rows:
        return [], ""

    query_vec = np.array(embeddings.embed([query])[0], dtype=float)
    norm = np.linalg.norm(query_vec) or 1.0

    scored: list[tuple[float, dict]] = []
    best_score = max((float(r["score"] or 0.0) for r in rows), default=0.0) or 1.0
    for row in rows:
        vec = np.array(row["embedding"], dtype=float)
        denom = (np.linalg.norm(vec) or 1.0) * norm
        similarity = float(np.dot(vec, query_vec) / denom)
        performance = float(row["score"] or 0.0) / best_score
        blended = SIMILARITY_WEIGHT * similarity + (1 - SIMILARITY_WEIGHT) * performance
        scored.append((blended, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:n]], borrowed


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
def status(
    consumerKey: str = "",
    consumerSecret: str = "",
    oauthToken: str = "",
    oauthTokenSecret: str = "",
) -> StatusResponse:
    """What the tool has to work with, and which niches are only borrowing."""
    spg_db, _, _ = _spg()
    client = spg_db.get_client()

    pools = _pool_counts()
    general_key = _general_key()

    counts: dict[str, int] = {}
    for row in client.table("posts").select("niche").eq("platform", PLATFORM).execute().data or []:
        counts[row["niche"]] = counts.get(row["niche"], 0) + 1

    niches: list[NicheOut] = []
    for name in _tumblr_niches():
        key = _corpus_niche(name)
        n_ex = pools.get(key, 0)
        niches.append(
            NicheOut(
                name=name,
                # Tags this niche's own posts actually carry — the closest thing it has
                # to keywords, since it was never given any by hand.
                keywords=_niche_tags(key),
                posts=counts.get(key, 0),
                exemplars=n_ex,
                borrowing=n_ex < MIN_NICHE_EXEMPLARS and pools.get(general_key, 0) > 0,
            )
        )

    corpus_path = tumblr_corpus.DEFAULT_CORPUS_PATH
    thin = [n.name for n in niches if n.borrowing]
    note = ""
    if not sum(counts.values()):
        note = "Nothing imported yet — run Import to read the collector's corpus."
    elif thin:
        one = len(thin) == 1
        note = (
            f"{', '.join(thin)} {'has' if one else 'have'} too little Tumblr material of "
            f"{'its' if one else 'their'} own and will borrow the general pool's register. "
            "Tumblr's corpus is mostly art and fandom, so tech-leaning niches stay thin."
        )

    return StatusResponse(
        corpusFound=corpus_path.exists(),
        corpusPath=str(corpus_path),
        posts=sum(counts.values()),
        exemplars=sum(pools.values()),
        generalPoolPosts=counts.get(general_key, 0),
        connected=bool(consumerKey and consumerSecret and oauthToken and oauthTokenSecret),
        niches=niches,
        note=note,
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"^#?([\w\- ]{2,30})$")


#: A tag must appear on at least this many of the retrieved exemplars to be suggested.
#
# Without it a single post donates its whole tag list, and on Tumblr that list is usually
# one fandom's vocabulary: the first run suggested "jay x reader", "enhypen fluff" for a
# *literature* draft, purely because one high-note fanfic was retrieved. Requiring two
# independent posts to share a tag is what separates "this is how this niche is tagged"
# from "this is what that one post was about". An empty list is the right answer when
# nothing corroborates — the composer can tag it themselves.
MIN_TAG_SUPPORT = 2


def _suggest_tags(exemplars: list[dict], limit: int = 5) -> list[str]:
    """Tags that recur across *several* of the chosen exemplars.

    Tumblr's discovery is tag-driven far more than the other two platforms', so a draft
    without tags is half a post. These come from what actually worked in the pool rather
    than from the model, which invents plausible-looking tags nobody follows.
    """
    spg_db, _, _ = _spg()
    client = spg_db.get_client()
    uris = [row["post_uri"] for row in exemplars]
    if not uris:
        return []
    counts: dict[str, int] = {}
    for post in (
        client.table("posts").select("hashtags").in_("uri", uris).execute().data or []
    ):
        # Deduplicated per post, so one post repeating a tag cannot fake corroboration.
        for tag in {
            str(t).strip().lower() for t in (post.get("hashtags") or []) if str(t).strip()
        }:
            if _TAG_RE.match(tag):
                counts[tag] = counts.get(tag, 0) + 1
    return [
        tag
        for tag, n in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
        if n >= MIN_TAG_SUPPORT
    ]


@router.post(
    "/generate", response_model=GenerateResponse, dependencies=[Depends(queue_slot("model"))]
)
def generate(body: GenerateRequest) -> GenerateResponse:
    """Write one Tumblr post, grounded in what performed in this niche."""
    _, _, spg_llm = _spg()

    if body.niche not in _tumblr_niches():
        raise HTTPException(
            status_code=400,
            detail=(
                f"No Tumblr niche called {body.niche!r}. Tumblr's niches come from the "
                f"collector's classification — run Import first."
            ),
        )

    retrieval_query = body.userInput.strip() or body.niche
    exemplars, borrowed = _retrieve_exemplars(body.niche, retrieval_query)
    if not exemplars:
        raise HTTPException(
            status_code=409,
            detail=(
                "No Tumblr exemplars yet. Run Import to read the collector's corpus, "
                "then try again."
            ),
        )

    source = None
    if body.sourceUrl.strip():
        from vendor.socialpost.src import sources

        try:
            source = sources.fetch(body.sourceUrl.strip())
        except Exception as err:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Could not read that link: {err}") from None

    try:
        text = spg_llm.generate_post(
            user_input=brand_voice.apply_voice(body.userInput, body.brandVoiceId, compact=True),
            niche=body.niche,
            platform=PLATFORM,
            exemplar_texts=[row["text"] for row in exemplars],
            kb_summaries=[],
            source=source,
            avoid_texts=body.avoidTexts[:MAX_AVOID_TEXTS],
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Generation failed: {err}") from None

    # The audit row is what /published later attaches a real post to, and what makes the
    # loop possible at all. Losing it must not lose the draft the user is waiting for.
    spg_db, _, _ = _spg()
    generation_id = 0
    try:
        resp = (
            spg_db.get_client()
            .table("generations")
            .insert(
                {
                    "created_at": spg_db.iso(spg_db.utcnow()),
                    "user_input": body.userInput,
                    "niche": _corpus_niche(body.niche),
                    "output_text": text,
                    "exemplar_ids": [e["id"] for e in exemplars],
                    "kb_ids": [],
                }
            )
            .execute()
        )
        generation_id = int(resp.data[0]["id"])
    except Exception:  # noqa: BLE001
        log.exception("[tumblr-post] could not record the generation")

    # Which of the exemplars are the user's own, so the draft can point at them rather
    # than leaving the reserved slot invisible.
    own = _own_post_uris(_corpus_niche(body.niche))

    return GenerateResponse(
        generationId=generation_id,
        text=text,
        tags=_suggest_tags(exemplars),
        niche=body.niche,
        exemplars=[
            ExemplarOut(
                text=row["text"][:280],
                blog=row["blog"],
                notes=row["notes"],
                postUrl=tumblr_api.post_url(row["blog"], row["post_uri"].rsplit("/", 1)[-1]),
                isYours=row["post_uri"] in own,
            )
            for row in exemplars
        ],
        borrowedFrom=borrowed,
        provider=spg_llm.provider(),
        model=spg_llm.model_name(),
    )


# ---------------------------------------------------------------------------
# The self-learning loop
#
# /published records that a draft became a real post; /measure re-reads how those posts
# actually did, at the same 1h/24h/48h buckets the other two generators use, and rebuilds
# the pools that gained a measurement. Without both, the tool only ever learns from
# strangers and never from the user's own results.
#
# WHY THIS IS APP-DRIVEN RATHER THAN A BACKGROUND TIMER, unlike the Mastodon loop.
# That one needs no credentials — public and unlisted statuses are readable
# unauthenticated — so a daemon thread can measure on its own without ever being able to
# act as the user. Tumblr's read API signs every call, and this app deliberately keeps
# Tumblr credentials in Electron's encrypted store and passes them per request; there is
# no server-side copy for a timer to use. Storing one to enable a background job would
# widen the credential surface for a measurement that is not urgent.
#
# So measurement runs when the app has credentials in hand: the Tumblr screen calls
# /measure on load. Buckets are due-based rather than exact, so a post is measured at the
# first visit after each bucket opens rather than at the minute it does. For an engagement
# curve that keeps climbing for days, that is the same answer.
# ---------------------------------------------------------------------------

#: (label, hours after posting the bucket opens). Matches the other two generators and
#: the window_label CHECK constraint in the shared schema, which permits exactly these.
_BUCKETS: tuple[tuple[str, float], ...] = (("1h", 1.0), ("24h", 24.0), ("48h", 48.0))

#: Ceiling on one /measure pass. Each post is its own signed request.
MAX_MEASURE_POSTS = 40


#: How many recent posts to read when estimating a blog's own baseline. Matches the
#: collector's keyed-v2 default (three 20-post pages) so the two produce the same statistic.
OWN_AUDIENCE_WINDOW = 60


def _own_audience_proxy(creds, blog: str) -> float:
    """The user's own `audience_proxy_notes`: median notes of their recent originals.

    IT IS DELIBERATELY *NOT* THEIR REAL FOLLOWER COUNT, even though Tumblr will hand that
    over for a blog they control. The corpus is scored as notes/(median-notes + prior),
    and a follower count is a different kind of quantity entirely — a blog with 1,000
    followers may have a median of 30 notes, so scoring the user's posts against followers
    would divide by a number ~30x larger and rank every one of their posts far below the
    corpus in the same pool. Comparability inside the ranking beats precision of the
    individual number, so this reproduces the collector's statistic instead.

    The exact follower count is still fetched, but only to *show* the user (see
    /published), never to rank with.

    Reblogs are excluded for the same reason the collector excludes them: a reblog's notes
    belong to the whole chain, not to this blog.
    """
    notes: list[int] = []
    try:
        for offset in range(0, OWN_AUDIENCE_WINDOW, 20):
            payload = tumblr_api.get(
                creds,
                f"/blog/{tumblr_api.blog_path(blog)}/posts",
                limit=20,
                offset=offset,
                npf="true",
                reblog_info="true",
            ) or {}
            page = [p for p in (payload.get("posts") or []) if isinstance(p, dict)]
            if not page:
                break
            for raw in page:
                if raw.get("reblogged_from_id") or raw.get("parent_post_id"):
                    continue
                notes.append(int(raw.get("note_count") or 0))
    except TumblrError as err:
        log.info("[tumblr-post] could not sample %s for a baseline: %s", blog, str(err)[:90])

    if not notes:
        # No baseline yet — a brand-new blog. The prior alone is the denominator, which
        # is the honest "we have no idea how big your audience is" answer.
        return 0.0
    notes.sort()
    return float(notes[len(notes) // 2])


def _fetch_own_post(creds, blog: str, post_id: str) -> dict | None:
    """One post from a blog, by id, with its current note count."""
    payload = tumblr_api.get(
        creds, f"/blog/{tumblr_api.blog_path(blog)}/posts", id=post_id, npf="true", limit=1
    ) or {}
    posts = payload.get("posts") if isinstance(payload, dict) else None
    for raw in posts or []:
        if isinstance(raw, dict):
            return raw
    return None


@router.post("/published")
def mark_published(body: PublishedRequest) -> dict:
    """Link a published Tumblr post to the draft that produced it.

    The post row has to exist before a generation can point at it — `generations.posted_uri`
    references `posts`, and something published a minute ago was never in the corpus.
    """
    spg_db, _, _ = _spg()
    creds = _creds(body)

    blog = (body.blog or creds.blog or "").strip()
    post_id = body.postId.strip()
    if not post_id and body.postUrl.strip():
        blog, post_id = _parse_post_url(body.postUrl.strip(), blog)
    if not blog or not post_id:
        raise HTTPException(
            status_code=400,
            detail="Paste the post's Tumblr link, or give its numeric id and blog.",
        )

    try:
        raw = _fetch_own_post(creds, blog, post_id)
    except TumblrError as err:
        raise HTTPException(status_code=502, detail=str(err)) from None
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tumblr did not return a post {post_id} on {blog}. Check the link is right.",
        )

    key = _corpus_niche(body.niche)
    uri = f"tumblr://{blog}/{post_id}"
    now = spg_db.utcnow()
    text = tumblr_api.npf_text(raw).strip()
    notes = int(raw.get("note_count") or 0)
    proxy = _own_audience_proxy(creds, blog)

    spg_db.upsert(
        "authors",
        [
            {
                "did": f"tumblr:{blog}",
                "handle": blog,
                # Same statistic the corpus stores for every other blog — median notes,
                # not followers. See _own_audience_proxy.
                "follower_count": int(round(proxy)),
                "niche": key,
                "last_seen_at": spg_db.iso(now),
            }
        ],
        on_conflict="did",
    )
    spg_db.upsert(
        "posts",
        [
            {
                "uri": uri,
                "platform": PLATFORM,
                "author_did": f"tumblr:{blog}",
                "text": text,
                "hashtags": [str(t) for t in (raw.get("tags") or [])],
                "has_media": int(bool(raw.get("content") or raw.get("photos"))),
                "created_at": _iso_from_timestamp(raw.get("timestamp")),
                "niche": key,
                "ingested_at": spg_db.iso(now),
            }
        ],
        on_conflict="uri",
    )
    if body.generationId:
        spg_db.get_client().table("generations").update({"posted_uri": uri}).eq(
            "id", body.generationId
        ).execute()

    return {
        "postedUri": uri,
        "webUrl": tumblr_api.post_url(blog, post_id),
        "notes": notes,
        # What this post will be ranked against, named for what it actually is.
        "medianNotes": int(round(proxy)),
        # Shown for context only — never used to rank, see _own_audience_proxy. None for
        # a blog Tumblr will not give a count for.
        "followers": tumblr_api.follower_count(creds, blog),
    }


def _parse_post_url(url: str, fallback_blog: str) -> tuple[str, str]:
    """Pull (blog, post id) out of a Tumblr permalink.

    Handles both shapes Tumblr uses: `https://<blog>.tumblr.com/post/<id>/...` and
    `https://www.tumblr.com/<blog>/<id>/...`.
    """
    match = re.search(r"https?://([\w-]+)\.tumblr\.com/post/(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    match = re.search(r"https?://(?:www\.)?tumblr\.com/([\w-]+)/(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    match = re.search(r"(\d{6,})", url)
    return fallback_blog, match.group(1) if match else ""


@router.post("/measure", response_model=MeasureResponse)
def measure(body: TumblrCreds) -> MeasureResponse:
    """Re-read note counts on the user's own published posts, then rebuild what changed.

    Called by the Tumblr screen on load — see the section comment for why this is not a
    background job. Cheap and idempotent: a bucket already captured is never re-measured,
    so repeated visits cost nothing.
    """
    spg_db, _, _ = _spg()
    creds = _creds(body)
    client = spg_db.get_client()
    now = spg_db.utcnow()

    suffix = f" · {PLATFORM}"
    posted = {
        g["posted_uri"]: g["niche"]
        for g in (
            client.table("generations").select("posted_uri, niche").execute().data or []
        )
        if (g.get("posted_uri") or "").startswith("tumblr://")
        and (g.get("niche") or "").endswith(suffix)
    }
    if not posted:
        return MeasureResponse(
            measured=0,
            rebuilt=[],
            note="Nothing published through this tool yet — use 'I published this' on a draft.",
        )

    uris = list(posted)[:MAX_MEASURE_POSTS]
    created = {
        p["uri"]: p.get("created_at")
        for p in (client.table("posts").select("uri, created_at").in_("uri", uris).execute().data or [])
    }
    captured: dict[str, set[str]] = {}
    for row in (
        client.table("engagement_snapshots")
        .select("post_uri, window_label")
        .in_("post_uri", uris)
        .execute()
        .data
        or []
    ):
        captured.setdefault(row["post_uri"], set()).add(row["window_label"])

    rows: list[dict] = []
    touched: set[str] = set()
    proxies: dict[str, float] = {}
    for uri in uris:
        age_hours = _age_days(created.get(uri), now) * 24.0
        due = [
            label
            for label, hours in _BUCKETS
            if age_hours >= hours and label not in captured.get(uri, set())
        ]
        if not due:
            continue

        _, _, rest = uri.partition("tumblr://")
        blog, _, post_id = rest.partition("/")
        try:
            raw = _fetch_own_post(creds, blog, post_id)
        except TumblrError as err:
            log.warning("[tumblr-post] could not re-read %s: %s", uri, str(err)[:100])
            continue
        if raw is None:
            # Deleted, or no longer served. Absent beats zero: a missing row reads as
            # "not measured", a zero row as "nobody cared".
            log.info("[tumblr-post] %s no longer returns %s", blog, post_id)
            continue

        notes = int(raw.get("note_count") or 0)
        # Sampled once per blog per pass, not once per post: it is a property of the blog
        # and each sample costs three signed requests.
        if blog not in proxies:
            proxies[blog] = _own_audience_proxy(creds, blog)
        for label in due:
            rows.append(
                {
                    "post_uri": uri,
                    "captured_at": spg_db.iso(now),
                    "window_label": label,
                    "likes": notes,
                    "reposts": 0,
                    "replies": 0,
                    # Identical formula to the corpus rows, so the user's own posts
                    # compete for pool slots on the same terms as everyone else's.
                    "engagement_rate": tumblr_corpus.audience_rate(notes, proxies[blog]),
                }
            )
        touched.add(posted[uri])

    if not rows:
        return MeasureResponse(measured=0, rebuilt=[], note="Everything is already measured.")

    spg_db.upsert("engagement_snapshots", rows, on_conflict="post_uri,window_label")

    # A new 48h measurement can change which posts deserve a pool slot, so rebuild the
    # niches that just gained one. `key` is namespaced; _rebuild_pool takes the plain name.
    rebuilt: list[str] = []
    for key in touched:
        plain = key[: -len(suffix)] if key.endswith(suffix) else key
        try:
            _rebuild_pool(plain)
            rebuilt.append(plain)
        except Exception:  # noqa: BLE001 — one niche must not stop the rest
            log.exception("[tumblr-post] could not rebuild the pool for %r", plain)

    return MeasureResponse(
        measured=len(rows),
        rebuilt=rebuilt,
        note=f"Measured {len(rows)} bucket(s) on your own posts.",
    )


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

# One job. The corpus import is the only thing that can run unattended: measurement of
# the user's own posts needs their Tumblr credentials, which live in Electron rather than
# here, so it runs from /measure instead (see the section above).
_SCHEDULE: tuple[tuple[str, timedelta], ...] = (("tumblr_import", timedelta(days=1)),)
_TICK_SECONDS = 300
_scheduler_thread: threading.Thread | None = None


def _run_tumblr_import() -> None:
    """Re-read the collector's corpus, which is resumable and still growing."""
    if not tumblr_corpus.DEFAULT_CORPUS_PATH.exists():
        return
    result = tumblr_corpus.run()
    for niche in result.get("perNiche", {}):
        try:
            _rebuild_pool(niche)
        except Exception:  # noqa: BLE001 — one niche must not stop the rest
            log.exception("[tumblr-post] could not rebuild the pool for %r", niche)


_JOBS = {"tumblr_import": _run_tumblr_import}


def _last_run(job_name: str):
    spg_db, _, _ = _spg()
    rows = (
        spg_db.get_client()
        .table("job_runs")
        .select("started_at")
        .eq("job_name", job_name)
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return datetime.fromisoformat(rows[0]["started_at"]) if rows else None


def _run_due_jobs() -> None:
    spg_db, _, _ = _spg()
    now = spg_db.utcnow()
    for name, every in _SCHEDULE:
        try:
            last = _last_run(name)
            if last is not None and (now - last) < every:
                continue
            with spg_db.JobRun(name):
                log.info("[tumblr-post] running %s", name)
                _JOBS[name]()
        except Exception:  # noqa: BLE001 — one bad job must not stop the loop
            log.exception("[tumblr-post] job %s failed", name)


def _scheduler_loop() -> None:
    while True:
        try:
            # Inert until there is a corpus to read. An install without the collector
            # should do nothing at all rather than log a failure every tick.
            if tumblr_corpus.DEFAULT_CORPUS_PATH.exists():
                _run_due_jobs()
        except Exception:  # noqa: BLE001
            log.exception("[tumblr-post] scheduler tick failed")
        time.sleep(_TICK_SECONDS)


def start_scheduler() -> None:
    """Start the import loop in the background. Safe to call once at startup."""
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="tumblr-post-scheduler", daemon=True
    )
    _scheduler_thread.start()
    log.info("[tumblr-post] import scheduler started")
