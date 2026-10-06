"""Audience Personas wizard and persisted cancellable collection jobs."""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..personas import collectors, core

router = APIRouter(prefix="/personas", tags=["personas"])
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="personas")
_cancelled: set[str] = set()
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize() -> None:
    """A process restart cancels in-flight work without publishing partial personas."""
    with db._connect() as conn:
        rows = conn.execute("SELECT id,document_json FROM persona_runs WHERE status IN ('queued','collecting')").fetchall()
        for row in rows:
            doc = json.loads(row["document_json"])
            doc["job_error"] = "Collection stopped when the app closed. Review the plan and run again."
            conn.execute("UPDATE persona_runs SET status='cancelled', document_json=?, updated_at=? WHERE id=?",
                         (json.dumps(doc), _now(), row["id"]))


def _row(run_id: str) -> dict:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM persona_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Persona run not found")
    return {"id": row["id"], "name": row["name"], "step": row["step"],
            "status": row["status"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], **json.loads(row["document_json"])}


def _update(run_id: str, *, step: int | None = None, status: str | None = None,
            name: str | None = None, changes: dict | None = None) -> dict:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM persona_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Persona run not found")
        doc = json.loads(row["document_json"])
        doc.update(changes or {})
        conn.execute("UPDATE persona_runs SET name=?,step=?,status=?,document_json=?,updated_at=? WHERE id=?",
                     (name if name is not None else row["name"], step if step is not None else row["step"],
                      status if status is not None else row["status"], json.dumps(doc), _now(), run_id))
    return _row(run_id)


def get_active_personas() -> list[dict]:
    with db._connect() as conn:
        row = conn.execute("SELECT document_json FROM persona_runs WHERE status='complete' ORDER BY updated_at DESC LIMIT 1").fetchone()
    return json.loads(row["document_json"]).get("personas", []) if row else []


def set_active_personas(run_id: str, personas: list[dict]) -> dict:
    current = _row(run_id)
    core.assert_evidence(personas, current.get("evidence", []))
    return _update(run_id, status="complete", step=5, changes={"personas": personas})


class RunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    demo: bool = False


class SaveRequest(BaseModel):
    answers: dict[str, Any] | None = None
    mode: str | None = None
    step: int | None = Field(default=None, ge=1, le=5)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    plan: dict | None = None


class ImportRequest(BaseModel):
    csv_text: str = Field(max_length=2_000_000)
    mapping: dict[str, str]


class CollectRequest(BaseModel):
    approved: bool
    terms_confirmed: bool = False
    youtube_key: str = ""
    mastodon_host: str = ""
    mastodon_token: str = ""


class ReviewRequest(BaseModel):
    candidates: list[dict]


class SplitRequest(BaseModel):
    candidate_id: str
    phrase: str = Field(min_length=2, max_length=80)


class PersonaEdit(BaseModel):
    label: str = Field(min_length=2, max_length=80)
    summary: str = Field(min_length=2, max_length=500)


@router.get("/questions")
def questions() -> dict:
    return {"version": 1, "questions": core.QUESTIONS}


@router.get("/runs")
def list_runs() -> dict:
    with db._connect() as conn:
        rows = conn.execute("SELECT id,name,step,status,updated_at FROM persona_runs ORDER BY updated_at DESC LIMIT 100").fetchall()
    return {"runs": [dict(row) for row in rows]}


