"""Stage 5 + 6: real screenshots via a one-time low-res video download + ffmpeg, at
weighted timestamps.

Two proxies are used because of how the Space's egress is filtered:

* Resolution hits ``youtube.com`` and must go through ``YT_PROXY`` — an HTTPS/TLS-wrapped
  proxy that hides the target host from the egress DPI that otherwise resets YouTube.
* The media itself lives on ``googlevideo.com`` (not DPI-filtered) and is many MB, which
  the TLS proxy can't sustain — so it's downloaded through ``YT_MEDIA_PROXY``, a plain
  HTTP proxy, in chunked Range requests (dodging YouTube's single-stream throttling).

We download one capped-resolution copy, then ``ffmpeg`` reads the *local* file to grab 3
candidates around each timestamp and keeps the sharpest (Laplacian variance). The capture
timestamp comes from the weighted indicator: a blend of the LLM's suggested timestamp and
the transcript segment timing of the text the step quotes.
"""
from __future__ import annotations

import difflib
import os
import subprocess

import requests
from yt_dlp import YoutubeDL


# --------------------------------------------------------------------------- weighting
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def best_match_segment(quote: str, t_llm: float, segs: list[dict]) -> dict:
    """The transcript segment a step refers to: best text match, else nearest to t_llm."""
    quote = (quote or "").strip().lower()
    if quote:
        ratio, best = max(
            ((difflib.SequenceMatcher(None, quote, s["text"].lower()).ratio(), s) for s in segs),
            key=lambda t: t[0])
        if ratio >= 0.3:
            return best
    return min(segs, key=lambda s: abs((s["start"] + s["end"]) / 2 - t_llm))


def pick_time(step: dict, segs: list[dict], *, w_llm: float = 0.4, w_whisper: float = 0.6,
              lead: float = 1.0, max_drift: float = 20.0) -> float:
    """Weighted capture time (seconds). No snapping — we grab on demand at any T.

    Transcript timing is ground truth for *when* the text is spoken; the LLM time is its
    guess of *what* matters. Blend them, clamping a far-off LLM time back into the matched
    segment so a hallucinated timestamp can't drag the shot off-topic.
    """
    seg = best_match_segment(step.get("quote", ""), float(step.get("t_llm", 0.0)), segs)
    t_whisper = (seg["start"] + seg["end"]) / 2 + lead
    t_llm = float(step.get("t_llm", t_whisper))
    if abs(t_llm - t_whisper) > max_drift:
        t_llm = _clamp(t_llm, seg["start"], seg["end"])
    total = (w_llm + w_whisper) or 1.0
    return max(0.0, (w_llm * t_llm + w_whisper * t_whisper) / total)


def compute_shot_times(steps: list[dict], segs: list[dict], *, w_llm: float = 0.4,
                       w_whisper: float = 0.6, lead: float = 1.0,
                       max_shots: int = 8) -> dict[int, float]:
    """Return ``{step_index: capture_time}`` for the top-``max_shots`` steps by importance."""
    if not segs:
        return {}
    order = sorted(range(len(steps)), key=lambda i: -float(steps[i].get("importance", 0.5)))
    chosen = set(order[:max_shots])
    return {
        i: pick_time(steps[i], segs, w_llm=w_llm, w_whisper=w_whisper, lead=lead)
        for i in range(len(steps)) if i in chosen
    }


# -------------------------------------------------------------------- download + grab
def _sharpness(path: str) -> float:
    """Variance of the Laplacian (higher = sharper). Used to reject blurry frames."""
    import numpy as np
    from PIL import Image

    im = np.asarray(Image.open(path).convert("L"), dtype=np.float64)
    if im.shape[0] < 3 or im.shape[1] < 3:
        return 0.0
    lap = (-4 * im[1:-1, 1:-1] + im[:-2, 1:-1] + im[2:, 1:-1]
           + im[1:-1, :-2] + im[1:-1, 2:])
    return float(lap.var())


