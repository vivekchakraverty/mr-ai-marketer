"""Measuring the display shape of a video before it is posted.

A clip posted to the live Bluesky API came back with `aspect_ratio=None`, because nothing
measured the file. The visible cost is a timeline that reflows when the video loads.

The rotation cases are the ones worth the trouble. A phone records portrait video as a
landscape stream plus a quarter turn in its display matrix, so the stored width and height
are the wrong way round — and a video rendered in a sideways box is a worse outcome than one
rendered in a default box, which is what makes "no answer" the right failure here.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ..services import video_attach


class _Probe:
    """A stand-in for ffprobe returning whatever a given file is supposed to contain."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.stdout = b"" if payload is None else json.dumps(payload).encode()


@pytest.fixture
def clip(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """An /outputs URL that resolves to a real file, so only the probe is faked."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not really a video")
    from ..services import share_links

    monkeypatch.setattr(share_links, "path_from_outputs_url", lambda _u: path)
    monkeypatch.setattr(shutil, "which", lambda _n: "ffprobe")
    return "/outputs/uploads/x/clip.mp4"


def _answer(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Probe(payload))


def test_a_landscape_clip_is_reported_reduced(clip, monkeypatch: pytest.MonkeyPatch) -> None:
    """16:9 says the same thing as 1280:720 without carrying the pixel count."""
    _answer(monkeypatch, {"streams": [{"width": 1280, "height": 720}]})
    assert video_attach.probe_aspect_ratio(clip) == (16, 9)


@pytest.mark.parametrize("rotation", [90, -90, 270])
def test_a_quarter_turn_swaps_the_shape(
    clip, monkeypatch: pytest.MonkeyPatch, rotation: int
) -> None:
    """The phone-video case: stored landscape, displayed portrait."""
    _answer(
        monkeypatch,
        {"streams": [{"width": 1280, "height": 720, "side_data_list": [{"rotation": rotation}]}]},
    )
    assert video_attach.probe_aspect_ratio(clip) == (9, 16)


@pytest.mark.parametrize("rotation", [0, 180, -180])
def test_a_half_turn_leaves_the_shape_alone(
    clip, monkeypatch: pytest.MonkeyPatch, rotation: int
) -> None:
    """Upside down is still the same shape."""
    _answer(
        monkeypatch,
        {"streams": [{"width": 1280, "height": 720, "side_data_list": [{"rotation": rotation}]}]},
    )
    assert video_attach.probe_aspect_ratio(clip) == (16, 9)


def test_no_ffprobe_means_no_answer_rather_than_a_guess(
    clip, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dev runs without it on PATH. The post must still go, just without the ratio."""
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert video_attach.probe_aspect_ratio(clip) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"streams": []},
        {"streams": [{"width": 0, "height": 720}]},
        {"streams": [{"width": 1280}]},
        {},
    ],
    ids=["no streams", "zero width", "no height", "empty"],
)
def test_an_unreadable_file_gives_no_answer(
    clip, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    _answer(monkeypatch, payload)
    assert video_attach.probe_aspect_ratio(clip) is None


def test_a_wedged_probe_does_not_hold_up_the_post(
    clip, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeout is an unknown shape, not a failed post."""

    def explode(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=video_attach._PROBE_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", explode)
    assert video_attach.probe_aspect_ratio(clip) is None


def test_a_file_outside_the_outputs_tree_is_never_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same containment rule as reading the bytes: nothing outside the app's own storage."""
    from ..services import share_links

    monkeypatch.setattr(share_links, "path_from_outputs_url", lambda _u: None)
    called = {"ran": False}

    def note(*_a, **_k):
        called["ran"] = True
        return _Probe({"streams": [{"width": 4, "height": 2}]})

    monkeypatch.setattr(subprocess, "run", note)
    assert video_attach.probe_aspect_ratio("/etc/passwd") is None
    assert called["ran"] is False
