"""Build a posting-time curve for a platform from a recent one-month window.

WHY A ROLLING WINDOW. The original Bluesky curve shipped as a table baked into
`routers/posting_time.py`, measured on a slice of the fine-tune corpus running
28 Nov – 23 Dec 2024. That was defensible as a starting point but it ages: it is
one December, and audiences move. This module recollects the same statistic over
the last ~31 days so the recommendation tracks the platform as it is now, and
writes the result to `DATA_DIR/posting_time/curves.json` where the router picks
it up in preference to the baked-in table.

THE STATISTIC IS UNCHANGED, deliberately — see routers/posting_time.py for why a
raw median engagement rate per hour is misleading (the best-looking hour was
mostly a follower-count artefact). Each post is ranked against *the same author's*
other posts in the window, and an hour's score is the mean of those percentiles,
so 0.500 means "an average slot for whoever posted in it". Every author is their
own control, which is what removes account quality and follower count from the
comparison.

TWO SAMPLING RULES THAT MATTER:

  settle    Engagement is read once, now, but posts in the window are of
            different ages, and a two-hour-old post has had no chance to
            accumulate. So the window ENDS `settle_hours` before now — every post
            scored has had at least that long. Without it the curve would mostly
            measure "how long ago was this posted", which correlates with hour of
            day and would look exactly like a real result.

  >=5 posts An author needs enough posts for a within-author rank to mean
            anything, and posts whose engagement is all-identical carry no
            ranking information at all (common for very small accounts, where
            everything is 0).

MASTODON IS HARDER THAN BLUESKY AND THE CODE SAYS SO. Counts are only
authoritative on the instance that HOSTS the account: a remote account's
favourite/boost totals as seen from your instance are just the part that happened
to federate to you, which can understate the truth by orders of magnitude. So the
Mastodon collector takes LOCAL accounts only, from an instance whose rules the
user has accepted. That is correct but it caps the sample at that instance's
population, and small instances cannot produce a usable curve — which is why
`compute_curve` measures its own split-half reliability and the caller is
expected to refuse to ship a curve that does not clear it.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .. import config

log = logging.getLogger(__name__)

CURVES_PATH = config.DATA_DIR / "posting_time" / "curves.json"

BASELINE = 0.5

# An author needs this many posts in the window before their internal ranking
# carries information.
MIN_POSTS_PER_AUTHOR = 5

# Below this, engagement_rate is a small-denominator artefact — the same floor
# the fine-tune tiering uses, and the reason its top tier was once 1-follower
# accounts with 3 likes.
MIN_FOLLOWERS_BLUESKY = 200
MIN_FOLLOWERS_MASTODON = 50

# Reliability floor a curve must clear to be shown at all. Split-half r on the
# pooled Bluesky corpus was +0.583; per-niche curves averaged +0.131 and were
# refused. 0.30 sits between them: comfortably better than the noise that got
# rejected, without demanding the full corpus.
MIN_RELIABILITY = 0.30


@dataclass
class Post:
    author: str
    created_at: datetime
    engagement: int
    followers: int


@dataclass
class Curve:
    platform: str
    hourly: list[float]
    daily: list[float]
    volume: list[int]
    scored_posts: int
    scored_authors: int
    reliability: float
    window_start: str
    window_end: str
    collected_at: str
    source: str
    # Mastodon only. An instance is its own community with its own working hours
    # and timezone spread, so its curve is stored and served separately rather
    # than pooled — averaging two instances would describe neither.
    instance: str = ""
    # False when no data was ever read — the rules gate refused, or the server was
    # unreachable. That is a REFUSAL, not a measurement, and storing it would let
    # the UI later report "this server has no usable posts" about a server nobody
    # ever looked at.
    attempted: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.instance}" if self.instance else self.platform

    @property
    def usable(self) -> bool:
        return self.reliability >= MIN_RELIABILITY

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "instance": self.instance,
            "hourly": self.hourly,
            "daily": self.daily,
            "volume": self.volume,
            "scoredPosts": self.scored_posts,
            "scoredAuthors": self.scored_authors,
            "reliability": round(self.reliability, 4),
            "usable": self.usable,
            "windowStart": self.window_start,
            "windowEnd": self.window_end,
            "collectedAt": self.collected_at,
            "source": self.source,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# The statistic
# ---------------------------------------------------------------------------


def _percentiles(values: list[float]) -> list[float]:
    """Average-rank percentiles in [0,1]; ties share the mean rank.

    Ties are the whole reason this is rank-based rather than a ratio to the
    author's median: small accounts have runs of identical engagement, and a
    plain ratio collapses to exactly 1.0 for most of them, which silently
    flattens the curve to a dead 0.500 at every hour.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2
        for k in range(i, j + 1):
            out[order[k]] = rank / (len(values) - 1) if len(values) > 1 else 0.5
        i = j + 1
    return out


