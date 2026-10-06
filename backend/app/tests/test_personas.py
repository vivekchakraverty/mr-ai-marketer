"""Offline checks for the persona wizard, evidence and public-source boundaries."""
from __future__ import annotations

import copy
import json

import pytest

from app import config, db
from app.personas import collectors, core
from app.routers import personas


@pytest.fixture
def persona_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "personas.sqlite3")
    db.init_db()
    yield


def test_questions_are_versioned_and_required_core_is_validated():
    assert len(core.QUESTIONS) >= 20
    assert len({q["id"] for q in core.QUESTIONS}) == len(core.QUESTIONS)
    assert {q["type"] for q in core.QUESTIONS} <= {"text", "single", "multi", "number", "scale", "list"}
    assert "A1" in core.validate_answers({"A2": "B2B"})
    assert "A1" not in core.validate_answers({"A1": "Don't know", "A2": "B2B"})


def test_seed_plan_is_deterministic_and_bounded():
    answers = {"A1": "Bookkeeping software", "A4": ["Late invoices", "Slow reporting"],
               "C1": ["Spreadsheets", "DIY"], "C3": ["where is my cash"]}
    assert core.plan(answers) == core.plan(answers)
    plan = core.plan(answers)
    assert 20 <= len(plan["queries"]) <= 60
    assert all(len(q) <= 120 for q in plan["queries"])
    assert not any(source["enabled"] for source in plan["sources"] if source["requests"])
    excluded = core.plan({**answers, "E5": ["source: Bluesky", "topic: pricing"]}, "deep")
    assert next(source for source in excluded["sources"] if source["id"] == "bluesky")["excluded"]
    assert all("pricing" not in query.lower() for query in excluded["queries"])
    assert next(source for source in excluded["sources"] if source["id"] == "hacker_news")["requests"] == 8


def test_pii_scrub_and_near_duplicate_removal():
    text = "My name is Alice Smith. Email alice@example.com or call +1 555 123 4567; see https://example.com/profile/AliceSmith and @alice."
    clean = core.scrub(text)
    assert "Alice" not in clean and "alice@" not in clean and "555" not in clean
    assert "example.com" not in clean and "@alice" not in clean
    one = core.evidence_unit("r", "survey", "I need faster monthly invoice reporting for our shop")
    two = core.evidence_unit("r", "tickets", "I need faster monthly invoice reporting for our shops")
    assert len(core.dedupe([one, two])) == 1
    assert one["id"] == core.evidence_unit("r", "survey", "I need faster monthly invoice reporting for our shop")["id"]


def test_csv_mapping_imports_anonymized_evidence():
    csv_text = "origin,remark\nSurvey,John Smith needs better invoice reports each month\nSurvey,Email me at jane@example.com for help with reconciliation\n"
    units = core.parse_csv("run", csv_text, {"source": "origin", "text": "remark"})
    assert len(units) == 2
    assert all("John Smith" not in u["text"] and "jane@" not in u["text"] for u in units)
    with pytest.raises(ValueError):
        core.parse_csv("run", csv_text, {"text": "missing"})


def test_demo_analysis_claims_have_real_evidence_and_stable_segments():
    units = core.demo_units("demo")
    answers = {"D1": ["Busy owner — time back — 5", "Careful manager — reliable controls — 4"], "A2": "B2B"}
    first = core.analyze(units, answers)
    second = core.analyze(units, answers)
    assert 2 <= first["k"] <= 4
    assert [c["evidence_ids"] for c in first["candidates"]] == [c["evidence_ids"] for c in second["candidates"]]
    assert first["thin"]
    result = core.finalize(first, answers)
    assert len(result) == first["k"]
    core.assert_evidence(result, units)
    bad = copy.deepcopy(result)
    bad[0]["pains"] = [{"text": "Unsupported claim", "evidence_ids": [], "origin": "evidence"}]
    with pytest.raises(ValueError):
        core.assert_evidence(bad, units)
    with pytest.raises(ValueError, match="Fewer than five"):
        core.analyze([], answers)


