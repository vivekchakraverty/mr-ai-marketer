"""Explainable matching and content-level virality for verified public posts."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass

from .media import visual_similarity


@dataclass(frozen=True)
class MatchConfig:
    visual_weight: float = .65
    transcript_weight: float = .25
    duration_weight: float = .10
    minimum_visual: float = .72
    possible: float = .70
    strong: float = .85
    certain: float = .95


DEFAULT_MATCH_CONFIG = MatchConfig()


def transcript_similarity(a: str, b: str) -> float | None:
    a_words = set(re.findall(r"\w+", a.lower()))
    b_words = set(re.findall(r"\w+", b.lower()))
    if not a_words or not b_words:
        return None
    return len(a_words & b_words) / len(a_words | b_words)


def score_match(upload: dict, candidate: dict, config: MatchConfig = DEFAULT_MATCH_CONFIG) -> dict:
    exact = upload["exact_file_hash"] == candidate["exact_file_hash"]
    visual = visual_similarity(upload, candidate)
    duration = min(upload["duration"], candidate["duration"]) / max(upload["duration"], candidate["duration"])
    transcript = transcript_similarity(upload.get("transcript", ""), candidate.get("transcript", ""))
    if exact:
        score = 1.0
    else:
        weight = config.visual_weight + config.duration_weight
        total = config.visual_weight * visual + config.duration_weight * duration
        if transcript is not None:
            weight += config.transcript_weight
            total += config.transcript_weight * transcript
        score = total / weight
    # A title, transcript, or duration alone cannot certify the underlying footage.
    if not exact and visual < config.minimum_visual:
        score = min(score, config.possible - .001)
    confidence = ("almost certain" if score >= config.certain else "strong" if score >= config.strong
                  else "possible derivative" if score >= config.possible else "no match")
    return {"similarity": round(score * 100, 1), "visual_similarity": round(visual * 100, 1),
            "transcript_similarity": round(transcript * 100, 1) if transcript is not None else None,
            "duration_similarity": round(duration * 100, 1), "exact": exact,
            "confidence": confidence, "is_match": confidence != "no match"}


def content_virality(matches: list[dict]) -> dict:
    """Observed views are a sum of post counters, never unique people or historical reach."""
    views = sum(max(0, int(m.get("views") or 0)) for m in matches)
    platforms = {m["platform"] for m in matches if m.get("platform")}
    strongest_outlier = max((float(m.get("outlier_multiplier") or 0) for m in matches), default=0)
    # Logarithmic components prevent one huge post from drowning out propagation.
    score = min(100, round(38 * min(1, math.log1p(views) / math.log1p(10_000_000))
                           + 26 * min(1, math.log1p(len(matches)) / math.log1p(12))
                           + 26 * min(1, len(platforms) / 4)
                           + 10 * min(1, math.log1p(strongest_outlier) / math.log1p(20))))
    measured = sum(m.get("views") is not None for m in matches)
    return {"score": score if matches and measured else None,
            "status": "measured" if matches and measured else "no_metrics" if matches else "not_established",
            "observed_views": views if measured else None, "measured_posts": measured,
            "detected_copies": len(matches), "platforms": sorted(platforms),
            "strongest_outlier_multiplier": strongest_outlier or None,
            "views_are_unique_people": False}


def comparison_benchmark(rows: list[dict]) -> dict:
    """Describe this search sample, without predicting the uploaded reel's reach."""
    measured = [row for row in rows if row.get("views") is not None]
    counts = [max(0, int(row["views"])) for row in measured]
    return {"sample_posts": len(rows), "measured_posts": len(measured),
            "median_views": round(statistics.median(counts)) if counts else None,
            "highest_views": max(counts) if counts else None}
