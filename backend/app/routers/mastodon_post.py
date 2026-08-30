"""Mastodon Post Creator — the Bluesky generator's sibling, adapted to the fediverse.

Same idea as routers/social_post.py: learn from posts that actually performed in
a niche, then write in that register. Three things are deliberately different,
each because Mastodon is different, not because this was written second.

1. A rules gate stands in front of generation.
   Mastodon has no central terms of service. Each instance publishes its own,
   several require generative-AI use to be disclosed, and several ban commercial
   promotion outright. So /generate refuses with 409 until the user has been
   shown their instance's live rules and accepted them, and the acceptance is
   fingerprinted against the rule text (services/mastodon.py InstancePolicy)
   so an edit upstream re-closes the gate.

2. Grounding is immediate rather than 48 hours out.
   The Bluesky tool collects fresh posts and must wait for engagement to
   accumulate before anything can be an exemplar. A Mastodon hashtag timeline
   hands back posts that already carry their favourite/boost/reply counts, so a
   settled post can be scored the moment it is collected. Collection therefore
   builds the exemplar pool in one pass, and there is no cold start.

3. The corpus is namespaced per platform *and* per instance.
   Rows go into the vendored socialpost store (so embeddings, exemplar ranking
   and the LLM plumbing are shared, and vendor/socialpost stays unmodified) but
   under a niche key of "<niche> · mastodon · <host>".

   The platform half is what stops the Bluesky scheduler's refresh_exemplars —
   which deactivates a niche's entire pool and replaces it — from periodically
   deleting every Mastodon exemplar and leaving the two tools grounding each
   other's drafts in the wrong platform's voice.

   The instance half exists because an instance is a culture, not just an
   endpoint. hachyderm.io and toot.garden reward visibly different registers, and
   one pooled corpus averages them into a voice that suits neither. Splitting it
   also brings what the tool learns into line with what it already asks
   permission for, since the rules gate was per-instance from the start.

   An instance with fewer than MIN_INSTANCE_EXEMPLARS of its own borrows from the
   wider Mastodon pool until it has collected enough, so a newly added server is
   grounded from its first post rather than starting blank. Rows written before
   the split keep the old platform-only key and stay readable as fallback: which
   instance they came from was never recorded, so assigning them now would be a
   guess, and guessing wrong is the blending this is here to stop.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..services.genqueue import queue_slot
from pydantic import BaseModel

from .. import db
from ..services import brand_voice, image_prompt
from ..services import mastodon as masto
from ..services import mastodon_gate as gate
from ..services.mastodon import MastodonError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mastodon-post", tags=["mastodon-post"])

# Corpus tuning. Smaller pools than the Bluesky tool's because a hashtag timeline
# is a narrower net than keyword search, and a thin pool of on-topic posts beats
# a fat one padded with near-misses.
TARGET_POOL_SIZE = 15
N_EXEMPLARS = 5
SIMILARITY_WEIGHT = 0.7
HALF_LIFE_DAYS = 14.0

# A post needs time on the timeline before its counts mean anything. Below this
# a zero is indistinguishable from "nobody has seen it yet", and scoring it would
# teach the pool that good posts get no engagement.
MIN_SETTLE_HOURS = 24

# Same floor as the Bluesky tool's exemplar pool, for the same reason: below it,
# engagement_rate is an artefact of dividing by a tiny number.
MIN_FOLLOWERS = 50

# How much engagement a settled post needs before it can be an exemplar.
#
# The pool takes the top TARGET_POOL_SIZE by score with no floor, which is fine while a
# niche has more measured posts than slots and silently wrong once it has fewer: every
# post gets in, including the ones nobody touched. Measured on the live corpus, 41 of 187
# active Mastodon exemplars had *zero* interactions — 10 of the 15 in one niche — so the
# generator was being shown posts that demonstrably did not work and told to write like
# them. A thin pool of posts that earned something beats a full one padded with silence,
# and an instance that drops below MIN_INSTANCE_EXEMPLARS already borrows from the wider
# Mastodon pool rather than generating ungrounded, so shrinking is a handled outcome.
#
# Two rather than one: a single interaction is hard to tell from a self-boost or a passing
# bot, and the median settled post in this corpus has exactly one.
MIN_EXEMPLAR_INTERACTIONS = 2

# Words a post must have left, once hashtags and URLs are removed, to be an exemplar.
#
# Exemplars are shown to the model as "write like this", so a hashtag wall teaches it to
# emit hashtag walls. These clear the engagement floor easily — tag spam is engagement
# bait and it works — so the floor alone does not catch them. Measured on the live corpus:
# a threshold of 4 drops exactly the six tag-wall exemplars ("#paintings #art #artist
# #painting #artistsoninstagram…") and nothing else, and touches 5% of stored posts.
#
# This also excludes genuinely short posts ("Shipped it!"). That is the right trade: a
# three-word post is not a useful model for writing one either way.
MIN_EXEMPLAR_PROSE_WORDS = 4

# Follower count added to every denominator before ranking (NOT before storing).
#
# Raw interactions/followers is the right *measurement* and the wrong *ranking*: it is
# dominated by whoever has fewest followers. Measured on the live mastodon.social corpus,
# the five highest-rate posts had 5-23 interactions from 52-205 followers, while a post
# with 158 interactions from 1,924 followers ranked below them — and the mean follower
# count of chosen exemplars (487) sat *below* the corpus mean (729), which is the bias
# stated plainly. Hashtag spam was winning slots on real niches.
#
# Adding a prior to the denominator scores every post as though its author had at least a
# median-sized following, so a tiny account has to earn its place instead of being handed
# it by division. 292 is that median, measured over the 601 Mastodon posts in the corpus.
# It is deliberately not a magic number: recompute it if the corpus shifts substantially.
RANKING_FOLLOWER_PRIOR = 292

PLATFORM = "mastodon"

# Matches the Bluesky composer: each attempt is up to a few hundred characters and they
# all ride in the prompt, so an unbounded list would quietly eat the generation budget.
MAX_AVOID_TEXTS = 3


def _spg():
    """Lazy handle on the vendored package (it pulls torch in via embeddings)."""
    from vendor.socialpost.src import db as spg_db
    from vendor.socialpost.src import embeddings, llm

    return spg_db, embeddings, llm


# Below this many exemplars of its own, an instance borrows from the wider Mastodon pool
# rather than generating ungrounded. Eight is where the retrieval starts having something
# to choose between; under that the "closest" exemplars are just whatever exists.
MIN_INSTANCE_EXEMPLARS = 8


def _corpus_niche(niche: str, host: str) -> str:
    """The namespaced key Mastodon rows live under. See the module docstring.

    Keyed by instance as well as platform, because an instance is a culture and not just
    an endpoint. hachyderm.io and toot.garden reward visibly different registers, and a
    single pooled corpus averages them into a voice that suits neither. The rules gate was
    already per-instance and fingerprinted; this makes what the tool *learns* match what it
    already asks permission for.
    """
    return f"{niche} · mastodon · {host}"


def _legacy_corpus_niche(niche: str) -> str:
    """The key used before the corpus was split by instance.

    Rows written under it are not migrated. There is no record of which instance they came
    from, so any assignment would be a guess, and guessing wrong is exactly the blending
    this change exists to stop. They stay readable as fallback material instead, which
    costs nothing and keeps existing installs grounded on the first post after updating.
    """
    return f"{niche} · mastodon"


def _split_corpus_niche(key: str) -> tuple[str, str] | None:
    """The inverse of `_corpus_niche`: (plain niche, host) from a namespaced key.

    Returns None for the host-less keys `_legacy_corpus_niche` writes. Those rows
    predate the split and carry no instance, so there is no pool to scope a rebuild
    to — inventing one would be exactly the blending the namespacing exists to stop.

    Matches the platform separator from the right rather than splitting on " · ",
    because a niche name is user-supplied and may contain the separator itself.
    """
    head, sep, host = key.rpartition(f" · {PLATFORM} · ")
    if sep and head and host:
        return head, host
    return None


def _fallback_keys(niche: str, host: str) -> list[str]:
    """Corpora to borrow from when this instance has too few exemplars of its own.

    A newly added instance starts empty, and a post grounded in a slightly-off register
    beats one grounded in nothing — the borrowing stops by itself once the instance has
    collected `MIN_INSTANCE_EXEMPLARS` of its own.

    Read from the local acks table rather than `_accepted_hosts()`: this runs on the
    generate path, and that helper makes a network call per instance to re-check
    fingerprints. Borrowing style from an instance whose rules have since changed is not
    the risk that check exists to prevent — publishing to it is, and that is gated
    separately.
    """
    keys = [_legacy_corpus_niche(niche)]
    for ack in db.list_mastodon_acks():
        other = (ack.get("instance") or "").strip().lower().removeprefix("https://").strip("/")
        if other and other != host:
            keys.append(_corpus_niche(niche, other))
    return keys


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RuleOut(BaseModel):
    id: str
    text: str
    hint: str
    relevant: bool  # touches AI, automation, or commercial use


class PolicyResponse(BaseModel):
    instance: str
    title: str
    version: str
    maxCharacters: int
    rules: list[RuleOut]
    extendedDescription: str
    policyHash: str
    accepted: bool
    acceptedAt: str | None = None
    # True when the instance has edited its rules since the user accepted them.
    changedSinceAccepted: bool = False


class AcceptRequest(BaseModel):
    instance: str
    policyHash: str


class NicheOut(BaseModel):
    name: str
    keywords: list[str]
    posts: int
    exemplars: int


class StatusResponse(BaseModel):
    instance: str
    configured: bool
    missing: list[str]
    reachable: bool
    detail: str = ""
    title: str = ""
    maxCharacters: int = 0
    rulesAccepted: bool = False
    niches: int = 0
    posts: int = 0
    exemplars: int = 0
    readyToGround: bool = False
    provider: str = ""
    model: str = ""


class CollectRequest(BaseModel):
    instance: str
    niche: str
    accessToken: str = ""
    limit: int = 60


class CollectResponse(BaseModel):
    scanned: int
    stored: int
    skipped: dict[str, int]
    exemplars: int


class GenerateRequest(BaseModel):
    instance: str
    niche: str
    userInput: str
    accessToken: str = ""
    sourceUrl: str = ""
    # Optional Library id of a Brand Studio document; folded into user_input in compact
    # form for the same length reasons as the Bluesky composer.
    brandVoiceId: str = ""
    # Posts already written for this request, sent when the author asks for another
    # attempt. Without them an identical prompt reproduces its own opening line however
    # high the temperature — the Bluesky composer hit exactly this and fixed it the same
    # way. Capped server-side so a client cannot grow the prompt without bound.
    avoidTexts: list[str] = []
    discloseAi: bool = True


class AnalyticsRequest(BaseModel):
    instance: str
    accessToken: str = ""
    limit: int = 40


class PostAnalyticsOut(BaseModel):
    postUri: str
    webUrl: str
    instance: str
    text: str
    publishedAt: str
    likes: int
    reposts: int
    replies: int
    engagementRate: float
    # True when this post is linked to a draft written here, via the composer's
    # "close the loop" step. False means "not linked", not "not written here".
    fromApp: bool = False


class AnalyticsResponse(BaseModel):
    posts: list[PostAnalyticsOut]
    totals: dict[str, float]
    account: str = ""


class ExemplarOut(BaseModel):
    id: int
    text: str
    similarity: float
    score: float
    webUrl: str
    author: str


class ComplianceOut(BaseModel):
    """What the instance requires of this post, resolved from its live rules."""

    disclosureApplied: bool
    disclosureLine: str
    suggestedVisibility: str
    notes: list[str]


class GenerateResponse(BaseModel):
    text: str
    generationId: int | None
    characters: int
    maxCharacters: int
    overLimit: bool
    exemplars: list[ExemplarOut]
    compliance: ComplianceOut
    libraryId: str | None = None


class PublishedRequest(BaseModel):
    instance: str
    generationId: int
    postedUrl: str
    niche: str
    accessToken: str = ""


# ---------------------------------------------------------------------------
# The rules gate
# ---------------------------------------------------------------------------


def _load_policy(instance: str) -> masto.InstancePolicy:
    try:
        return gate.load_policy(instance)
    except MastodonError as err:
        raise HTTPException(status_code=502, detail=str(err)) from None


@router.get("/policy", response_model=PolicyResponse)
def get_policy(instance: str) -> PolicyResponse:
    """The instance's live rules plus its About page, and whether they're accepted.

    Always re-fetched rather than served from the stored copy. The stored copy
    exists to prove what was agreed to, not to save a request — showing a cached
    rule set would defeat the point of a gate whose job is to reflect what the
    instance says right now.
    """
    policy = _load_policy(instance)
    ack = db.get_mastodon_ack(policy.info.host)
    current_hash = policy.fingerprint

    return PolicyResponse(
        instance=policy.info.host,
        title=policy.info.title,
        version=policy.info.version,
        maxCharacters=policy.info.max_characters,
        rules=[
            RuleOut(
                id=r.id,
                text=r.text,
                hint=r.hint,
                relevant=masto.is_relevant(f"{r.text} {r.hint}"),
            )
            for r in policy.rules
        ],
        extendedDescription=policy.extended_description,
        policyHash=current_hash,
        accepted=bool(ack and ack["policy_hash"] == current_hash),
        acceptedAt=ack["accepted_at"] if ack else None,
        changedSinceAccepted=bool(ack and ack["policy_hash"] != current_hash),
    )


@router.post("/policy/accept")
def accept_policy(body: AcceptRequest) -> dict:
    """Record acceptance — but only of the rules as they stand right now.

    The hash the client sends must match a freshly fetched one. If the instance
    edited its rules between the screen rendering and the click, this 409s rather
    than recording consent to text the user was never shown.
    """
    policy = _load_policy(body.instance)
    current_hash = policy.fingerprint
    if body.policyHash != current_hash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{policy.info.host} changed its rules while you were reading them. "
                f"Reload and review the current version before accepting."
            ),
        )
    gate.record(policy)
    log.info("[mastodon-post] rules accepted for %s (%s)", policy.info.host, current_hash)
    return {"accepted": True, "instance": policy.info.host, "policyHash": current_hash}


@router.delete("/policy/accept")
def revoke_policy(instance: str) -> dict:
    """Withdraw acceptance, sending the user back to the rules screen."""
    try:
        host = masto.normalise_host(instance)
    except MastodonError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    db.clear_mastodon_ack(host)
    return {"accepted": False}


def _require_accepted(instance: str) -> masto.InstancePolicy:
    """The gate, as a 409. See services/mastodon_gate.py for what it enforces and why.

    Every path that puts words on the fediverse — or reads other people's off it —
    goes through this.
    """
    try:
        return gate.require_accepted(instance)
    except gate.PolicyNotAccepted as err:
        raise HTTPException(status_code=409, detail=str(err)) from None
    except MastodonError as err:
        raise HTTPException(status_code=502, detail=str(err)) from None


# ---------------------------------------------------------------------------
# Status + niches
# ---------------------------------------------------------------------------


def _counts(niche: str, host: str) -> tuple[int, int]:
    """(posts, active exemplars) this instance has of its own.

    Deliberately not counting borrowed material. The number answers "how well does this
    tool know how to write for *this* instance", and folding in another server's corpus
    would report a readiness the instance has not earned.
    """
    spg_db, _, _ = _spg()
    key = _corpus_niche(niche, host)
    client = spg_db.get_client()
    try:
        posts = (
            client.table("posts")
            .select("*", count="exact")
            .eq("niche", key)
            .limit(0)
            .execute()
            .count
            or 0
        )
        exemplars = (
            client.table("exemplars")
            .select("*", count="exact")
            .eq("niche", key)
            .eq("active", True)
            .limit(0)
            .execute()
            .count
            or 0
        )
    except Exception:  # noqa: BLE001 — an unconfigured store reads as empty, not 500
        return 0, 0
    return posts, exemplars


@router.get("/status", response_model=StatusResponse)
def status(instance: str = "") -> StatusResponse:
    import os

    spg_db, _, spg_llm = _spg()

    missing: list[str] = []
    if not instance.strip():
        missing.append("Mastodon instance")
    # Hugging Face only — see the note in routers/social_post.py.
    if not (os.environ.get("HF_TOKEN") or "").strip():
        missing.append("HF_TOKEN")

    try:
        niches = spg_db.list_niches()
    except Exception:  # noqa: BLE001
        niches = []

    # Scoped to the instance being asked about. "Ready to ground" is a per-server
    # question now: the same niche can be well stocked on one instance and empty on
    # another, and a total across all of them would claim readiness this server has not.
    host = (instance or "").strip().lower().removeprefix("https://").strip("/")
    posts = exemplars = 0
    if host:
        for row in niches:
            p, e = _counts(row["name"], host)
            posts += p
            exemplars += e

    out = StatusResponse(
        instance=instance.strip(),
        configured=not [m for m in missing if m != "Mastodon instance"],
        missing=missing,
        reachable=False,
        niches=len(niches),
        posts=posts,
        exemplars=exemplars,
        readyToGround=exemplars > 0,
        provider=spg_llm.provider(),
        model=spg_llm.model_name(),
    )
    if not instance.strip():
        return out

    # Reachability is reported, never raised: an unreachable instance is a thing
    # the screen should say out loud, not a 502 that reads as the app being broken.
    try:
        info = masto.instance_info(instance)
    except MastodonError as err:
        return out.model_copy(update={"detail": str(err)})

    ack = db.get_mastodon_ack(info.host)
    return out.model_copy(
        update={
            "instance": info.host,
            "reachable": True,
            "title": info.title,
            "maxCharacters": info.max_characters,
            "rulesAccepted": bool(ack),
        }
    )


@router.get("/niches", response_model=list[NicheOut])
def list_niches(instance: str = "") -> list[NicheOut]:
    """Niches, shared with the Bluesky tool — a niche is a niche.

    The counts are Mastodon-only, because they answer "can this ground a
    Mastodon draft?" and a Bluesky corpus cannot. They are also per-instance: the
    same niche can be well grounded on one server and empty on another, and a
    single number would hide that.
    """
    spg_db, _, _ = _spg()
    host = (instance or "").strip().lower().removeprefix("https://").strip("/")
    out: list[NicheOut] = []
    for row in spg_db.list_niches():
        posts, exemplars = _counts(row["name"], host) if host else (0, 0)
        out.append(
            NicheOut(
                name=row["name"],
                keywords=[str(k) for k in (row["keywords"] or [])],
                posts=posts,
                exemplars=exemplars,
            )
        )
    return out


class VerifyRequest(BaseModel):
    instance: str
    accessToken: str


@router.post("/verify")
def verify(body: VerifyRequest) -> dict:
    """Check an access token, and report the flags an automated poster should set.

    Takes the token in the body rather than a query string. The backend only ever
    binds to localhost, but a credential in a URL still lands in access logs and
    anything that mirrors them, and a request body costs nothing to use instead.
    """
    try:
        return masto.verify_credentials(body.instance, body.accessToken)
    except MastodonError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _decay(age_days: float) -> float:
    """Exponential recency weight, same 14-day half-life as the Bluesky pool."""
    return 0.5 ** (max(age_days, 0.0) / HALF_LIFE_DAYS)


def _age_days(created_at: str | None, now: datetime) -> float:
    """How old a stored post is, in days. Unparseable dates score as brand new.

    Erring toward no decay rather than total decay: a bad timestamp should cost a
    post its recency bonus at worst, not silently exclude it from the pool.
    """
    if not created_at:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((now - parsed).total_seconds() / 86400.0, 0.0)


@router.post("/collect", response_model=CollectResponse)
def collect(body: CollectRequest) -> CollectResponse:
    """One collection pass, on demand.

    Gated on the rules. Collection reads other people's posts into a corpus,
    which is exactly the activity an instance's scraping and indexing rules speak
    to, so it would be incoherent to gate generation on the rules but not this.
    """
    policy = _require_accepted(body.instance)
    spg_db, _, _ = _spg()

    niche_row = spg_db.get_niche(body.niche)
    if niche_row is None:
        raise HTTPException(status_code=400, detail=f"No niche called {body.niche!r}.")
    if not [str(k) for k in (niche_row["keywords"] or [])]:
        raise HTTPException(status_code=400, detail=f"{body.niche!r} has no keywords.")

    return _collect_niche(policy.info.host, body.niche, body.accessToken, body.limit)


def _collect_niche(
    host: str, niche: str, access_token: str = "", limit: int = 60
) -> CollectResponse:
    """Read a niche's hashtags on one instance and store what may be learned from.

    Unlike the Bluesky ingest this also rebuilds the exemplar pool in the same
    pass — see the module docstring on why Mastodon needs no 48-hour wait.

    Split out from the endpoint so the background scheduler runs exactly the same
    code path rather than a parallel implementation that can drift from it.
    """
    spg_db, _, _ = _spg()
    niche_row = spg_db.get_niche(niche)
    if niche_row is None:
        return CollectResponse(scanned=0, stored=0, skipped={}, exemplars=0)
    keywords = [str(k) for k in (niche_row["keywords"] or [])]
    if not keywords:
        return CollectResponse(scanned=0, stored=0, skipped={}, exemplars=0)

    key = _corpus_niche(niche, host)
    now = spg_db.utcnow()
    settled_before = now - timedelta(hours=MIN_SETTLE_HOURS)

    scanned = 0
    skipped: dict[str, int] = {}
    keep: dict[str, masto.Status] = {}

    per_keyword = max(10, limit // max(len(keywords), 1))
    for keyword in keywords:
        try:
            statuses = masto.tag_timeline(host, keyword, per_keyword, access_token)
        except MastodonError as err:
            # One bad hashtag must not cost the whole pass.
            log.warning("[mastodon-post] #%s failed: %s", keyword, err)
            skipped["fetch failed"] = skipped.get("fetch failed", 0) + 1
            continue

        for status in statuses:
            scanned += 1
            ok, why = masto.should_learn_from(status)
            if not ok:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            if status.created_at is None or status.created_at > settled_before:
                skipped["too recent to score"] = skipped.get("too recent to score", 0) + 1
                continue
            if status.account.followers < MIN_FOLLOWERS:
                skipped["author below follower floor"] = (
                    skipped.get("author below follower floor", 0) + 1
                )
                continue
            keep[status.id] = status

    if not keep:
        return CollectResponse(scanned=scanned, stored=0, skipped=skipped, exemplars=0)

    # --- write the corpus ---------------------------------------------------
    authors = {s.account.url or s.account.acct: s.account for s in keep.values()}
    spg_db.upsert(
        "authors",
        [
            {
                "did": did,
                "handle": account.acct,
                "follower_count": account.followers,
                "niche": key,
                "last_seen_at": spg_db.iso(now),
            }
            for did, account in authors.items()
        ],
        on_conflict="did",
    )

    post_rows = []
    meta_rows = []
    for status in keep.values():
        uri = masto.corpus_uri(host, status.id)
        post_rows.append(
            {
                "uri": uri,
                "platform": PLATFORM,
                "author_did": status.account.url or status.account.acct,
                "text": status.text,
                "hashtags": status.hashtags,
                "has_media": status.has_media,
                "created_at": spg_db.iso(status.created_at),
                "niche": key,
                "ingested_at": spg_db.iso(now),
            }
        )
        meta_rows.append(
            {
                "post_uri": uri,
                "instance": host,
                "status_id": status.id,
                "web_url": status.url,
                "account_acct": status.account.acct,
            }
        )
    spg_db.upsert("posts", post_rows, on_conflict="uri")
    db.upsert_mastodon_post_meta(meta_rows)

    # Engagement is already on the statuses, so record it as the 48h measurement
    # rather than scheduling one. Anything past MIN_SETTLE_HOURS has done most of
    # what it is going to do; Mastodon has no algorithmic resurfacing to wait for.
    spg_db.upsert(
        "engagement_snapshots",
        [
            {
                "post_uri": masto.corpus_uri(host, s.id),
                "captured_at": spg_db.iso(now),
                "window_label": "48h",
                "likes": s.favourites,
                "reposts": s.reblogs,
                "replies": s.replies,
                "engagement_rate": masto.engagement_rate(
                    s.favourites, s.reblogs, s.replies, s.account.followers
                ),
            }
            for s in keep.values()
        ],
        on_conflict="post_uri,window_label",
    )

    n_exemplars = _rebuild_pool(niche, host)
    log.info(
        "[mastodon-post] %s: scanned %d, stored %d, pool %d",
        niche,
        scanned,
        len(keep),
        n_exemplars,
    )
    return CollectResponse(
        scanned=scanned, stored=len(keep), skipped=skipped, exemplars=n_exemplars
    )


_TAG_RE = re.compile(r"#\w+", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+")


def _prose_words(text: str) -> int:
    """How many words remain once hashtags and links are taken out. See the constant."""
    stripped = _URL_RE.sub(" ", _TAG_RE.sub(" ", text or ""))
    return len(re.findall(r"\w+", stripped, re.UNICODE))


def _smoothed_rate(interactions: int, followers: int) -> float:
    """Engagement rate for *ranking*, with a follower prior in the denominator.

    Deliberately not services.mastodon.engagement_rate, which stays exactly as it is.
    That function is the stored measurement and matches the Bluesky tool's definition
    so the two corpora remain comparable; this is only ever used to order candidates
    within one instance's pool. Keeping them separate means the numbers the Analytics
    screen shows are still the real ones. See RANKING_FOLLOWER_PRIOR.
    """
    return interactions / (max(followers, 0) + RANKING_FOLLOWER_PRIOR)


def _follower_counts(dids: set[str]) -> dict[str, int]:
    """Current follower count per author, from the corpus's own authors table."""
    if not dids:
        return {}
    spg_db, _, _ = _spg()
    client = spg_db.get_client()
    out: dict[str, int] = {}
    ordered = list(dids)
    for i in range(0, len(ordered), 100):
        rows = (
            client.table("authors")
            .select("did, follower_count")
            .in_("did", ordered[i : i + 100])
            .execute()
            .data
            or []
        )
        for row in rows:
            out[row["did"]] = int(row["follower_count"] or 0)
    return out