class Response:
    def __init__(self, data, status=200, text="", content_type="application/json"):
        self.data, self.status_code, self.text = data, status, text
        self.headers = {"content-type": content_type}

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            err = __import__("requests").HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


class Session:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.replies)


def test_api_collectors_use_documented_endpoints_and_scrub(monkeypatch):
    monkeypatch.setattr(collectors, "_public_host", lambda _host: True)
    monkeypatch.setattr(collectors.time, "sleep", lambda _seconds: None)
    hn = Session([Response({"hits": [{"comment_text": "I need better monthly reports from my shop software and @person", "created_at": "2026-01-01"}]})])
    stack = Session([Response({"items": [{"title": "How can our shop reconcile invoices before each deadline?", "score": 2}]})])
    bsky = Session([Response({"posts": [{"record": {"text": "My shop needs better monthly reports before closing time"}, "likeCount": 3}]})])
    youtube = Session([Response({"items": [{"snippet": {"topLevelComment": {"snippet": {"textOriginal": "I compare monthly costs with time saved on invoices"}}}}]})])
    assert collectors.collect_hacker_news("r", ["bookkeeping"], session=hn)
    assert collectors.collect_stack_exchange("r", ["bookkeeping"], session=stack)
    assert collectors.collect_bluesky("r", ["bookkeeping"], session=bsky)
    assert collectors.collect_youtube("r", ["AbCdEf12345"], key="secret", session=youtube)
    assert "search_by_date" in hn.calls[0][0]
    assert "search/advanced" in stack.calls[0][0]
    assert "searchPosts" in bsky.calls[0][0]
    assert all(call[1]["headers"]["User-Agent"] == collectors.USER_AGENT for session in (hn, stack, bsky, youtube) for call in session.calls)


def test_robots_disallow_blocks_public_page(monkeypatch):
    monkeypatch.setattr(collectors, "_public_host", lambda _host: True)
    monkeypatch.setattr(collectors.time, "sleep", lambda _seconds: None)
    session = Session([Response({}, text="User-agent: *\nDisallow: /private")])
    with pytest.raises(PermissionError):
        collectors.collect_web("run", ["https://example.org/private"] , session=session)
    assert len(session.calls) == 1
    assert session.calls[0][0].endswith("/robots.txt")


def test_review_sites_are_refused_without_network():
    session = Session([])
    with pytest.raises(PermissionError):
        collectors.collect_web("run", ["https://www.trustpilot.com/review/shop"], session=session)
    assert not session.calls


def test_wikipedia_pageviews_are_trend_only(monkeypatch):
    monkeypatch.setattr(collectors, "_public_host", lambda _host: True)
    session = Session([Response({"items": [{"views": 10}] * 30 + [{"views": 20}] * 30})])
    signal = collectors.collect_wikipedia("Bookkeeping", session=session)
    assert signal["relative_change"] == 1.0
    assert "not buyer intent" in signal["note"]
    assert "pageviews/per-article" in session.calls[0][0]


def test_reddit_tumblr_are_explicit_stubs():
    with pytest.raises(NotImplementedError):
        collectors.collect_reddit()
    with pytest.raises(NotImplementedError):
        collectors.collect_tumblr()


def test_offline_wizard_resume_review_and_delete(persona_db):
    run = personas.create_run(personas.RunCreate(name="Demo", demo=True))
    run_id = run["id"]
    assert personas.get_run(run_id)["answers"]["A1"]
    planned = personas.generate_plan(run_id)
    assert planned["step"] == 2 and planned["plan"]["sources"][0]["enabled"]
    personas._update(run_id, status="queued", step=3)
    personas._run_collection(run_id, {"youtube_key": "", "mastodon_host": "", "mastodon_token": ""})
    collected = personas.get_run(run_id)
    assert collected["status"] == "review" and collected["analysis"]["sample_size"] == 12
    finished = personas.review(run_id, personas.ReviewRequest(candidates=collected["analysis"]["candidates"]))
    assert finished["status"] == "complete" and 2 <= len(finished["personas"]) <= 4
    assert personas.get_active_personas()
    edited = personas.edit_persona(run_id, finished["personas"][0]["id"],
                                   personas.PersonaEdit(label="Revised buyer", summary="A reviewed directional segment"))
    assert edited["personas"][0]["edited_by_you"]
    assert personas.get_run(run_id)["personas"][0]["label"] == "Revised buyer"
    assert personas.delete_run(run_id) == {"deleted": True}
    assert personas.get_active_personas() == []


