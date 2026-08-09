"""Runtime retrieval + prompt assembly + generation.

This is the read path. The jobs build the knowledge; this module spends it.

    generate("announce my new CLI tool", "indie makers", "bluesky")
      -> embed the request
      -> retrieve 5 active exemplars for the niche (pgvector similarity x score)
      -> retrieve 3 active KB articles for the platform (by decay_weight)
      -> assemble prompt, call the LLM
      -> record the generation and which sources fed it
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import embeddings, llm, sources, telemetry
from .db import get_client, iso, rpc, utcnow

log = logging.getLogger(__name__)

N_EXEMPLARS = 5
N_KB_ARTICLES = 3

# Weighting inside the match_exemplars RPC: 0.7 similarity / 0.3 performance.
# Tilted toward similarity because an off-topic exemplar actively misleads the
# model, whereas a merely-good-not-great on-topic one still teaches the voice.
SIMILARITY_WEIGHT = 0.7


@dataclass
class Exemplar:
    id: int
    post_uri: str
    text: str
    score: float
    similarity: float


@dataclass
class KBArticle:
    id: int
    source: str
    url: str
    summary: str
    decay_weight: float
    # Cross-instance-stable reference for telemetry: the same article yields the
    # same hash everywhere, unlike the per-install serial `id`.
    url_hash: str = ""


@dataclass
class GenerationResult:
    """What was produced, and everything that shaped it.

    The exemplars/kb_articles are carried (not just their ids) so the UI can show
    provenance without a second round-trip.
    """

    text: str
    generation_id: int | None
    exemplars: list[Exemplar] = field(default_factory=list)
    kb_articles: list[KBArticle] = field(default_factory=list)
    source: sources.FetchedSource | None = None

    @property
    def exemplar_ids(self) -> list[int]:
        return [e.id for e in self.exemplars]

    @property
    def kb_ids(self) -> list[int]:
        return [k.id for k in self.kb_articles]


def retrieve_exemplars(user_input: str, niche: str, n: int = N_EXEMPLARS) -> list[Exemplar]:
    """Top-n active exemplars for the niche, by blended similarity and score.

    Returns [] when the niche has no active pool yet (a fresh install, or a niche
    whose posts have not reached 48h). generate() handles that by falling back to
    platform norms alone rather than failing.
    """
    rows = rpc(
        "match_exemplars",
        {
            "query_embedding": embeddings.embed_one(user_input),
            "target_niche": niche,
            "match_count": n,
            "similarity_weight": SIMILARITY_WEIGHT,
        },
    )
    return [
        Exemplar(
            id=r["id"],
            post_uri=r["post_uri"],
            text=r["text"],
            score=float(r["score"] or 0.0),
            similarity=float(r["similarity"] or 0.0),
        )
        for r in rows
    ]


def retrieve_kb(platform: str, n: int = N_KB_ARTICLES) -> list[KBArticle]:
    """Top-n active KB articles for the platform, freshest guidance first.

    Ordered by decay_weight (which cleanup.py multiplies down weekly) rather than
    published_at, so a still-relevant older change can outrank recent noise.
    published_at breaks ties: everything ingested in the same week carries an
    identical decay_weight — on a fresh install every row is exactly 1.0 — and
    without a tiebreak the top-3 would be whatever order Postgres happened to
    return.

    Matches articles tagged for this platform or tagged "general".
    """
    rows = (
        get_client()
        .table("kb_articles")
        .select("id, source, url, url_hash, summary, decay_weight")
        .eq("active", True)
        .overlaps("platform_tags", [platform, "general"])
        .order("decay_weight", desc=True)
        .order("published_at", desc=True, nullsfirst=False)
        .limit(n)
        .execute()
        .data
        or []
    )
    return [
        KBArticle(
            id=r["id"],
            source=r["source"] or "",
            url=r["url"] or "",
            summary=r["summary"] or "",
            decay_weight=float(r["decay_weight"] or 0.0),
            url_hash=r.get("url_hash") or "",
        )
        for r in rows
    ]


def generate(
    user_input: str,
    niche: str,
    platform: str = "bluesky",
    persist: bool = True,
    source_url: str = "",
    avoid_texts: Sequence[str] = (),
) -> GenerationResult:
    """Generate one post grounded in the niche's exemplars and the platform KB.

    `source_url`, when given, is fetched and its readable text added to the prompt
    as sanctioned source material — the way to write about a specific article or
    release note without retyping its facts. A fetch failure raises
    sources.SourceError rather than silently generating an ungrounded post, since
    a post that quietly ignores the link the author supplied is worse than an
    error that says why.

    `avoid_texts` are posts already generated for this same request. Pass them
    when the author has asked for another attempt: an identical prompt produces a
    near-identical post however high the temperature, so the model has to be shown
    what it already wrote (see llm._AVOID_TEMPLATE).

    `persist=False` skips the generations row — used by tests and CLI probes that
    should not pollute the learning loop, and it also skips the consent gate and
    telemetry, since a probe is not real use.

    When pooled telemetry is enabled (TELEMETRY_ENDPOINT set) and the user has not
    accepted the current terms, this raises telemetry.ConsentRequired. Callers
    (UI, CLI) catch it to show the consent flow rather than erroring — participation
    is a precondition of use, by design.
    """
    if not user_input.strip():
        raise ValueError("generate() needs a non-empty user_input")

    if persist and telemetry.needs_consent():
        raise telemetry.ConsentRequired(
            "This tool pools anonymous performance metrics to improve results for "
            "everyone. Review and accept the telemetry terms before generating."
        )

    fetched = sources.fetch_url(source_url) if source_url.strip() else None

    # The retrieval query is the request plus the source's title: a bare "write
    # about this" carries almost no signal for picking exemplars, and the title
    # is the shortest honest summary of what the post will be about.
    retrieval_query = f"{user_input} {fetched.title}".strip() if fetched else user_input

    exemplars = retrieve_exemplars(retrieval_query, niche)
    kb_articles = retrieve_kb(platform)

    log.info(
        "Retrieved %d exemplars, %d KB articles for niche=%r platform=%r",
        len(exemplars),
        len(kb_articles),
        niche,
        platform,
    )
    if not exemplars:
        log.warning(
            "No active exemplars for niche %r — generation falls back to platform "
            "norms only. Run refresh_exemplars once posts have 48h snapshots.",
            niche,
        )

    text = llm.generate_post(
        user_input=user_input,
        niche=niche,
        platform=platform,
        exemplar_texts=[e.text for e in exemplars],
        kb_summaries=[k.summary for k in kb_articles],
        source=fetched,
        avoid_texts=avoid_texts,
    )

    generation_id: int | None = None
    if persist:
        created_at = iso(utcnow())
        uid = uuid.uuid4().hex
        resp = (
            get_client()
            .table("generations")
            .insert(
                {
                    "created_at": created_at,
                    "user_input": user_input,
                    "niche": niche,
                    "output_text": text,
                    "exemplar_ids": [e.id for e in exemplars],
                    "kb_ids": [k.id for k in kb_articles],
                    "uid": uid,
                }
            )
            .execute()
        )
        generation_id = resp.data[0]["id"]

        # Best-effort, non-blocking: queue the (references-only) generation record.
        # Its outcome is queued later by the telemetry job once a 48h snapshot lands.
        telemetry.enqueue(
            "generation",
            telemetry.build_generation_record(
                uid=uid,
                niche=niche,
                platform=platform,
                created_at=created_at,
                user_input=user_input,
                output_text=text,
                exemplars=[
                    {"uri": e.post_uri, "similarity": e.similarity, "score": e.score}
                    for e in exemplars
                ],
                kb_articles=[
                    {"url_hash": k.url_hash, "decay_weight": k.decay_weight}
                    for k in kb_articles
                ],
                similarity_weight=SIMILARITY_WEIGHT,
            ),
        )

    return GenerationResult(
        text=text,
        generation_id=generation_id,
        exemplars=exemplars,
        kb_articles=kb_articles,
        source=fetched,
    )


_BSKY_WEB_RE = re.compile(
    r"^https?://(?:www\.)?bsky\.app/profile/(?P<actor>[^/]+)/post/(?P<rkey>[^/?#]+)"
)


def normalise_post_uri(value: str) -> str:
    """Accept either an at:// URI or a bsky.app web link; return an at:// URI.

    People copy what the browser shows them, which is
    https://bsky.app/profile/<handle>/post/<rkey>, not the at:// URI the protocol
    uses. Rejecting that would make the feedback loop feel broken, so resolve the
    handle to a DID and rebuild the canonical URI.
    """
    value = value.strip()
    if value.startswith("at://"):
        return value

    match = _BSKY_WEB_RE.match(value)
    if not match:
        raise ValueError(
            f"Could not read {value!r} as a Bluesky post. Paste either the post's "
            f"web link (https://bsky.app/profile/.../post/...) or its at:// URI."
        )

    actor, rkey = match.group("actor"), match.group("rkey")
    if actor.startswith("did:"):
        did = actor
    else:
        # bluesky is imported lazily: generation.py is imported by the Streamlit
        # app, which should not need Bluesky credentials just to render.
        from . import bluesky

        profile = bluesky.get_client().app.bsky.actor.get_profile({"actor": actor})
        did = profile.did

    return f"at://{did}/app.bsky.feed.post/{rkey}"


def ensure_post_exists(post_uri: str, niche: str) -> bool:
    """Make sure `post_uri` has a row in posts, fetching it if needed.

    generations.posted_uri is a FK to posts. A post the user published seconds ago
    has not been ingested yet, so attaching it would violate the FK — the loop
    would only close for posts that happened to match a niche keyword, which is
    exactly the wrong subset. Fetch it on demand instead.

    Returns True if the post is now present, False if Bluesky would not return it.
    """
    from . import bluesky

    client = get_client()
    existing = client.table("posts").select("uri").eq("uri", post_uri).execute().data
    if existing:
        return True

    fetched = bluesky.get_posts([post_uri])
    post = fetched.get(post_uri)
    if post is None:
        return False

    profiles = bluesky.get_profiles([post.author_did])
    profile = profiles.get(post.author_did)
    if profile is None:
        return False

    now = utcnow()
    client.table("authors").upsert(
        {
            "did": profile.did,
            "handle": profile.handle,
            "follower_count": profile.follower_count,
            "niche": niche,
            "last_seen_at": iso(now),
        },
        on_conflict="did",
    ).execute()
    client.table("posts").upsert(
        {
            "uri": post.uri,
            "platform": "bluesky",
            "author_did": post.author_did,
            "text": post.text,
            "hashtags": post.hashtags,
            "has_media": post.has_media,
            "created_at": iso(post.created_at),
            "niche": niche,
            "ingested_at": iso(now),
        },
        on_conflict="uri",
    ).execute()
    log.info("Backfilled published post %s into posts", post_uri)
    return True


def attach_posted_uri(generation_id: int, posted_uri: str, niche: str) -> str:
    """Record that a generation was actually published, and return the at:// URI.

    This is what closes the learning loop: watchdog.py can only judge the system
    against reality for generations that carry a posted_uri, and snapshot.py will
    then pick the post up at the 1h/24h/48h buckets like any other.

    Accepts a web link or an at:// URI, and backfills the post row if needed.
    """
    uri = normalise_post_uri(posted_uri)
    if not ensure_post_exists(uri, niche):
        raise ValueError(
            f"Bluesky did not return a post for {uri}. Check the link is right and "
            f"the post is public."
        )
    get_client().table("generations").update({"posted_uri": uri}).eq(
        "id", generation_id
    ).execute()
    return uri