#: Pool slots held for the user's own published posts, when they have one that qualifies.
#
# One, for the same reason the Tumblr tool reserves one: the point is that the generator sees
# *an* example of what worked for this account, not that the pool fills up with self-imitation.
RESERVED_OWN_SLOTS = 1


def _own_post_uris(key: str) -> set[str]:
    """Toots the user published from a draft, in this niche.

    `generations.posted_uri` is the record of that — written by /published when the link is
    pasted in, and by the automatic sweep in services/generation_link.py when the app was
    what published it.
    """
    spg_db, _, _ = _spg()
    rows = (
        spg_db.get_client()
        .table("generations")
        .select("posted_uri, niche")
        .eq("niche", key)
        .execute()
        .data
        or []
    )
    return {
        row["posted_uri"]
        for row in rows
        if (row.get("posted_uri") or "").startswith("mastodon://")
    }


def _reserve_own_slot(
    chosen: list[tuple[float, dict]], scored: list[tuple[float, dict]], key: str
) -> list[tuple[float, dict]]:
    """Give the user's best qualifying toot a slot, displacing the weakest earned entry.

    WHY A RESERVED SLOT RATHER THAN FAIR COMPETITION. Ranking divides interactions by
    followers plus RANKING_FOLLOWER_PRIOR, which is right for comparing strangers and
    hopeless for a new account: three followers against a prior of 292 means a post that did
    genuinely well for this account still scores near zero beside a corpus of posts that did
    well for accounts with thousands. Without a reserved slot the generator would never once
    be shown something that worked for the person using it.

    DELIBERATELY NOT A BYPASS OF THE FLOORS. `scored` has already dropped anything under
    MIN_EXEMPLAR_INTERACTIONS or without real prose, so a post nobody reacted to gets no slot
    — reserving one for a flop would teach the generator to write like a post that did not
    work, which is exactly what those floors exist to prevent.

    A no-op when the user has published nothing here, when nothing they published clears the
    floors, or when their post already made the pool on merit.
    """
    if not chosen:
        return chosen
    own_uris = _own_post_uris(key)
    if not own_uris:
        return chosen
    if sum(1 for _, post in chosen if post["uri"] in own_uris) >= RESERVED_OWN_SLOTS:
        return chosen

    best_own = next((pair for pair in scored if pair[1]["uri"] in own_uris), None)
    if best_own is None:
        return chosen

    # Drop the weakest earned entry rather than growing the pool. Trimmed against the pool
    # actually handed over, not against TARGET_POOL_SIZE: a niche that scored fewer posts
    # than the target would otherwise gain an entry instead of trading one, and the pool is
    # a budget on what rides in the prompt rather than a target to reach.
    kept = [pair for pair in chosen if pair[1]["uri"] != best_own[1]["uri"]]
    kept = kept[: max(0, len(chosen) - RESERVED_OWN_SLOTS)]
    log.info(
        "[mastodon-post] %s: reserving a pool slot for the user's own %s (score %.6f vs "
        "pool best %.6f)",
        key,
        best_own[1]["uri"],
        best_own[0],
        chosen[0][0],
    )
    return [best_own, *kept]