@router.post("/runs")
def create_run(body: RunCreate) -> dict:
    run_id = uuid.uuid4().hex
    now = _now()
    demo_answers = {
        "A1": "Simple bookkeeping software for independent shops",
        "A2": "B2B", "A4": ["Close books faster", "Understand cash flow", "Reduce tax stress"],
        "B1": ["Shop owner who switched from spreadsheets for time savings",
               "Operations lead who needed cleaner records for an accountant"],
        "B3": ["Migration risk", "Monthly price"],
        "C1": ["Spreadsheets", "DIY bookkeeping", "Other bookkeeping software"],
        "C2": ["Search", "Bookkeeper communities", "Small business newsletters"],
        "D1": ["Time-strapped shop owner — wants time back — 5",
               "Careful operations evaluator — wants reliable controls — 4"],
        "E1": ["Surveys", "Support tickets", "Reviews"],
    } if body.demo else {}
    document = {"version": 1, "mode": "quick", "demo": body.demo, "answers": demo_answers, "plan": None,
                "imported": [], "evidence": [], "analysis": None, "personas": [],
                "source_status": {}, "manifest": {"created_at": now, "schema_version": 1}}
    with db._connect() as conn:
        conn.execute("INSERT INTO persona_runs VALUES (?,?,?,?,?,?,?)",
                     (run_id, body.name, 1, "draft", json.dumps(document), now, now))
    return _row(run_id)


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return _row(run_id)


@router.patch("/runs/{run_id}")
def save_run(run_id: str, body: SaveRequest) -> dict:
    current = _row(run_id)
    if current["status"] in ("queued", "collecting", "cancelling"):
        raise HTTPException(409, "Wait for collection or cancel it before editing.")
    changes = {}
    if body.answers is not None:
        unknown = set(body.answers) - set(core.QUESTION_BY_ID)
        if unknown:
            raise HTTPException(400, "Unknown question ID")
        clean_answers = {}
        for key, value in body.answers.items():
            clean_answers[key] = [core.scrub(str(v)) for v in value] if isinstance(value, list) else core.scrub(str(value))
        changes["answers"] = {**current["answers"], **clean_answers}
    if body.mode is not None:
        if body.mode not in ("quick", "full"):
            raise HTTPException(400, "Mode must be quick or full")
        changes["mode"] = body.mode
    if body.plan is not None:
        # Only these editable plan fields can be saved; no credentials in run JSON.
        previous = current.get("plan") or {}
        sources = [{**s, "enabled": bool(next((x.get("enabled") for x in body.plan.get("sources", []) if x.get("id") == s["id"]), s["enabled"]))}
                   for s in previous.get("sources", [])]
        for source in sources:
            if source.get("excluded"):
                source["enabled"] = False
        changes["plan"] = {**previous,
                           "queries": [core.scrub(str(q))[:120] for q in body.plan.get("queries", previous.get("queries", []))[:60]
                                       if not any(topic in str(q).lower() for topic in previous.get("excluded_topics", []))],
                           "urls": [str(u)[:500] for u in body.plan.get("urls", previous.get("urls", []))[:8]],
                           "sources": sources}
    return _update(run_id, step=body.step, name=body.name, changes=changes)


@router.post("/runs/{run_id}/plan")
def generate_plan(run_id: str) -> dict:
    current = _row(run_id)
    missing = core.validate_answers(current["answers"], current["mode"] == "quick")
    if missing and not current["demo"]:
        raise HTTPException(400, f"Complete required questions: {', '.join(missing)}")
    depth = str(current["answers"].get("E3") or ("Quick" if current["mode"] == "quick" else "Standard")).lower()
    research_plan = core.plan(current["answers"], depth)
    if current["demo"] and not research_plan["sources"][0].get("excluded"):
        research_plan["sources"][0]["enabled"] = True
    return _update(run_id, step=2, changes={"plan": research_plan})


@router.post("/runs/{run_id}/import")
def import_csv(run_id: str, body: ImportRequest) -> dict:
    current = _row(run_id)
    if current["status"] in ("queued", "collecting", "cancelling"):
        raise HTTPException(409, "Wait for collection to finish before importing another CSV.")
    try:
        units = core.parse_csv(run_id, body.csv_text, body.mapping)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    combined = core.dedupe(current.get("imported", []) + units)[:2000]
    return _update(run_id, changes={"imported": combined})


