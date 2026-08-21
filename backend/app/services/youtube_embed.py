"""Turn a YouTube link into whatever each network means by "embed".

The three networks mean three different things, and pretending otherwise would produce a
feature that works on one of them:

  Tumblr     a real embed. NPF has a `video` block that takes a third-party URL and Tumblr
             resolves it into an inline player.
  Bluesky    a link card. `app.bsky.embed.external` carries a title, description and
             thumbnail; clients render it as a play-able card. There is no inline player in
             the protocol, so this is the ceiling.
  Mastodon   nothing to build. A status is text — there is no embed field in the API at all.
             The *server* fetches links in the body and generates a preview card itself, so
             the only thing that matters is that the URL survives into the text. Instances
             can and do disable cards, which is a fact worth telling the user rather than
             discovering after posting.

METADATA COMES FROM oEMBED, which is public and needs no key — verified against the live
endpoint. Title and thumbnail are what make a Bluesky card look like a video rather than a
bare link, and asking YouTube for them is one request with no credential to manage.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"
REQUEST_TIMEOUT = 12

#: Every shape a person might paste. youtu.be short links and /shorts/ are the two that a
#: naive "take the v= parameter" reader gets wrong, and both are what people actually copy
#: from a phone.
_PATTERNS = (
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([\w-]{11})"),
    re.compile(r"youtu\.be/([\w-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/(?:shorts|embed|v|live)/([\w-]{11})"),
)


class NotYouTube(ValueError):
    """The text given is not a YouTube link. The message is written for a user."""


@dataclass(frozen=True)
class Video:
    video_id: str
    #: The canonical watch URL. Always this form, whatever was pasted: a short link in a
    #: post body is a redirect a reader has to trust, and some servers will not unfurl one.
    url: str
    title: str
    author: str
    thumbnail_url: str

    @property
    def description(self) -> str:
        return f"{self.author} on YouTube" if self.author else "YouTube"


def video_id(raw: str) -> str:
    """The eleven-character id out of any YouTube URL, or '' if this is not one."""
    text = (raw or "").strip()
    if not text:
        return ""
    for pattern in _PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(1)
    # A bare id, which is what someone pastes when they have copied from a URL by hand.
    if re.fullmatch(r"[\w-]{11}", text):
        return text
    return ""


def canonical_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def describe(raw: str) -> Video:
    """Resolve a pasted link into the video, with its title and thumbnail.

    Raises NotYouTube for anything that is not a YouTube link — including a link to a video
    that does not exist, since oEmbed answers 404 for those and a card titled after a dead
    link is worse than a refusal.
    """
    vid = video_id(raw)
    if not vid:
        raise NotYouTube(
            "That does not look like a YouTube link. Paste the full address of the video."
        )

    url = canonical_url(vid)
    title = author = thumbnail = ""
    try:
        response = requests.get(
            OEMBED_URL,
            params={"url": url, "format": "json"},
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "MrAIMarketer/1.0"},
        )
        if response.status_code == 404:
            raise NotYouTube(
                "YouTube does not have a video at that link — check it is public and not deleted."
            )
        response.raise_for_status()
        data = response.json()
        title = str(data.get("title") or "").strip()
        author = str(data.get("author_name") or "").strip()
        thumbnail = str(data.get("thumbnail_url") or "").strip()
    except NotYouTube:
        raise
    except Exception as err:  # noqa: BLE001
        # A card with a plain title is still a card. Refusing the whole post because a
        # metadata lookup timed out would be the wrong trade.
        log.info("[youtube] could not read details for %s: %s", vid, str(err)[:160])

    return Video(
        video_id=vid,
        url=url,
        title=title or "YouTube video",
        author=author,
        thumbnail_url=thumbnail or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
    )


def thumbnail_bytes(video: Video) -> bytes | None:
    """The card image for Bluesky, or None if it cannot be fetched.

    Optional by design: `app.bsky.embed.external` accepts a card with no thumb, and a
    thumbnail that will not download is not a reason to fail a post.
    """
    try:
        response = requests.get(
            video.thumbnail_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "MrAIMarketer/1.0"}
        )
        response.raise_for_status()
        return response.content
    except Exception as err:  # noqa: BLE001
        log.info("[youtube] no thumbnail for %s: %s", video.video_id, str(err)[:120])
        return None


def with_link(text: str, video: Video) -> str:
    """The post text with the video's URL present, which is how Mastodon embeds at all.

    Appended only when it is not already there — people paste the link into the draft as
    well as the box, and a status carrying the same URL twice gets one card and one piece of
    litter.
    """
    body = (text or "").strip()
    if video.video_id in body:
        return body
    return f"{body}\n\n{video.url}" if body else video.url