def _rebuild_pool(niche: str, host: str) -> int:
    """Replace the niche's Mastodon exemplar pool from everything measured so far.

    A compact mirror of vendor/socialpost's refresh_exemplars, scoped to the
    namespaced niche. Same shape — score by follower-normalised engagement decayed
    by age, deactivate the old pool, insert the new one — minus the diversity
    de-duplication pass, which needs a much larger candidate set than a hashtag
    timeline produces to be worth its cost.

    Two deliberate departures from the vendored job, both because a hashtag timeline
    on a small instance yields far fewer measured posts than a Bluesky keyword search:
    a post must clear MIN_EXEMPLAR_INTERACTIONS to be eligible at all, and ranking uses
    _smoothed_rate rather than the stored engagement_rate. Without the first, a niche
    with fewer measured posts than slots admits everything including posts with no
    engagement; without the second, whoever has fewest followers wins.
    """
    spg_db, embeddings, _ = _spg()
    key = _corpus_niche(niche, host)
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
    counts: dict[str, int] = {}
    uris = list(by_uri)
    for i in range(0, len(uris), 100):
        rows = (
            client.table("engagement_snapshots")
            .select("post_uri, likes, reposts, replies")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        )
        for row in rows:
            counts[row["post_uri"]] = (
                (row["likes"] or 0) + (row["reposts"] or 0) + (row["replies"] or 0)
            )

    followers = _follower_counts({p["author_did"] for p in posts if p.get("author_did")})

    scored: list[tuple[float, dict]] = []
    for uri, interactions in counts.items():
        post = by_uri.get(uri)
        if not post or not (post.get("text") or "").strip():
            continue
        # An exemplar has to be evidence the post worked, and nothing is not evidence.
        if interactions < MIN_EXEMPLAR_INTERACTIONS:
            continue
        # ...and it has to be a piece of writing, not a wall of tags.
        if _prose_words(post.get("text") or "") < MIN_EXEMPLAR_PROSE_WORDS:
            continue
        rate = _smoothed_rate(interactions, followers.get(post.get("author_did") or "", 0))
        scored.append((rate * _decay(_age_days(post.get("created_at"), now)), post))

    if not scored:
        return 0

    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = _reserve_own_slot(scored[:TARGET_POOL_SIZE], scored, key)
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


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _retrieve_exemplars(niche: str, host: str, query: str, n: int = N_EXEMPLARS) -> list[dict]:
    """Top-n Mastodon exemplars for the niche, by blended similarity and score.

    Reimplemented over the client's public surface rather than the vendored
    match_exemplars RPC because that one is niche-scoped only — it has no way to
    express "and only this platform's posts", which is the whole point here.
    """
    import numpy as np

    spg_db, embeddings, _ = _spg()
    client = spg_db.get_client()

    def _pool(keys: list[str]) -> list[dict]:
        return (
            client.table("exemplars")
            .select("id, post_uri, score, embedding")
            .in_("niche", keys)
            .eq("active", True)
            .execute()
            .data
            or []
        )

    rows = _pool([_corpus_niche(niche, host)])
    # A thin pool means retrieval has nothing to choose between, so top it up from the
    # wider Mastodon corpus rather than ground the post on two near-misses. Own exemplars
    # still come first and this stops once the instance has enough of its own.
    if len(rows) < MIN_INSTANCE_EXEMPLARS:
        seen = {r["id"] for r in rows}
        rows += [r for r in _pool(_fallback_keys(niche, host)) if r["id"] not in seen]
    rows = [r for r in rows if r.get("embedding") is not None]
    if not rows:
        return []

    texts = {
        p["uri"]: p["text"]
        for p in (
            client.table("posts")
            .select("uri, text")
            .in_("uri", [r["post_uri"] for r in rows][:100])
            .execute()
            .data
            or []
        )
    }
    rows = [r for r in rows if texts.get(r["post_uri"])]
    if not rows:
        return []

    query_vec = np.asarray(embeddings.embed_one(query), dtype=np.float32)
    qnorm = float(np.linalg.norm(query_vec))
    if qnorm == 0.0:
        return []

    matrix = np.stack([np.asarray(r["embedding"], dtype=np.float32) for r in rows])
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    sims = (matrix @ query_vec) / (norms * qnorm)

    scores = np.array([float(r["score"] or 0.0) for r in rows], dtype=np.float64)
    lo, hi = float(scores.min()), float(scores.max())
    # A pool with no spread contributes a flat 0.5 rather than dividing by zero.
    norm_scores = np.full_like(scores, 0.5) if hi == lo else (scores - lo) / (hi - lo)
    blended = SIMILARITY_WEIGHT * sims + (1.0 - SIMILARITY_WEIGHT) * norm_scores

    order = np.argsort(-blended)[:n]
    return [
        {
            "id": rows[i]["id"],
            "post_uri": rows[i]["post_uri"],
            "text": texts[rows[i]["post_uri"]],
            "similarity": float(sims[i]),
            "score": float(rows[i]["score"] or 0.0),
        }
        for i in order
    ]


