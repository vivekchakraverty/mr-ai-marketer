"""Suggest post topics for a niche — what to write about, grounded in evidence.

The spine is OUR OWN corpus: the last 48h of posts collected for the niche, with
the engagement we measured ourselves. Every external "trends" service is
network-wide and engagement-blind from this system's perspective; the ingest
pipeline is the only source that is niche-scoped, engagement-weighted, fresh, and
consent-filtered all at once. So:

  1. Cluster the recent niche corpus by embedding similarity, rank clusters by
     volume x engagement — "what is this niche talking about, and which of it
     is landing".
  2. Overlay free, live, authoritative sources — every one of them queried with
     THE NICHE'S OWN KEYWORDS, never as a general trending feed:
       * Google Trends interest  — pytrends interest_over_time, giving the
                                   direction of search interest in each keyword
                                   (rising/falling/flat). Measured: this call
                                   works where related_queries reliably 429s, so
                                   only the working half is used.
       * Google News RSS         — headlines for the keyword as a quoted phrase.
       * Wikipedia pageviews     — official Wikimedia REST API, no auth. Search
                                   resolves a keyword to an article, then daily
                                   pageviews say whether public attention to that
                                   subject is climbing.
       * Hacker News (Algolia)   — stories matching the keyword in the last 48h;
                                   naturally empty for non-tech niches.


  3. One LLM call turns clusters + overlays into composer-ready angles, each with
     a one-line "why now" and its sources listed — same transparency ethos as the
     exemplars expander.

Global trending feeds were tried and deliberately dropped. Bluesky's getTrends
and Google Trends' country RSS are general-audience firehoses — in a live run
against "indie makers" they surfaced Taco Bell and the Pittsburgh Pirates, and
contributed nothing. A trend the niche does not care about is noise however hot
it is elsewhere, so this suggests topics ONLY within the niches the user chose.

Every overlay is a sidecar. Any of them can be down, rate-limited or empty and
the feature still works off the corpus — the same rule that keeps telemetry from
ever breaking generation. Degradation is honest at every level: no corpus yet ->
overlays only; overlays down -> corpus only; both empty -> "collect some posts
first", never invented trends.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import timedelta

import requests

from . import llm
from .db import get_client, iso, load_niches, utcnow

log = logging.getLogger(__name__)

# How far back the corpus signal reaches. 48h matches the measurement window; the
# 7d fallback keeps the feature useful on a corpus that is thin this week.
CORPUS_WINDOW_HOURS = 48
CORPUS_FALLBACK_DAYS = 7

# Cluster shaping. Two posts about the same thing sit well above this similarity;
# it is deliberately looser than the exemplar DEDUPE_THRESHOLD (0.85) because a
# topic is broader than a near-duplicate.
CLUSTER_THRESHOLD = 0.55
MAX_CLUSTERS = 6
MIN_CLUSTER_SIZE = 2

# A cluster must not point *away* from the niche it claims to be about.
#
# Zero, not something higher, and the measurements are the reason. Against the
# niche string "indie makers: indie hackers, building in public", MiniLM scores:
#
#     solo founder is the headline, but leverage is the actual bet   +0.365
#     Feed: "St. Louis Magazine" — best restaurants in town          +0.141
#     Feed: "Free Stuff Finder" — deals and coupons on groceries     +0.139
#     shipped my saas this morning                                   +0.038
#     shipping my saas today                                         +0.028
#     Taylor Farms lettuce recall expands to 12 states               -0.078
#
# Read that ordering carefully: two RSS spam bots outscore a genuine indie-maker
# post 4:1. Short text embeds toward the origin, so any threshold above zero
# filters by *length* far more than by topic — an earlier 0.18 here would have
# thrown away both real posts and kept both bots. What the signal does support
# is a sign test: only content actively unlike the niche goes negative. So this
# catches the lettuce recall and nothing subtler, which is all it can honestly do.
# Judging "is this bot spam" is left to the LLM, which is good at it (see
# _TOPIC_PROMPT); the durable fix is better keywords and tracked authors.
NICHE_RELEVANCE_THRESHOLD = 0.0

N_SUGGESTIONS = 5

HTTP_TIMEOUT = 15
_UA = {"User-Agent": "social-post-generator/topics"}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
WIKI_PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user/{article}/daily/{start}/{end}"
)

# Wikimedia asks API clients to identify themselves; an anonymous default UA is
# the documented way to get rate-limited.
_WIKI_UA = {"User-Agent": "social-post-generator/topics (trend lookup)"}

@dataclass
class TopicSuggestion:
    topic: str
    why_now: str
    sources: list[str] = field(default_factory=list)


@dataclass
class TopicReport:
    suggestions: list[TopicSuggestion]
    # Evidence, for the transparency expander.
    clusters: list[dict] = field(default_factory=list)
    overlays: dict[str, list[str]] = field(default_factory=dict)
    corpus_posts: int = 0
    note: str = ""


# ---------------------------------------------------------------------------
# 1. The spine: cluster our own recent corpus
# ---------------------------------------------------------------------------


def _recent_posts(niche: str) -> list[dict]:
    """Recent niche posts joined with any 48h engagement we measured."""
    client = get_client()

    def fetch(cutoff: str) -> list[dict]:
        return (
            client.table("posts")
            .select("uri, text, created_at")
            .eq("niche", niche)
            .gte("created_at", cutoff)
            .execute()
            .data
            or []
        )

    posts = fetch(iso(utcnow() - timedelta(hours=CORPUS_WINDOW_HOURS)))
    if len(posts) < MIN_CLUSTER_SIZE * 2:
        posts = fetch(iso(utcnow() - timedelta(days=CORPUS_FALLBACK_DAYS)))
    posts = [p for p in posts if (p.get("text") or "").strip()]
    if not posts:
        return []

    rates: dict[str, float] = {}
    uris = [p["uri"] for p in posts]
    for i in range(0, len(uris), 100):
        for row in (
            client.table("engagement_snapshots")
            .select("post_uri, engagement_rate")
            .eq("window_label", "48h")
            .in_("post_uri", uris[i : i + 100])
            .execute()
            .data
            or []
        ):
            if row["engagement_rate"] is not None:
                rates[row["post_uri"]] = float(row["engagement_rate"])

    for p in posts:
        p["engagement_rate"] = rates.get(p["uri"])
    return posts


def _cluster_corpus(posts: list[dict], niche: str = "", keywords: list[str] | None = None) -> list[dict]:
    """Greedy cosine clustering, ranked by size x engagement.

    Same brute-force philosophy as exemplar dedupe: the corpus here is a couple
    hundred posts at most, so an index would cost more than it saves.
    """
    if len(posts) < MIN_CLUSTER_SIZE:
        return []

    from . import embeddings

    texts = [p["text"] for p in posts]
    vectors = embeddings.embed(texts)

    assigned = [-1] * len(posts)
    seeds: list[int] = []
    for i in range(len(posts)):
        placed = False
        for c, seed in enumerate(seeds):
            if embeddings.cosine_similarity(vectors[i], vectors[seed]) >= CLUSTER_THRESHOLD:
                assigned[i] = c
                placed = True
                break
        if not placed:
            seeds.append(i)
            assigned[i] = len(seeds) - 1

    # Keyword search drags in homographs: the seeded keyword "shipped it" matches
    # package-delivery complaints as happily as product launches, and those form
    # perfectly good clusters about entirely the wrong subject. Measured on a real
    # corpus, this produced confident suggestions about a lettuce recall for an
    # "indie makers" niche. Drop clusters that point away from the niche — a
    # coarse cut, for the reasons documented at NICHE_RELEVANCE_THRESHOLD.
    niche_vec = None
    if niche or keywords:
        niche_vec = embeddings.embed([f"{niche}: {', '.join(keywords or [])}"])[0]

    clusters: list[dict] = []
    for c in range(len(seeds)):
        members = [posts[i] for i in range(len(posts)) if assigned[i] == c]
        if len(members) < MIN_CLUSTER_SIZE:
            continue

        if niche_vec is not None:
            member_idx = [i for i in range(len(posts)) if assigned[i] == c]
            relevance = max(
                embeddings.cosine_similarity(vectors[i], niche_vec) for i in member_idx
            )
            if relevance < NICHE_RELEVANCE_THRESHOLD:
                log.debug(
                    "dropping off-topic cluster (relevance %.2f): %s",
                    relevance,
                    members[0]["text"][:60],
                )
                continue
        measured = [m["engagement_rate"] for m in members if m["engagement_rate"] is not None]
        best = max(measured) if measured else 0.0
        # Volume says "being talked about"; measured engagement says "landing".
        # A cluster with real engagement beats a merely chatty one.
        clusters.append(
            {
                "size": len(members),
                "best_engagement": round(best, 5),
                "score": len(members) * (1.0 + 10.0 * best),
                "samples": [m["text"][:160] for m in members[:3]],
            }
        )
    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters[:MAX_CLUSTERS]


# ---------------------------------------------------------------------------
# 2. Overlays — free, live, optional
# ---------------------------------------------------------------------------

def _overlay_google_news(keywords: list[str]) -> list[str]:
    """Fresh headlines for the niche's strongest keywords. Keyword-scoped."""
    import feedparser

    from urllib.parse import urlencode

    socket.setdefaulttimeout(HTTP_TIMEOUT)  # feedparser has no timeout of its own
    headlines: list[str] = []
    for kw in keywords[:2]:  # two queries is plenty; this is an overlay
        # urlencode, not an f-string: the quoted phrase contains characters that
        # are illegal raw in a URL, which silently kills the whole overlay.
        query = urlencode({"q": f'"{kw}"', "hl": "en-US", "gl": "US", "ceid": "US:en"})
        parsed = feedparser.parse(f"{GOOGLE_NEWS_RSS}?{query}")
        for entry in parsed.entries[:4]:
            title = (entry.get("title") or "").rsplit(" - ", 1)[0].strip()
            if title and title not in headlines:
                headlines.append(title)
    return headlines[:6]


