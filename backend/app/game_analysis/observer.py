"""Ground Qwen's chunk observations and merge the full recording."""
from __future__ import annotations

import json
import re
from collections import Counter

from . import modal_runtime

DIMENSIONS = ("action_intensity", "strategic_depth", "creative_expression", "mechanical_difficulty",
              "management_depth", "narrative_emphasis", "exploration_emphasis", "social_emphasis",
              "competitive_emphasis", "cooperative_potential", "systemic_emergence", "session_commitment",
              "learning_curve", "spectator_readability")


def _json(raw: str) -> dict:
    text = re.sub(r"^\s*```(?:json)?|```\s*$", "", raw.strip(), flags=re.I).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Qwen returned no structured gameplay analysis.") from None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("Qwen returned malformed gameplay JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Qwen gameplay analysis must be a JSON object.")
    return value


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(x).strip()[:100] for x in value if isinstance(x, str) and x.strip()))[:20]


def _records(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _score(value: object, maximum: float = 1) -> float:
    try:
        return round(max(0, min(maximum, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def observe_chunk(frames: list[tuple[float, bytes]], start: float, end: float) -> dict:
    prompt = f"""You are a careful professional game analyst seeing an unknown game for the first time.
These timestamped frames come from {start:.2f}–{end:.2f} seconds of a gameplay video. The game may be an unreleased prototype. Analyze only what is visible. Do not infer title, multiplayer, monetization, progression or mechanics without visual evidence. Separate observation from inference. Return ONLY JSON with this shape:
{{"description":"vivid, concrete account of what playing this segment appears to feel like", "observations":[{{"timestamp":0,"observation":"visible action or interface","confidence":0.0}}], "events":[{{"start":0,"end":0,"type":"action","description":"visible event","confidence":0.0}}], "observed_actions":[""], "inferred_mechanics":[{{"name":"","confidence":0.0,"evidence_timestamps":[0]}}], "claims":[{{"claim":"genre, loop or other major inference","category":"strongly_inferred or tentative","confidence":0.0,"evidence_timestamps":[0]}}], "genres":[""], "subgenres":[""], "themes":[""], "keywords":[""], "player_perspective":[""], "game_modes":[""], "core_loop":[""], "pace":"", "complexity":"", "presentation":{{"visual_style":"unknown","ui_density":"unknown"}}, "skill_profile":{{}}, "social_structure":{{}}, "progression":{{}}, "economy":{{}}, "combat":{{}}, "exploration":{{}}, "construction":{{}}, "management":{{}}, "narrative":{{}}, "accessibility_observations":[""], "dimensions":{{"action_intensity":{{"score":0,"confidence":0.0,"evidence_timestamps":[0]}}}}, "uncertainties":[""]}}
For every inference, supply frame timestamps. Use [] or "unknown" when evidence is insufficient. Dimensions are estimates from 0 to 100. Don't imply you saw what happened between sparse frames."""
    data = _json(modal_runtime.observe(prompt, frames))
    observations = []
    for item in _records(data.get("observations")):
        timestamp = _score(item.get("timestamp"), end)
        note = str(item.get("observation") or "").strip()[:300]
        if start <= timestamp <= end and note:
            observations.append({"timestamp": timestamp, "observation": note,
                                 "confidence": _score(item.get("confidence"))})
    events = []
    for item in _records(data.get("events")):
        a, b = _score(item.get("start"), end), _score(item.get("end"), end)
        description = str(item.get("description") or "").strip()[:300]
        if start <= a <= b <= end and description:
            events.append({"start": a, "end": b, "type": str(item.get("type") or "action")[:40],
                           "description": description, "confidence": _score(item.get("confidence"))})
    mechanics = []
    for item in _records(data.get("inferred_mechanics")):
        if not item.get("name"):
            continue
        times = [float(t) for t in (item.get("evidence_timestamps") or []) if isinstance(t, (int, float))
                 and start <= t <= end and any(abs(t - obs["timestamp"]) <= 2 for obs in observations)]
        if times:
            mechanics.append({"name": str(item["name"])[:100], "confidence": _score(item.get("confidence")),
                              "evidence_timestamps": times})
    claims = []
    for item in _records(data.get("claims")):
        if not item.get("claim"):
            continue
        times = [float(t) for t in (item.get("evidence_timestamps") or []) if isinstance(t, (int, float))
                 and any(abs(t - obs["timestamp"]) <= 2 for obs in observations)]
        if times:
            claims.append({"claim": str(item["claim"])[:180], "category": "strongly_inferred" if _score(item.get("confidence")) >= .75 else "tentative",
                           "confidence": _score(item.get("confidence")), "evidence_timestamps": times})
    dimensions = {}
    if isinstance(data.get("dimensions"), dict):
        for key in DIMENSIONS:
            value = data["dimensions"].get(key)
            if value is None:
                continue
            item = value if isinstance(value, dict) else {"score": value, "confidence": .5, "evidence_timestamps": []}
            times = [float(t) for t in (item.get("evidence_timestamps") or []) if isinstance(t, (int, float))
                     and any(abs(t - obs["timestamp"]) <= 2 for obs in observations)]
            if times:
                dimensions[key] = {"score": _score(item.get("score"), 100), "confidence": _score(item.get("confidence")),
                                   "evidence_timestamps": times}
    detail_keys = ("presentation", "skill_profile", "social_structure", "progression", "economy", "combat",
                   "exploration", "construction", "management", "narrative")
    return {"start": start, "end": end, "description": str(data.get("description") or "").strip()[:1600],
            "observations": observations, "events": events, "observed_actions": _strings(data.get("observed_actions")), "claims": claims,
            "inferred_mechanics": mechanics,
            **{key: _strings(data.get(key)) for key in ("genres", "subgenres", "themes", "keywords",
                                                      "player_perspective", "game_modes", "core_loop", "uncertainties",
                                                      "accessibility_observations")},
            **{key: data.get(key) if isinstance(data.get(key), dict) else {} for key in detail_keys},
            "pace": str(data.get("pace") or "unknown")[:80], "complexity": str(data.get("complexity") or "unknown")[:80],
            "dimensions": dimensions}


def merge(chunks: list[dict]) -> dict:
    if not chunks:
        raise ValueError("No gameplay was observed.")
    observations = sorted((obs for part in chunks for obs in part["observations"]), key=lambda x: x["timestamp"])
    events = []
    for event in sorted((event for part in chunks for event in part["events"]), key=lambda x: x["start"]):
        if any(abs(event["start"] - prior["start"]) <= 3 and
               (event["description"].casefold() == prior["description"].casefold() or
                event["type"] == prior["type"] and event["start"] <= prior["end"])
               for prior in events):
            continue
        events.append(event)
    profile: dict = {"observations": observations, "events": events,
                     "inferred_mechanics": [], "uncertainties": [], "confidence": 0.0}
    for key in ("observed_actions", "genres", "subgenres", "themes", "keywords", "player_perspective",
                "game_modes", "core_loop", "uncertainties", "accessibility_observations"):
        votes = Counter(item for part in chunks for item in part.get(key, []))
        profile[key] = [item for item, _ in votes.most_common(15)]
    for key in ("presentation", "skill_profile", "social_structure", "progression", "economy", "combat",
                "exploration", "construction", "management", "narrative"):
        profile[key] = {}
        for part in chunks:
            for field, value in part.get(key, {}).items():
                if field not in profile[key] and value not in (None, "", "unknown", [], {}):
                    profile[key][field] = value
    for key in ("pace", "complexity"):
        votes = Counter(part[key] for part in chunks if part[key] != "unknown")
        profile[key] = votes.most_common(1)[0][0] if votes else "unknown"
        if len(votes) > 1:
            profile["uncertainties"].append(f"Different sections suggest different {key}.")
    dimension_votes = {key: [part["dimensions"][key] for part in chunks if key in part["dimensions"]]
                       for key in DIMENSIONS}
    profile["dimensions"] = {key: {"score": round(sum(v["score"] if isinstance(v, dict) else v for v in values) / len(values)),
                                   "confidence": round(sum(v["confidence"] if isinstance(v, dict) else .5 for v in values) / len(values), 2),
                                   "evidence_timestamps": list(dict.fromkeys(t for v in values if isinstance(v, dict) for t in v.get("evidence_timestamps", [])))[:8]}
                             for key, values in dimension_votes.items() if values}
    seen = set()
    for part in chunks:
        for mechanic in part["inferred_mechanics"]:
            key = mechanic["name"].casefold()
            if key not in seen:
                seen.add(key)
                profile["inferred_mechanics"].append(mechanic)
    profile["confidence"] = round(sum(obs["confidence"] for obs in observations) / len(observations), 2) if observations else 0
    profile["chunk_count"] = len(chunks)
    profile["chunk_summaries"] = [{"start": part["start"], "end": part["end"],
                                   "description": part["description"][:300],
                                   "observations": [{**obs, "observation": obs["observation"][:160]}
                                                    for obs in part["observations"][:2]]} for part in chunks]
    profile["core_loop_prose"] = " → ".join(profile["core_loop"])
    snapshots = [part["description"] for part in chunks if part["description"]]
    sample_indexes = sorted({0, len(snapshots) // 2, len(snapshots) - 1}) if snapshots else []
    profile["description"] = "\n\n".join(snapshots[index] for index in sample_indexes)[:4500]
    profile["claims"] = []
    for part in chunks:
        for claim in part.get("claims", []):
            evidence = [obs for obs in observations if any(abs(obs["timestamp"] - t) <= 2
                                                         for t in claim["evidence_timestamps"])]
            if evidence and not any(existing["claim"].casefold() == claim["claim"].casefold() for existing in profile["claims"]):
                profile["claims"].append({**{k: claim[k] for k in ("claim", "category", "confidence")}, "evidence": evidence[:5]})
    for mechanic in profile["inferred_mechanics"]:
        evidence = [obs for obs in observations if any(abs(obs["timestamp"] - t) <= 2
                                                     for t in mechanic["evidence_timestamps"])]
        if evidence:
            profile["claims"].append({"claim": mechanic["name"], "category": "strongly_inferred" if mechanic["confidence"] >= .75 else "tentative",
                                      "confidence": mechanic["confidence"], "evidence": evidence[:5]})
    return profile


def synthesize(profile: dict) -> dict:
    """A text-only pass after all chunks, bounded to the observations already stored."""
    evidence = {key: profile[key] for key in ("chunk_summaries", "observed_actions", "inferred_mechanics",
                                              "genres", "themes", "core_loop", "pace", "complexity", "uncertainties")}
    prompt = """Use ONLY this merged, timestamped gameplay evidence. Return JSON with:
{"description":"2–4 vivid paragraphs describing what the player does, pace, attention, tension, satisfaction and presentation; explicitly qualify uncertain mechanics", "audience_archetypes":[{"archetype":"behavioral motivation, never demographic","affinity":0,"reason":"why visible play supports it","evidence_timestamps":[0]}], "audience_summary":"what players may enjoy/dislike, learning curve and session style"}.
Never claim market demand or identity. Every time range below is part of the recording; cover the full sequence. Evidence:\n""" + json.dumps(evidence, ensure_ascii=False)[:30000]
    try:
        result = _json(modal_runtime.observe(prompt, []))
    except (ValueError, RuntimeError, TimeoutError):
        return {"description": profile["description"], "audience_archetypes": [], "audience_summary": "Audience fit is uncertain because synthesis was unavailable."}
    archetypes = []
    for item in _records(result.get("audience_archetypes")):
        times = [float(t) for t in (item.get("evidence_timestamps") or []) if isinstance(t, (int, float))
                 and any(abs(t - obs["timestamp"]) <= 2 for obs in profile["observations"])]
        if times and item.get("archetype"):
            archetypes.append({"archetype": str(item["archetype"])[:60], "affinity": round(_score(item.get("affinity"), 100)),
                               "reason": str(item.get("reason") or "")[:400], "evidence_timestamps": times,
                               "confidence": min(.85, profile["confidence"])})
    return {"description": str(result.get("description") or profile["description"])[:4500],
            "audience_archetypes": archetypes[:10],
            "audience_summary": str(result.get("audience_summary") or "")[:1000]}