DISCLOSURE_LINE = "🤖 Written with AI assistance."

#: A line that is nothing but hashtags. Mirrors what Mastodon itself looks for — see
#: _with_disclosure.
_HASHTAG_LINE = re.compile(r"#\S+(?:\s+#\S+)*")


def _with_disclosure(text: str) -> str:
    """Add the AI disclosure without displacing a trailing hashtag line.

    Mastodon lifts hashtags out of a post and renders them as a separate bar under the
    content — but only when they are the LAST line. Its `hashtag_bar.tsx` walks back from
    the end and gives up the moment it meets a node that is not a hashtag: "if the last
    line only contains hashtags". Anything after them, including one short sentence, leaves
    every tag inline in the body instead.

    Appending the disclosure to the end did exactly that. The generator is told hashtags are
    Mastodon's primary discovery mechanism and duly ends on them, and this line then landed
    after and broke the bar on every disclosed post — visible against any other post in the
    timeline, which shows tidy chips.

    So the disclosure goes immediately before that trailing block instead. It is no less
    visible for being one line earlier, and the rule it satisfies is about the post saying
    plainly that a machine wrote it, not about where the sentence sits.
    """
    body = text.strip()
    if not body:
        return DISCLOSURE_LINE

    lines = body.split("\n")
    tail: list[str] = []
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if _HASHTAG_LINE.fullmatch(last):
            tail.insert(0, last)
            lines.pop()
            continue
        break

    if not tail:
        return f"{body}\n\n{DISCLOSURE_LINE}"

    head = "\n".join(lines).rstrip()
    tags = "\n".join(tail)
    # A post that is nothing but hashtags still keeps them last, so a media post can carry
    # its bar; the disclosure simply leads.
    return f"{head}\n\n{DISCLOSURE_LINE}\n\n{tags}" if head else f"{DISCLOSURE_LINE}\n\n{tags}"


