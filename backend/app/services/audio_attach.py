"""Read an uploaded audio file off disk for posting, and give Bluesky something it can take.

Audio is the one attachment kind the two networks disagree about at the level of what
exists, not merely how large it may be:

  Mastodon   takes audio natively. An mp3 is uploaded to the same media endpoint as a
             picture or a clip and rendered with a player, so nothing here has to be clever
             — the ceiling is the instance's own video limit, which covers audio too.
  Bluesky    has no audio embed at all. app.bsky.embed.* is images, video, external and
             record; there is no fifth option and no lexicon to extend. A post carrying a
             sound file is not a thing the protocol can represent.

So the file goes to Mastodon untouched, and Bluesky is handed a VIDEO of the same sound: the
waveform drawn over the app's own dark ground, with the original audio as its track. That is
the only shape Bluesky will accept, and it is what a music account there already does by
hand. The conversion is named in the UI rather than done silently, because a person who
attached an mp3 should not have to work out why their Bluesky post is a square video.

CONTAINMENT IS THE SAME RULE AS IMAGES AND VIDEO. Files are read only from the app's own
outputs tree; the picker in the main process copies a chosen file in there so that rule never
has to be widened. The derived mp4 is written back into the same tree, next to the prepared
Bluesky images, and named from a digest of its source so a second send of the same audio
reuses the first conversion instead of paying for it again.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import config
from . import video_attach

log = logging.getLogger(__name__)

MEGABYTE = 1024 * 1024

#: What the picker offers and this module will read. Deliberately narrower than the set
#: ffmpeg can decode: an obscure container that Mastodon then refuses is a slow way to learn
#: the same no. Every suffix here appears in Mastodon's own AUDIO_FILE_EXTENSIONS
#: (app/models/media_attachment.rb), which is also where the poster Space's mime table
#: comes from — see resources/poster-space/networks.py.
ALLOWED_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus"}

# What Bluesky will take is a VIDEO limit, because by the time it gets there this is a video.
# Aliased rather than restated so the two cannot drift apart the next time the network moves
# its numbers — which it did on 2026-08-26, from 3 minutes and 50MB to these.
#
# The length is really a limit on the audio, since the clip is exactly as long as its track,
# and it is checked BEFORE the conversion: spending two minutes of ffmpeg to produce a file
# the API will refuse is the worst of both.
BLUESKY_MAX_SECONDS = video_attach.BLUESKY_MAX_SECONDS

#: Checked again on the OUTPUT of the conversion: the input can be small and the render
#: large, since a dense waveform is close to worst-case material for a video encoder.
BLUESKY_MAX_BYTES = video_attach.BLUESKY_MAX_BYTES

# The rendered clip. Square because that is the shape which survives every client's timeline
# layout, and in the app's own colours because a black rectangle reads as a broken video.
_CANVAS = 720
_WAVE_HEIGHT = 400
_FPS = 15
_BACKGROUND = "0x2b2420"  # --ink
_WAVE_COLORS = "0xff7a5c|0xffcb4d"  # --accent, then --tool-guest for a second channel

#: Known exactly, so it is stated rather than probed: without it a client reserves a default
#: box and the timeline reflows when the video loads.
BLUESKY_ASPECT_RATIO = {"width": 1, "height": 1}


class AudioUnusable(RuntimeError):
    """The audio cannot be posted. The message is written to be shown to a user."""


def is_audio(url: str) -> bool:
    """Whether this staged upload is a sound file rather than a clip.

    By suffix and without touching the disk: callers use this to decide which of the two
    attachment services owns a URL, and that decision has to be answerable for a payload
    persisted months ago whose file may since have gone.
    """
    if not url:
        return False
    return Path(url.split("?", 1)[0]).suffix.lower() in ALLOWED_SUFFIXES


def source_name(url: str) -> str:
    """The uploaded file's own name.

    Worth having because the rendered clip is named from a digest: a post that falls back to
    its filename for alt text would otherwise describe itself as a hex string.
    """
    from urllib.parse import unquote

    return Path(unquote(url.split("?", 1)[0])).name


def attachment_path(url: str, max_bytes: int, network: str) -> Path:
    """Validate one staged local audio file without reading it into memory."""
    from . import share_links

    path = share_links.path_from_outputs_url(url)
    if path is None:
        raise AudioUnusable(
            "That audio is not one this app is holding. Choose it again with the upload button."
        )
    if not path.exists():
        raise AudioUnusable("That audio file is no longer on disk. Choose it again.")

    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise AudioUnusable(
            f"{network} does not take {suffix or 'that kind of'} files. "
            f"Use one of: {', '.join(sorted(ALLOWED_SUFFIXES))}."
        )

    size = path.stat().st_size
    if size == 0:
        raise AudioUnusable("That audio file is empty.")
    if size > max_bytes:
        raise AudioUnusable(
            f"That audio is {size / 1e6:.1f}MB and {network} allows "
            f"{max_bytes / 1e6:.0f}MB. Export it at a lower bitrate."
        )

    return path


def attachment_bytes(url: str, max_bytes: int, network: str) -> tuple[str, bytes]:
    """Read an uploaded audio file for `network`, or explain why it cannot be posted."""
    path = attachment_path(url, max_bytes, network)

    return path.name, path.read_bytes()


#: A wedged probe must not hold up a post; the length is knowable in well under this.
_PROBE_TIMEOUT = 20


def duration_seconds(path: Path) -> float | None:
    """How long the audio runs, or None when that cannot be determined."""
    import json
    import shutil
    import subprocess

    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
        value = (json.loads(out.stdout or b"{}").get("format") or {}).get("duration")
        seconds = float(value)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        # Not fatal on its own: an unknown length costs the early refusal below, and the
        # rendered file is still size-checked before anything is uploaded.
        log.info("could not probe %s for its duration", path.name, exc_info=True)
        return None
    return seconds if seconds > 0 else None


def _conversion_timeout(seconds: float | None) -> float:
    """Generous enough for a slow machine, short enough not to hang a send.

    Measured at roughly 5x realtime on an ordinary desktop, so the ceiling covers a machine
    several times slower encoding the longest clip Bluesky now takes. It is deliberately not
    tighter: giving up at 90% of a ten-minute render wastes the whole wait and produces
    nothing, which is worse than a slow send the person can see happening.
    """
    if seconds is None:
        return 1200.0
    return min(60.0 + seconds * 3.0, 1200.0)


def prepare_bluesky_video(url: str) -> str:
    """Return an /outputs URL for a Bluesky-postable video of this audio, rendering it once.

    Bluesky cannot carry sound on its own, so the sound is carried by a picture of itself.
    The result is deterministic in the source bytes, so re-sending the same attachment — a
    retry, a second channel, a scheduled job re-materialising its media — reuses the render.
    """
    import hashlib
    import shutil
    import subprocess

    from .image_prompt import outputs_url

    source = attachment_path(url, BLUESKY_MAX_BYTES, "Bluesky")

    length = duration_seconds(source)
    if length is not None and length > BLUESKY_MAX_SECONDS:
        raise AudioUnusable(
            f"That audio runs {length / 60:.1f} minutes and Bluesky takes about "
            f"{BLUESKY_MAX_SECONDS // 60} minutes. Post a shorter excerpt, or leave Bluesky "
            "off this send."
        )

    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(MEGABYTE), b""):
                digest.update(chunk)
    except OSError as err:
        raise AudioUnusable(f"That audio could not be read: {err}") from None

    prepared_dir = config.OUTPUTS_DIR / ".distribution-media"
    prepared = prepared_dir / f"{digest.hexdigest()[:24]}.bluesky.mp4"
    if prepared.is_file() and prepared.stat().st_size > 0:
        return outputs_url(prepared)

    if shutil.which("ffmpeg") is None:
        raise AudioUnusable(
            "Bluesky has no way to carry a sound file, so this app turns one into a video — "
            "which needs ffmpeg, and it is not on this machine. Attach a video instead, or "
            "leave Bluesky off this send."
        )

    prepared_dir.mkdir(parents=True, exist_ok=True)
    # Rendered under a working name and moved into place, so a conversion killed halfway
    # cannot leave a truncated file that the reuse check above would then happily serve.
    working = prepared.with_suffix(".partial")
    command = [
        "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={_BACKGROUND}:s={_CANVAS}x{_CANVAS}:r={_FPS}",
        "-i", str(source),
        # overlay, not blend: showwaves draws on a TRANSPARENT ground, and a blend discards
        # the alpha — the "background" it then mixes in is the wave colour itself, which
        # comes out as a solid slab of colour with the waveform invisible inside it.
        # draw=full, not the default: the default scales each sample's alpha by its
        # amplitude, which over a dark ground renders a quiet passage as almost nothing.
        "-filter_complex",
        f"[1:a]showwaves=s={_CANVAS}x{_WAVE_HEIGHT}:mode=cline:rate={_FPS}:draw=full:"
        f"colors={_WAVE_COLORS}[w];"
        f"[0:v][w]overlay=x=0:y={(_CANVAS - _WAVE_HEIGHT) // 2}:shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-shortest",
        # Named, because the working file deliberately is not called .mp4 and ffmpeg would
        # otherwise have no extension to guess a muxer from — it fails with "Error opening
        # output files: Invalid argument", which says nothing about the actual cause.
        "-f", "mp4",
        str(working),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=_conversion_timeout(length), check=False
        )
    except subprocess.TimeoutExpired:
        working.unlink(missing_ok=True)
        raise AudioUnusable(
            "Turning that audio into a video for Bluesky took too long. Try a shorter clip."
        ) from None
    except OSError as err:
        working.unlink(missing_ok=True)
        raise AudioUnusable(f"That audio could not be converted for Bluesky: {err}") from None

    if result.returncode != 0 or not working.is_file() or working.stat().st_size == 0:
        # ffmpeg's own last line, not a summary of it. Three rounds of guessing at Bluesky's
        # video upload were spent on exactly this mistake: the service said which field it
        # objected to, and the message was being discarded before anyone could read it.
        said = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        working.unlink(missing_ok=True)
        raise AudioUnusable(
            "That audio could not be turned into a video for Bluesky"
            + (f": {said[-1]}" if said else ".")
        )

    size = working.stat().st_size
    if size > BLUESKY_MAX_BYTES:
        working.unlink(missing_ok=True)
        raise AudioUnusable(
            f"The video made from that audio is {size / 1e6:.1f}MB and Bluesky allows "
            f"{BLUESKY_MAX_BYTES // 1_000_000}MB. Post a shorter excerpt."
        )

    working.replace(prepared)
    log.info("[audio] wrapped %s for Bluesky (%.1fMB)", source.name, size / 1e6)
    return outputs_url(prepared)
