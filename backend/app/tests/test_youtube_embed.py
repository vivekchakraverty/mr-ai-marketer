"""Reading a pasted YouTube link, and what each network can do with it.

The parsing tests matter because of what people actually paste: a share link from a phone is
`youtu.be/...`, a Short is `/shorts/...`, and a desktop copy carries `&t=` and `&app=`. A
reader that only knows `watch?v=` gets most of those wrong and refuses a perfectly good link.

Network behaviour is not identical and the tests say so rather than pretending: only Tumblr
carries a real player, Bluesky carries a card, and Mastodon has no embed field at all.
"""

from __future__ import annotations

import pytest

from app.services import youtube_embed as ye


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=share-tracking",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&t=42s",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ",
        "dQw4w9WgXcQ",
    ],
)
def test_every_shape_a_person_pastes_is_understood(raw):
    assert ye.video_id(raw) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "raw",
    [
        "https://vimeo.com/123456789",
        "https://www.youtube.com/",
        "https://www.youtube.com/@someone",
        "not a link at all",
        "",
        "https://example.com/watch?v=dQw4w9WgXcQ",  # right shape, wrong host
    ],
)
def test_anything_else_is_refused(raw):
    assert ye.video_id(raw) == ""


def test_a_short_link_becomes_the_canonical_one():
    """A redirect in a post body is something the reader has to trust, and some servers
    will not unfurl one — so whatever was pasted, the post carries the full address."""
    assert ye.canonical_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_a_non_youtube_link_is_refused_before_any_network_call(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("no request should be made for a link that is not YouTube")

    monkeypatch.setattr(ye.requests, "get", explode)
    with pytest.raises(ye.NotYouTube):
        ye.describe("https://vimeo.com/123456789")


def test_a_metadata_failure_still_yields_a_usable_card(monkeypatch):
    """A card with a plain title is still a card.

    Refusing the whole post because a title lookup timed out would be the wrong trade — the
    link is valid and the user asked for it.
    """
    def timeout(*_a, **_k):
        raise TimeoutError("oembed is slow today")

    monkeypatch.setattr(ye.requests, "get", timeout)
    video = ye.describe("https://youtu.be/dQw4w9WgXcQ")
    assert video.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert video.title == "YouTube video"
    # The thumbnail is derivable from the id without asking anyone.
    assert video.video_id in video.thumbnail_url


def test_a_deleted_video_is_refused(monkeypatch):
    """oEmbed answers 404 for these. A card titled after a dead link is worse than a no."""
    class _Gone:
        status_code = 404

    monkeypatch.setattr(ye.requests, "get", lambda *_a, **_k: _Gone())
    with pytest.raises(ye.NotYouTube, match="public"):
        ye.describe("https://youtu.be/dQw4w9WgXcQ")


def _video() -> ye.Video:
    return ye.Video(
        video_id="dQw4w9WgXcQ",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="A song",
        author="Rick Astley",
        thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    )


def test_mastodon_gets_the_link_in_the_body():
    """Mastodon has no embed field: the instance builds a card from a URL in the text, so
    the link being in the body IS the embed."""
    assert ye.with_link("watch this", _video()).endswith(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )


def test_a_link_already_in_the_draft_is_not_repeated():
    """People paste the link into the post as well as the box. Two copies of the same URL
    gets one card and one piece of litter."""
    draft = "watch this https://youtu.be/dQw4w9WgXcQ now"
    assert ye.with_link(draft, _video()) == draft


def test_an_empty_draft_becomes_just_the_link():
    assert ye.with_link("", _video()) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_the_card_description_names_the_channel():
    """What distinguishes a video card from a bare link at a glance."""
    assert _video().description == "Rick Astley on YouTube"
    assert ye.Video("x", "u", "t", "", "th").description == "YouTube"
