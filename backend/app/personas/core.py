"""Deterministic persona planning and analysis. No model or network is needed."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from difflib import SequenceMatcher
from collections import Counter, defaultdict
from importlib.resources import files

QUESTIONS = json.loads(files("app.personas").joinpath("questions.json").read_text(encoding="utf-8"))
CONFIG = json.loads(files("app.personas").joinpath("config.json").read_text(encoding="utf-8"))
QUESTION_BY_ID = {q["id"]: q for q in QUESTIONS}
SENSITIVE = re.compile(r"\b(?:religion|ethnicity|sexual orientation|politics|medical condition)\b", re.I)
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{8,}\d)(?!\w)")
URL = re.compile(r"https?://\S+|\bwww\.\S+", re.I)
HANDLE = re.compile(r"(?<!\w)@[\w.-]+")
NAME = re.compile(r"\b(?:my name is|i am|i'm|signed by)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.I)
FULL_NAME = re.compile(r"\b[A-Z][a-z]{2,24}\s+[A-Z][a-z]{2,24}\b")
SPACE = re.compile(r"\s+")
TAGS = {
    "pain": ("struggle", "hard", "difficult", "problem", "slow", "stress", "risk", "too long", "cannot", "can't", "need"),
    "goal": ("want", "hope", "goal", "improve", "save", "clear", "faster", "easier"),
    "trigger": ("when", "before", "after", "deadline", "switched", "started"),
    "objection": ("cost", "price", "expensive", "worry", "risky", "trust", "concern"),
    "criterion": ("does it", "can i", "must", "requires", "connect", "compare"),
    "alternative": ("spreadsheet", "diy", "manual", "instead", "alternative"),
    "role": ("team", "owner", "manager", "lead", "accountant", "staff"),
    "channel": ("search", "newsletter", "social", "forum", "video", "referral"),
}


def scrub(text: str) -> str:
    """Conservative PII cleanup before evidence is persisted or displayed."""
    value = str(text or "")
    for pattern, replacement in ((EMAIL, "[email]"), (PHONE, "[phone]"), (URL, "[link]"),
                                 (HANDLE, "[handle]"), (NAME, "[name]"), (FULL_NAME, "[name]")):
        value = pattern.sub(replacement, value)
    return SPACE.sub(" ", value).strip()[:1200]


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower(), flags=re.UNICODE)


def detect_language(text: str) -> str:
    """Script-aware label; ambiguous Latin-language text remains undetermined."""
    for code, span in (("hi", (0x0900, 0x097F)), ("bn", (0x0980, 0x09FF)),
                       ("ta", (0x0B80, 0x0BFF)), ("ar", (0x0600, 0x06FF))):
        if sum(span[0] <= ord(c) <= span[1] for c in text) >= 3:
            return code
    return "en" if text.isascii() and len(re.findall(r"[A-Za-z]+", text)) >= 5 else "und"


def validate_answers(answers: dict, quick: bool = True) -> list[str]:
    missing = []
    buyer = str(answers.get("A2", ""))
    for q in QUESTIONS:
        if not q["required"] or (quick and not q["core"]):
            continue
        if q.get("condition") and q["condition"] not in (buyer, "Both"):
            continue
        value = answers.get(q["id"])
        if not value or value == []:
            missing.append(q["id"])
    return missing


def answer_lines(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip(" -•\t") for line in re.split(r"[\n;]+", str(value or "")) if line.strip(" -•\t")]


def plan(answers: dict, depth: str = "quick") -> dict:
    """Generate stable bounded seeds and editable requests from interview answers."""
    product = answer_lines(answers.get("A1"))[:1]
    pains = answer_lines(answers.get("A4"))[:3]
    phrases = answer_lines(answers.get("C3"))[:5]
    alternatives = answer_lines(answers.get("C1"))[:5]
    seeds = list(dict.fromkeys(scrub(s) for s in product + pains + phrases + alternatives if s.strip()))
    seeds = [s for s in seeds if s and not SENSITIVE.search(s)]
    excluded = answer_lines(answers.get("E5"))
    excluded_topics = [line.split(":", 1)[1].strip().lower() for line in excluded if line.lower().startswith("topic:") and ":" in line]
    excluded_sources = [line.split(":", 1)[1].strip().lower() if line.lower().startswith("source:") else line.lower()
                        for line in excluded if not line.lower().startswith("topic:")]
    queries = list(dict.fromkeys(
        q[:120] for seed in seeds[:10]
        for q in (seed, f"{seed} problem", f"{seed} alternative", f"{seed} review")
        if not any(topic in q.lower() for topic in excluded_topics)
    ))[:60]
    if len(queries) < 20:
        product_seed = product[0] if product else "product"
        queries.extend(f"{product_seed} {suffix}" for suffix in (
            "pricing", "setup", "comparison", "questions", "benefits", "issues", "workflow",
            "small business", "experience", "support", "migration", "why buy", "when buy",
            "community", "best tools", "manual option", "how to choose", "common objections",
            "use cases", "features") if f"{product_seed} {suffix}" not in queries and
            not any(topic in f"{product_seed} {suffix}".lower() for topic in excluded_topics))
    urls = re.findall(r"https?://[^\s;,]+", str(answers.get("A3", "")) + " " + str(answers.get("C1", "")))[:8]
    estimate = {"quick": 2, "standard": 4, "deep": 8}.get(depth, 2)
    sources = [
                {"id": "demo", "label": "Offline demo", "enabled": False, "requests": 0},
                {"id": "first_party", "label": "Imported CSV and account history", "enabled": True, "requests": 0},
                {"id": "web", "label": "Approved public websites", "enabled": False, "requests": min(len(urls), estimate)},
                {"id": "hacker_news", "label": "Hacker News", "enabled": False, "requests": estimate},
                {"id": "stack_exchange", "label": "Stack Exchange", "enabled": False, "requests": estimate},
                {"id": "bluesky", "label": "Bluesky public search", "enabled": False, "requests": estimate},
                {"id": "mastodon", "label": "Mastodon public search", "enabled": False, "requests": estimate},
                {"id": "wikipedia", "label": "Wikipedia pageviews (trend only)", "enabled": False, "requests": 1},
                {"id": "youtube", "label": "YouTube comments (API key)", "enabled": False, "requests": 1},
            ]
    for source in sources:
        source["excluded"] = any(exclusion and (exclusion in source["id"].replace("_", " ") or
                                   exclusion in source["label"].lower()) for exclusion in excluded_sources)
        if source["excluded"]:
            source["enabled"] = False
    return {"version": 1, "seeds": seeds, "queries": queries[:60], "urls": urls,
            "excluded_topics": excluded_topics, "sources": sources}


def evidence_unit(run_id: str, source: str, text: str, *, kind: str = "comment",
                  date: str = "", engagement: float = 0, domain: str = "", source_type: str = "public") -> dict | None:
    cleaned = scrub(text)
    if len(words(cleaned)) < 5 or SENSITIVE.search(cleaned):
        return None
    digest = hashlib.sha256(f"{run_id}:{source}:{cleaned.lower()}".encode()).hexdigest()[:20]
    return {"id": digest, "source": source, "source_type": source_type, "domain": domain,
            "date": date, "language": detect_language(cleaned), "text": cleaned, "kind": kind,
            "engagement": float(engagement or 0), "meta": {},
            "tags": [tag for tag, terms in TAGS.items() if any(term in cleaned.lower() for term in terms)]}


def parse_csv(run_id: str, csv_text: str, mapping: dict[str, str]) -> list[dict]:
    """User-selected column mapping; text and IDs never retain names or profile links."""
    rows = csv.DictReader(io.StringIO(csv_text[:2_000_000]))
    if not rows.fieldnames or mapping.get("text") not in rows.fieldnames:
        raise ValueError("Choose a text column from the CSV headers.")
    result = []
    for row in list(rows)[:1000]:
        unit = evidence_unit(run_id, scrub(str(row.get(mapping.get("source", ""), "CSV") or "CSV"))[:60],
                             row.get(mapping["text"], ""), kind="survey",
                             date=str(row.get(mapping.get("date", ""), ""))[:20],
                             source_type="first_party")
        if unit:
            result.append(unit)
    return dedupe(result)


def dedupe(units: list[dict]) -> list[dict]:
    seen: set[str] = set()
    buckets: dict[str, list[str]] = defaultdict(list)
    result = []
    for unit in units:
        normalized = " ".join(words(unit["text"]))
        key = hashlib.sha256(normalized.encode()).hexdigest()[:20]
        bucket = " ".join(normalized.split()[:2])
        nearby = buckets[bucket][-30:]
        near_duplicate = any(abs(len(normalized) - len(old)) / max(1, len(normalized)) < .15 and
                             SequenceMatcher(None, normalized, old).ratio() >= .9 for old in nearby)
        if key not in seen and not near_duplicate:
            result.append(unit)
            seen.add(key)
            buckets[bucket].append(normalized)
    return result


def demo_units(run_id: str) -> list[dict]:
    content = files("app.personas").joinpath("sample_data/demo.csv").read_text(encoding="utf-8")
    return parse_csv(run_id, content, {"text": "text", "source": "source", "date": "date"})


def _hypothesis_labels(answers: dict) -> list[str]:
    raw = answer_lines(answers.get("D1"))[:4]
    labels = [re.split(r"\s+[—–-]\s+", line)[0].strip()[:60] for line in raw]
    defaults = ["Time-constrained buyer", "Risk-conscious evaluator", "Growth-focused buyer", "Practical comparer"]
    return (labels + defaults)[:max(2, min(4, len(labels)))] if labels else defaults[:2]


def _cluster_units(units: list[dict], answers: dict, thin: bool) -> tuple[list[str], dict[int, list[dict]], dict]:
    """Choose k using separation, bootstrap stability, share and distinctiveness."""
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics import adjusted_rand_score, silhouette_score
    import numpy as np

    non_english = bool(re.search(r"\b(?:hindi|spanish|french|german|arabic|bengali|tamil)\b", str(answers.get("A7", "")), re.I))
    vectorizer = TfidfVectorizer(min_df=1 if len(units) < 30 else 2, max_features=1500,
                                 stop_words=None if non_english else "english",
                                 analyzer="char_wb" if non_english else "word",
                                 ngram_range=(3, 5) if non_english else (1, 2))
    matrix = vectorizer.fit_transform([u["text"] for u in units])
    if matrix.shape[1] < 3:
        raise ValueError("Not enough distinct terms to segment")
    candidate_ks = range(2, min(3 if thin else 4, len(units) - 1) + 1)
    scored = []
    for k in candidate_ks:
        model = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = model.fit_predict(matrix)
        shares = np.bincount(labels, minlength=k) / len(labels)
        if np.min(shares) < CONFIG["minimum_cluster_share"]:
            continue
        separation = float(silhouette_score(matrix, labels, metric="cosine"))
        sample_idx = np.random.default_rng(42).choice(len(units), size=max(k * 3, int(.8 * len(units))), replace=False)
        boot = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(matrix[sample_idx])
        stability = float(adjusted_rand_score(labels[sample_idx], boot))
        centers = model.cluster_centers_
        distinctiveness = float(np.mean(np.max(centers, axis=0) - np.mean(centers, axis=0)))
        score = .45 * separation + .35 * stability + .20 * distinctiveness
        scored.append((score, k, labels, separation, stability, distinctiveness))
    if not scored:
        raise ValueError("No stable clusters with at least 10% share")
    _, k, labels, separation, stability, distinctiveness = max(scored, key=lambda row: (row[0], -row[1]))
    buckets = {i: [unit for unit, assigned in zip(units, labels) if assigned == i] for i in range(k)}
    stop = {"this", "that", "with", "from", "have", "your", "they", "their", "about", "when",
            "what", "does", "need", "want", "would", "could", "should", "because", "without"}
    names = []
    for i in range(k):
        own = Counter(word for u in buckets[i] for word in words(u["text"]) if len(word) > 3 and word not in stop)
        other = Counter(word for j, batch in buckets.items() if j != i for u in batch for word in words(u["text"]))
        ranked = sorted(own, key=lambda term: (own[term] / max(1, len(buckets[i])) - other[term] / max(1, len(units) - len(buckets[i])), own[term], term), reverse=True)
        names.append(" / ".join(ranked[:2]).title() + " needs" if ranked else f"Need group {i + 1}")
    return names, buckets, {"k": k, "silhouette": round(separation, 3), "bootstrap_ari": round(stability, 3),
                            "term_distinctiveness": round(distinctiveness, 3), "method": "TF-IDF + seeded KMeans"}


def analyze(units: list[dict], answers: dict) -> dict:
    """Create evidence-linked candidates, labeling sparse results as hypotheses."""
    units = dedupe([u for u in units if len(words(u["text"])) >= 5])
    if len(units) < 5:
        raise ValueError("Fewer than five usable evidence units. Import customer feedback or enable a public source, then retry.")
    source_count = len({u["source"] for u in units})
    thin = len(units) < CONFIG["thin_unit_threshold"] or source_count < CONFIG["minimum_source_count"]
    cluster_info = None
    if len(units) >= 10 and source_count >= 2:
        try:
            labels, buckets, cluster_info = _cluster_units(units, answers, thin)
        except (ImportError, ValueError):
            cluster_info = None
    if cluster_info is None:
        labels = _hypothesis_labels(answers)[:3 if thin else 4]
        buckets = defaultdict(list)
        anchors = [set(words(label)) for label in labels]
        for i, unit in enumerate(units):
            terms = set(words(unit["text"]))
            scores = [len(terms & anchor) for anchor in anchors]
            target = scores.index(max(scores)) if max(scores) else i % len(labels)
            buckets[target].append(unit)
    candidates = []
    for i, label in enumerate(labels):
        batch = buckets[i]
        tags = Counter(t for u in batch for t in u["tags"])
        batch_ids = {u["id"] for u in batch}
        other_terms = Counter(w for u in units if u["id"] not in batch_ids for w in words(u["text"]))
        own_terms = Counter(w for u in batch for w in words(u["text"]))
        unique_terms = sum(1 for term, count in own_terms.items() if count >= 2 and other_terms[term] == 0)
        distinctiveness = min(1.0, unique_terms / 10)
        strength = sum(1 for u in batch if u["tags"] and
                       (u.get("meta", {}).get("relative_engagement") is None or u["meta"]["relative_engagement"] >= 1)) / max(1, len(batch))
        channels = answer_lines(answers.get("C2"))[:6]
        reachability = sum(1 for ch in channels if any(ch.lower() in u["text"].lower() for u in batch)) / max(1, len(channels)) if channels else .5
        hypothesis_ratings = []
        for line in answer_lines(answers.get("D1")):
            match = re.search(r"(?:^|\D)([1-5])\s*$", line)
            if match and set(words(line)) & set(words(label)):
                hypothesis_ratings.append(int(match.group(1)))
        business_value = (max(hypothesis_ratings) if hypothesis_ratings else 3) / 5
        components = {"evidence_share": len(batch) / max(1, len(units)), "evidence_strength": strength,
                      "distinctiveness": distinctiveness, "reachability": reachability, "business_value": business_value}
        score = round(sum(components[key] * weight for key, weight in CONFIG["score_weights"].items()), 3)
        batch_sources = len({u["source"] for u in batch})
        stability = cluster_info["bootstrap_ari"] if cluster_info else 0
        confidence = ("Low — hypothesis" if thin or len(batch) < 30 or batch_sources < 2 else
                      "High — corroborated" if len(batch) >= 100 and batch_sources >= 3 and stability >= .7 else
                      "Moderate — directional")
        snippets = [{"text": " ".join(u["text"].split()[:25]), "evidence_ids": [u["id"]]}
                    for u in batch[:3]]
        pain_claims = [{"text": u["text"][:150], "evidence_ids": [u["id"]]} for u in batch if "pain" in u["tags"]][:3]
        goal_claims = [{"text": u["text"][:150], "evidence_ids": [u["id"]]} for u in batch if "goal" in u["tags"]][:3]
        trigger_claims = [{"text": u["text"][:150], "evidence_ids": [u["id"]]} for u in batch if "trigger" in u["tags"]][:3]
        objection_claims = [{"text": u["text"][:150], "evidence_ids": [u["id"]]} for u in batch if "objection" in u["tags"]][:3]
        criterion_claims = [{"text": u["text"][:150], "evidence_ids": [u["id"]]} for u in batch if "criterion" in u["tags"]][:3]
        summary = (f"{len(batch)} of {len(units)} evidence units discuss {label.lower()}."
                   if batch else f"Proposed buyer type: {label}. Validate with customer interviews.")
        candidates.append({"id": f"segment-{i + 1}", "label": label, "summary": summary,
                           "evidence_ids": [u["id"] for u in batch], "evidence_share": round(len(batch) / max(1, len(units)), 3),
                           "top_pains": [item["text"] for item in pain_claims],
                           "top_goals": [item["text"] for item in goal_claims],
                           "pain_claims": pain_claims, "goal_claims": goal_claims,
                           "trigger_claims": trigger_claims, "objection_claims": objection_claims,
                           "criterion_claims": criterion_claims,
                           "snippets": snippets, "channels": answer_lines(answers.get("C2"))[:4],
                           "tags": dict(tags), "score": score, "score_components": components,
                           "confidence": confidence,
                           "review": {"rings_true": 3, "business_value": 3, "priority": 3, "missing": ""}})
    method = cluster_info["method"] if cluster_info else "Lexical routing with user-supplied hypotheses"
    k_reason = (f"Selected k={len(labels)} using silhouette {cluster_info['silhouette']}, bootstrap ARI {cluster_info['bootstrap_ari']}, "
                f"term distinctiveness {cluster_info['term_distinctiveness']} and minimum 10% cluster share.") if cluster_info else \
               "Insufficient distinct source data for stable clustering; user hypotheses are the starting segments."
    limitations = (["Small or single-source samples produce hypotheses, not validated market segments."] if thin else
                   ["Public-platform and self-selected response bias remain possible."])
    if answers.get("E4") == "Yes":
        limitations.append("Regulated industry: a qualified human must review marketing claims before use.")
    return {"candidates": candidates, "method": method,
            "k": len(labels), "k_reason": k_reason, "cluster_diagnostics": cluster_info,
            "sample_size": len(units), "source_count": source_count, "thin": thin,
            "limitations": limitations,
            "tag_counts": dict(Counter(t for u in units for t in u["tags"]))}


def claim(text: str, evidence_ids: list[str], origin: str = "evidence") -> dict:
    if origin == "evidence" and not evidence_ids:
        raise ValueError("Evidence claims require evidence IDs")
    cleaned = scrub(text)
    if origin == "evidence":
        cleaned = " ".join(cleaned.split()[:25])
    return {"text": cleaned, "evidence_ids": evidence_ids, "origin": origin}


def finalize(analysis: dict, answers: dict) -> list[dict]:
    result = []
    for c in analysis["candidates"]:
        review = c.get("review", {})
        if review.get("merge_with"):
            continue
        ids = c["evidence_ids"]
        origin = "evidence" if ids else "user-assumed"
        pains = [claim(item["text"], item["evidence_ids"], "evidence") for item in c.get("pain_claims", [])[:2] if set(item["evidence_ids"]) <= set(ids)]
        goals = [claim(item["text"], item["evidence_ids"], "evidence") for item in c.get("goal_claims", [])[:2] if set(item["evidence_ids"]) <= set(ids)]
        triggers = [claim(item["text"], item["evidence_ids"], "evidence") for item in c.get("trigger_claims", [])[:2] if set(item["evidence_ids"]) <= set(ids)]
        objections = [claim(item["text"], item["evidence_ids"], "evidence") for item in c.get("objection_claims", [])[:2] if set(item["evidence_ids"]) <= set(ids)]
        criteria = [claim(item["text"], item["evidence_ids"], "evidence") for item in c.get("criterion_claims", [])[:2] if set(item["evidence_ids"]) <= set(ids)]
        voice = [claim(s["text"], s["evidence_ids"], "evidence") for s in c["snippets"][:2]]
        hooks = [claim(f"Draft hook: How to address {pains[0]['text'][:90]}?", [], "draft idea")] if pains else []
        role = answer_lines(answers.get("D2"))[:1]
        reviewed_score = round(.5 * c.get("score", 0) + .25 * review.get("business_value", 3) / 5 +
                               .25 * review.get("priority", 3) / 5, 3)
        result.append({"id": c["id"], "label": c["label"], "summary": c["summary"],
                       "score": reviewed_score, "strategic_priority": review.get("priority", 3),
                       "context": claim(c["summary"], ids[:3], origin),
                       "jobs_to_be_done": goals or [claim("Evaluate whether this offer solves the stated problem", [], "user-assumed")],
                       "goals": goals, "pains": pains, "triggers": triggers, "objections": objections,
                       "decision_criteria": criteria, "buying_role": claim(role[0] if role else "Unknown; validate in interviews", [], "user-assumed"),
                       "channels": [claim(ch, [], "user-assumed") for ch in c["channels"]],
                       "content_preferences": [], "value_props": [], "phrases_to_use": voice, "phrases_to_avoid": [],
                       "hook_ideas": hooks, "kpis": ["Interview confirmation", "Qualified message response rate"],
                       "demographics": [{"text": scrub(s), "origin": "user-assumed"} for s in answer_lines(answers.get("D3"))] if answers.get("A2") in ("B2C", "Both") else [],
                       "confidence": c["confidence"], "confidence_reasons": [f"{len(ids)} evidence units", f"{analysis['source_count']} source(s)"],
                       "evidence_ids": ids, "edited_by_you": bool(c.get("edited_by_you")),
                       "validation": {"interview_questions": [
                           "What was happening when you first looked for a solution?",
                           "What did you try before this?", "What outcome matters most to you?",
                           "What would make you hesitate?", "Who else helps decide?",
                           "Where do you look for advice?", "How would you compare options?",
                           "What result would make this worthwhile?"],
                           "experiments": ["Test a pain-led landing page against an outcome-led page; compare qualified enquiry rate.",
                                           "Test two value propositions in the same channel; compare relevant response rate."]}})
    return result[:4]


def assert_evidence(personas: list[dict], units: list[dict]) -> None:
    valid = {unit["id"] for unit in units}
    for persona in personas:
        for field in ("context", "buying_role"):
            item = persona[field]
            if item["origin"] == "evidence" and not item["evidence_ids"]:
                raise ValueError("Unsupported evidence claim")
            if not set(item["evidence_ids"]) <= valid:
                raise ValueError("Unknown evidence ID")
        for field in ("jobs_to_be_done", "goals", "pains", "triggers", "objections", "decision_criteria", "channels", "content_preferences", "value_props", "phrases_to_use", "phrases_to_avoid", "hook_ideas"):
            for item in persona[field]:
                if item["origin"] == "evidence" and not item["evidence_ids"]:
                    raise ValueError("Unsupported evidence claim")
                if not set(item["evidence_ids"]) <= valid:
                    raise ValueError("Unknown evidence ID")