def _compliance_for(policy: masto.InstancePolicy, disclose: bool) -> ComplianceOut:
    """Turn the instance's own rules into concrete instructions for this post.

    The notes come from gate.policy_notes, which Engage's terms panel also reads,
    so the two screens can never tell the user different things about the same
    server.
    """
    notes = gate.policy_notes(policy)

    return ComplianceOut(
        disclosureApplied=disclose,
        disclosureLine=DISCLOSURE_LINE if disclose else "",
        # Unlisted keeps an automated post out of the federated firehose while
        # still being public to anyone who visits. It is what most instances ask
        # of automation, and the safe default when they have not said.
        suggestedVisibility="unlisted",
        notes=notes,
    )


def _norms_for(policy: masto.InstancePolicy) -> list[str]:
    """Platform guidance for the prompt, built from this instance's live facts.

    Passed as KB summaries rather than relying on the vendored llm module's
    built-in norms table, which has entries for Bluesky, X and LinkedIn and would
    otherwise fall through to its generic default for Mastodon.
    """
    return [
        f"Mastodon, instance {policy.info.host}. Hard limit "
        f"{policy.info.max_characters} characters — the post must fit.",
        "Mastodon has no engagement algorithm: nothing is boosted or suppressed by "
        "the platform. Reach comes from being boosted by humans, so write something "
        "a person would want to pass on rather than something engineered for a feed.",
        "Hashtags are the primary discovery mechanism and are genuinely useful here, "
        "unlike on Bluesky. Two to four relevant ones, written in CamelCase for screen "
        "readers (#RustGameDev, not #rustgamedev).",
        "Marketing register is strongly disliked and is against the rules on many "
        "instances. Write as a person sharing something, never as a brand announcing it.",
        "Long posts are normal and accepted; the culture rewards substance over "
        "brevity. Do not compress a real thought into a slogan.",
        "Put anything sensitive or spoiler-ish behind a content warning rather than in "
        "the body.",
    ]


