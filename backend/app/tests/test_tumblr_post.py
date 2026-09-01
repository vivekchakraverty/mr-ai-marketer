"""The Tumblr generator's decisions that are easy to get wrong and quiet when they are.

Everything here is pure — no database, no network — so it guards the reasoning rather
than the plumbing: which taxonomy the tool uses, what a post is ranked against, and how a
pasted permalink is read.
"""

from __future__ import annotations

from app.routers import tumblr_post as tp
from app.services import tumblr_corpus as tc


# --- the ranking denominator ------------------------------------------------


def test_the_prior_stops_a_tiny_blog_dividing_by_almost_nothing():
    """A blog whose median original gets 3 notes must not turn one post into infinity."""
    tiny = tc.audience_rate(300, 3)
    big = tc.audience_rate(300, 3000)
    assert tiny > big  # still rewards outperforming a small baseline
    # ...but bounded: without the prior this would be 100.0.
    assert tiny < 300 / tc.AUDIENCE_PRIOR


def test_audience_rate_never_divides_by_zero():
    # A brand-new blog has no baseline at all; the prior alone carries it.
    assert tc.audience_rate(10, 0.0) == round(10 / tc.AUDIENCE_PRIOR, 6)


def test_the_prior_is_the_measured_corpus_median():
    # Recomputed when the collector stopped on 2026-08-14 (7,621 rows). If the corpus is
    # ever rebuilt this needs recomputing — the constant is a measurement, not a taste.
    assert tc.AUDIENCE_PRIOR == 36.0


# --- Tumblr's taxonomy is its own -------------------------------------------


def test_corpus_key_carries_the_platform():
    # This is what stops the Bluesky scheduler's refresh_exemplars deactivating every
    # Tumblr exemplar when it rebuilds a niche of the same name.
    assert tc.corpus_niche("art_design") == "art_design · tumblr"


def test_unclassified_posts_go_to_the_general_pool():
    assert tc._niche_of({"niche": "other", "niche_confidence": 0.99}) == tc.GENERAL_NICHE
    assert tc._niche_of({"niche": None, "niche_confidence": 0.99}) == tc.GENERAL_NICHE


def test_a_low_confidence_guess_is_not_a_niche():
    """A mis-filed exemplar is shown to the model as 'write like this'."""
    low = {"niche": "technology", "niche_confidence": tc.MIN_NICHE_CONFIDENCE - 0.01}
    high = {"niche": "technology", "niche_confidence": tc.MIN_NICHE_CONFIDENCE}
    assert tc._niche_of(low) == tc.GENERAL_NICHE
    assert tc._niche_of(high) == "technology"


# --- reading a pasted permalink ---------------------------------------------


def test_parses_the_classic_subdomain_permalink():
    assert tp._parse_post_url("https://myblog.tumblr.com/post/12345/a-slug", "") == (
        "myblog",
        "12345",
    )


def test_parses_the_modern_www_permalink():
    # Tumblr serves both shapes; a user will paste whichever their browser showed.
    assert tp._parse_post_url("https://www.tumblr.com/myblog/67890/a-slug", "") == (
        "myblog",
        "67890",
    )


def test_falls_back_to_the_known_blog_when_only_an_id_is_pasted():
    assert tp._parse_post_url("12345678", "myblog") == ("myblog", "12345678")


def test_a_permalink_with_no_id_yields_no_id_rather_than_a_wrong_one():
    blog, post_id = tp._parse_post_url("https://myblog.tumblr.com/", "myblog")
    assert post_id == ""


# --- prose floor -------------------------------------------------------------


def test_a_tag_wall_is_not_a_writing_exemplar():
    # Tumblr is the most tag-heavy of the three platforms, so this floor matters most here.
    assert tc._prose_words("#art #fanart #digitalart #artistsontumblr #sketch") == 0
    assert tc._prose_words("finally finished this one, took me three weeks") >= 4


def test_multiword_inline_tags_are_only_partly_stripped():
    """A known, bounded limitation, asserted so it stays known.

    Tumblr tags may contain spaces, unlike Mastodon's or Bluesky's, and `#\\w+` can only
    take the first word — "#digital art" leaves "art" behind. That makes the floor
    slightly *permissive* for inline multi-word tags, never stricter, so nothing is
    wrongly excluded. It matters little in practice because Tumblr keeps tags in their
    own field rather than in the post body; this only affects tags typed inline.
    """
    assert tc._prose_words("#digital art #artists on tumblr") == 3


# --- the reserved slot for the user's own post ------------------------------


def _pair(uri: str, score: float) -> tuple[float, dict]:
    return (score, {"uri": uri, "text": "a real post with several words in it"})


def _corpus_pool(n: int = 15) -> list[tuple[float, dict]]:
    """A full pool of viral corpus posts, best first — what a Tumblr niche looks like."""
    return [_pair(f"tumblr://someone/{i}", 200.0 - i) for i in range(n)]