def _author_percentiles(posts: Iterable[Post]) -> list[tuple[datetime, float]]:
    """(created_at, within-author percentile) for every scorable post."""
    by_author: dict[str, list[Post]] = defaultdict(list)
    for p in posts:
        by_author[p.author].append(p)

    scored: list[tuple[datetime, float]] = []
    for group in by_author.values():
        if len(group) < MIN_POSTS_PER_AUTHOR:
            continue
        rates = [p.engagement / p.followers for p in group if p.followers > 0]
        if len(rates) != len(group) or len(set(rates)) == 1:
            continue
        for p, pct in zip(group, _percentiles(rates)):
            scored.append((p.created_at, pct))
    return scored


def _curve_from(scored: list[tuple[datetime, float]]) -> tuple[list[float], list[float]]:
    hourly_buckets: dict[int, list[float]] = defaultdict(list)
    daily_buckets: dict[int, list[float]] = defaultdict(list)
    for created, pct in scored:
        hourly_buckets[created.hour].append(pct)
        daily_buckets[created.weekday()].append(pct)

    hourly = [
        round(sum(hourly_buckets[h]) / len(hourly_buckets[h]), 4) if hourly_buckets[h] else BASELINE
        for h in range(24)
    ]
    daily = [
        round(sum(daily_buckets[d]) / len(daily_buckets[d]), 4) if daily_buckets[d] else BASELINE
        for d in range(7)
    ]
    return hourly, daily


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 6:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    den = (sum((x - ma) ** 2 for x in a) * sum((x - mb) ** 2 for x in b)) ** 0.5
    return num / den if den else 0.0


def _reliability(scored: list[tuple[datetime, float]], trials: int = 40) -> float:
    """Split-half correlation of the hourly curve — does it reproduce on itself?

    The same test that disqualified per-niche curves. A curve that cannot agree
    with a random half of its own data is noise, and shipping it as advice would
    be worse than shipping nothing.
    """
    if len(scored) < 200:
        return 0.0
    rng = random.Random(0)
    rs: list[float] = []
    for _ in range(trials):
        shuffled = scored[:]
        rng.shuffle(shuffled)
        half = len(shuffled) // 2
        a_buckets: dict[int, list[float]] = defaultdict(list)
        b_buckets: dict[int, list[float]] = defaultdict(list)
        for created, pct in shuffled[:half]:
            a_buckets[created.hour].append(pct)
        for created, pct in shuffled[half:]:
            b_buckets[created.hour].append(pct)
        common = [
            h for h in range(24) if len(a_buckets[h]) >= 5 and len(b_buckets[h]) >= 5
        ]
        if len(common) < 8:
            continue
        rs.append(
            _pearson(
                [sum(a_buckets[h]) / len(a_buckets[h]) for h in common],
                [sum(b_buckets[h]) / len(b_buckets[h]) for h in common],
            )
        )
    return sum(rs) / len(rs) if rs else 0.0


def compute_curve(
    posts: list[Post],
    *,
    platform: str,
    window_start: datetime,
    window_end: datetime,
    source: str,
    instance: str = "",
    notes: list[str] | None = None,
) -> Curve:
    scored = _author_percentiles(posts)
    hourly, daily = _curve_from(scored)
    volume_buckets: dict[int, int] = defaultdict(int)
    for p in posts:
        volume_buckets[p.created_at.hour] += 1

    return Curve(
        platform=platform,
        hourly=hourly,
        daily=daily,
        volume=[volume_buckets[h] for h in range(24)],
        scored_posts=len(scored),
        scored_authors=len({p.author for p in posts}),
        reliability=_reliability(scored),
        window_start=window_start.date().isoformat(),
        window_end=window_end.date().isoformat(),
        collected_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        instance=instance,
        notes=notes or [],
    )


# ---------------------------------------------------------------------------
# Bluesky collection
# ---------------------------------------------------------------------------