def _own_history(run_id: str) -> list[dict]:
    with db._connect() as conn:
        rows = conn.execute("""SELECT p.uri,p.author_did,p.text,p.created_at,
                                   COALESCE(s.likes,0)+COALESCE(s.reposts,0)+COALESCE(s.replies,0)+COALESCE(s.quotes,0) AS engagement
                            FROM bluesky_analytics_posts p JOIN bluesky_analytics_accounts a ON a.did=p.author_did
                            LEFT JOIN bluesky_analytics_snapshots s ON s.post_uri=p.uri
                            WHERE a.is_owner=1 ORDER BY p.created_at DESC,s.captured_at DESC LIMIT 600""").fetchall()
    latest = {}
    for row in rows:
        latest.setdefault(row["uri"], row)
    units = []
    for row in latest.values():
        unit = core.evidence_unit(run_id, "Own Bluesky history", row["text"], kind="post",
                                  date=row["created_at"][:10], domain="bsky.app",
                                  engagement=float(row["engagement"] or 0), source_type="first_party")
        if unit:
            try:
                posted = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            except ValueError:
                posted = datetime.now(timezone.utc)
            peers = []
            for peer in latest.values():
                if peer["author_did"] != row["author_did"] or peer["uri"] == row["uri"]:
                    continue
                try:
                    peer_date = datetime.fromisoformat(peer["created_at"].replace("Z", "+00:00"))
                except ValueError:
                    continue
                if posted - timedelta(days=30) <= peer_date < posted:
                    peers.append(float(peer["engagement"] or 0))
            baseline = median(peers) if peers else None
            unit["meta"]["relative_engagement"] = round(unit["engagement"] / max(1, baseline), 2) if baseline is not None else None
            unit["meta"]["baseline_posts"] = len(peers)
            units.append(unit)
    return units


def _run_collection(run_id: str, credentials: dict[str, str]) -> None:
    try:
        current = _row(run_id)
        research_plan = current["plan"]
        queries = research_plan["queries"]
        staged = []
        counts = {}
        statuses = {}
        signals = {}
        for source in research_plan["sources"]:
            if run_id in _cancelled:
                raise InterruptedError()
            source_id = source["id"]
            if not source["enabled"]:
                continue
            statuses[source_id] = {"status": "running", "count": 0}
            _update(run_id, status="collecting", changes={"source_status": statuses})
            try:
                if source_id == "demo":
                    batch = core.demo_units(run_id)
                elif source_id == "first_party":
                    batch = current.get("imported", []) + _own_history(run_id)
                elif source_id == "web":
                    batch = collectors.collect_web(run_id, research_plan["urls"])
                elif source_id == "hacker_news":
                    batch = collectors.collect_hacker_news(run_id, queries[:source["requests"]])
                elif source_id == "stack_exchange":
                    batch = collectors.collect_stack_exchange(run_id, queries[:source["requests"]])
                elif source_id == "bluesky":
                    batch = collectors.collect_bluesky(run_id, queries[:source["requests"]])
                elif source_id == "mastodon":
                    batch = collectors.collect_mastodon(run_id, queries[:source["requests"]], host=credentials["mastodon_host"], token=credentials["mastodon_token"])
                elif source_id == "youtube":
                    import re
                    video_ids = []
                    for url in research_plan["urls"]:
                        match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url)
                        if match:
                            video_ids.append(match.group(1))
                    batch = collectors.collect_youtube(run_id, video_ids, key=credentials["youtube_key"])
                elif source_id == "wikipedia":
                    signals["wikipedia"] = collectors.collect_wikipedia(research_plan["seeds"][0])
                    batch = []
                else:
                    statuses[source_id] = {"status": "unavailable", "count": 0,
                                           "error": "This connector needs account/key setup; import CSV for now."}
                    continue
                staged.extend(batch)
                counts[source_id] = len(batch)
                statuses[source_id] = {"status": "done", "count": len(batch)}
            except Exception as exc:
                message = str(exc)
                for secret in (credentials.get("youtube_key", ""), credentials.get("mastodon_token", "")):
                    if secret:
                        message = message.replace(secret, "[secret]")
                statuses[source_id] = {"status": "error", "count": 0, "error": message[:200]}
            _update(run_id, changes={"source_status": statuses})
        if run_id in _cancelled:
            raise InterruptedError()
        units = core.dedupe(staged)
        analysis = core.analyze(units, current["answers"])
        _update(run_id, status="review", step=4, changes={"evidence": units, "analysis": analysis,
                "personas": [], "manifest": {"schema_version": 1, "created_at": current["created_at"],
                "completed_at": _now(), "seeds": research_plan["seeds"],
                "enabled_sources": [s["id"] for s in research_plan["sources"] if s["enabled"]],
                "source_counts": counts, "signals": signals}})
    except InterruptedError:
        _update(run_id, status="cancelled", step=3, changes={"personas": [], "job_error": "Collection cancelled."})
    except Exception as exc:
        _update(run_id, status="error", step=3, changes={"personas": [], "job_error": str(exc)[:300]})
    finally:
        with _lock:
            _cancelled.discard(run_id)