def test_the_users_post_takes_a_slot_without_out_scoring_the_corpus(monkeypatch):
    """The whole point: the corpus is pre-filtered to viral posts, so merit alone never works."""
    chosen = _corpus_pool()
    mine = _pair("tumblr://me/1", 3.5)  # a good post by its own blog's standards
    scored = chosen + [mine]
    monkeypatch.setattr(tp, "_own_post_uris", lambda key: {"tumblr://me/1"})

    out = tp._reserve_own_slot(chosen, scored, "art_design · tumblr")

    assert any(post["uri"] == "tumblr://me/1" for _, post in out)
    assert len(out) == tp.TARGET_POOL_SIZE  # a slot is taken, not added
    assert max(score for score, _ in out) == 200.0  # the best corpus post is untouched
    # The weakest earned entry is the one displaced.
    assert not any(post["uri"] == "tumblr://someone/14" for _, post in out)


def test_a_post_that_never_cleared_the_floors_gets_no_slot(monkeypatch):
    """Guaranteed means 'need not out-score the corpus', not 'gets in having earned nothing'.

    `scored` has already dropped anything under the note and prose floors, so a flopped
    post simply is not in it — reserving a slot for one would teach the generator to write
    like a post that did not work.
    """
    chosen = _corpus_pool()
    monkeypatch.setattr(tp, "_own_post_uris", lambda key: {"tumblr://me/flopped"})

    out = tp._reserve_own_slot(chosen, chosen, "art_design · tumblr")

    assert out == chosen


def test_no_double_reservation_when_the_users_post_already_earned_a_place(monkeypatch):
    mine = _pair("tumblr://me/1", 500.0)  # genuinely out-performed the corpus
    chosen = [mine] + _corpus_pool(14)
    monkeypatch.setattr(tp, "_own_post_uris", lambda key: {"tumblr://me/1"})

    out = tp._reserve_own_slot(chosen, chosen, "art_design · tumblr")

    assert out == chosen
    assert sum(1 for _, post in out if post["uri"] == "tumblr://me/1") == 1


def test_nothing_published_yet_leaves_the_pool_alone(monkeypatch):
    chosen = _corpus_pool()
    monkeypatch.setattr(tp, "_own_post_uris", lambda key: set())
    assert tp._reserve_own_slot(chosen, chosen, "art_design · tumblr") == chosen


def test_an_empty_pool_is_not_reserved_into(monkeypatch):
    monkeypatch.setattr(tp, "_own_post_uris", lambda key: {"tumblr://me/1"})
    assert tp._reserve_own_slot([], [], "art_design · tumblr") == []


def test_buckets_match_the_shared_schema_constraint():
    # engagement_snapshots.window_label has a CHECK permitting exactly these three.
    assert [label for label, _ in tp._BUCKETS] == ["1h", "24h", "48h"]


# --- the multipart post has to outlive its own body -------------------------
#
# Tumblr takes video inline, up to video_attach.TUMBLR_MAX_BYTES (100MB), through the one
# request in this module that is multipart. It used to be written under REQUEST_TIMEOUT, the
# same 25 seconds a JSON call gets. The identical mistake on Mastodon surfaced as
# SSLError(SSLWantWriteError(...)) rather than a timeout — see upload_budget.

import pytest

from app.services import tumblr, upload_budget, video_attach

_CREDS = tumblr.Credentials("ck", "cs", "tok", "sec", blog="example")


class _Resp:
    status_code = 201

    def json(self) -> dict:
        return {"response": {"id": "9"}}


def _budget_for(monkeypatch: pytest.MonkeyPatch, content: bytes) -> float:
    """The timeout the multipart post actually hands to requests for a body this size."""
    seen: dict[str, float] = {}

    def fake_post(*_a, **kwargs) -> _Resp:
        seen["timeout"] = kwargs["timeout"]
        return _Resp()

    monkeypatch.setattr(tumblr.requests, "post", fake_post)
    tumblr.create_npf_post_with_media(_CREDS, "example", {}, "clip.mp4", content)
    return seen["timeout"]


def test_a_video_post_is_not_written_under_a_json_call_s_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _budget_for(monkeypatch, b"x" * 30_302_290) > tumblr.REQUEST_TIMEOUT


def test_the_budget_covers_the_largest_video_tumblr_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling video_attach enforces must be postable, not merely uploadable."""
    budget = _budget_for(monkeypatch, b"x" * video_attach.TUMBLR_MAX_BYTES)
    assert budget < upload_budget.MAX_SECONDS  # covered on its own terms, not by the cap
    assert budget == pytest.approx(860, abs=2)


def test_a_failed_upload_says_which_file_and_how_long_it_waited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_a, **_k):
        raise tumblr.requests.RequestException("SSLWantWriteError")

    monkeypatch.setattr(tumblr.requests, "post", fake_post)
    with pytest.raises(tumblr.TumblrError) as excinfo:
        tumblr.create_npf_post_with_media(_CREDS, "example", {}, "clip.mp4", b"x" * (2 << 20))
    message = str(excinfo.value)
    assert "clip.mp4" in message and "2.0MB" in message
