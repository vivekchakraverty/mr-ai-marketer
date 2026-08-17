"""Report the finest resolution the data supports, and no finer.

An hourly curve asks for more precision than most instances hold, and the reliability
gate then refused everything — including the coarser statement the same data supported.
Measured on two real instances: hourly failed on both (+0.142, +0.263) while blocks of a
few hours passed comfortably (+0.316, +0.426). The ladder exists to find that rung.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from app.services import posting_time_corpus as ptc


def _sample(block_hours: int, n: int = 4000, noise: float = 0.25) -> list[ptc.Post]:
    """Posts whose engagement follows a block-shaped pattern of the given width.

    One author per 10 posts so the within-author ranking has something to rank, and the
    signal is on the block, not the hour — which is exactly the case the ladder is for.
    """
    rng = random.Random(7)
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    posts: list[ptc.Post] = []
    for i in range(n):
        created = start + timedelta(hours=rng.randrange(24), days=rng.randrange(60))
        block = created.hour // block_hours
        # A smooth preference across blocks, plus noise that hides hour-level detail.
        signal = math.sin(block / (24 / block_hours) * 2 * math.pi)
        posts.append(
            ptc.Post(
                author=f"author-{i // 10}",
                created_at=created,
                engagement=max(0, int(100 + 60 * signal + rng.gauss(0, 60 * noise))),
                followers=1000,
            )
        )
    return posts


def _curve(posts: list[ptc.Post]) -> ptc.Curve:
    now = datetime.now(timezone.utc)
    return ptc.compute_curve(
        posts,
        platform="mastodon",
        window_start=now - timedelta(days=60),
        window_end=now,
        source="test",
        instance="test.instance",
    )


def test_the_ladder_is_finest_first():
    """Order matters: it must stop at the first rung that clears, not the best one."""
    assert ptc.RESOLUTION_LADDER == (1, 2, 3, 4)


def test_it_stops_at_the_first_rung_that_clears(monkeypatch):
    """Which rung gets picked, tested directly rather than through synthetic statistics.

    A block-shaped signal is also an hour-shaped one — hours inside a block share a
    value — so no amount of synthetic data produces "blocks reproduce but hours do not".
    Real data gets there through thin per-hour samples instead, which is why the
    coarsening path is evidenced by replaying two real instances (mastodon.social chose
    2-hour blocks at +0.426, hachyderm.io chose 4-hour at +0.316) rather than simulated
    here. What *is* worth pinning down is the choice itself.
    """
    calls: list[int] = []
    # Hourly fails, 2-hour fails, 3-hour clears, 4-hour would clear better — the ladder
    # must take 3 and stop, not shop for the highest score.
    scores = {1: 0.10, 2: 0.20, 3: 0.55, 4: 0.90}

    def fake(scored, bucket_of, n_buckets, trials=40):
        if n_buckets == 7:  # the day-of-week question, asked separately
            return 0.0
        width = 24 // n_buckets
        calls.append(width)
        return scores[width]

    monkeypatch.setattr(ptc, "_reliability_of", fake)
    curve = _curve(_sample(block_hours=4))

    assert curve.resolution_hours == 3
    assert curve.reliability == 0.55
    assert calls == [1, 2, 3], "must stop at the first rung that clears"


def test_the_reported_curve_never_implies_more_precision_than_the_rung(monkeypatch):
    """Whatever width is chosen, the hourly array is a plateau of exactly that width."""
    monkeypatch.setattr(
        ptc, "_reliability_of",
        lambda s, b, n, trials=40: 0.9 if n == 6 else 0.0,  # only 4-hour blocks clear
    )
    curve = _curve(_sample(block_hours=4))

    assert curve.resolution_hours == 4
    assert len({round(v, 6) for v in curve.hourly}) == 6
    # And the block boundaries are where they should be.
    for block_start in range(0, 24, 4):
        block = curve.hourly[block_start : block_start + 4]
        assert len(set(round(v, 6) for v in block)) == 1


def test_noise_still_gets_refused():
    """Coarsening must not become a way to manufacture a result from nothing."""
    rng = random.Random(3)
    now = datetime.now(timezone.utc)
    posts = [
        ptc.Post(
            author=f"author-{i // 10}",
            created_at=now - timedelta(hours=rng.randrange(24 * 60)),
            engagement=rng.randrange(200),
            followers=1000,
        )
        for i in range(4000)
    ]
    curve = _curve(posts)
    assert not curve.usable
    assert curve.resolution_hours == 0, "no rung should have claimed pure noise"


def test_the_hourly_array_keeps_its_shape_at_every_resolution():
    """The renderer maps 24 UTC values to local time; that contract does not change."""
    for width in (1, 4):
        curve = _curve(_sample(block_hours=width))
        assert len(curve.hourly) == 24
        assert len(curve.daily) == 7
        assert len(curve.volume) == 24


def test_day_of_week_is_scored_separately():
    """A different question, not a coarser one.

    Measured, the two disagree about which is stronger: hachyderm's weekly rhythm beat
    its daily one (+0.454 vs +0.316), mastodon.social's was the reverse (+0.311 vs
    +0.426). Gating one on the other would throw away a real answer.
    """
    curve = _curve(_sample(block_hours=4))
    assert curve.daily_reliability != curve.reliability
    assert curve.anything_usable == (curve.usable or curve.daily_usable)


def test_a_bucketing_too_coarse_to_test_scores_zero_rather_than_passing():
    """_pearson refuses fewer than six points, so anything under six buckets is untestable.

    It must read as "cannot say", never as a passing correlation — which is why the
    ladder stops at four hours.
    """
    scored = [(datetime(2026, 6, 1, h % 24, tzinfo=timezone.utc), 0.5) for h in range(500)]
    assert ptc._reliability_of(scored, lambda d: d.hour // 6, 4) == 0.0
    assert ptc._reliability_of(scored, lambda d: d.weekday() >= 5, 2) == 0.0