@router.post("/runs/{run_id}/collect")
def collect(run_id: str, body: CollectRequest) -> dict:
    current = _row(run_id)
    if not body.approved or not current.get("plan"):
        raise HTTPException(400, "Review and approve the research plan first.")
    if any(s.get("excluded") and s["enabled"] for s in current["plan"]["sources"]):
        raise HTTPException(400, "A source excluded in the interview cannot be enabled.")
    if current["status"] in ("queued", "collecting"):
        raise HTTPException(409, "Collection is already running.")
    if any(s["enabled"] and s["id"] not in ("demo", "first_party") for s in current["plan"]["sources"]) and not body.terms_confirmed:
        raise HTTPException(400, "Confirm you can use the enabled sources under their terms.")
    old_ratios = [u.get("meta", {}).get("relative_engagement") for u in current.get("evidence", [])]
    old_ratios = [float(value) for value in old_ratios if isinstance(value, (int, float))]
    _update(run_id, status="queued", step=3, changes={"source_status": {}, "job_error": "",
            "previous_personas": current.get("personas", []),
            "previous_evidence_count": len(current.get("evidence", [])),
            "previous_relative_median": median(old_ratios) if old_ratios else None})
    credentials = {"youtube_key": body.youtube_key, "mastodon_host": body.mastodon_host,
                   "mastodon_token": body.mastodon_token}
    _pool.submit(_run_collection, run_id, credentials)
    return _row(run_id)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    current = _row(run_id)
    if current["status"] in ("queued", "collecting"):
        with _lock:
            _cancelled.add(run_id)
        return _update(run_id, status="cancelling", changes={"personas": []})
    return current