def _overlay_hacker_news(keywords: list[str]) -> list[str]:
    """Front-page-adjacent stories from the last 48h. Tech niches only, by nature."""
    since = int(time.time()) - 48 * 3600
    out: list[str] = []
    for kw in keywords[:2]:
        resp = requests.get(
            HN_SEARCH_URL,
            params={"query": kw, "tags": "story", "numericFilters": f"created_at_i>{since}"},
            headers=_UA,
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        for hit in resp.json().get("hits", [])[:3]:
            title = (hit.get("title") or "").strip()
            if title and title not in out:
                out.append(f"{title} ({hit.get('points', 0)} pts)")
    return out[:5]


def _overlay_google_trends_interest(keywords: list[str]) -> list[str]:
    """Direction of Google search interest in the niche's own keywords.

    The most authoritative "is the world paying more attention to this" signal
    available for free, and unlike every other overlay it speaks about the exact
    keywords the user chose rather than the internet at large.

    Only interest_over_time is used. Measured against the live service:
    related_queries returns HTTP 429 essentially always, while interest_over_time
    succeeds — so this deliberately uses the half that works instead of failing
    the whole overlay on the half that does not.
    """
    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=0, timeout=(HTTP_TIMEOUT, HTTP_TIMEOUT))
    out: list[str] = []
    for kw in keywords[:2]:  # each keyword is a separate request; two is polite
        pytrends.build_payload([kw], timeframe="now 7-d")
        frame = pytrends.interest_over_time()
        if frame.empty or kw not in frame:
            continue
        series = [v for v in frame[kw].tolist() if isinstance(v, (int, float))]
        if len(series) < 8:
            continue
        # Compare the most recent quarter of the window against the oldest.
        window = max(2, len(series) // 4)
        recent = sum(series[-window:]) / window
        earlier = sum(series[:window]) / window
        direction = "rising" if recent > earlier * 1.1 else (
            "falling" if recent < earlier * 0.9 else "flat"
        )
        out.append(
            f'search interest in "{kw}" is {direction} over the last 7 days '
            f"(relative index {earlier:.0f} -> {recent:.0f})"
        )
    return out

def _overlay_wikipedia_attention(keywords: list[str]) -> list[str]:
    """Whether public attention to the niche's subject is climbing.

    Two official Wikimedia calls: search resolves a keyword to a real article
    title, then the pageviews API gives a daily series for it. Slower-moving than
    social trends, which is the point — it distinguishes a durable shift in
    interest from a single viral afternoon.
    """
    from datetime import datetime

    end = utcnow()
    start = end - timedelta(days=8)
    out: list[str] = []

    for kw in keywords[:2]:
        search = requests.get(
            WIKI_SEARCH_URL,
            params={"action": "query", "list": "search", "srsearch": kw,
                    "format": "json", "srlimit": 1},
            headers=_WIKI_UA,
            timeout=HTTP_TIMEOUT,
        )
        search.raise_for_status()
        hits = search.json().get("query", {}).get("search", [])
        if not hits:
            continue

        # Wikipedia's search is fuzzy and will happily return "Astor Library
        # Building" for "building in public". A title sharing no meaningful word
        # with the keyword is a false match, and a confident-sounding trend line
        # about the wrong subject is worse than no trend line at all.
        title = hits[0]["title"]
        kw_words = {w.lower() for w in kw.split() if len(w) > 3}
        if kw_words and not (kw_words & {w.lower().strip("()") for w in title.split()}):
            log.debug("Wikipedia matched %r for %r; ignoring as unrelated", title, kw)
            continue
        article = title.replace(" ", "_")

        def stamp(dt: datetime) -> str:
            return dt.strftime("%Y%m%d")

        views = requests.get(
            WIKI_PAGEVIEWS_URL.format(article=article, start=stamp(start), end=stamp(end)),
            headers=_WIKI_UA,
            timeout=HTTP_TIMEOUT,
        )
        if views.status_code != 200:
            continue
        items = views.json().get("items", [])
        if len(items) < 4:
            continue
        series = [i["views"] for i in items]
        half = len(series) // 2
        earlier = sum(series[:half]) / max(half, 1)
        recent = sum(series[half:]) / max(len(series) - half, 1)
        direction = "up" if recent > earlier * 1.15 else (
            "down" if recent < earlier * 0.85 else "steady"
        )
        out.append(
            f'Wikipedia attention on "{hits[0]["title"]}" is {direction} '
            f"({earlier:.0f} -> {recent:.0f} daily views)"
        )
    return out


# Every source here is scoped to the NICHE'S OWN KEYWORDS. Global trending
# feeds (Bluesky getTrends, Google Trends' country RSS) were tried and removed:
# they are general-audience firehoses — sport, celebrities, politics — and in a
# live run against "indie makers" neither produced a single relevant item.
# A trend the niche does not care about is noise no matter how hot it is.
#
# Ordered most-authoritative first, which is the order the model sees them.
_OVERLAYS = {
    "google_trends_interest": _overlay_google_trends_interest,
    "google_news": _overlay_google_news,
    "wikipedia_attention": _overlay_wikipedia_attention,
    "hacker_news": _overlay_hacker_news,
}


def _gather_overlays(keywords: list[str]) -> dict[str, list[str]]:
    """Run every overlay, tolerating any of them being down or empty.

    An overlay failure is logged and dropped — a trends sidecar being down must
    never break topic suggestion, same rule as telemetry never breaking
    generation.
    """
    out: dict[str, list[str]] = {}
    for name, fn in _OVERLAYS.items():
        try:
            items = fn(keywords)
            if items:
                out[name] = items
        except Exception as err:  # noqa: BLE001 — sidecar sources fail routinely
            log.warning("overlay %s unavailable: %s", name, str(err)[:80])
    return out


# ---------------------------------------------------------------------------
# 3. Synthesis
# ---------------------------------------------------------------------------

_TOPIC_PROMPT = """\
You suggest social media post topics for someone active in the "{niche}" niche.

Below is evidence about what this niche is talking about RIGHT NOW. Two kinds:

{evidence}

The evidence is collected by keyword search and is NOT pre-cleaned. Some of it
will not belong to this niche at all: keywords catch homographs (a niche keyword
"shipped it" also matches parcel-delivery complaints), and RSS republishing bots
post syndicated headlines about anything. Ignore every piece of evidence that is
not plausibly about "{niche}", however popular it looks. Never build a suggestion
on evidence you had to stretch to make relevant.

Suggest up to {n} post topics this person could write TODAY. Requirements:
- Each topic must be grounded in the evidence above, not generic evergreen advice.
- Return FEWER than {n} — even none — if the on-topic evidence does not support
  {n} distinct topics. Padding with generic advice is the worst outcome here.
- Phrase each as a concrete angle the person could type into a post composer
  (e.g. "your take on X", "what Y means for solo developers"), not a headline.
- "why" must cite which evidence makes it timely, in one short sentence.
- Do not invent events, numbers, names or trends that are not in the evidence.

Respond with ONLY a JSON array, no prose, in exactly this shape:
[{{"topic": "...", "why": "...", "sources": ["corpus"|"bluesky_trends"|"google_news"|"hacker_news", ...]}}]
"""


def _format_evidence(clusters: list[dict], overlays: dict[str, list[str]]) -> str:
    lines: list[str] = []
    if clusters:
        lines.append(
            "1. CONVERSATION CLUSTERS from real posts in this niche (last 48h, "
            "with engagement we measured ourselves; higher = landing better):"
        )
        for i, c in enumerate(clusters, 1):
            lines.append(
                f"   cluster {i} [{c['size']} posts, best 48h engagement rate "
                f"{c['best_engagement']}] — samples:"
            )
            for s in c["samples"]:
                lines.append(f"     • {s}")
    else:
        lines.append("1. CONVERSATION CLUSTERS: none available yet.")

    lines.append("")
    if overlays:
        lines.append("2. LIVE TREND OVERLAYS:")
        for name, items in overlays.items():
            lines.append(f"   {name}:")
            for item in items:
                lines.append(f"     • {item}")
    else:
        lines.append("2. LIVE TREND OVERLAYS: none available right now.")
    return "\n".join(lines)


def _parse_suggestions(raw: str) -> list[TopicSuggestion]:
    """Parse the model's JSON, tolerating a ```json fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    data = json.loads(text.strip())
    out = []
    for item in data:
        topic = str(item.get("topic") or "").strip()
        if not topic:
            continue
        out.append(
            TopicSuggestion(
                topic=topic,
                why_now=str(item.get("why") or "").strip(),
                sources=[str(s) for s in (item.get("sources") or [])],
            )
        )
    return out


def suggest_topics(niche: str, n: int = N_SUGGESTIONS) -> TopicReport:
    """The feature: evidence-grounded topic suggestions for one niche."""
    niches = load_niches(active_only=False)
    if niche not in niches:
        raise ValueError(
            f"No niche called {niche!r}. See `python -m src.jobs.niches --list`."
        )
    keywords = niches[niche]

    posts = _recent_posts(niche)
    clusters = _cluster_corpus(posts, niche=niche, keywords=keywords)
    overlays = _gather_overlays(keywords)

    if not clusters and not overlays:
        return TopicReport(
            suggestions=[],
            corpus_posts=len(posts),
            note=(
                "No evidence to ground suggestions in yet: the corpus has too few "
                "recent posts and no trend source is reachable. Run an ingest for "
                "this niche and try again."
            ),
        )

    prompt = _TOPIC_PROMPT.format(
        niche=niche, n=n, evidence=_format_evidence(clusters, overlays)
    )
    raw = llm._call(prompt, temperature=0.4, max_output_tokens=900)
    try:
        suggestions = _parse_suggestions(raw)
    except (json.JSONDecodeError, AttributeError) as err:
        raise llm.LLMError(f"Model returned unparseable topic JSON: {str(err)[:80]}") from err

    return TopicReport(
        suggestions=suggestions[:n],
        clusters=clusters,
        overlays=overlays,
        corpus_posts=len(posts),
    )


def suggest_for_all_niches(n: int = N_SUGGESTIONS) -> dict[str, TopicReport]:
    """Topic suggestions for every active niche, keyed by niche name.

    Typical installs run 2-4 niches, so this is a handful of LLM calls and a
    handful of polite HTTP requests per niche. One niche failing (an LLM hiccup,
    a rate limit) must not lose the others' results, so failures are recorded as
    a note on that niche's report rather than raised.
    """
    out: dict[str, TopicReport] = {}
    for niche in load_niches():
        try:
            out[niche] = suggest_topics(niche, n=n)
        except Exception as err:  # noqa: BLE001 — one niche must not sink the rest
            log.exception("Topic suggestion failed for %r", niche)
            out[niche] = TopicReport(
                suggestions=[], note=f"{type(err).__name__}: {str(err)[:150]}"
            )
    return out
