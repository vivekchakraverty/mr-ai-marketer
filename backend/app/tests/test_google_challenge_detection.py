"""Telling Google's "prove you are human" page apart from an ordinary results page.

Getting this wrong in the permissive direction is expensive and silent: the run parks
itself on `needs_attention`, tells the user to go and complete a check that is not there,
and sits out a five-minute timeout beside a panel already full of the numbers it wanted.

That is exactly what happened. The detector treated any page containing "not a robot" as a
challenge — and that phrase is the label on every reCAPTCHA checkbox on the web, so any
page discussing captchas quotes it. A search about plugins or AI can easily surface one.

Driven with a fake page, because what is under test is the rule, not Chrome.
"""

from __future__ import annotations

from app.services.keyword_surfer_collector import _is_google_challenge

# The page script the detector runs, reduced to the decision it makes. Kept in the test as a
# Python mirror so the fake page can answer it the way a browser would.
def _decide(text: str, has_form: bool) -> bool:
    lowered = text.lower()
    if has_form:
        return True
    if len(lowered) > 1500:
        return False
    return (
        "unusual traffic from your computer network" in lowered
        or "our systems have detected unusual traffic" in lowered
    )


class _FakePage:
    def __init__(self, url: str, text: str = "", has_form: bool = False):
        self.url = url
        self._text = text
        self._has_form = has_form

    def evaluate(self, _script: str):
        return _decide(self._text, self._has_form)


REAL_CHALLENGE_TEXT = (
    "About this page\n\nOur systems have detected unusual traffic from your computer "
    "network. This page checks to see if it's really you sending the requests."
)


def test_the_sorry_url_is_a_challenge():
    assert _is_google_challenge(_FakePage("https://www.google.com/sorry/index?continue=x"))


def test_googles_own_wording_on_a_short_page_is_a_challenge():
    assert _is_google_challenge(_FakePage("https://www.google.com/search?q=x", REAL_CHALLENGE_TEXT))


def test_the_challenge_form_is_a_challenge_whatever_the_text():
    assert _is_google_challenge(_FakePage("https://www.google.com/search?q=x", "", has_form=True))


def test_a_results_page_that_merely_mentions_robots_is_not_a_challenge():
    """The regression this exists for.

    A results page whose snippets say "I'm not a robot" is a results page. Reading it as a
    challenge is what left a finished search waiting on a check nobody was being asked for.
    """
    page = _FakePage(
        "https://www.google.com/search?q=ai+plugin+for+game+engines&sei=abc",
        "Keyword ideas\n" + "godot engine plugin results ... " * 60 + "\nHow to click I'm not a robot",
    )
    assert not _is_google_challenge(page)


def test_a_long_page_quoting_the_exact_wording_is_still_not_a_challenge():
    """An article *about* the interstitial is not the interstitial.

    Length is the tell: Google's own check page is a few hundred characters, and a page of
    search results is thousands.
    """
    page = _FakePage(
        "https://www.google.com/search?q=unusual+traffic+error",
        REAL_CHALLENGE_TEXT + "\n" + ("explanatory article text " * 200),
    )
    assert not _is_google_challenge(page)


def test_an_unreadable_page_is_not_reported_as_a_challenge():
    """A page mid-navigation raises. Guessing "challenge" there would stop a healthy run."""

    class _Exploding:
        url = "https://www.google.com/search?q=x"

        def evaluate(self, _script):
            raise RuntimeError("Execution context was destroyed")

    assert not _is_google_challenge(_Exploding())