@router.post("/runs/{run_id}/review")
def review(run_id: str, body: ReviewRequest) -> dict:
    current = _row(run_id)
    analysis = current.get("analysis")
    if not analysis:
        raise HTTPException(400, "Collect and analyze data first.")
    original = {c["id"]: c for c in analysis["candidates"]}
    if set(c.get("id") for c in body.candidates) != set(original):
        raise HTTPException(400, "Candidate IDs changed.")
    revised = []
    for edited in body.candidates:
        old = original[edited["id"]]
        label = core.scrub(str(edited.get("label", old["label"])))[:60]
        review_fields = edited.get("review") or {}
        revised.append({**old, "label": label,
                        "summary": f"{len(old['evidence_ids'])} of {len(current['evidence'])} evidence units discuss {label.lower()}." if old["evidence_ids"] else f"Proposed buyer type: {label}. Validate with customer interviews.",
                        "edited_by_you": label != old["label"] or bool(review_fields.get("missing")), "review": {
            "rings_true": max(1, min(5, int(review_fields.get("rings_true", 3)))),
            "business_value": max(1, min(5, int(review_fields.get("business_value", 3)))),
            "priority": max(1, min(5, int(review_fields.get("priority", 3)))),
            "missing": core.scrub(str(review_fields.get("missing", "")))[:300],
            "merge_with": str(review_fields.get("merge_with", "")) if review_fields.get("merge_with") in original else ""}})
    by_id = {c["id"]: c for c in revised}
    merged = []
    for candidate in revised:
        target_id = candidate["review"].get("merge_with")
        if not target_id:
            merged.append(candidate)
            continue
        target = by_id[target_id]
        if target["review"].get("merge_with"):
            raise HTTPException(400, "Choose one final destination for each merge.")
        target["evidence_ids"] = list(dict.fromkeys(target["evidence_ids"] + candidate["evidence_ids"]))
        target["snippets"] = (target["snippets"] + candidate["snippets"])[:3]
        target["top_pains"] = (target["top_pains"] + candidate["top_pains"])[:3]
        target["top_goals"] = (target["top_goals"] + candidate["top_goals"])[:3]
        target["pain_claims"] = (target.get("pain_claims", []) + candidate.get("pain_claims", []))[:3]
        target["goal_claims"] = (target.get("goal_claims", []) + candidate.get("goal_claims", []))[:3]
        for field in ("trigger_claims", "objection_claims", "criterion_claims"):
            target[field] = (target.get(field, []) + candidate.get(field, []))[:3]
        target["evidence_share"] = round(len(target["evidence_ids"]) / max(1, len(current["evidence"])), 3)
        target["summary"] = f"{len(target['evidence_ids'])} of {len(current['evidence'])} evidence units discuss {target['label'].lower()}."
        target["edited_by_you"] = True
    if not 2 <= len(merged) <= 4:
        raise HTTPException(400, "Keep between two and four distinct personas.")
    updated = {**analysis, "candidates": merged, "review_before": [c["label"] for c in analysis["candidates"]],
               "review_after": [c["label"] for c in merged]}
    personas = core.finalize(updated, current["answers"])
    overrides = current.get("persona_edits", {})
    applied_edits: set[str] = set()
    for persona in personas:
        ids = set(persona["evidence_ids"])
        matching = [(len(ids & set(edit.get("evidence_ids", []))) / max(1, len(ids | set(edit.get("evidence_ids", [])))), key, edit)
                    for key, edit in overrides.items() if key not in applied_edits]
        best = max(matching, default=None)
        if best and best[0] >= .5:
            persona.update({k: best[2][k] for k in ("label", "summary")})
            persona["edited_by_you"] = True
            applied_edits.add(best[1])
    core.assert_evidence(personas, current.get("evidence", []))
    previous = current.get("previous_personas", [])
    ratios = [u.get("meta", {}).get("relative_engagement") for u in current.get("evidence", [])]
    ratios = [float(value) for value in ratios if isinstance(value, (int, float))]
    current_median = median(ratios) if ratios else None
    previous_median = current.get("previous_relative_median")
    diff = {"previous_labels": [p["label"] for p in previous], "current_labels": [p["label"] for p in personas],
            "evidence_growth": round((len(current["evidence"]) - current.get("previous_evidence_count", 0)) /
                                     max(1, current.get("previous_evidence_count", 0)), 2) if previous else None,
            "relative_engagement_shift": round((current_median - previous_median) / max(.01, previous_median), 2)
            if current_median is not None and previous_median is not None else None}
    return _update(run_id, status="complete", step=5, changes={"analysis": updated, "personas": personas, "diff": diff})


@router.patch("/runs/{run_id}/personas/{persona_id}")
def edit_persona(run_id: str, persona_id: str, body: PersonaEdit) -> dict:
    current = _row(run_id)
    personas = current.get("personas", [])
    persona = next((p for p in personas if p["id"] == persona_id), None)
    if not persona:
        raise HTTPException(404, "Persona not found")
    override = {"label": core.scrub(body.label), "summary": core.scrub(body.summary),
                "evidence_ids": persona["evidence_ids"]}
    persona.update({k: override[k] for k in ("label", "summary")})
    persona["edited_by_you"] = True
    edits = {**current.get("persona_edits", {}), persona_id: override}
    return _update(run_id, changes={"personas": personas, "persona_edits": edits})


