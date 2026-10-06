"""Explainable structural similarity and predicted audience/platform fit."""
from __future__ import annotations

import math
import re

from .config import SIMILARITY_WEIGHTS
from .igdb import taxonomy_terms


def _tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.casefold()) if len(word) > 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _names(game: dict, field: str) -> set[str]:
    return {str(item.get("name", "")).casefold() for item in (game.get(field) or []) if isinstance(item, dict)}


def rank(profile: dict, games: list[dict]) -> list[dict]:
    # Free-form model labels outside the catalog taxonomy are not additional
    # conflicting genres or perspectives. Score the mapped structural concepts.
    def mapped_terms(endpoint, field, observed):
        catalog = set().union(*(taxonomy_terms(endpoint, _names(game, field)) for game in games))
        return taxonomy_terms(endpoint, observed) & catalog
    genre = mapped_terms("genres", "genres", profile.get("genres", []))
    theme = mapped_terms("themes", "themes", profile.get("themes", []))
    perspective = mapped_terms("player_perspectives", "player_perspectives", profile.get("player_perspective", []))
    keywords = _tokens(" ".join(profile.get("keywords", []) + [m["name"] for m in profile.get("inferred_mechanics", [])]))
    loop = _tokens(" ".join(profile.get("core_loop", [])))
    archetypes = _tokens(" ".join(a["archetype"] for a in profile.get("audience_archetypes", [])))
    result = []
    for game in games:
        source_text = " ".join(str(game.get(k) or "") for k in ("summary", "storyline"))
        keyword_text = " ".join(_names(game, "keywords")) + " " + source_text
        text_tokens = _tokens(keyword_text)
        factors = {"mechanics": _jaccard(keywords, text_tokens),
                   "genres": _jaccard(genre, taxonomy_terms("genres", _names(game, "genres"))),
                   "themes": _jaccard(theme, taxonomy_terms("themes", _names(game, "themes"))),
                   "perspective": _jaccard(perspective, taxonomy_terms("player_perspectives", _names(game, "player_perspectives"))),
                   "loop": _jaccard(loop, text_tokens),
                   "pace": _jaccard(_tokens(str(profile.get("pace", "")) + " " + str(profile.get("complexity", ""))), _tokens(source_text)),
                   "audience": _jaccard(archetypes, text_tokens)}
        available = sum(weight for key, weight in SIMILARITY_WEIGHTS.items()
                        if (key in ("genres", "themes", "perspective") and {"genres": genre, "themes": theme, "perspective": perspective}[key])
                        or (key == "mechanics" and keywords) or (key == "loop" and loop)
                        or (key == "pace" and profile.get("pace") != "unknown")
                        or (key == "audience" and archetypes))
        similarity = round(100 * sum(factors[key] * weight for key, weight in SIMILARITY_WEIGHTS.items()) / available) if available else 0
        matches = [f"Shared {key.replace('_', ' ')}" for key in ("genres", "themes", "perspective", "mechanics", "loop") if factors[key] > 0]
        if not matches:
            continue
        cover = game.get("cover") or {}
        result.append({"igdb_id": game["id"], "name": game["name"], "slug": game.get("slug"),
                       "summary": game.get("summary"), "similarity": similarity,
                       "similarity_confidence": round(min(1, available) * profile.get("confidence", 0), 2),
                       "score_factors": factors, "why": matches,
                       "difference": "Only catalog metadata is available; detailed mechanical differences need review.",
                       "rating": game.get("rating"), "rating_count": game.get("rating_count"),
                       "aggregated_rating": game.get("aggregated_rating"),
                       "aggregated_rating_count": game.get("aggregated_rating_count"),
                       "total_rating": game.get("total_rating"), "total_rating_count": game.get("total_rating_count"),
                       "genres": sorted(_names(game, "genres")), "platforms": sorted(_names(game, "platforms")),
                       "cover_url": f'https://images.igdb.com/igdb/image/upload/t_cover_big/{cover["image_id"]}.jpg' if cover.get("image_id") else None,
                       "igdb_url": f'https://www.igdb.com/games/{game["slug"]}' if game.get("slug") else None,
                       "source": "IGDB v4"})
    return sorted(result, key=lambda item: (-item["similarity"], item["name"]))[:10]


def _category(score: int, confidence: float) -> str:
    if confidence < .2:
        return "Insufficient Evidence"
    if score >= 85:
        return "Excellent Fit"
    if score >= 70:
        return "Strong Fit"
    if score >= 50:
        return "Possible Fit"
    return "Weak Fit"


def _platform_group(name: str) -> str | None:
    value = name.casefold()
    if "windows" in value or "pc (" in value or value == "pc":
        return "Windows PC"
    if "mac" in value:
        return "macOS"
    if "linux" in value:
        return "Linux"
    if "playstation" in value:
        return "PlayStation"
    if "xbox" in value:
        return "Xbox"
    if "nintendo" in value or "switch" in value:
        return "Nintendo"
    if "android" in value or "ios" in value or "iphone" in value:
        return "Mobile"
    if "browser" in value or "web" in value:
        return "Browser"
    return None


