"""Short-lived, unguessable URLs for one generated file, readable without the app token.

WHY THIS EXISTS. The Distribute engine (Activepieces) posts on the user's behalf, and to
attach media it needs a URL it can fetch *itself*. Everything this app generates lives
under OUTPUTS_DIR and is served from `/outputs`, which sits behind the same session token
as the rest of the API — the renderer sends that header, a container has no way to. So the
image needs a second door: narrow, temporary, and openable exactly once per file.

WHAT IT IS NOT. Not a public share feature and not a general bypass. A link names one
relative path, carries an expiry, and is signed; the path is re-checked against
OUTPUTS_DIR after resolution, so a signature over `../../secrets` still refuses.

WHY SIGNED RATHER THAN A RANDOM ID IN A TABLE. Nothing to store, nothing to clean up, and
a restart cannot strand a scheduled send. The secret is persisted (unlike the session API
token, which is minted per launch) precisely because a post scheduled for Thursday has to
still be fetchable on Thursday.

THE EXPIRY IS THE POINT. A leaked link is leaked media for minutes, not forever. Scheduled
jobs retain their canonical local path and mint this temporary URL only when they fire.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import quote, unquote

from .. import config

log = logging.getLogger(__name__)

#: Long enough for a flow to be picked up, retried once and still work.
DEFAULT_TTL_SECONDS = 30 * 60

#: Ceiling on any caller's request, so a bug cannot mint a link that never expires.
MAX_TTL_SECONDS = 14 * 24 * 3600

_SECRET_FILE = config.DATA_DIR / "share-secret"


def _secret() -> bytes:
    """The signing key, created once and kept.

    Deliberately not config.API_TOKEN: that is minted per launch, so every restart would
    invalidate the links of every scheduled send still waiting to go out.
    """
    try:
        existing = _SECRET_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing.encode("utf-8")
    except OSError:
        pass
    fresh = secrets.token_urlsafe(32)
    try:
        _SECRET_FILE.write_text(fresh, encoding="utf-8")
    except OSError as err:
        # An unwritable data dir must not take the app down; the links simply stop
        # surviving a restart, which is visible as a scheduled send failing to fetch.
        log.warning("[share] could not persist the signing secret: %s", err)
    return fresh.encode("utf-8")


def _sign(relpath: str, expires: int) -> str:
    return hmac.new(
        _secret(), f"{relpath}|{expires}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def token_for(path: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """A signed token naming one file under OUTPUTS_DIR. Raises if it is not one."""
    resolved = Path(path).resolve()
    root = config.OUTPUTS_DIR.resolve()
    try:
        relpath = resolved.relative_to(root).as_posix()
    except ValueError as err:
        raise ValueError(f"{path} is not inside the outputs directory") from err

    expires = int(time.time()) + max(60, min(int(ttl_seconds), MAX_TTL_SECONDS))
    return f"{expires}.{_sign(relpath, expires)}.{relpath}"


def resolve(token: str) -> Path | None:
    """The file a token names, or None if it is expired, forged or out of bounds.

    Every failure returns None rather than distinguishing "expired" from "bad signature":
    the caller answers 404 either way, and telling an unauthenticated stranger which of
    the two it was is free information about what exists.
    """
    try:
        expires_raw, signature, relpath = token.split(".", 2)
        expires = int(expires_raw)
    except (ValueError, AttributeError):
        return None

    if expires < time.time():
        return None
    # compare_digest, not ==, so a signature cannot be narrowed down by timing.
    if not hmac.compare_digest(signature, _sign(relpath, expires)):
        return None

    root = config.OUTPUTS_DIR.resolve()
    candidate = (root / relpath).resolve()
    # Re-checked after resolution: a validly signed "../.." is still not ours to serve.
    if not candidate.is_file() or root not in candidate.parents:
        return None
    return candidate


def url_for(path: Path, base: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """A full URL another process can fetch. `base` decides who can reach it."""
    token = token_for(path, ttl_seconds)
    return f"{base.rstrip('/')}/shared/{quote(token, safe='/.')}"


def path_from_outputs_url(url: str) -> Path | None:
    """Turn the app's own `/outputs/...` URL back into a file path.

    The renderer holds those URLs (it is what the image panel was handed), so this is the
    translation between what the UI knows and what a share link needs.
    """
    marker = "/outputs/"
    if marker not in url:
        return None
    relpath = unquote(url.split(marker, 1)[1].split("?", 1)[0])
    candidate = (config.OUTPUTS_DIR / relpath).resolve()
    root = config.OUTPUTS_DIR.resolve()
    return candidate if candidate.is_file() and root in candidate.parents else None


def path_from_shared_url(url: str) -> Path | None:
    """Recover the local file named by one of our signed links, even after expiry.

    This is only for migrating app-owned job payloads before minting a fresh link. The
    unauthenticated HTTP handler still uses :func:`resolve`, which always enforces expiry.
    Signature and output-tree containment remain mandatory here, so a lookalike public URL
    cannot turn into a local file read.
    """
    marker = "/shared/"
    if marker not in url:
        return None
    token = unquote(url.split(marker, 1)[1].split("?", 1)[0])
    try:
        expires_raw, signature, relpath = token.split(".", 2)
        expires = int(expires_raw)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(signature, _sign(relpath, expires)):
        return None

    root = config.OUTPUTS_DIR.resolve()
    candidate = (root / relpath).resolve()
    return candidate if candidate.is_file() and root in candidate.parents else None