@router.post("/runs/{run_id}/split")
def split(run_id: str, body: SplitRequest) -> dict:
    current = _row(run_id)
    analysis = current.get("analysis")
    if not analysis or len(analysis["candidates"]) >= 4:
        raise HTTPException(400, "Split is available before four candidates exist.")
    candidate = next((c for c in analysis["candidates"] if c["id"] == body.candidate_id), None)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    evidence = {u["id"]: u for u in current["evidence"]}
    matching = [eid for eid in candidate["evidence_ids"] if body.phrase.lower() in evidence[eid]["text"].lower()]
    remaining = [eid for eid in candidate["evidence_ids"] if eid not in matching]
    if len(matching) < 2 or len(remaining) < 2:
        raise HTTPException(400, "The phrase must match at least two units and leave two others.")
    candidate["evidence_ids"] = remaining
    candidate["evidence_share"] = round(len(remaining) / len(evidence), 3)
    candidate["snippets"] = [{"text": " ".join(evidence[eid]["text"].split()[:25]), "evidence_ids": [eid]} for eid in remaining[:3]]
    candidate["summary"] = f"{len(remaining)} of {len(evidence)} evidence units discuss {candidate['label'].lower()}."
    original_pains = candidate.get("pain_claims", [])
    original_goals = candidate.get("goal_claims", [])
    original_tagged = {field: candidate.get(field, []) for field in ("trigger_claims", "objection_claims", "criterion_claims")}
    candidate["pain_claims"] = [item for item in original_pains if set(item["evidence_ids"]) <= set(remaining)]
    candidate["goal_claims"] = [item for item in original_goals if set(item["evidence_ids"]) <= set(remaining)]
    for field, items in original_tagged.items():
        candidate[field] = [item for item in items if set(item["evidence_ids"]) <= set(remaining)]
    new_id = f"segment-{uuid.uuid4().hex[:8]}"
    new_candidate = {**candidate, "id": new_id, "label": core.scrub(body.phrase).title() + " needs",
                     "evidence_ids": matching, "evidence_share": round(len(matching) / len(evidence), 3),
                     "snippets": [{"text": " ".join(evidence[eid]["text"].split()[:25]), "evidence_ids": [eid]} for eid in matching[:3]],
                     "summary": f"{len(matching)} of {len(evidence)} evidence units mention {core.scrub(body.phrase).lower()}.",
                     "pain_claims": [item for item in original_pains if set(item["evidence_ids"]) <= set(matching)],
                     "goal_claims": [item for item in original_goals if set(item["evidence_ids"]) <= set(matching)],
                     **{field: [item for item in items if set(item["evidence_ids"]) <= set(matching)]
                        for field, items in original_tagged.items()},
                     "edited_by_you": True}
    analysis["candidates"].append(new_candidate)
    return _update(run_id, changes={"analysis": analysis})


@router.get("/active")
def active() -> dict:
    return {"personas": get_active_personas()}


@router.get("/options")
def persona_options() -> dict:
    with db._connect() as conn:
        rows = conn.execute("SELECT id,name,document_json FROM persona_runs WHERE status='complete' ORDER BY updated_at DESC LIMIT 30").fetchall()
    return {"options": [{"runId": row["id"], "runName": row["name"], "personaId": p["id"], "label": p["label"],
                         "confidence": p["confidence"]} for row in rows for p in json.loads(row["document_json"]).get("personas", [])]}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    _row(run_id)
    with _lock:
        _cancelled.add(run_id)
    with db._connect() as conn:
        conn.execute("DELETE FROM persona_runs WHERE id=?", (run_id,))
    return {"deleted": True}