@router.post("/generate", response_model=GenerateResponse, dependencies=[Depends(queue_slot("model"))])
def generate(body: GenerateRequest) -> GenerateResponse:
    """Write one post — but only for an instance whose rules have been accepted."""
    if not body.userInput.strip():
        raise HTTPException(status_code=400, detail="Tell it what to post about first.")

    policy = _require_accepted(body.instance)
    # The corpus is keyed on the instance, so take the host from the policy rather than
    # from body.instance: the policy's is normalised and is the one collection wrote under.
    host = policy.info.host
    spg_db, _, spg_llm = _spg()

    from vendor.socialpost.src import sources as spg_sources

    fetched = None
    if body.sourceUrl.strip():
        try:
            fetched = spg_sources.fetch_url(body.sourceUrl)
        except spg_sources.SourceError as err:
            # 400, not 502: a bad link is the caller's input, not an upstream fault.
            raise HTTPException(status_code=400, detail=str(err)) from None

    retrieval_query = (
        f"{body.userInput} {fetched.title}".strip() if fetched else body.userInput
    )
    exemplars = _retrieve_exemplars(body.niche, host, retrieval_query)
    compliance = _compliance_for(policy, body.discloseAi)

    # Leave room for the disclosure line so the finished post fits the instance's
    # limit, not just the model's share of it.
    budget = policy.info.max_characters
    if body.discloseAi:
        budget -= len(DISCLOSURE_LINE) + 2

    norms = _norms_for(policy) + [
        f"Keep the post under {budget} characters so the required disclosure fits."
        if body.discloseAi
        else f"Keep the post under {budget} characters."
    ]

    try:
        text = spg_llm.generate_post(
            user_input=brand_voice.apply_voice(body.userInput, body.brandVoiceId, compact=True),
            niche=body.niche,
            platform=PLATFORM,
            exemplar_texts=[e["text"] for e in exemplars],
            kb_summaries=norms,
            source=fetched,
            avoid_texts=[t for t in body.avoidTexts if t.strip()][-MAX_AVOID_TEXTS:],
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(err)) from None

    text = text.strip()
    if body.discloseAi and DISCLOSURE_LINE not in text:
        text = _with_disclosure(text)

    generation_id: int | None = None
    try:
        resp = (
            spg_db.get_client()
            .table("generations")
            .insert(
                {
                    "created_at": spg_db.iso(spg_db.utcnow()),
                    "user_input": body.userInput,
                    "niche": _corpus_niche(body.niche, host),
                    "output_text": text,
                    "exemplar_ids": [e["id"] for e in exemplars],
                    "kb_ids": [],
                }
            )
            .execute()
        )
        generation_id = resp.data[0]["id"]
    except Exception:  # noqa: BLE001 — losing the audit row must not lose the draft
        log.exception("[mastodon-post] could not record the generation")

    item = db.add_item(
        tool="Social",
        title=text[:70] + ("…" if len(text) > 70 else ""),
        subtitle=f"mastodon · {policy.info.host} · {body.niche}",
        content=text,
    )

    # The one moment the draft's identity and its Library entry are both in hand. The
    # instance rides along because a status id means nothing without the host that issued
    # it — see services/generation_link.py, which closes the loop from the other end.
    if generation_id:
        db.record_generation_link(item["id"], generation_id, PLATFORM, body.niche, host)

    meta = db.get_mastodon_post_meta([e["post_uri"] for e in exemplars])
    return GenerateResponse(
        text=text,
        generationId=generation_id,
        characters=len(text),
        maxCharacters=policy.info.max_characters,
        overLimit=len(text) > policy.info.max_characters,
        exemplars=[
            ExemplarOut(
                id=e["id"],
                text=e["text"],
                similarity=round(e["similarity"], 3),
                score=round(e["score"], 5),
                webUrl=(meta.get(e["post_uri"]) or {}).get("web_url", ""),
                author=(meta.get(e["post_uri"]) or {}).get("account_acct", ""),
            )
            for e in exemplars
        ],
        compliance=compliance,
        libraryId=item["id"],
    )


