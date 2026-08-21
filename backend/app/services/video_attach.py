"""Read an uploaded video off disk for posting, within each network's limits.

Separate from the image path for one reason that matters: the numbers. An image attachment
is capped at 16MB and that one figure works everywhere, but the three networks disagree
sharply about video, and the disagreement is the whole difficulty of the feature:

  Bluesky    50MB and about three minutes, plus a daily allowance per account.
  Mastodon   the instance decides, and publishes the figure — see InstanceInfo, which
             already carries video_size_limit_mb. Commonly 40MB, sometimes 16.
  Tumblr     the most generous of the three.

Checked here rather than at the network, because a rejected upload of a 90MB file is a long
wait followed by a failure, and the person still has to be told the number they exceeded.
Refusing first is one message, immediately, with the actual limit in it.

CONTAINMENT IS THE SAME RULE AS IMAGES. Files are read only from the app's own outputs tree.
The picker in the main process copies a chosen video in there precisely so this rule does not
have to be widened — a compose request cannot name an arbitrary path on the machine.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024

#: Bluesky's published ceiling. The daily per-account allowance is not knowable from here,
#: so that one is left to the API to refuse with its own message.
BLUESKY_MAX_BYTES = 50 * MEGABYTE

#: Tumblr's is larger, but this is a marketing tool posting a clip, not a video host.
TUMBLR_MAX_BYTES = 100 * MEGABYTE

#: Used when an instance publishes no figure of its own.
MASTODON_DEFAULT_MAX_BYTES = 40 * MEGABYTE

#: What the networks accept. Anything else is refused by name rather than uploaded and
#: rejected, which is a slower way to learn the same thing.
ALLOWED_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}

_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}


class VideoUnusable(RuntimeError):
    """The video cannot be posted. The message is written to be shown to a user."""


def mime_for(filename: str) -> str:
    return _MIME.get(Path(filename).suffix.lower(), "video/mp4")


def attachment_bytes(url: str, max_bytes: int, network: str) -> tuple[str, bytes]:
    """Read an uploaded video for `network`, or explain why it cannot be posted."""
    from . import share_links

    path = share_links.path_from_outputs_url(url)
    if path is None:
        raise VideoUnusable(
            "That video is not one this app is holding. Choose it again with the upload button."
        )
    if not path.exists():
        raise VideoUnusable("That video is no longer on disk. Choose it again.")

    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise VideoUnusable(
            f"{network} does not take {suffix or 'that kind of'} files. "
            f"Use one of: {', '.join(sorted(ALLOWED_SUFFIXES))}."
        )

    size = path.stat().st_size
    if size == 0:
        raise VideoUnusable("That video file is empty.")
    if size > max_bytes:
        raise VideoUnusable(
            f"That video is {size / MEGABYTE:.1f}MB and {network} allows "
            f"{max_bytes / MEGABYTE:.0f}MB. Trim it or export it smaller."
        )

    return path.name, path.read_bytes()


#: Long enough for a large file on a slow disk, short enough that a wedged probe does not
#: hold up a post. Failing this is not fatal — the aspect ratio is an optimisation.
_PROBE_TIMEOUT = 20


def probe_aspect_ratio(url: str) -> tuple[int, int] | None:
    """The display shape of an uploaded video, or None if it cannot be determined.

    Bluesky's video embed carries an optional aspect ratio, and omitting it is visible: the
    client reserves a default box and the layout jumps when the real dimensions arrive. A
    posted clip verified against the live API came back with `aspect_ratio=None` for exactly
    this reason — nothing was measuring the file.

    ffprobe rather than parsing the container here, and specifically ffprobe's rotation side
    data. A phone video is commonly stored landscape with a 90-degree rotation in its display
    matrix, so the stored width and height are the wrong way round for a portrait clip —
    reporting those unswapped would render the video in a sideways box, which is worse than
    reporting nothing. ffprobe is bundled and put on PATH for the packaged app; when it is
    missing this returns None and the embed simply goes without, as it did before.
    """
    import json
    import shutil
    import subprocess

    from . import share_links

    path = share_links.path_from_outputs_url(url)
    if path is None or not path.exists():
        return None
    if shutil.which("ffprobe") is None:
        log.info("ffprobe not on PATH; posting video without an aspect ratio")
        return None

    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:stream_side_data=rotation",
                "-of", "json", str(path),
            ],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
        streams = (json.loads(out.stdout or b"{}").get("streams") or [])
        if not streams:
            return None
        stream = streams[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        # Every failure here is the same failure: the shape is unknown. The post still goes.
        log.info("could not probe %s for its aspect ratio", path.name, exc_info=True)
        return None

    if width <= 0 or height <= 0:
        return None

    rotation = 0
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rotation = int(side["rotation"])
            except (TypeError, ValueError):
                rotation = 0
    # A quarter turn either way swaps the shape. Python's % is never negative for a positive
    # modulus, so this covers ffprobe's -90 as well as its 90 without a second clause.
    if rotation % 180 == 90:
        width, height = height, width

    # Reduced, because the lexicon wants a ratio and 16:9 is the same statement as 1280:720
    # without asking a client to carry the pixel count around.
    from math import gcd

    divisor = gcd(width, height) or 1
    return width // divisor, height // divisor


def mastodon_max_bytes(limit_mb: int | None) -> int:
    """The instance's own video ceiling, or a sane default when it publishes none."""
    if limit_mb and limit_mb > 0:
        return int(limit_mb) * MEGABYTE
    return MASTODON_DEFAULT_MAX_BYTES
