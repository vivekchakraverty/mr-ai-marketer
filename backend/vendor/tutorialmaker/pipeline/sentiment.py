"""Stage 2: rank candidate videos by the sentiment of their comments.

Comments are fetched via the **YouTube Data API v3** (commentThreads) using an API key,
then scored with the BERT classifier ``OmarMedhat7/youtube-sentiment-analysis-model``
running inside the Space. The video with the highest positive share wins. Any video whose
comments are disabled or fail to fetch simply scores 0 and never aborts the ranking.

The fetched comments are kept on each scored item (``comments``) rather than discarded:
they're already paid for in quota, and the winning video's comments are the best available
source of the questions real viewers ask — which the tutorial stage turns into the FAQ.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

SENTIMENT_MODEL = "OmarMedhat7/youtube-sentiment-analysis-model"
COMMENTS_API = "https://www.googleapis.com/youtube/v3/commentThreads"
MAX_COMMENTS = 200  # cap per video to bound time/quota (commentThreads = 1 unit/100)

_POSITIVE = {"positive", "pos", "label_2", "label_1"}
_NEGATIVE = {"negative", "neg", "label_0"}


class SentimentError(RuntimeError):
    """Raised for API-key / quota problems that should surface to the user."""


@lru_cache(maxsize=1)
def _classifier():
    from transformers import pipeline

    return pipeline(
        "text-classification",
        model=SENTIMENT_MODEL,
        truncation=True,
        max_length=256,
        top_k=None,
    )


def _polarity(scores) -> float:
    val = 0.0
    for s in scores:
        label = str(s["label"]).strip().lower()
        if label in _POSITIVE:
            val += s["score"]
        elif label in _NEGATIVE:
            val -= s["score"]
    return val


def _fetch_comments(video_id: str, api_key: str, cap: int = MAX_COMMENTS) -> list[str]:
    """Fetch up to ``cap`` top-relevance comments via the YouTube Data API.

    Returns [] if comments are disabled. Raises SentimentError on auth/quota failures
    (which apply to every video, so the caller should stop).
    """
    comments: list[str] = []
    page = None
    while len(comments) < cap:
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": "100",
            "order": "relevance",
            "textFormat": "plainText",
            "key": api_key,
        }
        if page:
            params["pageToken"] = page
        url = COMMENTS_API + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            if exc.code == 403 and "commentsDisabled" in body:
                return []
            if exc.code in (400, 403) and ("keyInvalid" in body or "quota" in body.lower()
                                           or "forbidden" in body.lower()):
                raise SentimentError(
                    f"YouTube Data API rejected the key (HTTP {exc.code}). Check the key "
                    f"and that YouTube Data API v3 is enabled / has quota. {body[:160]}")
            # video-specific error (e.g. not found): treat as no comments
            return comments
        except Exception:
            return comments

        for item in data.get("items", []):
            sn = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            text = sn.get("textDisplay") or sn.get("textOriginal")
            if text:
                comments.append(text.strip())
        page = data.get("nextPageToken")
        if not page:
            break
    return comments[:cap]


def rank_by_sentiment(videos: list[dict], api_key: str,
                      progress=None) -> tuple[dict, list[dict]]:
    """Score each video by comment sentiment; return ``(best_video, scored)``.

    ``scored`` mirrors ``videos`` with ``positive_share`` (0..1), ``n_comments``, ``note``,
    the raw ``comments`` themselves, and is sorted by positive_share desc (search order as
    tie-break / fallback).
    """
    if not videos:
        raise ValueError("No videos to rank.")
    if not api_key:
        raise SentimentError("A YouTube Data API key is required to fetch comments.")

    clf = _classifier()
    scored: list[dict] = []
    for i, v in enumerate(videos):
        if progress:
            progress((i + 1) / len(videos), desc=f"Sentiment {i + 1}/{len(videos)}")
        item = dict(v)
        item["comments"] = []
        try:
            comments = _fetch_comments(v["video_id"], api_key)
            if comments:
                results = clf(comments)
                pols = [_polarity(r) for r in results]
                positives = sum(1 for p in pols if p > 0)
                item["positive_share"] = positives / len(pols)
                item["n_comments"] = len(comments)
                item["comments"] = comments
                item["note"] = ""
            else:
                item["positive_share"] = 0.0
                item["n_comments"] = 0
                item["note"] = "no comments"
        except SentimentError:
            raise  # key/quota problem affects all videos
        except Exception as exc:
            item["positive_share"] = 0.0
            item["n_comments"] = 0
            item["note"] = f"fetch failed: {type(exc).__name__}"
        item["search_rank"] = i
        scored.append(item)

    scored.sort(key=lambda d: (-d["positive_share"], d["search_rank"]))
    return scored[0], scored