def platform_recommendations(profile: dict, comparables: list[dict]) -> dict:
    dense = profile.get("dimensions", {}).get("management_depth", {}).get("score", 0) >= 65 or profile.get("complexity", "").casefold() in ("high", "complex")
    short = profile.get("dimensions", {}).get("session_commitment", {}).get("score", 50) < 40
    action = profile.get("dimensions", {}).get("action_intensity", {}).get("score", 50)
    suggestions = []
    for name in ("Windows PC", "macOS", "Linux", "PlayStation", "Xbox", "Nintendo", "Mobile", "Browser"):
        present = [game for game in comparables if name in {_platform_group(p) for p in game["platforms"]}]
        ratio = len(present) / len(comparables) if comparables else 0
        avg = round(sum(g["similarity"] for g in present) / len(present)) if present else None
        # Small rating counts cannot outweigh visible input/UI fit. Ratings are not demand.
        weighted = [g["similarity"] * (g["rating"] / 100) * min(1, math.log1p(g.get("rating_count") or 0) / math.log1p(1000))
                    for g in present if g.get("rating") is not None]
        input_fit = (90 if name == "Windows PC" and dense else 75 if name == "Windows PC" else
                     82 if name in ("macOS", "Linux") and dense else 68 if name in ("macOS", "Linux") else
                     35 if name == "Mobile" and dense else 70 if name == "Mobile" and short else 52 if name == "Mobile" else
                     72 if name in ("PlayStation", "Xbox", "Nintendo") and action >= 60 and not dense else
                     48 if name in ("PlayStation", "Xbox", "Nintendo") and dense else 62 if name in ("PlayStation", "Xbox", "Nintendo") else
                     48 if dense else 65)
        if short and name in ("Mobile", "Nintendo"):
            input_fit += 12
        score = round(.65 * input_fit + .35 * ratio * 100) if comparables else round(input_fit * .6)
        confidence = round(min(.9, profile.get("confidence", 0) * (.55 + .45 * min(1, len(comparables) / 5))), 2)
        reasons = [f"{len(present)} of {len(comparables)} structural comparables list this hardware ecosystem" if comparables else "No IGDB comparable coverage yet",
                   "Visible complexity suggests a precise input and readable interface matter" if dense else "The visible control and session style inform input fit"]
        suggestions.append({"name": name, "score": score, "category": _category(score, confidence),
                            "confidence": confidence, "reasons": reasons, "comparable_presence": len(present),
                            "comparable_total": len(comparables), "average_similarity": avg,
                            "rating_adjusted_presence": round(sum(weighted) / len(comparables), 1) if weighted and comparables else None,
                            "input_suitability": input_fit, "kind": "predicted_platform_fit"})
    suggestions.sort(key=lambda row: -row["score"])
    storefronts = []
    for name, hardware, note in (("Steam", "Windows PC", "Audience fit inferred from PC comparables; publishing requires developer setup."),
                                 ("itch.io", "Windows PC", "Indie and prototype discovery is plausible; publishing feasibility is separate."),
                                 ("Microsoft Store / Xbox", "Xbox", "Console release requires platform approval and porting."),
                                 ("PlayStation ecosystem", "PlayStation", "Console release requires platform approval and porting."),
                                 ("Nintendo ecosystem", "Nintendo", "Console release requires platform approval and porting."),
                                 ("Mobile stores", "Mobile", "Mobile release requires suitable controls and store review."),
                                 ("Browser distribution", "Browser", "Requires a browser-capable build.")):
        base = next(row for row in suggestions if row["name"] == hardware)
        storefronts.append({"name": name, "score": base["score"], "category": base["category"],
                            "confidence": base["confidence"], "reasons": base["reasons"] + [note],
                            "kind": "predicted_storefront_fit", "publishing_feasibility": "Not assessed"})
    discovery = []
    readable = profile.get("dimensions", {}).get("spectator_readability", {}).get("score", 50)
    for name, base, reason in (("YouTube", 72, "Long-form play and explanations can show the visible gameplay loop."),
                               ("Reddit", 67, "Genre-specific communities may discuss these mechanics."),
                               ("Discord", 63, "A community can support discussion and feedback."),
                               ("Twitch", 55, "Live play may reveal moment-to-moment decisions."),
                               ("TikTok", 48, "Short clips need immediately legible moments."),
                               ("Instagram", 44, "Visual clips may communicate presentation more readily than deep systems."),
                               ("itch.io communities", 58, "Indie and prototype players may be receptive to testing.")):
        score = max(0, min(100, round(base + (readable - 50) * (.3 if name in ("TikTok", "Instagram", "Twitch") else .1))))
        confidence = round(min(.65, profile.get("confidence", 0) * .65), 2)
        discovery.append({"name": name, "score": score, "category": _category(score, confidence),
                          "confidence": confidence, "reasons": [reason, "Predicted content fit; no audience performance data was measured."],
                          "kind": "predicted_discovery_fit"})
    return {"hardware": suggestions, "distribution": storefronts, "discovery": discovery,
            "caveat": "IGDB platform and website records show presence, not sales, demand, or community success. All recommendations are predictions."}
