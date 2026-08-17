"""Best-time-to-post recommendations for Bluesky.

The curve below is measured, not assumed. It comes from the fine-tune corpus'
re-hydrated Bluesky posts (`DATA_DIR/finetune/corpus.sqlite3`): posts captured from
the firehose at creation, then re-fetched through `app.bsky.feed.getPosts` so each one
carries a real lifetime like/repost/reply count alongside its creation timestamp.

Why a baked-in table rather than a live query: the firehose dump itself has no
engagement columns at all (it captures posts the moment they are created), so the
only way to score an hour is the re-hydration pass, which is a one-off offline job
over ~49k posts. Recomputing it per request would buy nothing — the corpus does not
change between releases — and the staging DB is not present in a packaged install.

EVERYTHING HERE IS IN UTC, ON PURPOSE. This module does no timezone conversion.
It did originally, taking a `utc_offset_minutes` query parameter, and that was wrong
in three ways worth not repeating: a whole-hour rotation put every half-hour zone
(India, Nepal, Chatham) out by up to 45 minutes; a single offset captured at request
time was then applied to a whole week of upcoming slots, so any DST boundary inside
that week was scored on the wrong side of it; and it made the browser and the server
two competing sources of truth about local time. The renderer maps this curve with
real `Date` objects, which get all of that right by construction — see
PostingTimePanel.tsx.

HOW THE NUMBERS WERE DERIVED — worth not relearning:

* Raw median engagement rate by hour is *misleading*. It peaks at 08:00 UTC at +137%
  of the daily median, but hour 08 also has the lowest median follower count in the
  corpus (808 vs ~1,050 elsewhere), and rate is engagement÷followers. Much of that
  spike is the denominator, not the hour.

* So HOURLY/DAILY below are within-author percentile ranks instead: for every author
  with >=5 posts that were not all identical, each of their posts is ranked against
  *their own* other posts, and the hour's score is the mean of those percentiles.
  0.500 means "an average slot for the person posting in it". This removes the
  account-quality and follower-count confounds that wreck the raw curve, because
  every author is compared only to themselves.

* The honest effect size is small: 0.5379 at the best hour vs 0.4704 at the worst,
  a ~7 percentile-point swing. This is a tiebreaker, not a growth lever, and the UI
  says so. For comparison, in the same corpus 55.2% of top-tier posts had media
  attached vs 23.5% of low-tier ones. Attaching an image matters far more than timing.

* Engagement anti-correlates with posting volume across hours (Pearson r = -0.641):
  the quiet hours are the good ones. VOLUME is exposed so the UI can show that.

* DAILY is indexed by *UTC* weekday, because that is how it was measured. For a
  reader far from UTC a local day overlaps two of these; the difference is far
  smaller than the noise, so the renderer labels by local day and scores by UTC.

* Per-NICHE curves were tested and deliberately NOT shipped. Split-half reliability
  of a niche's own 24h curve averages r=+0.131 across the 16 niches with >=400 posts
  (several are negative — music -0.01, finance -0.25, food -0.28), against r=+0.583
  for this pooled curve. A niche's "best hour" computed on two random halves of its
  own data disagrees by 4.76 hours on average, versus 5.99 for pure chance. It is a
  sample-size wall, not a claim that niches don't differ: subsampling shows even
  n=12,800 only reaches r=+0.277, so a stable per-niche curve needs roughly 30k posts
  *per niche*. Do not add a niche parameter here without that data behind it.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services import posting_time_corpus as ptc

router = APIRouter(prefix="/posting-time", tags=["posting-time"])

# Mean within-author percentile by UTC hour (index == hour). See module docstring.
HOURLY: list[float] = [
    0.4945, 0.5043, 0.5283, 0.5072, 0.5194, 0.5128, 0.5195, 0.5113,
    0.5379, 0.5254, 0.5112, 0.5230, 0.5085, 0.4871, 0.4969, 0.4704,
    0.4860, 0.4748, 0.4826, 0.4803, 0.4882, 0.5013, 0.5058, 0.5049,
]

# Same statistic by UTC weekday, Monday == 0 (matches datetime.weekday(), and
# JavaScript's getUTCDay() once Sunday is rotated from 0 to 6).
DAILY: list[float] = [0.5146, 0.5022, 0.4846, 0.4815, 0.4963, 0.5227, 0.5089]

# Posts created in each UTC hour across the scored corpus — the competition curve.
VOLUME: list[int] = [
    1472, 1698, 1640, 1296, 1226, 984, 857, 789, 847, 781, 807, 973,
    1204, 1376, 1647, 1692, 1813, 1868, 2018, 1883, 1780, 1462, 1185, 1281,
]

# Provenance, surfaced in the response so the screen can never overstate the evidence.
SAMPLE = {
    "scoredPosts": 17359,
    "scoredAuthors": 1844,
    "corpusPosts": 32579,
    "corpusAuthors": 10484,
    "windowStart": "2024-11-28",
    "windowEnd": "2024-12-23",
    "platform": "bluesky",
}

CAVEATS = [
    "Measured in UTC against a corpus with no audience-location data. The pattern may "
    "partly reflect who is awake and posting at each hour rather than when your own "
    "followers are reading.",
    "Drawn from a single four-week window (28 Nov – 23 Dec 2024), so it carries whatever "
    "was seasonal about that December.",
    "Correlational: nobody randomised posting time. Treat it as a tiebreaker between two "
    "otherwise equal slots.",
    "Timing is a small effect. Attaching an image separated high from low performers far "
    "more strongly than any hour did.",
]

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# An average slot for the account posting in it. Shared with the renderer rather than
# duplicated there, so "above average" means one thing in both places.
BASELINE = 0.5

# Width of a suggested window. A single argmax hour would imply a precision this curve
# does not have; three hours is schedulable and still wider than the noise between
# adjacent hours.
WINDOW_HOURS = 3


class HourScore(BaseModel):
    hourUtc: int
    score: float
    lift: float
    volume: int
    volumeShare: float


class DayScore(BaseModel):
    weekday: int  # UTC weekday, Monday == 0
    name: str
    score: float
    lift: float


class Recommendation(BaseModel):
    platform: str
    # False when this platform has no curve that reproduces on its own data. The
    # renderer shows the reason instead of a curve; there is deliberately no
    # fallback to "some other platform's numbers", which would be a fabrication.
    available: bool
    unavailableReason: str | None
    hours: list[HourScore]
    days: list[DayScore]
    baseline: float
    windowHours: int
    effect: dict
    sample: dict
    caveats: list[str]


def _correlation(xs: list[float] | list[int], ys: list[float]) -> float:
    """Pearson r, or 0.0 when a series is flat (a stored curve may carry no volume)."""
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = (
        sum((xs[i] - mx) ** 2 for i in range(n)) * sum((ys[i] - my) ** 2 for i in range(n))
    ) ** 0.5
    return round(num / den, 3) if den else 0.0


def _empty(platform: str, reason: str) -> Recommendation:
    return Recommendation(
        platform=platform,
        available=False,
        unavailableReason=reason,
        hours=[],
        days=[],
        baseline=BASELINE,
        windowHours=WINDOW_HOURS,
        effect={},
        sample={},
        caveats=[],
    )


def _build(
    platform: str,
    hourly: list[float],
    daily: list[float],
    volume: list[int],
    sample: dict,
    caveats: list[str],
) -> Recommendation:
    # A window can never be narrower than the resolution the sample supported: if the
    # data only distinguishes 4-hour blocks, suggesting a 3-hour slot inside one of them
    # is precision the measurement does not have. The default stays the floor.
    window_hours = max(WINDOW_HOURS, int(sample.get("resolutionHours") or 1))

    total_volume = sum(volume) or 1
    hours = [
        HourScore(
            hourUtc=h,
            score=hourly[h],
            lift=round(hourly[h] - BASELINE, 4),
            volume=volume[h],
            volumeShare=round(volume[h] / total_volume, 4),
        )
        for h in range(24)
    ]
    days = [
        DayScore(
            weekday=i,
            name=_DAY_NAMES[i],
            score=daily[i],
            lift=round(daily[i] - BASELINE, 4),
        )
        for i in range(7)
    ]
    best, worst = max(hourly), min(hourly)
    swing = round((best - worst) * 100, 1)
    # Computed from the curve actually being served, never asserted as a constant:
    # it was -0.641 on the 2024 corpus, and a refreshed window is free to disagree.
    vol_corr = _correlation(volume, hourly)

    return Recommendation(
        platform=platform,
        available=True,
        unavailableReason=None,
        hours=hours,
        days=days,
        baseline=BASELINE,
        windowHours=window_hours,
        effect={
            "bestScore": best,
            "worstScore": worst,
            "swingPercentilePoints": swing,
            "volumeEngagementCorrelation": vol_corr,
            "summary": (
                f"Roughly a {swing:.0f} percentile-point swing between the best and worst "
                "hour — a tiebreaker, not a growth lever."
            ),
        },
        sample=sample,
        caveats=caveats,
    )


@router.get("/recommendation", response_model=Recommendation)
def recommendation(
    platform: str = Query("bluesky", description="bluesky | mastodon"),
    instance: str = Query("", description="mastodon only: which instance you post to"),
) -> Recommendation:
    """The measured curve for one platform, in UTC.

    Local-time mapping belongs to the caller — see the module docstring.

    A curve collected over a recent one-month window (posting_time_cli) wins over
    the baked-in table, because it is the same statistic measured on data that has
    not aged. Each platform answers only from its OWN data: there is no cross-
    platform fallback, since a Bluesky curve says nothing about the fediverse.

    Mastodon answers PER INSTANCE. There is no "Mastodon" to average — each server
    is a community with its own hours, and the user posts to exactly one of them —
    so the curve is looked up by host and a server with too little data is told so
    by name rather than being quietly handed someone else's numbers.
    """
    platform = platform.lower().strip()
    if platform not in ("bluesky", "mastodon"):
        return _empty(platform, f"No posting-time data is collected for {platform}.")

    key = platform
    host = ""
    if platform == "mastodon":
        from ..services import mastodon as m

        host = m.normalise_host(instance) if instance.strip() else ""
        if not host:
            return _empty(
                "mastodon",
                "Pick the server you're posting to first — timing is measured per "
                "instance, because each one is its own community with its own hours.",
            )
        key = f"mastodon:{host}"

    stored = ptc.load_curves().get(key)
    if stored and (stored.get("usable") or stored.get("dailyUsable")):
        return _build(
            platform,
            stored["hourly"],
            stored["daily"],
            stored.get("volume") or [0] * 24,
            {
                "scoredPosts": stored["scoredPosts"],
                "scoredAuthors": stored["scoredAuthors"],
                "windowStart": stored["windowStart"],
                "windowEnd": stored["windowEnd"],
                "collectedAt": stored.get("collectedAt", ""),
                "reliability": stored.get("reliability"),
                # How wide the time-of-day buckets are. 1 is the hourly curve; anything
                # larger means the sample supported a window and not an hour, and the
                # panel must not claim more precision than that.
                "resolutionHours": stored.get("resolutionHours", 1),
                "dailyUsable": stored.get("dailyUsable", True),
                "platform": platform,
                "instance": stored.get("instance", ""),
                "source": stored.get("source", ""),
            },
            [
                "Measured in UTC with no audience-location data. The pattern may partly "
                "reflect who is awake and posting at each hour rather than when your own "
                "followers are reading.",
                f"Collected over one month ({stored['windowStart']} – {stored['windowEnd']}), "
                "so it reflects that period rather than the whole year.",
                "Correlational: nobody randomised posting time. Treat it as a tiebreaker "
                "between two otherwise equal slots.",
                # Kept on every path, not just the baked-in one: it is the single most
                # useful thing this panel can tell someone, and it argues against the
                # panel's own importance.
                "Timing is a small effect. Attaching an image separated high from low "
                "performers far more strongly than any hour did.",
                *stored.get("notes", []),
            ],
        )

    if platform == "bluesky":
        # The original measurement, kept as the floor so the tool always has an
        # answer for the platform it was built on even before a refresh is run.
        return _build("bluesky", HOURLY, DAILY, VOLUME, SAMPLE, CAVEATS)

    # Why this is hard on the fediverse, and what would actually change it: an
    # instance is only the system of record for its OWN accounts' counts, so the
    # sample is capped by that one server's population. A small instance cannot get
    # there no matter how long it is left running, which is worth saying rather
    # than implying "check back later".
    if not stored:
        return _empty(
            "mastodon",
            f"No timing data has been collected for {host} yet. Engagement counts are "
            f"only accurate on the server that hosts an account, so this can only learn "
            f"from {host}'s own members.",
        )

    posts = stored.get("scoredPosts", 0)
    authors = stored.get("scoredAuthors", 0)
    if posts == 0:
        reason = f"{host} has no usable public posts to learn from in the last month."
    else:
        reason = (
            f"Not enough data from {host} to call it. Its last month gave "
            f"{posts:,} scored posts from {authors:,} accounts, which did not reproduce "
            f"on their own data (reliability {stored.get('reliability', 0):+.2f}) — "
            f"showing a curve from that would be noise dressed as advice."
        )
    return _empty(
        "mastodon",
        reason
        + " Timing here is measured per server, so a larger instance you also post from "
        "could still have enough.",
    )


class MeasureResponse(BaseModel):
    instance: str
    enough: bool
    scoredPosts: int
    scoredAuthors: int
    reliability: float
    detail: str


class MeasureRequest(BaseModel):
    instance: str
    days: int = 31
    #: Optional. The larger instances refuse anonymous reads of the public local
    #: timeline, which is the only sample this can learn from — see collect_mastodon.
    #: In the body rather than the query string on purpose: a token in a URL ends up
    #: in server logs and browser history, and this one can post as the user.
    accessToken: str = ""
    #: Which instance issued that token. A Mastodon token is only valid on the server
    #: that granted it, and sending it anywhere else hands a third party a credential
    #: that can post as the user — so the collector drops it unless this matches.
    tokenInstance: str = ""


@router.post("/measure", response_model=MeasureResponse)
def measure(body: MeasureRequest) -> MeasureResponse:
    """Read one instance's last month and work out whether it can support a curve.

    Exposed because the instance is the user's to name — they type a server into
    the composer, and the answer for that server has to be obtainable there rather
    than from a terminal. Runs the same collector and the same reliability gate as
    the CLI, and stores the result the same way, so there is one code path and one
    verdict.

    The rules gate is inside the collector: an instance whose published rules have
    not been accepted (or have changed since) is never read, and comes back here
    as a plain explanation instead.
    """
    from ..services import mastodon as m

    days = max(7, min(90, body.days))
    # normalise_host RAISES on empty or malformed input rather than returning "", so the
    # old `if not host` guard below could never fire and a blank instance surfaced as a
    # 500. Its message is already written for a user, so it is passed straight through.
    try:
        host = m.normalise_host(body.instance)
    except m.MastodonError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    if not host:
        raise HTTPException(status_code=400, detail="Give a Mastodon host, e.g. toot.garden")

    curve = ptc.collect_mastodon(
        host, days=days, token=body.accessToken, token_instance=body.tokenInstance
    )
    ptc.save_curve(curve)

    if not curve.attempted:
        # Nothing was read — the gate refused or the server was unreachable. Say
        # that, rather than reporting zero posts as if we had looked and found none.
        detail = curve.notes[0] if curve.notes else f"{host} could not be read."
    elif curve.usable:
        detail = (
            f"{host} has enough: {curve.scored_posts:,} scored posts from "
            f"{curve.scored_authors:,} accounts."
        )
    elif curve.scored_posts == 0:
        detail = f"{host} had no usable public posts in the last {days} days."
    else:
        detail = (
            f"Not enough from {host} — {curve.scored_posts:,} scored posts from "
            f"{curve.scored_authors:,} accounts, which did not reproduce on their own "
            f"data (reliability {curve.reliability:+.2f})."
        )

    return MeasureResponse(
        instance=host,
        enough=curve.usable,
        scoredPosts=curve.scored_posts,
        scoredAuthors=curve.scored_authors,
        reliability=round(curve.reliability, 4),
        detail=detail,
    )