def _resolve_media_url(video_id: str, max_height: int, cookiefile: str | None,
                       proxy: str | None) -> str | None:
    """Resolve a single-file (progressive/DASH) video URL <= ``max_height`` via the
    HTTPS ``proxy``. Returns the ``googlevideo.com`` media URL, or None."""
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "format": (f"bv*[height<={max_height}][ext=mp4]/bv*[height<={max_height}]/"
                   f"best[height<={max_height}]/best"),
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if proxy:
        opts["proxy"] = proxy
        # yt-dlp validates against certifi only and ignores SSL_CERT_FILE; switch it to
        # the default cert path (which honors our combined bundle) so the self-signed
        # HTTPS-proxy cert validates. See app._install_proxy_ca.
        if os.environ.get("SSL_CERT_FILE"):
            opts["compat_opts"] = ["no-certifi"]
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if info.get("url"):
        return info["url"]
    vids = [f for f in info.get("formats", [])
            if f.get("vcodec") not in (None, "none") and f.get("url")
            and (f.get("height") or 0) <= max_height
            and str(f.get("protocol", "")).startswith("http")]
    if vids:
        return sorted(vids, key=lambda f: f.get("height") or 0)[-1]["url"]
    return None


def _download_media(url: str, dest: str, media_proxy: str | None,
                    chunk: int = 1 << 20, timeout: int = 30) -> None:
    """Download ``url`` to ``dest`` through the plain ``media_proxy`` using chunked Range
    requests — small ranges dodge YouTube's single-stream throttling and stay within the
    plain proxy's throughput (the TLS proxy can't sustain large transfers)."""
    proxies = {"http": media_proxy, "https": media_proxy} if media_proxy else None
    probe = requests.get(url, proxies=proxies, headers={"Range": "bytes=0-0"}, timeout=timeout)
    probe.raise_for_status()
    cr = probe.headers.get("Content-Range", "")
    total = int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else 0
    with open(dest, "wb") as fh:
        if not total:  # server ignored Range — fall back to a single stream
            with requests.get(url, proxies=proxies, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                for c in r.iter_content(chunk):
                    if c:
                        fh.write(c)
            return
        start = 0
        while start < total:
            end = min(start + chunk - 1, total - 1)
            for attempt in range(3):
                try:
                    rr = requests.get(url, proxies=proxies, timeout=timeout,
                                      headers={"Range": f"bytes={start}-{end}"})
                    rr.raise_for_status()
                    fh.write(rr.content)
                    break
                except requests.RequestException:
                    if attempt == 2:
                        raise
            start = end + 1


def _grab_local(media_path: str, t: float, out_dir: str, idx: int) -> str | None:
    """Grab 3 candidates around ``t`` from the *local* ``media_path`` and keep the
    sharpest. ffmpeg reads a local file here, so no proxy/network is involved."""
    cands = []
    for k, dt in enumerate((-0.5, 0.0, 0.5)):
        p = os.path.join(out_dir, f"shot_{idx}_{k}.jpg")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t + dt):.2f}",
               "-i", media_path, "-frames:v", "1", "-q:v", "2", p]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        if os.path.exists(p) and os.path.getsize(p) > 0:
            cands.append(p)
    if not cands:
        return None
    best = max(cands, key=_sharpness)
    for p in cands:
        if p != best:
            try:
                os.remove(p)
            except OSError:
                pass
    return best


def capture_shots(times: dict[int, float], video_id: str, out_dir: str,
                  cookiefile: str | None = None, proxy: str | None = None,
                  media_proxy: str | None = None, progress=None, *,
                  max_height: int = 360) -> dict[int, dict]:
    """Download one capped-resolution copy of the video (resolve via ``proxy``, fetch
    media via ``media_proxy``) and keep the sharpest frame per ``{step_index: time}``;
    return ``{idx: {"time", "path"}}``."""
    os.makedirs(out_dir, exist_ok=True)
    items = list(times.items())
    if not items:
        return {}
    url = _resolve_media_url(video_id, max_height, cookiefile, proxy)
    if not url:
        raise RuntimeError("Could not resolve a downloadable video stream URL.")
    media = os.path.join(out_dir, "_video.bin")
    if progress:
        progress(0.0, desc="Downloading video for screenshots")
    _download_media(url, media, media_proxy or proxy)

    out: dict[int, dict] = {}
    for n, (idx, t) in enumerate(items):
        if progress:
            progress((n + 1) / max(1, len(items)), desc=f"Screenshot {n + 1}/{len(items)}")
        path = _grab_local(media, t, out_dir, idx)
        if path:
            out[idx] = {"time": t, "path": path}
    try:
        os.remove(media)
    except OSError:
        pass
    return out