@router.post("/analytics", response_model=AnalyticsResponse)
def analytics(body: AnalyticsRequest) -> AnalyticsResponse:
    """How the posts on your Mastodon account have actually done.

    Read live from the instance rather than from anything this app recorded, because the
    app only knows about a post if the user came back and pasted its link — a step almost
    nobody performs, which would leave this screen permanently empty. Your account already
    knows every post you made and carries the counts on each one.

    POST rather than GET so the access token travels in a body instead of a URL, where it
    would end up in logs and history.

    Deliberately not the Bluesky screen's cohort comparison. That works because Bluesky has
    a searchable firehose to draw a comparable cohort from; Mastodon has no equivalent, and
    a "versus similar accounts" figure assembled from whatever a hashtag timeline happened
    to return would look authoritative and mean very little.
    """
    policy = _require_accepted(body.instance)
    host = policy.info.host
    token = body.accessToken.strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail=f"Add your {host} access token in Settings to see how your posts did.",
        )

    try:
        account, statuses = masto.account_statuses(host, token, limit=max(1, min(body.limit, 80)))
    except MastodonError as err:
        raise HTTPException(status_code=502, detail=str(err)) from None
    if account is None:
        raise HTTPException(status_code=502, detail=f"{host} did not recognise that token.")

    # Which of these this app wrote. Matched on the linked URI only — the loop the composer
    # closes when a draft is marked published. Guessing from text would mislabel edits and
    # anything written by hand, and a wrong attribution here is worse than none.
    from_app: set[str] = set()
    try:
        spg_db, _, _ = _spg()
        for g in (
            spg_db.get_client().table("generations").select("posted_uri").execute().data or []
        ):
            uri = g.get("posted_uri") or ""
            if uri.startswith("mastodon://"):
                from_app.add(uri)
    except Exception:  # noqa: BLE001 — an unconfigured store just means nothing is flagged
        pass

    posts = [
        PostAnalyticsOut(
            postUri=masto.corpus_uri(host, st.id),
            webUrl=st.url,
            instance=host,
            text=st.text,
            publishedAt=st.created_at.isoformat() if st.created_at else "",
            likes=st.favourites,
            reposts=st.reblogs,
            replies=st.replies,
            engagementRate=masto.engagement_rate(
                st.favourites, st.reblogs, st.replies, account.followers
            ),
            fromApp=masto.corpus_uri(host, st.id) in from_app,
        )
        for st in statuses
    ]

    n = len(posts)
    totals = {
        "posts": float(n),
        "followers": float(account.followers),
        "likes": float(sum(p.likes for p in posts)),
        "reposts": float(sum(p.reposts for p in posts)),
        "replies": float(sum(p.replies for p in posts)),
        "avgEngagementRate": (sum(p.engagementRate for p in posts) / n) if n else 0.0,
        "fromApp": float(sum(1 for p in posts if p.fromApp)),
    }
    return AnalyticsResponse(posts=posts, totals=totals, account=account.acct)


@router.post("/published")
def mark_published(body: PublishedRequest) -> dict:
    """Close the loop: link a published toot to the draft that produced it.

    Needs the access token because a web URL alone is not addressable — engagement
    has to be re-read by the id the user's own instance assigned the status, which
    only a search with resolve=true can tell us.
    """
    policy = _require_accepted(body.instance)
    host = policy.info.host
    spg_db, _, _ = _spg()

    try:
        status = masto.resolve_status(host, body.postedUrl, body.accessToken)
    except MastodonError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    if status is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{host} could not find a post at that link. Check it is right and "
                f"that the post is public."
            ),
        )

    return link_published_status(status, host, body.niche, body.generationId)


def link_published_status(status, host: str, niche: str, generation_id: int) -> dict:
    """Record that a published toot came from a draft, and make it measurable.

    Shared by /published — where the user pastes the link — and by the automatic sweep in
    services/generation_link.py, which knows the status id already because the app was what
    published it. One implementation so a post linked automatically lands in exactly the
    same shape as one linked by hand.
    """
    spg_db, _, _ = _spg()
    key = _corpus_niche(niche, host)
    uri = masto.corpus_uri(host, status.id)
    now = spg_db.utcnow()

    # The post has to exist before a generation can reference it: generations.posted_uri
    # is a foreign key, and a toot published thirty seconds ago has not been collected.
    spg_db.upsert(
        "authors",
        [
            {
                "did": status.account.url or status.account.acct,
                "handle": status.account.acct,
                "follower_count": status.account.followers,
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
                "author_did": status.account.url or status.account.acct,
                "text": status.text,
                "hashtags": status.hashtags,
                "has_media": status.has_media,
                "created_at": spg_db.iso(status.created_at),
                "niche": key,
                "ingested_at": spg_db.iso(now),
            }
        ],
        on_conflict="uri",
    )
    db.upsert_mastodon_post_meta(
        [
            {
                "post_uri": uri,
                "instance": host,
                "status_id": status.id,
                "web_url": status.url,
                "account_acct": status.account.acct,
            }
        ]
    )
    spg_db.get_client().table("generations").update({"posted_uri": uri}).eq(
        "id", generation_id
    ).execute()

    return {"postedUri": uri, "webUrl": status.url}


# ---------------------------------------------------------------------------
# Companion image
#
# The prompt is suggested, shown, and only drawn once the user has approved it —
# see services/image_prompt.py. Deliberately two calls rather than one: an image
# decided by text nobody read is an image nobody can fix.
# ---------------------------------------------------------------------------


class ImagePromptRequest(BaseModel):
    postText: str
    niche: str = ""
    hfToken: str = ""


class ImagePromptResponse(BaseModel):
    prompt: str
    #: "model" when a language model wrote it, "template" when the fallback did.
    source: str
    note: str
    width: int
    height: int


class GenerateImageRequest(BaseModel):
    #: The prompt the user reviewed and approved. Required.
    prompt: str
    #: Only used to title the Library entry the image is filed under.
    postText: str = ""
    hfToken: str = ""
    modalTokenId: str = ""
    modalTokenSecret: str = ""
    useModal: bool = False


class GenerateImageResponse(BaseModel):
    url: str
    promptUsed: str
    width: int
    height: int


@router.post("/image-prompt", response_model=ImagePromptResponse)
def suggest_image_prompt(body: ImagePromptRequest) -> ImagePromptResponse:
    """Propose an image direction for a draft, for the user to edit before drawing."""
    result = image_prompt.suggest(body.postText, body.niche, PLATFORM, body.hfToken)
    width, height = image_prompt.dimensions_for(PLATFORM)
    return ImagePromptResponse(
        prompt=result.prompt, source=result.source, note=result.note, width=width, height=height
    )


@router.post(
    "/images", response_model=GenerateImageResponse, dependencies=[Depends(queue_slot("image"))]
)
def generate_image(body: GenerateImageRequest) -> GenerateImageResponse:
    """Draw the approved prompt. Refuses an empty one rather than inventing a fallback."""
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=400,
            detail="Ask for a prompt suggestion and approve it before generating an image.",
        )
    try:
        url, width, height = image_prompt.render(
            prompt,
            PLATFORM,
            body.hfToken,
            tool='mastodon',
            post_text=body.postText,
            use_modal=body.useModal,
            modal_token_id=body.modalTokenId,
            modal_token_secret=body.modalTokenSecret,
        )
    except image_prompt.ImageRenderError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return GenerateImageResponse(url=url, promptUsed=prompt, width=width, height=height)


# ---------------------------------------------------------------------------
# Background learning loop
#
# Two jobs. `mastodon_snapshot` is the one that closes the loop on the user's own
# posts: /published records that a draft became a real toot, and this re-reads how
# that toot actually did, at 1h/24h/48h. Without it a published post is registered
# and then ignored, and the tool only ever learns from strangers.
#
# Neither job needs credentials. Public and unlisted statuses are readable
# unauthenticated (verified against hachyderm.io), so the access token stays in
# Electron's encrypted store and is never needed by a background thread — which
# also means nothing here can act as the user.
#
# Both are scoped to instances in mastodon_rule_acks, and both re-check the
# fingerprint before touching one. A timer must not be a way around the gate.
# ---------------------------------------------------------------------------

