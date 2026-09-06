"""Posting a sound file to two networks, one of which has no idea what sound is.

Mastodon takes audio natively. Bluesky's lexicon has four embeds — images, video, external,
record — and none of them carries a track, so the app renders the waveform into a video and
posts that instead. The consequence worth testing is that a send to BOTH puts two different
files in one payload, and every consumer downstream has to pick by channel rather than by
which field happens to be filled. Getting that wrong does not fail loudly: it publishes a
square video to Mastodon, or uploads an mp3 to Bluesky and is refused with "InvalidRequest".
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app import config
from app.routers import distribution
from app.services import audio_attach, cloud_poster, mastodon_delivery, share_links

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture()
def outputs(tmp_path, monkeypatch):
    """An outputs tree holding one staged upload, the way the picker leaves one."""
    root = tmp_path / "outputs"
    staged = root / "uploads" / "abc123"
    staged.mkdir(parents=True)
    track = staged / "jingle.mp3"
    track.write_bytes(b"ID3" + b"\x00" * 512)
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(share_links, "_SECRET_FILE", tmp_path / "share-secret")
    return track


_URL = "/outputs/uploads/abc123/jingle.mp3"


# ---------------------------------------------------------------------------
# Which service owns a staged upload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, audio",
    [
        ("/outputs/uploads/x/track.mp3", True),
        ("/outputs/uploads/x/track.M4A", True),
        ("/outputs/uploads/x/voice.opus", True),
        ("/outputs/uploads/x/clip.mp4", False),
        ("/outputs/uploads/x/clip.mov", False),
        ("/outputs/uploads/x/track.mp3?v=2", True),
        ("", False),
    ],
)
def test_the_suffix_decides_which_kind_of_upload_this_is(url, audio):
    """One field carries both kinds, so this call is the whole routing decision."""
    assert audio_attach.is_audio(url) is audio


def test_an_outputs_url_reads_back_as_bytes(outputs):
    filename, content = audio_attach.attachment_bytes(_URL, 40 * 1024 * 1024, "Mastodon")

    assert filename == "jingle.mp3"
    assert content == outputs.read_bytes()


@pytest.mark.parametrize(
    "url",
    [
        "/outputs/../../../Windows/System32/drivers/etc/hosts",
        "https://example.com/someone-elses.mp3",
        "C:/Users/someone/private.mp3",
        "",
    ],
)
def test_anything_outside_the_outputs_tree_is_refused(outputs, url):
    """Same containment rule as images and video; a renderer sends what it is made to send."""
    with pytest.raises(audio_attach.AudioUnusable):
        audio_attach.attachment_bytes(url, 40 * 1024 * 1024, "Mastodon")


def test_an_oversized_file_is_refused_before_the_upload_and_names_the_size(outputs):
    with pytest.raises(audio_attach.AudioUnusable) as err:
        audio_attach.attachment_bytes(_URL, 64, "Mastodon")

    assert "Mastodon allows" in str(err.value)


def test_a_video_is_not_read_as_audio(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    (root / "uploads" / "x").mkdir(parents=True)
    (root / "uploads" / "x" / "clip.mp4").write_bytes(b"\x00" * 16)
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)

    with pytest.raises(audio_attach.AudioUnusable, match=r"\.mp4"):
        audio_attach.attachment_bytes("/outputs/uploads/x/clip.mp4", 1_000_000, "Mastodon")


# ---------------------------------------------------------------------------
# The conversion Bluesky needs
# ---------------------------------------------------------------------------


def test_audio_longer_than_bluesky_takes_is_refused_before_ffmpeg_runs(outputs, monkeypatch):
    """Rendering first and being refused after is the slowest possible way to learn this."""
    monkeypatch.setattr(audio_attach, "duration_seconds", lambda _path: 15 * 60.0)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("ffmpeg should not have been run")
    )

    with pytest.raises(audio_attach.AudioUnusable) as err:
        audio_attach.prepare_bluesky_video(_URL)

    assert "Bluesky" in str(err.value)


def test_a_failed_conversion_reports_what_ffmpeg_itself_said(outputs, monkeypatch):
    """The service's own sentence, not a summary of it.

    Three rounds of guessing at Bluesky's video upload were spent on the opposite habit: the
    API named the field it objected to every time, and the message was being thrown away.
    """
    monkeypatch.setattr(audio_attach, "duration_seconds", lambda _path: 12.0)
    monkeypatch.setattr(shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, b"", b"[libx264 @ 0x1] height not divisible by 2\n"
        ),
    )

    with pytest.raises(audio_attach.AudioUnusable) as err:
        audio_attach.prepare_bluesky_video(_URL)

    assert "height not divisible by 2" in str(err.value)


def test_a_half_written_render_is_not_left_behind_to_be_reused(outputs, monkeypatch):
    """A killed ffmpeg must not leave a file the digest check would happily serve next time."""
    monkeypatch.setattr(audio_attach, "duration_seconds", lambda _path: 12.0)
    monkeypatch.setattr(shutil, "which", lambda _name: "ffmpeg")

    def half_written(command, **_kwargs):
        # Where the real one writes as it encodes, then is interrupted.
        from pathlib import Path

        Path(command[-1]).write_bytes(b"truncated mp4")
        return subprocess.CompletedProcess(command, 255, b"", b"Exiting normally, received signal 2.")

    monkeypatch.setattr(subprocess, "run", half_written)

    with pytest.raises(audio_attach.AudioUnusable):
        audio_attach.prepare_bluesky_video(_URL)

    assert list((config.OUTPUTS_DIR / ".distribution-media").glob("*")) == []


def test_no_ffmpeg_is_said_plainly_rather_than_crashing(outputs, monkeypatch):
    monkeypatch.setattr(audio_attach, "duration_seconds", lambda _path: 12.0)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(audio_attach.AudioUnusable, match="ffmpeg"):
        audio_attach.prepare_bluesky_video(_URL)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe are not on PATH")
def test_a_real_conversion_produces_a_postable_clip_and_is_only_done_once(tmp_path, monkeypatch):
    """The filtergraph itself, against the real binary the packaged app ships.

    Worth an actual encode: every failure this covers — a filter name that moved, an option
    the bundled build was compiled without — is invisible to a mocked subprocess and would
    surface as a scheduled post that quietly never went out.
    """
    root = tmp_path / "outputs"
    staged = root / "uploads" / "real"
    staged.mkdir(parents=True)
    # wav, so the test does not depend on which encoders a given ffmpeg build carries.
    source = staged / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(source)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(config, "OUTPUTS_DIR", root)

    url = audio_attach.prepare_bluesky_video("/outputs/uploads/real/tone.wav")

    rendered = share_links.path_from_outputs_url(url)
    assert rendered is not None and rendered.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
         "-of", "csv=p=0", str(rendered)],
        capture_output=True,
        check=True,
    ).stdout.decode()
    # Both streams, because a "video" with no track is not what anyone attached.
    assert "h264" in probe and "aac" in probe

    stamp = rendered.stat().st_mtime_ns
    assert audio_attach.prepare_bluesky_video("/outputs/uploads/real/tone.wav") == url
    assert rendered.stat().st_mtime_ns == stamp


# ---------------------------------------------------------------------------
# What each channel is handed
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_real_ffmpeg(monkeypatch):
    """Stand in for the render, so payload routing can be tested without an encode."""
    monkeypatch.setattr(
        audio_attach,
        "prepare_bluesky_video",
        lambda url: "/outputs/.distribution-media/deadbeef.bluesky.mp4",
    )


def test_mastodon_alone_carries_the_sound_file_and_nothing_else(outputs, no_real_ffmpeg):
    body = distribution.SendRequest(
        libraryItemId="lib-1", channels=["mastodon"], text="listen", videoFileUrl=_URL
    )

    payload = distribution._payload_for(body)

    assert payload["audioUrl"] == _URL
    assert payload["mediaUrl"] == _URL
    # No render was needed, so none was made — a Mastodon-only send never pays for ffmpeg.
    assert "videoUrl" not in payload


def test_a_send_to_both_carries_the_file_for_each(outputs, no_real_ffmpeg):
    body = distribution.SendRequest(
        libraryItemId="lib-1",
        channels=["mastodon", "bluesky"],
        text="listen",
        videoFileUrl=_URL,
        videoFileAlt="a jingle",
    )

    payload = distribution._payload_for(body)

    assert payload["audioUrl"] == _URL
    assert payload["videoUrl"] == "/outputs/.distribution-media/deadbeef.bluesky.mp4"
    # Stated rather than probed: the canvas is square and known, and an embed without it
    # makes the timeline reflow when the video lands.
    assert payload["aspectRatio"] == {"width": 1, "height": 1}
    assert payload["videoFileAlt"] == "a jingle"


def test_audio_is_refused_for_a_channel_that_cannot_take_it(outputs, no_real_ffmpeg):
    body = distribution.SendRequest(
        libraryItemId="lib-1", channels=["linkedin"], text="listen", videoFileUrl=_URL
    )

    with pytest.raises(distribution.HTTPException) as err:
        distribution._payload_for(body)

    assert err.value.status_code == 400
    assert "audio" in err.value.detail.lower()


def test_a_signed_link_is_minted_for_the_sound_file_too(outputs, no_real_ffmpeg, monkeypatch):
    """Activepieces fetches attachments over HTTP; a field it cannot fetch becomes null,
    and the connector then reports success for a text-only post."""
    monkeypatch.setattr(distribution, "_shareable_media_url", lambda url: f"signed:{url}")

    materialized = distribution._materialize_media_payload({"audioUrl": _URL, "text": "hi"})

    assert materialized["audioUrl"] == f"signed:{_URL}"


def test_each_channel_is_uploaded_its_own_file_from_one_payload(outputs, tmp_path, monkeypatch):
    """The bug this exists to catch: uploading whichever media field is filled in.

    A send to both networks leaves audioUrl AND videoUrl set, so a consumer that checks
    videoUrl first hands Mastodon the waveform wrapper — a post that succeeds and is wrong.
    """
    rendered = config.OUTPUTS_DIR / ".distribution-media" / "deadbeef.bluesky.mp4"
    rendered.parent.mkdir(parents=True)
    rendered.write_bytes(b"\x00\x00\x00 ftypmp42")
    payload = {
        "text": "listen",
        "audioUrl": _URL,
        "videoUrl": "/outputs/.distribution-media/deadbeef.bluesky.mp4",
    }

    assert cloud_poster._attachment(payload, "mastodon") == ("jingle.mp3", outputs.read_bytes())
    assert cloud_poster._attachment(payload, "bluesky") == (
        "deadbeef.bluesky.mp4",
        rendered.read_bytes(),
    )


def test_the_local_mastodon_path_makes_the_same_choice(outputs, monkeypatch):
    """The scheduler's own delivery, which bypasses Activepieces for Mastodon media."""
    monkeypatch.setattr(
        mastodon_delivery,
        "get_credentials",
        lambda: mastodon_delivery.Credentials(host="toot.example", access_token="t"),
    )
    uploaded: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "upload_media",
        lambda host, token, filename, content, description="": uploaded.append((filename, content))
        or "media-1",
    )
    monkeypatch.setattr(
        mastodon_delivery.mastodon,
        "api_post",
        lambda *a, **k: {"id": "9", "media_attachments": [{"id": "media-1"}]},
    )
    payload = {
        "text": "listen",
        "audioUrl": _URL,
        "videoUrl": "/outputs/.distribution-media/deadbeef.bluesky.mp4",
    }

    assert mastodon_delivery.carries_media(payload)
    mastodon_delivery.publish(payload, idempotency_key="job-1")

    assert uploaded == [("jingle.mp3", outputs.read_bytes())]
