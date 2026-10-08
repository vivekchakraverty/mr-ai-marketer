"""Prepare Bluesky external cards for links found in a post.

Bluesky stores link metadata in the post record; it does not unfurl plain post text
server-side. CardyB is the same metadata proxy used by Bluesky's own composer. YouTube
uses its public oEmbed endpoint so a video keeps its title, channel and thumbnail.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from . import youtube_embed

log = logging.getLogger(__name__)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CARDYB = "https://cardyb.bsky.app/v1/extract"
_TIMEOUT = 10
_MAX_THUMB = 1_000_000


@dataclass(frozen=True)
class Card:
    uri: str
    title: str
    description: str
    image_url: str = ""

    def record(self, thumb: dict | None = None) -> dict:
        external = {"uri": self.uri, "title": self.title, "description": self.description}
        if thumb:
            external["thumb"] = thumb
        return {"$type": "app.bsky.embed.external", "external": external}


def public_url(raw: str) -> str:
    """Accept public HTTP links only; never ask a preview service for local URLs."""
    url = (raw or "").strip().rstrip(".,;:!?)]}")
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return ""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return ""
    except ValueError:
        if "." not in host:
            return ""
    return url


def first_url(text: str) -> str:
    for match in _URL.finditer(text or ""):
        url = public_url(match.group())
        if url:
            return url
    return ""


def describe(url: str) -> Card | None:
    """Best-effort metadata. A proxy failure still leaves a clickable card."""
    url = public_url(url)
    if not url:
        return None
    if youtube_embed.video_id(url):
        try:
            video = youtube_embed.describe(url)
        except youtube_embed.NotYouTube:
            return None
        return Card(video.url, video.title, video.description, video.thumbnail_url)

    host = urlparse(url).hostname or "Link"
    try:
        response = requests.get(_CARDYB, params={"url": url}, timeout=_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and not data.get("error"):
            image = public_url(str(data.get("image") or ""))
            # CardyB's image proxy is intentional: it fetches remote thumbnails without
            # giving our local backend arbitrary URLs to download.
            if urlparse(image).hostname != "cardyb.bsky.app":
                image = ""
            return Card(
                url,
                str(data.get("title") or host).strip()[:300],
                str(data.get("description") or "").strip()[:1000],
                image,
            )
    except (requests.RequestException, ValueError, TypeError) as err:
        log.info("[bluesky-card] metadata unavailable for %s: %s", host, str(err)[:120])
    return Card(url, host, "")


def thumbnail(card: Card) -> tuple[bytes, str] | None:
    """Fetch a bounded image from the two trusted thumbnail hosts."""
    host = (urlparse(card.image_url).hostname or "").lower()
    if host not in {"cardyb.bsky.app", "i.ytimg.com"}:
        return None
    try:
        with requests.get(card.image_url, timeout=_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                return None
            chunks = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > _MAX_THUMB:
                    return None
                chunks.append(chunk)
            return b"".join(chunks), mime
    except requests.RequestException as err:
        log.info("[bluesky-card] thumbnail unavailable: %s", str(err)[:120])
        return None


def sdk_embed(client, card: Card):
    """Build the atproto SDK model, uploading the thumbnail when available."""
    from atproto import models

    thumb = None
    image = thumbnail(card)
    if image:
        try:
            thumb = client.upload_blob(image[0]).blob
        except Exception as err:  # noqa: BLE001 - card remains usable without an image
            log.info("[bluesky-card] thumbnail upload failed: %s", str(err)[:120])
    return models.AppBskyEmbedExternal.Main(
        external=models.AppBskyEmbedExternal.External(
            uri=card.uri, title=card.title, description=card.description, thumb=thumb
        )
    )