_SCHEDULE: tuple[tuple[str, timedelta], ...] = (
    ("mastodon_snapshot", timedelta(hours=1)),
    # Half as often as the Bluesky ingest. A hashtag timeline is a narrower net
    # than keyword search, so a faster cadence would mostly re-read posts we
    # already have while costing someone else's server the requests.
    ("mastodon_collect", timedelta(hours=12)),
)

_TICK_SECONDS = 300

# Each measurement is its own request with a courtesy delay, so a run is paced,
# not parallel. The cap keeps one tick bounded at a couple of minutes.
MAX_SNAPSHOT_POSTS = 60

# Matches vendor/socialpost's snapshot buckets, and the window_label CHECK
# constraint in its schema, which permits exactly these three.
_BUCKETS: tuple[tuple[str, float], ...] = (("1h", 1.0), ("24h", 24.0), ("48h", 48.0))

_scheduler_thread: threading.Thread | None = None


def _accepted_hosts() -> list[str]:
    """Instances whose accepted rules still match what they currently publish.

    An instance that has edited its rules drops out until the user reviews them,
    exactly as it does for a manual generate. Unreachable instances also drop out
    rather than failing the tick — a server being down is not consent to skip the
    check.
    """
    hosts: list[str] = []
    for ack in db.list_mastodon_acks():
        try:
            policy = masto.fetch_policy(ack["instance"])
        except MastodonError as err:
            log.warning("[mastodon-post] %s unreachable this tick: %s", ack["instance"], err)
            continue
        if policy.fingerprint != ack["policy_hash"]:
            log.info(
                "[mastodon-post] skipping %s: its rules changed and need re-reading",
                ack["instance"],
            )
            continue
        hosts.append(policy.info.host)
    return hosts


def _run_mastodon_snapshot() -> None:
    """Re-read engagement on posts the user actually published.

    Only posts reachable from a generation's posted_uri: those are the ones the
    user told us they published, and measuring them is what lets their own
    results compete for a place in the exemplar pool.
    """
    spg_db, _, _ = _spg()
    client = spg_db.get_client()
    now = spg_db.utcnow()

    # Hoisted: _accepted_hosts() re-checks each instance's rule fingerprint over the
    # network, so evaluating it inside the comprehension would repeat that per niche.
    hosts = _accepted_hosts()
    niche_keys = [
        _corpus_niche(r["name"], h) for r in spg_db.list_niches() for h in hosts
    ]
    if not niche_keys:
        return

    generations = (
        client.table("generations")
        .select("id, posted_uri, niche")
        .in_("niche", niche_keys)
        .execute()
        .data
        or []
    )
    posted = {
        g["posted_uri"]: g["niche"]
        for g in generations
        if (g.get("posted_uri") or "").startswith("mastodon://")
    }
    if not posted:
        return

    uris = list(posted)[:MAX_SNAPSHOT_POSTS]
    posts = {
        p["uri"]: p
        for p in (
            client.table("posts").select("uri, created_at").in_("uri", uris).execute().data
            or []
        )
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

    allowed = set(_accepted_hosts())
    rows: list[dict] = []
    touched: set[str] = set()

    for uri in uris:
        post = posts.get(uri)
        if not post:
            continue
        try:
            host, status_id = masto.parse_corpus_uri(uri)
        except ValueError:
            continue
        if host not in allowed:
            continue

        age_hours = _age_days(post.get("created_at"), now) * 24.0
        due = [
            label
            for label, hours in _BUCKETS
            if age_hours >= hours and label not in captured.get(uri, set())
        ]
        if not due:
            continue

        status = masto.get_status(host, status_id)
        if status is None:
            # Deleted, or the instance will not serve it. Absent beats zeroes:
            # a missing row reads as "not measured", a zero row as "nobody cared".
            log.info("[mastodon-post] %s no longer returns %s", host, status_id)
            continue

        for label in due:
            rows.append(
                {
                    "post_uri": uri,
                    "captured_at": spg_db.iso(now),
                    "window_label": label,
                    "likes": status.favourites,
                    "reposts": status.reblogs,
                    "replies": status.replies,
                    "engagement_rate": masto.engagement_rate(
                        status.favourites,
                        status.reblogs,
                        status.replies,
                        status.account.followers,
                    ),
                }
            )
        touched.add(posted[uri])

    if not rows:
        return

    spg_db.upsert("engagement_snapshots", rows, on_conflict="post_uri,window_label")
    log.info("[mastodon-post] wrote %d engagement snapshots", len(rows))

    # A new 48h measurement can change which posts deserve a pool slot, so rebuild
    # the niches that just gained one. `key` is the namespaced name; _rebuild_pool
    # takes the plain niche and the instance, which both come back out of the key.
    for key in touched:
        split = _split_corpus_niche(key)
        if split is None:
            # A legacy, host-less key: fallback material only, with no pool of its own.
            log.info("[mastodon-post] no instance in corpus key %r, nothing to rebuild", key)
            continue
        niche, host = split
        try:
            _rebuild_pool(niche, host)
        except Exception:  # noqa: BLE001 — one niche must not stop the rest
            log.exception("[mastodon-post] could not rebuild the pool for %r", key)


def _run_mastodon_collect() -> None:
    """Top the corpus up from every accepted instance's hashtag timelines."""
    spg_db, _, _ = _spg()
    hosts = _accepted_hosts()
    if not hosts:
        return
    for host in hosts:
        for row in spg_db.list_niches():
            if not row.get("active"):
                continue
            try:
                _collect_niche(host, row["name"])
            except Exception:  # noqa: BLE001 — one niche must not stop the rest
                log.exception(
                    "[mastodon-post] collection failed for %r on %s", row["name"], host
                )


_JOBS = {
    "mastodon_snapshot": _run_mastodon_snapshot,
    "mastodon_collect": _run_mastodon_collect,
}


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
    """Catch-up semantics, same as the Bluesky scheduler.

    Due-ness is measured against the last run recorded in job_runs rather than a
    wall clock, so a laptop that was asleep for two days resumes with one run of
    each job instead of either skipping them or firing forty-eight times.
    """
    spg_db, _, _ = _spg()
    now = spg_db.utcnow()

    for name, every in _SCHEDULE:
        try:
            last = _last_run(name)
            if last is not None and (now - last) < every:
                continue
            with spg_db.JobRun(name):
                log.info("[mastodon-post] running %s", name)
                _JOBS[name]()
        except Exception:  # noqa: BLE001 — one bad job must not stop the loop
            log.exception("[mastodon-post] job %s failed", name)


def _scheduler_loop() -> None:
    while True:
        try:
            # Inert until the user has accepted at least one instance's rules.
            # An install where nobody has opened this tool should make no network
            # requests at all, least of all to someone else's server.
            if db.list_mastodon_acks():
                _run_due_jobs()
        except Exception:  # noqa: BLE001
            log.exception("[mastodon-post] scheduler tick failed")
        time.sleep(_TICK_SECONDS)


def start_scheduler() -> None:
    """Start the learning loop in the background. Safe to call once at startup."""
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="mastodon-post-scheduler", daemon=True
    )
    _scheduler_thread.start()
    log.info("[mastodon-post] learning-loop scheduler started")