def _author_feed_in_window(did: str, start: datetime, end: datetime, max_pages: int = 4) -> list:
    """One author's posts inside the window, stopping as soon as the feed leaves it.

    Not `bluesky.get_author_feed`, which pages until it has collected N posts
    regardless of date. That reads months of history for a quiet account, and its
    page size shrinks to `limit - len(out)` as reposts are filtered out, so it ends
    up making ~25 small requests per author. Measured at ~9s/author, which is 3.5
    hours for a useful sample. Breaking on the window edge instead bounds the work
    to what is actually being scored.
    """
    from atproto_client.exceptions import AtProtocolError, ModelError
    from vendor.socialpost.src import bluesky as bs

    client = bs.get_client()
    out: list = []
    cursor: str | None = None

    for _ in range(max_pages):
        params: dict = {"actor": did, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = bs.with_backoff(client.app.bsky.feed.get_author_feed, params)
        except (AtProtocolError, ModelError):
            # A deactivated, suspended or renamed account costs this one author.
            break

        page = resp.feed or []
        if not page:
            break

        past_window = False
        for item in page:
            # Reposts carry someone else's post; crediting them here would score
            # the wrong author.
            if getattr(item, "reason", None) is not None:
                continue
            post = bs._post_view_to_post(item.post)
            if not post or not post.created_at:
                continue
            created = post.created_at.astimezone(timezone.utc)
            if created < start:
                past_window = True
                continue
            if created <= end:
                out.append(post)

        cursor = resp.cursor
        if past_window or not cursor:
            break

    return out


def collect_bluesky(
    days: int = 31,
    settle_hours: int = 48,
    target_authors: int = 1500,
    seed_terms: int = 60,
) -> Curve:
    """Seed authors from search, then take each one's own feed across the window."""
    from vendor.socialpost.src import bluesky as bs

    from .finetune import acquire

    now = datetime.now(timezone.utc)
    end = now - timedelta(hours=settle_hours)
    start = end - timedelta(days=days)

    terms: list[str] = []
    for group in acquire.PREFILTER.values():
        terms.extend(group)
    rng = random.Random(7)
    rng.shuffle(terms)
    terms = terms[:seed_terms]

    # Seed: who is posting at all in this window. Search is a biased sample of
    # posts, but it is only used to DISCOVER accounts — every post that gets
    # scored comes from the author's own feed, so search's ranking bias does not
    # reach the curve.
    seeds: dict[str, None] = {}
    for i, term in enumerate(terms):
        if len(seeds) >= target_authors:
            break
        try:
            for p in bs.search_posts(term, limit=100, since=start, until=end):
                seeds.setdefault(p.author_did, None)
        except Exception as err:  # noqa: BLE001
            log.warning("[posting-time] seed %r failed: %s", term, err)
        if i % 10 == 0:
            log.info("[posting-time] seeded %d authors from %d terms", len(seeds), i + 1)

    dids = list(seeds)[:target_authors]
    log.info("[posting-time] %d seed authors", len(dids))

    # Follower counts, and the no-index consent check, 25 at a time.
    profiles: dict[str, object] = {}
    for i in range(0, len(dids), 25):
        try:
            profiles.update(bs.get_profiles(dids[i : i + 25]))
        except Exception as err:  # noqa: BLE001
            log.warning("[posting-time] profiles chunk failed: %s", err)

    posts: list[Post] = []
    kept_authors = 0
    for n, did in enumerate(dids):
        author = profiles.get(did)
        if author is None:
            continue
        # Honour the one explicit "do not surface me" signal Bluesky exposes.
        if getattr(author, "no_index", False):
            continue
        followers = getattr(author, "follower_count", 0)
        if followers < MIN_FOLLOWERS_BLUESKY:
            continue
        try:
            feed = _author_feed_in_window(did, start, end)
        except Exception as err:  # noqa: BLE001
            log.warning("[posting-time] feed for %s failed: %s", did, err)
            continue
        window = [
            Post(
                author=did,
                created_at=p.created_at.astimezone(timezone.utc),
                engagement=p.likes + p.reposts + p.replies,
                followers=followers,
            )
            for p in feed
        ]
        if len(window) >= MIN_POSTS_PER_AUTHOR:
            posts.extend(window)
            kept_authors += 1
        if n % 50 == 0:
            log.info(
                "[posting-time] %d/%d authors scanned, %d kept, %d posts",
                n, len(dids), kept_authors, len(posts),
            )

    return compute_curve(
        posts,
        platform="bluesky",
        window_start=start,
        window_end=end,
        source="Bluesky author feeds, seeded by topic search",
        notes=[
            f"Engagement read {settle_hours}h or more after each post was created.",
            f"Accounts under {MIN_FOLLOWERS_BLUESKY} followers excluded.",
        ],
    )


# ---------------------------------------------------------------------------
# Mastodon collection
# ---------------------------------------------------------------------------


def _accepted_mastodon_hosts() -> list[str]:
    """Instances whose accepted rules still match what they publish right now.

    A data-collection job must not be a way around the gate: `require_accepted`
    re-fetches and re-fingerprints the rules, so an instance that edited them
    drops out until the user has read them again.
    """
    from .. import db
    from . import mastodon as m
    from . import mastodon_gate as gate

    hosts: list[str] = []
    for ack in db.list_mastodon_acks():
        host = ack["instance"]
        try:
            hosts.append(gate.require_accepted(host).info.host)
        except gate.PolicyNotAccepted:
            log.info("[posting-time] %s: rules changed since acceptance, skipping", host)
        except m.MastodonError as err:
            log.warning("[posting-time] %s unreachable: %s", host, str(err)[:90])
    return hosts


def collect_mastodon(
    instance: str,
    days: int = 31,
    settle_hours: int = 24,
    pages: int = 60,
    token: str = "",
) -> Curve:
    """One instance's own curve, from its own local accounts.

    PER-INSTANCE, NOT POOLED. An instance is a community: its members share a
    rough geography, a language and a daily rhythm, and two instances can have
    genuinely opposite curves. Averaging them would produce a number that
    describes neither, and a user posts to one specific server — so the answer
    has to come from that server's own data or not at all.

    LOCAL IS A CORRECTNESS CHOICE, NOT A PERFORMANCE ONE. An instance is the
    system of record for its own accounts' favourite/boost counts; for everyone
    else it holds only what federated to it. Scoring remote posts on a local view
    would rank an author's posts by how well they federated rather than how they
    did, so remote accounts are dropped entirely.

    THE TOKEN IS OFTEN THE DIFFERENCE BETWEEN A CURVE AND NOTHING. The public
    local timeline is the only broad sample of an instance's own accounts, and the
    larger instances no longer serve it to anonymous callers: mastodon.social
    answers `422 {"error":"This method requires an authenticated user"}` to every
    form of the request. Reading it as the user — the same account the composer
    already publishes with — is what makes the biggest instances measurable at
    all. It stays optional, because smaller servers (hachyderm, toot.garden) do
    serve it anonymously and should not require a login to be measured.
    """
    from . import mastodon as m
    from . import mastodon_gate as gate

    host = m.normalise_host(instance)
    now = datetime.now(timezone.utc)
    end = now - timedelta(hours=settle_hours)
    start = end - timedelta(days=days)

    def refused(reason: str) -> Curve:
        curve = compute_curve(
            [], platform="mastodon", instance=host, window_start=start,
            window_end=end, source="not collected", notes=[reason],
        )
        curve.attempted = False
        return curve

    # The gate is re-checked here, not trusted from an earlier acceptance: a
    # collection job must not be a way to read an instance whose rules have
    # changed since the user last saw them.
    try:
        gate.require_accepted(host)
    except gate.PolicyNotAccepted:
        return refused(f"{host}'s rules have not been accepted, or changed since they were.")
    except m.MastodonError as err:
        return refused(f"{host} could not be reached: {str(err)[:120]}")

    by_account: dict[str, list[Post]] = defaultdict(list)
    rejected = 0
    below_floor = 0
    max_id = ""
    seen_ids: set[str] = set()
    reached: datetime | None = None

    for _ in range(pages):
        try:
            batch = m.public_timeline(host, limit=40, max_id=max_id, local=True, token=token)
        except m.MastodonError as err:
            log.warning("[posting-time] %s local timeline unavailable: %s", host, err)
            if not seen_ids:
                # Nothing was readable at all. Two different causes, and telling them
                # apart is the difference between "try connecting your account" and
                # "this server cannot be measured": the big instances refuse anonymous
                # reads of this endpoint outright.
                needs_login = "authenticated" in str(err).lower() or "401" in str(err)
                if needs_login and not token:
                    return refused(
                        f"{host} only serves its public timeline to a logged-in account. "
                        f"Connect your {host} account in the composer and measure again."
                    )
                return refused(
                    f"{host} does not serve a public local timeline, so there is no "
                    "sample to learn from."
                )
            break
        if not batch:
            break

        oldest: datetime | None = None
        for s in batch:
            if s.id in seen_ids:
                continue
            seen_ids.add(s.id)
            if not s.created_at:
                continue
            created = s.created_at.astimezone(timezone.utc)
            oldest = created if oldest is None or created < oldest else oldest
            if not (start <= created <= end):
                continue
            ok, _why = m.should_learn_from(s)
            if not ok:
                rejected += 1
                continue
            if s.account.followers < MIN_FOLLOWERS_MASTODON:
                below_floor += 1
                continue
            by_account[s.account.acct].append(
                Post(
                    author=f"{host}:{s.account.acct}",
                    created_at=created,
                    engagement=s.favourites + s.reblogs + s.replies,
                    followers=s.account.followers,
                )
            )
        if oldest:
            reached = oldest if reached is None or oldest < reached else reached
        max_id = batch[-1].id
        # Stop once the timeline has run past the far edge of the window.
        if oldest and oldest < start:
            break
        time.sleep(m.POLITE_DELAY_SECONDS)

    posts = [p for group in by_account.values() for p in group]
    eligible = sum(1 for group in by_account.values() if len(group) >= MIN_POSTS_PER_AUTHOR)

    notes = [
        f"{host}: {len(posts)} in-window posts from {len(by_account)} local accounts, "
        f"{eligible} of which have the {MIN_POSTS_PER_AUTHOR}+ posts needed to be scored.",
        "Local accounts only — an instance's counts are authoritative for the accounts "
        "it hosts, but only a partial federated view for everyone else.",
        f"Engagement read {settle_hours}h or more after each post was created.",
    ]
    if rejected:
        notes.append(f"{rejected} posts skipped because their author had not consented.")
    if below_floor:
        notes.append(f"{below_floor} posts skipped from accounts under {MIN_FOLLOWERS_MASTODON} followers.")
    if reached and reached > start:
        # The timeline ran dry before the window did — the instance simply has no
        # more history, which is a size fact worth reporting rather than hiding.
        notes.append(
            f"The local timeline only reaches back to {reached.date().isoformat()}, "
            f"short of the {days}-day window."
        )

    return compute_curve(
        posts,
        platform="mastodon",
        instance=host,
        window_start=start,
        window_end=end,
        source=f"{host} local public timeline",
        notes=notes,
    )


def collect_mastodon_all(**kwargs) -> list[Curve]:
    """One curve per accepted instance."""
    return [collect_mastodon(host, **kwargs) for host in _accepted_mastodon_hosts()]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def load_curves() -> dict:
    if not CURVES_PATH.exists():
        return {}
    try:
        return json.loads(CURVES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_curve(curve: Curve) -> bool:
    """Store a curve. Returns False if an existing, better curve was kept instead.

    An unusable result is still worth recording when there is nothing better for
    that platform: the router turns its numbers into a specific explanation ("242
    posts from 10 accounts, which did not reproduce") rather than a vague one. But
    it must never displace a curve that DID clear the bar, or one bad night of
    collection would silently downgrade a working recommendation.
    """
    if not curve.attempted:
        log.info("[posting-time] %s was not read, so nothing is stored for it", curve.key)
        return False

    curves = load_curves()
    existing = curves.get(curve.key)
    if not curve.usable and existing and existing.get("usable"):
        log.info(
            "[posting-time] keeping the existing usable %s curve; this run scored %.3f",
            curve.key, curve.reliability,
        )
        return False

    curves[curve.key] = curve.to_dict()
    CURVES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURVES_PATH.write_text(json.dumps(curves, indent=2), encoding="utf-8")
    log.info("[posting-time] wrote %s curve to %s", curve.key, CURVES_PATH)
    return True


def summarise(curve: Curve) -> str:
    head = (
        f"{curve.key}: {curve.scored_posts:,} scored posts / "
        f"{curve.scored_authors:,} authors, window {curve.window_start}..{curve.window_end}\n"
        f"  reliability (split-half r) = {curve.reliability:+.3f} "
        f"[{'USABLE' if curve.usable else 'INSUFFICIENT DATA — will not be shown'}]"
    )
    if curve.scored_posts == 0:
        return head
    best = max(range(24), key=lambda h: curve.hourly[h])
    worst = min(range(24), key=lambda h: curve.hourly[h])
    return head + (
        f"\n  best hour {best:02d}:00 UTC ({curve.hourly[best]:.4f}), "
        f"worst {worst:02d}:00 UTC ({curve.hourly[worst]:.4f}), "
        f"swing {100 * (curve.hourly[best] - curve.hourly[worst]):.1f} pts"
    )
