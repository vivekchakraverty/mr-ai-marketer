"""Where the AI disclosure sits relative to a post's hashtags.

Mastodon renders hashtags as a separate bar under the post, but only when they are the very
last line — its hashtag_bar.tsx stops the moment it meets anything that is not a hashtag.
The disclosure used to be appended after them, which left every tag inline in the body while
other people's posts showed tidy chips. These pin the ordering down.
"""

from __future__ import annotations

import pytest

from app.routers.mastodon_post import DISCLOSURE_LINE, _with_disclosure


def test_the_disclosure_goes_before_a_trailing_hashtag_line():
    text = "a thought worth having.\n\n#CreativeWorks #Imagination"
    assert _with_disclosure(text) == (
        "a thought worth having.\n\n" f"{DISCLOSURE_LINE}\n\n" "#CreativeWorks #Imagination"
    )


def test_the_hashtags_are_the_last_line_which_is_the_whole_point():
    out = _with_disclosure("words.\n\n#Tag #Other")
    assert out.splitlines()[-1] == "#Tag #Other"


def test_a_post_without_hashtags_still_ends_with_the_disclosure():
    assert _with_disclosure("just words.") == f"just words.\n\n{DISCLOSURE_LINE}"


def test_hashtags_inside_a_sentence_are_not_a_trailing_line():
    """Only a line that is nothing but hashtags counts — the same rule Mastodon applies."""
    text = "i wrote about #rust today and it went well."
    assert _with_disclosure(text) == f"{text}\n\n{DISCLOSURE_LINE}"


def test_a_sentence_after_the_hashtags_is_kept_as_body():
    """The trailing block ends at the first non-hashtag line, so text below the tags stays
    where the author put it and the disclosure lands before it rather than after."""
    text = "words.\n\n#Tag\n\nand a closing thought."
    out = _with_disclosure(text)
    assert out == f"words.\n\n#Tag\n\nand a closing thought.\n\n{DISCLOSURE_LINE}"


def test_several_trailing_hashtag_lines_all_stay_at_the_end():
    out = _with_disclosure("words.\n\n#One #Two\n#Three")
    assert out == f"words.\n\n{DISCLOSURE_LINE}\n\n#One #Two\n#Three"


def test_a_post_that_is_only_hashtags_keeps_them_last():
    """Mastodon allows a hashtag-only post to show its bar when it carries media, so the
    disclosure leads rather than displacing the tags."""
    assert _with_disclosure("#Only #Tags") == f"{DISCLOSURE_LINE}\n\n#Only #Tags"


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_an_empty_draft_is_just_the_disclosure(text):
    assert _with_disclosure(text) == DISCLOSURE_LINE


def test_trailing_whitespace_does_not_hide_the_hashtag_line():
    """The generator emits trailing double-spaces for Markdown-style line breaks, so the
    last line is rarely clean."""
    out = _with_disclosure("words.  \n\n#Tag #Other   \n")
    assert out.splitlines()[-1] == "#Tag #Other"
