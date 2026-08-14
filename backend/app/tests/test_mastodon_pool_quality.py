"""What is allowed to become a Mastodon exemplar, and in what order.

These guard three defects found by measuring the live corpus after the first big
mastodon.social collection, all of which the pool builder had silently: posts nobody
engaged with became exemplars whenever a niche had fewer measured posts than slots
(41 of 187 active exemplars had zero interactions), the smallest accounts won the
ranking regardless of how well a post actually did, and hashtag walls were shown to
the model as writing to imitate.
"""

from __future__ import annotations

from app.routers import mastodon_post as mp


# --- the engagement floor --------------------------------------------------


def test_zero_engagement_never_qualifies():
    # The whole point: a post nobody touched is evidence the post did not work.
    assert mp.MIN_EXEMPLAR_INTERACTIONS > 0


def test_a_single_interaction_is_not_enough():
    # Indistinguishable from a self-boost, and the median settled post has exactly one.
    assert mp.MIN_EXEMPLAR_INTERACTIONS > 1


# --- the ranking prior -----------------------------------------------------


def test_a_popular_post_outranks_a_tiny_accounts_lucky_one():
    """The exact inversion measured on the live corpus, in miniature.

    Before the prior, 13 interactions from 96 followers (rate 0.135) beat 158 from
    1,924 (rate 0.082), so tag spam took the top slot on a real niche.
    """
    spam = mp._smoothed_rate(13, 96)
    popular = mp._smoothed_rate(158, 1924)
    assert popular > spam

    # And the raw measurement really did rank them the other way round — this is what
    # makes the prior necessary rather than decorative.
    assert 13 / 96 > 158 / 1924


def test_the_prior_still_normalises_for_reach():
    """It dampens small denominators; it does not just rank by raw interactions."""
    small_account_did_well = mp._smoothed_rate(100, 200)
    huge_account_did_poorly = mp._smoothed_rate(120, 50_000)
    assert small_account_did_well > huge_account_did_poorly


def test_zero_followers_cannot_divide_by_zero():
    # The prior is what makes this safe; there is no follower floor at ranking time.
    assert mp._smoothed_rate(5, 0) > 0


def test_more_interactions_always_scores_higher_at_equal_reach():
    assert mp._smoothed_rate(10, 500) > mp._smoothed_rate(9, 500)


# --- the prose floor -------------------------------------------------------


def test_a_hashtag_wall_has_no_prose():
    wall = "#paintings #art #artist #painting #artistsoninstagram #arts #love #sketch"
    assert mp._prose_words(wall) == 0
    assert mp._prose_words(wall) < mp.MIN_EXEMPLAR_PROSE_WORDS


def test_tags_and_links_do_not_count_as_writing():
    # A real post from the corpus: tag wall plus a link, and nothing else.
    assert (
        mp._prose_words("#biblepoem #poetry #writing #poems https://blessedbymag.example/x")
        < mp.MIN_EXEMPLAR_PROSE_WORDS
    )


def test_a_real_post_that_merely_uses_hashtags_survives():
    """Tagging a genuine post must not be punished — only tagging *instead of* posting."""
    tagged = '#SilentSunday "Remember that wherever your heart is, there you will find your treasure."'
    assert mp._prose_words(tagged) >= mp.MIN_EXEMPLAR_PROSE_WORDS


def test_prose_counting_handles_non_ascii():
    # The corpus is multilingual — the top-ranked post on mastodon.social is Spanish.
    assert mp._prose_words("El Tribunal Regional de Múnich falla en favor de los creadores") >= 4


def test_empty_text_is_not_writing():
    assert mp._prose_words("") == 0
    assert mp._prose_words(None) == 0
