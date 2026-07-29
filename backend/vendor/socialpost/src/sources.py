"""Fetch a URL the user supplied and reduce it to plain text for grounding.

This is the one place in the system that fetches an arbitrary address chosen by
whoever is using the app, which makes it the one place that can be pointed at
something it should not reach. That matters more here than it looks: this code
runs inside the master app's backend on 127.0.0.1, alongside that backend's own
settings endpoints and a local automation engine, and on a Space where cloud
metadata lives at a link-local address. A pasted (or mistyped) `http://127.0.0.1:8803/...`
would otherwise be fetched and its response handed to an LLM and shown back.

So every hop is validated before it is followed:
  * http/https only — no file://, ftp://, gopher://
  * the resolved IP must be public — loopback, private, link-local, multicast and
    reserved ranges are refused
  * redirects are followed by hand, re-validating each hop, because a public URL
    is free to redirect to a private one
  * the response is capped by size and content type, so a 4GB binary or a video
    stream cannot be pulled into memory

Extraction stays dependency-free (regex, not a parser) for the same reason
ingest_kb does it that way: the text is truncated and handed to a model that
tolerates imperfect whitespace, and a parser dependency would have to be
installed on every runner and Space for no gain in output quality.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 3
MAX_BYTES = 2_000_000

# What the extracted text is truncated to before it reaches the prompt. Enough
# for a long article's substance, small enough to leave room for exemplars and
# platform guidance in the same context window.
MAX_CHARS = 6000

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml", "application/xml", "text/xml")

# A browser-ish UA: a default python-requests UA is refused outright by a lot of
# publishers, which would look like a broken feature rather than a blocked one.
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; social-post-generator/1.0; +https://github.com/) "
        "Python-requests"
    )
}


class SourceError(Exception):
    """Fetching or reading the URL failed, with a message safe to show the user."""


@dataclass
class FetchedSource:
    url: str
    title: str
    text: str
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def _assert_public_address(url: str) -> None:
    """Refuse anything that does not resolve to a public IP.

    Note the honest limit: between this check and the socket actually connecting,
    a hostile DNS server could return a different answer (rebinding). Closing
    that needs a custom adapter that pins the validated IP, which is not worth
    the complexity for a desktop tool where the URL comes from the person using
    it. This stops the realistic cases — a pasted internal address, a mistyped
    localhost port, a redirect into the metadata service.
    """
    parts = urlparse(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise SourceError(f"Only http and https links can be fetched (got {parts.scheme or 'no scheme'}).")
    host = parts.hostname
    if not host:
        raise SourceError("That does not look like a complete link.")

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as err:
        raise SourceError(f"Could not resolve {host} — {err}") from None

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise SourceError(
                f"{host} resolves to a non-public address ({ip}). Only public web "
                f"pages can be fetched."
            )


def _get_with_validated_redirects(url: str) -> requests.Response:
    seen = url
    for _ in range(MAX_REDIRECTS + 1):
        _assert_public_address(seen)
        try:
            resp = requests.get(
                seen,
                headers=_UA,
                timeout=TIMEOUT_SECONDS,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as err:
            raise SourceError(f"Could not reach {seen} — {err}") from None

        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise SourceError(f"{seen} redirected without saying where.")
            seen = urljoin(seen, location)
            continue
        return resp

    raise SourceError(f"{url} redirected too many times.")


def _read_capped(resp: requests.Response) -> str:
    """Read at most MAX_BYTES, then decode. Streamed so an oversized body is
    never fully pulled into memory just to be discarded."""
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(8192):
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_BYTES:
            break
    raw = b"".join(chunks)[:MAX_BYTES]
    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    return raw.decode(encoding, errors="replace")


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
    if not match:
        match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html, flags=re.I)
    return _clean(match.group(1)) if match else ""


def _extract_text(html: str) -> str:
    """Strip markup down to readable prose.

    Non-content elements are removed with their contents (a nav's link text is
    noise that would otherwise dominate a short page), then remaining tags go.
    """
    for tag in ("script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"):
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", html, flags=re.S | re.I)
    # Keep block boundaries as breaks so sentences do not run together.
    html = re.sub(r"<(?:br|/p|/div|/li|/h[1-6])[^>]*>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return _clean(html)


def _clean(text: str) -> str:
    # html.unescape rather than a hand-written table: real pages use numeric
    # entities (&#x27;, &#8217;) as freely as named ones, and a partial table
    # leaks them into the prompt verbatim — measured on docs.bsky.app, which
    # ships &#x27; for every apostrophe.
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def fetch_url(url: str) -> FetchedSource:
    """Fetch `url` and return its readable text, or raise SourceError."""
    url = (url or "").strip()
    if not url:
        raise SourceError("No link given.")
    if not urlparse(url).scheme:
        url = f"https://{url}"

    resp = _get_with_validated_redirects(url)
    try:
        if resp.status_code >= 400:
            raise SourceError(f"{url} returned HTTP {resp.status_code}.")

        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith(ALLOWED_CONTENT_TYPES):
            raise SourceError(
                f"That link is {content_type}, which has no text to read. Give a web page."
            )

        body = _read_capped(resp)
    finally:
        resp.close()

    title = _extract_title(body)
    text = _extract_text(body) if "<" in body else _clean(body)
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0]

    source = FetchedSource(url=resp.url or url, title=title, text=text, truncated=truncated)
    if source.is_empty:
        raise SourceError(
            f"Nothing readable was found at {url}. It may be a page that builds "
            f"itself with JavaScript."
        )
    log.info("Fetched %s — %d chars%s", url, len(text), " (truncated)" if truncated else "")
    return source
