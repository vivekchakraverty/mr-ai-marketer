"""Stage 1 — scan the Bluesky dump and select niche-relevant candidates.

Reads the local HF parquet snapshot of Roronotalt/bluesky (~95M rows, 8.2GB) and
writes filtered candidates into the staging store. No network, no engagement yet
— that is rehydrate.py's job.

Two properties of the dump drive the design:

  * IT IS SORTED BY AUTHOR. Consecutive rows share an author_did, and the file
    starts with handles beginning '0'. Reading the first N rows would therefore
    yield a sample biased to a handful of alphabetically-early accounts. So this
    scans row groups STRIDED across the whole file instead of sequentially, and
    an early stop is still representative.

  * IT HAS NO ENGAGEMENT COLUMNS. The firehose captures posts at creation, so
    'high-performing' is not a filter available at this stage — every candidate
    here is unlabelled by construction.

Relevance is two-stage, because the niches' own keywords are tuned for Bluesky
phrase SEARCH, not substring matching over a corpus. Measured on this dump: the
raw niche keywords match ~798 posts in 95M (0.0008%), which is unusably thin. A
broadened prefilter vocabulary matches ~174k (0.184%), and the embedding pass
then decides which of those are actually about the niche.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter
from pathlib import Path

from . import store

log = logging.getLogger(__name__)

DEFAULT_DATASET_DIR = Path(r"V:/bluesky-dataset/datasets--Roronotalt--bluesky")

# Columns actually needed. Projection matters: `embedded_array` is by far the
# largest column and skipping it is most of the scan speedup (~68k rows/s).
COLUMNS = ["type", "text", "created_at", "author", "author_did", "uri", "langs", "reply_to"]

MIN_LEN, MAX_LEN = 20, 300

# Broad prefilter vocabulary, per topic. Deliberately loose — a cheap net, not a
# relevance judgement.
#
# BREADTH IS THE POINT. A fine-tune's job here is platform REGISTER (length,
# hashtag density, casing convention, tone); topical narrowness is what
# retrieval supplies at inference time. A corpus spanning many topics teaches
# "how a Bluesky post is shaped" far better than one spanning two, and the topic
# label is then metadata for mixture balancing rather than a hard filter.
#
# Terms are matched case-insensitively with word boundaries where the term is a
# bare word, so "ml" does not fire inside "html".
PREFILTER: dict[str, tuple[str, ...]] = {
    "ai tools": (
        "llm", "gpt", "chatgpt", "claude", "openai", "anthropic", "prompt",
        "fine-tun", "finetun", "ai agent", "ai tool", "copilot", "huggingface",
        "hugging face", "stable diffusion", "midjourney", "machine learning",
        "neural net", "transformer model", "open weights", "genai",
    ),
    "indie makers": (
        "saas", "indie hacker", "indiehacker", "bootstrapped", "solopreneur",
        "side project", "sideproject", "mrr", "product hunt", "producthunt",
        "building in public", "buildinpublic", "micro saas", "first customer",
        "paying customer", "my startup", "indie dev",
    ),
    "software dev": (
        "refactor", "codebase", "pull request", "code review", "unit test",
        "compiler", "debugging", "api design", "tech debt", "merge conflict",
        "rust", "golang", "typescript", "python script", "regex", "git rebase",
    ),
    "web dev": (
        "css", "html", "javascript", "react", "nextjs", "next.js", "tailwind",
        "frontend", "front-end", "webdev", "browser bug", "safari", "dom ",
        "web component", "accessibility", "a11y", "svelte", "vue ",
    ),
    "devops": (
        "kubernetes", "k8s", "docker", "terraform", "ci/cd", "deploy", "aws ",
        "cloudflare", "postgres", "nginx", "observability", "incident",
        "on-call", "oncall", "latency", "self-host", "selfhost", "homelab",
    ),
    "security": (
        "infosec", "vulnerability", "cve-", "phishing", "ransomware", "exploit",
        "zero-day", "zeroday", "encryption", "password manager", "2fa",
        "threat model", "pentest", "malware", "data breach",
    ),
    "data science": (
        "dataset", "pandas", "jupyter", "regression", "statistic", "data viz",
        "dataviz", "sql query", "duckdb", "analytics", "a/b test", "p-value",
        "correlation", "histogram",
    ),
    "design": (
        "figma", "typography", "ux ", "ui design", "wireframe", "design system",
        "user research", "prototyp", "kerning", "colour palette", "color palette",
        "usability", "product design",
    ),
    "gamedev": (
        "gamedev", "godot", "unity3d", "unreal engine", "pixel art", "playtest",
        "game jam", "gamejam", "roguelike", "indie game", "level design",
        "speedrun", "steam page", "bevy",
    ),
    "open source": (
        "open source", "opensource", "foss", "linux", "debian", "ubuntu",
        "nixos", "arch linux", "maintainer", "licence", "gpl", "self-hosted",
        "contributor", "pull request welcome",
    ),
    "science": (
        "research paper", "peer review", "preprint", "arxiv", "hypothesis",
        "astronomy", "telescope", "physics", "biology", "chemistry", "genome",
        "microscope", "experiment", "scientist", "nasa", "quantum",
    ),
    "climate": (
        "climate", "emissions", "renewable", "solar panel", "wind farm",
        "carbon", "sustainab", "biodiversity", "conservation", "recycling",
        "heat pump", "ev charging",
    ),
    "health": (
        "mental health", "therapy", "anxiety", "adhd", "autism", "chronic pain",
        "long covid", "vaccine", "sleep", "burnout", "diagnosis", "medication",
        "healthcare", "disability",
    ),
    "fitness": (
        "workout", "marathon", "running", "gym ", "deadlift", "yoga", "cycling",
        "hiking", "swimming", "training plan", "parkrun", "5k ", "climbing",
    ),
    "food": (
        "recipe", "baking", "sourdough", "cooking", "restaurant", "coffee",
        "espresso", "vegan", "dinner", "pasta", "cocktail", "brewing", "bbq",
        "kitchen",
    ),
    "photography": (
        "photograph", "camera", "lens", "aperture", "shutter", "darkroom",
        "film photo", "35mm", "lightroom", "golden hour", "long exposure",
    ),
    "music": (
        "album", "guitar", "synth", "vinyl", "concert", "playlist", "band ",
        "songwriting", "drummer", "bassline", "jazz", "techno", "spotify",
        "live set",
    ),
    "books": (
        "novel", "reading", "bookstore", "author", "sci-fi", "fantasy book",
        "poetry", "library", "paperback", "book club", "chapter", "memoir",
        "translation",
    ),
    "writing": (
        "newsletter", "blogging", "wrote a post", "first draft", "editing",
        "manuscript", "wordcount", "word count", "nanowrimo", "essay",
        "copywriting", "substack",
    ),
    "film tv": (
        "movie", "cinema", "netflix", "season finale", "documentary",
        "screenplay", "director", "rewatch", "sitcom", "trailer", "oscars",
        "letterboxd",
    ),
    "art": (
        "illustration", "drawing", "sketch", "painting", "watercolour",
        "watercolor", "digital art", "comic", "animation", "ceramics",
        "printmaking", "sculpture", "artstation",
    ),
    "finance": (
        "investing", "index fund", "mortgage", "inflation", "pension",
        "savings", "budgeting", "tax return", "interest rate", "recession",
        "salary", "freelance rate",
    ),
    "marketing": (
        "seo", "content marketing", "email list", "conversion rate", "landing page",
        "ad spend", "brand voice", "copy that", "funnel", "churn", "customer research",
        "positioning", "cold email",
    ),
    "education": (
        "teaching", "classroom", "student", "curriculum", "lecture", "university",
        "phd", "grading", "syllabus", "tutoring", "learning to code", "bootcamp",
    ),
    "nature": (
        "birding", "birdwatch", "wildlife", "garden", "allotment", "national park",
        "mushroom", "foraging", "hedgehog", "butterfly", "botany", "trail",
    ),
    "pets": (
        "my dog", "my cat", "puppy", "kitten", "rescue dog", "vet ", "adoption",
        "good boy", "catsofbluesky", "dogsofbluesky",
    ),
}

# Spam / low-value heuristics.
MAX_HASHTAGS = 4
MAX_POSTS_PER_AUTHOR = 40  # RSS republishers and bots dominate otherwise
_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#(\w{1,64})")
_WS_RE = re.compile(r"\s+")


def _norm_hash(text: str) -> str:
    """Dedupe key: collapse whitespace + case, drop URLs (tracking params vary)."""
    cleaned = _URL_RE.sub("", text).lower()
    return hashlib.sha1(_WS_RE.sub(" ", cleaned).strip().encode("utf-8")).hexdigest()


def _parquet_file(dataset_dir: Path):
    import pyarrow.parquet as pq

    matches = sorted((dataset_dir / "snapshots").glob("*/data/*.parquet"))
    if not matches:
        raise FileNotFoundError(f"No parquet found under {dataset_dir}/snapshots/*/data/")
    return pq.ParquetFile(matches[0]), matches[0]


def _safe_group(niche: str) -> str:
    return "n_" + re.sub(r"[^0-9a-z]+", "_", niche.lower())


def _build_matcher() -> tuple[re.Pattern, dict[str, str]]:
    """One combined regex over every topic's vocabulary.

    A per-topic loop would be ~390 substring scans per post across 26 topics,
    which at tens of millions of rows is the difference between minutes and
    hours. A single alternation with named groups does it in one C-level pass.

    Bare-word terms get \\b anchors so "ml" does not fire inside "html" and
    "art" not inside "start"; terms already containing a space or punctuation
    are matched as written.
    """
    groups: dict[str, str] = {}
    parts: list[str] = []
    for niche, terms in PREFILTER.items():
        name = _safe_group(niche)
        groups[name] = niche
        alts = []
        for term in terms:
            escaped = re.escape(term.strip())
            if re.fullmatch(r"[a-z0-9]+", term.strip()):
                escaped = rf"\b{escaped}\b"
            alts.append(escaped)
        parts.append(f"(?P<{name}>" + "|".join(alts) + ")")
    return re.compile("|".join(parts), re.IGNORECASE), groups


_MATCHER, _GROUP_TO_NICHE = _build_matcher()


def _match_niche(text: str) -> str | None:
    """The topic with the most vocabulary hits in the text, or None.

    Best-match rather than leftmost-match: "I shipped my new game today" hits
    both 'indie makers' and 'gamedev', and leftmost would award it to whichever
    term happened to appear first in the sentence rather than the better fit.
    """
    hits: Counter[str] = Counter()
    for match in _MATCHER.finditer(text):
        if match.lastgroup:
            hits[_GROUP_TO_NICHE[match.lastgroup]] += 1
    if not hits:
        return None
    return hits.most_common(1)[0][0]


def scan(
    target: int = 20_000,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    stride: int = 7,
    max_row_groups: int | None = None,
) -> dict:
    """Select up to `target` candidates, strided across the whole dump.

    `stride` picks every Nth row group. With ~95k row groups of ~1000 rows, a
    stride of 7 covers ~13.5k groups spread evenly end to end — representative
    despite the author ordering, and far cheaper than a full 23-minute scan.
    """
    parquet, path = _parquet_file(dataset_dir)
    n_groups = parquet.metadata.num_row_groups

    # Stride must scale with the target, or the early stop reintroduces exactly
    # the author-sort bias the striding exists to avoid: a fixed small stride
    # reaches the target while still inside the first slice of the file, so the
    # sample covers only alphabetically-early handles. Estimate how many groups
    # are actually needed at the observed hit rate and spread THAT many evenly
    # end to end. HIT_RATE is measured on this dump (~2% of rows survive every
    # filter with the 26-topic vocabulary).
    HIT_RATE = 0.02
    rows_per_group = max(parquet.metadata.num_rows // max(n_groups, 1), 1)
    needed = max(int(target / max(HIT_RATE, 1e-6) / rows_per_group), 1)
    # Cushion, then never stride below the caller's floor.
    auto_stride = max(n_groups // int(needed * 1.3), 1)
    stride = max(stride, auto_stride)

    groups = list(range(0, n_groups, stride))
    if max_row_groups:
        groups = groups[:max_row_groups]

    seen_hashes: set[str] = set()
    author_counts: Counter[str] = Counter()
    stats = Counter()
    batch: list[dict] = []
    kept = 0
    started = time.time()

    log.info(
        "scanning %s: %d row groups (stride %d of %d), target %d candidates",
        path.name, len(groups), stride, n_groups, target,
    )

    for n_done, group_index in enumerate(groups, 1):
        if kept >= target:
            break
        table = parquet.read_row_group(group_index, columns=COLUMNS)

        for row in table.to_pylist():
            stats["scanned"] += 1

            if row["type"] != "post":
                stats["skip_not_post"] += 1
                continue
            # Replies are conversational fragments whose engagement reflects the
            # parent, not the post. Reposts carry someone else's text.
            if row["reply_to"] is not None:
                stats["skip_reply"] += 1
                continue
            if "en" not in (row["langs"] or []):
                stats["skip_lang"] += 1
                continue

            text = (row["text"] or "").strip()
            if not (MIN_LEN <= len(text) <= MAX_LEN):
                stats["skip_length"] += 1
                continue

            niche = _match_niche(text)
            if niche is None:
                stats["skip_off_niche"] += 1
                continue

            stripped = _URL_RE.sub("", text).strip()
            if len(stripped) < MIN_LEN:
                stats["skip_url_only"] += 1
                continue

            hashtags = _HASHTAG_RE.findall(text)
            if len(hashtags) > MAX_HASHTAGS:
                stats["skip_hashtag_spam"] += 1
                continue

            digest = _norm_hash(text)
            if digest in seen_hashes:
                stats["skip_duplicate"] += 1
                continue

            did = row["author_did"] or ""
            if author_counts[did] >= MAX_POSTS_PER_AUTHOR:
                stats["skip_author_cap"] += 1
                continue

            seen_hashes.add(digest)
            author_counts[did] += 1
            batch.append(
                {
                    "uri": row["uri"],
                    "platform": "bluesky",
                    "text": text,
                    "hashtags": hashtags,
                    "author_did": did,
                    "author_handle": row["author"],
                    "created_at": row["created_at"],
                    "niche": niche,
                }
            )
            kept += 1
            stats[f"kept_{niche}"] += 1
            if kept >= target:
                break

        if len(batch) >= 2000:
            stats["inserted"] += store.add_candidates(batch)
            batch = []

        if n_done % 2000 == 0:
            rate = stats["scanned"] / max(time.time() - started, 1e-9)
            log.info(
                "  %d/%d groups · %d scanned · %d kept · %.0f rows/s",
                n_done, len(groups), stats["scanned"], kept, rate,
            )

    if batch:
        stats["inserted"] += store.add_candidates(batch)

    elapsed = round(time.time() - started, 1)
    result = {
        "elapsed_seconds": elapsed,
        "unique_authors": len(author_counts),
        **dict(stats),
    }
    store.update_manifest(
        dataset_dir=str(dataset_dir),
        dataset_revision=store.dataset_revision(dataset_dir),
        dataset_rows=parquet.metadata.num_rows,
        stage1_filters={
            "min_len": MIN_LEN, "max_len": MAX_LEN,
            "max_hashtags": MAX_HASHTAGS,
            "max_posts_per_author": MAX_POSTS_PER_AUTHOR,
            "stride": stride,
            "lang": "en",
        },
        stage1_counts=result,
    )
    log.info("scan complete in %ss: %s", elapsed, result)
    return result


# ---------------------------------------------------------------------------
# Embedding relevance pass
# ---------------------------------------------------------------------------


def rerank(min_relevance: float = 0.0, batch_size: int = 512) -> dict:
    """Score candidates by embedding similarity to their niche definition.

    The prefilter is a keyword net and catches homographs — "shipped" matches
    parcel-delivery complaints as happily as product launches. This is the pass
    that judges topical fit.

    `min_relevance` defaults to 0.0 as a SIGN TEST, not a similarity threshold.
    Measured in vendor/socialpost/src/topics.py: short text embeds toward the
    origin, so any positive threshold filters by length far more than by topic —
    it rejected genuine niche posts while keeping RSS spam. Only content actively
    unlike the niche goes negative, so that is the only cut defensible here.
    Rejected rows are marked, never deleted, so the threshold can be revisited.
    """
    from vendor.socialpost.src import embeddings

    # Most topics here are corpus-only and have no row in the live niches table,
    # so the embedding target is built from the topic's own vocabulary. Where a
    # live niche of the same name DOES exist, its user-authored keywords are
    # preferred — they describe what the user actually means by that niche.
    live: dict[str, list[str]] = {}
    try:
        from vendor.socialpost.src import db as spg_db

        live = spg_db.load_niches(active_only=False)
    except Exception as err:  # noqa: BLE001 — an unconfigured store is not fatal here
        log.info("live niches unavailable, using prefilter vocab only: %s", str(err)[:80])

    niche_vecs = {}
    for niche, terms in PREFILTER.items():
        keywords = live.get(niche) or list(terms)[:12]
        niche_vecs[niche] = embeddings.embed([f"{niche}: {', '.join(keywords)}"])[0]

    with store.connect() as conn:
        rows = conn.execute(
            """
            select uri, text, niche from ft_posts
             where platform = 'bluesky' and relevance is null and niche is not null
            """
        ).fetchall()

    scored = rejected = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        vectors = embeddings.embed([r["text"] for r in chunk])
        updates = []
        for row, vector in zip(chunk, vectors):
            target = niche_vecs.get(row["niche"])
            if target is None:
                continue
            score = float(embeddings.cosine_similarity(vector, target))
            status = "rejected" if score < min_relevance else "candidate"
            if status == "rejected":
                rejected += 1
            updates.append((round(score, 5), status, row["uri"]))
            scored += 1
        with store.connect() as conn:
            conn.executemany(
                "update ft_posts set relevance = ?, status = ? where uri = ?", updates
            )
        log.info("  reranked %d/%d", min(start + batch_size, len(rows)), len(rows))

    store.update_manifest(stage1_rerank={"scored": scored, "rejected": rejected,
                                         "min_relevance": min_relevance})
    return {"scored": scored, "rejected": rejected}
