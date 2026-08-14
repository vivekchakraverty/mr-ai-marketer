"""Import the standalone Tumblr collector's corpus into this app's post store.

The corpus is built by a separate project (`V:\\tumblr social post creator`), not by
this app, and that split is deliberate rather than incidental.

WHY IMPORT INSTEAD OF CRAWL. The Bluesky and Mastodon tools collect their own corpora
because they can: a keyword search or a hashtag timeline answers in one request and the
result is immediately usable. Tumblr's high-engagement set cannot be built that way.
Selecting a post as "high engagement" needs the author's own baseline — Tumblr publishes
no follower count for arbitrary blogs, so the only honest audience measure is the median
note count across that blog's recent originals — and computing it costs a page of API
calls *per blog* before a single post can be judged. The collector therefore walks 382,652
blogs from Tumblr's sitemaps at a documented 1 request/second under a 1,000-call hourly
key limit, and takes days. Reimplementing that inside a desktop app's 12-hourly job would
be both wrong and rude to Tumblr. So the crawl stays where it belongs and this module
reads its output.

WHAT THE COLLECTOR ALREADY DECIDED, AND IS NOT RE-DECIDED HERE. Its selection rule is
stricter than anything this app applies: English originals only (Lingua, ambiguous text
dropped), reblogs excluded because a reblog's notes belong to the whole chain rather than
the reblogger, at least 25 notes, and then either 250+ notes outright (`absolute_viral`)
or top-20%-for-that-blog with at least twice its median (`relative_breakout`). Every row
also carries `audience_proxy_notes`, `within_blog_percentile` and `engagement_lift`. This
importer re-derives none of that. It maps the rows onto the app's schema, decides which
niche each belongs to, and applies the two quality floors the app's own exemplar rules
add on top.

THE ONE MAPPING THAT NEEDS EXPLAINING. `engagement_snapshots` has likes/reposts/replies;
Tumblr publishes a single aggregate "notes" and does not break it down on the read API.
Notes therefore land in `likes` with reposts/replies at zero, which is honest for the
sum every consumer actually uses (likes + reposts + replies) and would be a fabrication
if split three ways. Ranking then divides by `audience_proxy_notes` — the collector's
follower-count substitute — rather than a follower count that does not exist.

Re-running is cheap and expected: the external collector is resumable and still growing
its corpus, so this upserts by URI and picks up whatever has appeared since.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

PLATFORM = "tumblr"

# Where the standalone collector keeps its working store. Overridable because the
# path is one user's machine, not a property of the app; the endpoint also accepts
# an explicit path so a corpus copied elsewhere can be imported without reconfiguring.
DEFAULT_CORPUS_PATH = Path(
    os.environ.get("TUMBLR_CORPUS_PATH", r"V:\tumblr social post creator\corpus.sqlite3")
)

# Tumblr is one service with one set of terms, so unlike Mastodon there is no instance
# to namespace by. The platform half is still load-bearing: it is what stops the Bluesky
# scheduler's refresh_exemplars — which deactivates a niche's entire pool and replaces
# it — from deleting every Tumblr exemplar on its next run.
def corpus_niche(niche: str) -> str:
    return f"{niche} · {PLATFORM}"


# Where posts the collector could not confidently place go.
#
# A niche below MIN_NICHE_EXEMPLARS borrows this pool for *register* — what a post that
# works on Tumblr sounds like — and the UI says so rather than letting a full-looking
# pool imply grounding it does not have. Same shape as the Mastodon tool's per-instance
# fallback, for the same reason.
GENERAL_NICHE = "general"

# TUMBLR'S NICHES ARE THE COLLECTOR'S, NOT THE APP'S SHARED LIST.
#
# The first version of this importer keyword-matched the corpus against the niches the
# Bluesky and Mastodon tools use, and it was the wrong idea: 5,483 of 6,793 posts matched
# nothing, "ai tools" drew a single post, "indie makers" and "science" drew none. Not
# because the corpus was thin — because those are not the topics Tumblr is organised
# around. The collector has since grown its own classification (`posts.niche`,
# `niche_confidence`, method "tags-and-text-keywords-v1") over 20 niches that are
# genuinely Tumblr's: art_design, books_writing, lgbtq_community, fashion_beauty,
# humor_memes, anime_manga, and so on. Every one of them clears a full 15-post pool.
#
# So the taxonomy is imported alongside the posts, and deliberately NOT written into the
# shared `niches` table: those rows drive the other two generators' dropdowns, and 20
# Tumblr categories that are empty on Bluesky would be noise there. Tumblr's niche list is
# instead *derived* from what is in the corpus — see routers/tumblr_post._tumblr_niches.
# The namespacing that already keeps platforms apart is what makes this possible.

#: The collector's label for "classified into nothing in particular".
UNCLASSIFIED = "other"

# Below this the classifier is guessing, and a mis-filed exemplar is shown to the model as
# "write like this". Measured: a 0.5 floor drops ~970 borderline rows and still leaves
# every one of the 20 niches above a full pool (thinnest is technology at 26, vs 15
# needed). Confidence is bimodal — no classified row sits between 0 and 0.5 — so this is
# a real gap in the data rather than an arbitrary cut.
MIN_NICHE_CONFIDENCE = 0.5


# Same floor as the Mastodon pool, for the same reason: exemplars are shown to the model
# as "write like this", and Tumblr is a famously tag-heavy platform, so a wall of tags
# with no prose would teach it to emit walls of tags.
MIN_PROSE_WORDS = 4

# Below this there is not enough text to learn a register from, whatever its note count.
MIN_TEXT_CHARS = 40

# Added to the audience proxy before dividing, exactly as the Mastodon pool adds a prior
# to follower counts: without it a blog whose median original gets 5 notes turns one lucky
# post into an enormous rate.
#
# Set to the measured median proxy over the *finished* corpus: 36 across 7,621 rows
# (p10 5, p25 10, p50 36, p75 178, p90 567 — a steep distribution, which is exactly why
# the prior is needed). The smallest blogs are damped roughly eightfold while a large one
# is barely touched. Recompute if the collector is ever run again; it was stopped on
# 2026-08-14, and the earlier value of 30 came from the partial 6,793-row corpus.
AUDIENCE_PRIOR = 36.0

_TAG_RE = re.compile(r"#\w+", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _prose_words(text: str) -> int:
    return len(_WORD_RE.findall(_URL_RE.sub(" ", _TAG_RE.sub(" ", text or ""))))


def _tags_of(raw: str) -> list[str]:
    """The collector stores tags as a JSON array; be forgiving about anything else."""
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(t).strip() for t in parsed if str(t).strip()] if isinstance(parsed, list) else []


def _niche_of(row: dict) -> str:
    """Which pool a corpus row belongs in: its collector niche, or the general one.

    The classification is the collector's and is not second-guessed here beyond the
    confidence floor — it has the tags, the raw API object and the blog's own history to
    work from, none of which survive into this app's schema.
    """
    niche = (row.get("niche") or "").strip()
    if not niche or niche == UNCLASSIFIED:
        return GENERAL_NICHE
    if float(row.get("niche_confidence") or 0.0) < MIN_NICHE_CONFIDENCE:
        return GENERAL_NICHE
    return niche


def audience_rate(notes: int, audience_proxy: float) -> float:
    """Notes normalised by the blog's own typical reach. The Tumblr engagement rate.

    `audience_proxy` is the collector's `audience_proxy_notes`: the median note count of
    originals seen on the same blog. It is a proxy and named like one — Tumblr does not
    expose follower counts for blogs you do not control, with or without an API key.
    """
    return round(notes / (max(audience_proxy, 0.0) + AUDIENCE_PRIOR), 6)


def read_corpus(path: Path) -> list[dict]:
    """Every usable row from the collector's store. Read-only, and never writes to it."""
    if not path.exists():
        raise FileNotFoundError(
            f"No Tumblr corpus at {path}. Run the collector in "
            f"'V:\\tumblr social post creator' first, or point TUMBLR_CORPUS_PATH at one."
        )
    # Read-only URI so an import can never modify or lock out the collector, which may
    # still be running: this app is a consumer of that project's output, not a peer.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT uri, blog, post_id, post_url, text, tags, notes, created_at,
                       has_media, audience_proxy_notes, within_blog_percentile,
                       engagement_lift, selection_reason, niche, niche_confidence
                FROM posts
                WHERE text IS NOT NULL AND TRIM(text) <> ''
                """
            )
        ]
    except sqlite3.OperationalError as err:
        # The collector gained its niche columns partway through this app's life. An older
        # corpus is a clear message rather than a confusing SQL error.
        if "niche" in str(err):
            raise RuntimeError(
                f"The corpus at {path} predates the collector's niche classification. "
                f"Re-run the collector so it can classify posts, then import again."
            ) from None
        raise
    finally:
        conn.close()


def run(path: Path | None = None) -> dict:
    """Import the corpus into the app's post store. Returns per-niche counts and skips.

    The niches are the collector's own, not the app's shared list — see the comment on
    GENERAL_NICHE. Nothing here writes to the `niches` table.

    Safe to re-run: everything is upserted by URI, so a corpus that has grown since the
    last import contributes only its new rows and nothing is duplicated.
    """
    from vendor.socialpost.src import db as spg_db

    corpus_path = Path(path) if path else DEFAULT_CORPUS_PATH
    rows = read_corpus(corpus_path)
    now = spg_db.utcnow()
    stats: Counter = Counter()
    stats["scanned"] = len(rows)

    authors: dict[str, dict] = {}
    posts: list[dict] = []
    snapshots: list[dict] = []
    per_niche: Counter = Counter()

    for row in rows:
        text = (row["text"] or "").strip()
        tags = _tags_of(row["tags"])

        if len(text) < MIN_TEXT_CHARS:
            stats["skip_too_short"] += 1
            continue
        if _prose_words(text) < MIN_PROSE_WORDS:
            stats["skip_no_prose"] += 1
            continue

        niche = _niche_of(row)
        if niche == GENERAL_NICHE:
            stats["unclassified_to_general"] += 1

        key = corpus_niche(niche)
        did = f"tumblr:{row['blog']}"
        proxy = float(row["audience_proxy_notes"] or 0.0)

        # The proxy doubles as the author's audience size. It is a median note count,
        # not a follower count, and the column name in the shared schema says
        # follower_count — so it is rounded to an int and never presented as followers
        # anywhere in the UI.
        authors[did] = {
            "did": did,
            "handle": row["blog"],
            "follower_count": int(round(proxy)),
            "niche": key,
            "last_seen_at": spg_db.iso(now),
        }
        posts.append(
            {
                "uri": row["uri"],
                "platform": PLATFORM,
                "author_did": did,
                "text": text,
                "hashtags": tags,
                "has_media": int(row["has_media"] or 0),
                "created_at": row["created_at"],
                "niche": key,
                "ingested_at": spg_db.iso(now),
            }
        )
        snapshots.append(
            {
                "post_uri": row["uri"],
                "captured_at": spg_db.iso(now),
                # Tumblr note counts are lifetime totals read long after posting, so they
                # are settled by any definition. '48h' is the settled bucket the shared
                # schema permits, and the Mastodon collector labels its own settled
                # readings the same way for the same reason.
                "window_label": "48h",
                "likes": int(row["notes"] or 0),
                "reposts": 0,
                "replies": 0,
                "engagement_rate": audience_rate(int(row["notes"] or 0), proxy),
            }
        )
        per_niche[niche] += 1
        stats[f"reason_{row['selection_reason']}"] += 1

    if posts:
        spg_db.upsert("authors", list(authors.values()), on_conflict="did")
        spg_db.upsert("posts", posts, on_conflict="uri")
        spg_db.upsert(
            "engagement_snapshots", snapshots, on_conflict="post_uri,window_label"
        )

    result = {
        "corpus": str(corpus_path),
        "imported": len(posts),
        "blogs": len(authors),
        "perNiche": dict(per_niche),
        **{k: v for k, v in stats.items()},
    }
    log.info("[tumblr-post] import: %s", result)
    return result