def test_cancelled_run_never_publishes_partial_personas(persona_db):
    run = personas.create_run(personas.RunCreate(name="Cancelled", demo=True))
    personas.generate_plan(run["id"])
    personas._cancelled.add(run["id"])
    personas._run_collection(run["id"], {"youtube_key": "", "mastodon_host": "", "mastodon_token": ""})
    current = personas.get_run(run["id"])
    assert current["status"] == "cancelled" and not current["personas"]


def test_one_failed_source_does_not_stop_demo_collection(persona_db, monkeypatch):
    run = personas.create_run(personas.RunCreate(name="Mixed", demo=True))
    planned = personas.generate_plan(run["id"])
    plan = planned["plan"]
    for source in plan["sources"]:
        if source["id"] == "hacker_news":
            source["enabled"] = True
    personas._update(run["id"], changes={"plan": plan})
    monkeypatch.setattr(collectors, "collect_hacker_news", lambda *_args: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    personas._run_collection(run["id"], {"youtube_key": "", "mastodon_host": "", "mastodon_token": ""})
    result = personas.get_run(run["id"])
    assert result["status"] == "review"
    assert result["source_status"]["hacker_news"]["status"] == "error"
    assert result["source_status"]["demo"]["count"] == 12


def test_split_and_merge_keep_evidence_attached(persona_db):
    run = personas.create_run(personas.RunCreate(name="Review", demo=True))
    units = core.demo_units(run["id"])
    invoice = [u for u in units if "invoice" in u["text"].lower()]
    other = [u for u in units if u not in invoice]
    assert len(invoice) >= 2 and len(other) >= 2
    def candidate(identifier, batch):
        return {"id": identifier, "label": identifier, "summary": "Evidence group", "evidence_ids": [u["id"] for u in batch],
                "evidence_share": len(batch) / len(units), "top_pains": [], "top_goals": [], "pain_claims": [], "goal_claims": [],
                "snippets": [], "channels": [], "confidence": "Low — hypothesis",
                "review": {"rings_true": 3, "business_value": 3, "priority": 3, "missing": ""}}
    first = candidate("segment-1", units[:9])
    second = candidate("segment-2", units[9:])
    analysis = {"candidates": [first, second], "source_count": 3, "sample_size": 12, "method": "test", "k": 2,
                "k_reason": "test", "thin": True, "limitations": [], "tag_counts": {}}
    personas._update(run["id"], status="review", step=4, changes={"evidence": units, "analysis": analysis})
    # Use a phrase that cleanly separates at least two units from the first segment.
    first_ids = set(first["evidence_ids"])
    phrase = next((term for term in ("invoice", "cash", "shop", "records")
                   if 2 <= sum(term in u["text"].lower() for u in units if u["id"] in first_ids) <= 7), None)
    assert phrase
    split = personas.split(run["id"], personas.SplitRequest(candidate_id="segment-1", phrase=phrase))
    candidates = split["analysis"]["candidates"]
    assert len(candidates) == 3
    candidates[-1]["review"]["merge_with"] = "segment-2"
    finished = personas.review(run["id"], personas.ReviewRequest(candidates=candidates))
    assert len(finished["personas"]) == 2
    assert set().union(*(set(p["evidence_ids"]) for p in finished["personas"])) == {u["id"] for u in units}
