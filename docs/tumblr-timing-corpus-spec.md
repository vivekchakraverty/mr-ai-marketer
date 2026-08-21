# Measuring what Tumblr rewards — a collection spec

## Why the current corpus cannot answer this

Every post-level question tested against the existing 7,621-post corpus failed to reproduce
under split-half testing, at the same 0.30 gate the Bluesky and Mastodon curves had to clear:

| Candidate | Split-half reliability | Verdict |
|---|---|---|
| Posting hour | +0.06 | fails |
| Day of week | −0.05 | fails |
| Tag count | +0.17 | fails |
| Post length (25-word bins) | +0.267 across five seeds (.21–.30) | fails |
| Post length (10-word bins) | +0.05 | fails |

Only one thing held: posts with media beat posts without by **+4.0 percentile points**,
positive in all 200 half-samples (5th–95th: +2.5 to +5.3).

The reason is in how the corpus was gathered, not in Tumblr:

- The median post was **1,312 hours old (55 days)** when collected.
- **166 of 2,942** posts were collected within 48 hours of being published.
- Note counts are **lifetime** totals.

Tumblr notes accumulate through reblogs for weeks. A lifetime total on a two-month-old post
is dominated by how far it travelled, not by anything decidable at the moment of posting. No
statistic recovers a posting-hour effect from that, and no amount of additional retrospective
collection changes it — the sample is already 6,732 scored posts, far more than Mastodon
needed to produce a usable curve.

## What would work

The same shape the other two networks already use: catch posts while they are new, then
measure the same post again at fixed ages.

**1. Watch, do not trawl.** Follow a fixed set of blogs and pick up posts within hours of
publication rather than sampling old ones. A post first seen at 55 days can never be
back-measured.

**2. Snapshot at fixed ages.** Record notes at **1h, 24h and 48h** after publication, into
`engagement_snapshots` keyed by `(post_uri, window_label)` — the table and the window labels
the Mastodon collector already writes (`routers/mastodon_post.py`, `_BUCKETS`). Reusing them
means the posting-time machinery, the reliability gate and the resolution ladder all work on
Tumblr data with no new statistics.

**3. Score on the 48h figure**, within-blog percentile, exactly as now. What changes is that
the number being ranked is comparable across posts instead of being a function of age.

## Cost

Tumblr allows **300 requests/minute per IP and 1,000/hour per consumer key**
(`services/tumblr.py`). `/blog/{blog}/posts` returns 20 posts with their note counts in one
call, so a blog is re-measured in a single request regardless of how many posts it has in
flight.

| | Requests |
|---|---|
| One sweep of 150 blogs | 150 |
| Sweeps per day (hourly) | 24 |
| **Per day** | **3,600** |
| Ceiling (1,000/hour) | 24,000 |

That is 15% of the allowance, leaving the Engage and generator paths untouched. Nothing here
needs a second key or a proxy.

## How long, and how much is enough

Reference points from the two networks that already have curves:

| Corpus | Scored posts | Authors | Reliability |
|---|---|---|---|
| Bluesky | 68,346 | 748 | +0.55 |
| mastodon.social | 2,613 | 25 | +0.48 |
| toot.garden | 242 | 10 | +0.11 — refused |

mastodon.social is the useful precedent: **~2,600 posts from 25 accounts was enough**. The
existing Tumblr corpus already identifies **327 blogs with 10+ posts**, so blog supply is not
the constraint — freshness is.

Target: **2,500 scored posts from 100+ blogs**, which at typical posting rates is roughly
**three to four weeks** of continuous watching.

## What it looks like when it is done

The honest outcome is decided by the gate, not by the effort spent:

- **Reliability ≥ 0.30** → the Tumblr creator gets the same "When to post" panel the other
  two have, at whatever resolution the ladder supports.
- **Below 0.30** → the panel says so, naming the sample, exactly as toot.garden does today.
  That is a real answer: *"timing does not move Tumblr notes"* is worth knowing and worth
  saying, and the collection is what earns the right to say it.

The same corpus also re-tests tag count and post length for free, since both fail today for
the same reason — they are being asked of lifetime totals.

## Work items

1. `services/tumblr_timing.py` — blog watch list, hourly sweep, snapshot writer against the
   existing `engagement_snapshots` shape.
2. A scheduler entry beside the three that already run (`social_post`, `mastodon_post`,
   `tumblr_post` in `main.py`), inert until credentials and a watch list exist.
3. `posting_time_corpus.collect_tumblr()` — build a curve from 48h snapshots, reusing
   `_curve_from`, `_reliability_of` and `RESOLUTION_LADDER` unchanged.
4. `routers/posting_time.py` — accept `platform=tumblr`; the refusal message names the
   sample the way the Mastodon one does.
5. `PostingTimePanel` on the Tumblr creator — no component changes, it is already generic.

Items 3–5 are small and mostly reuse. Item 1 is the real work, and item 2 is what makes it
run without anyone remembering to.

## The decision this spec exists for

Three to four weeks of background collection, for an answer that may well be "no effect".
That is a fair trade only because the alternative on offer is a curve drawn from noise —
which is worse than no panel, since it would be followed.
