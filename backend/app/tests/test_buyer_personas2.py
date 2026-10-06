"""Evidence boundaries, Align adapter, caching and persisted job lifecycle."""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app import config, db
from app.services import buyer_personas2 as service
from app.services import align_saved_reports
from app.services.buyer_personas2_models import Evidence, Persona, Synthesis


@pytest.fixture(autouse=True)
def model_routes(monkeypatch):
    monkeypatch.setattr(service, "_route_cooldowns", {})
    monkeypatch.setattr(service, "MODEL", service.DEFAULT_MODEL)
    monkeypatch.setattr(service, "PROVIDER", "nscale")
    monkeypatch.delenv("HF_PERSONA_FALLBACK_ROUTES", raising=False)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "buyer-personas-2.sqlite3")
    db.init_db()
    align_saved_reports.initialize()
    service.initialize()


def test_game_audience_adapter_keeps_cluster_trace_and_predicted_status(database):
    with db._connect() as conn:
        conn.execute("""CREATE TABLE game_analysis_jobs (
          id TEXT, filename TEXT, updated_at TEXT, status TEXT, report_json TEXT)""")
        report = {"description": "Tactical puzzle game", "profile": {"genre": "Strategy"},
                  "audience": {"archetypes": [{"archetype": "Planning player", "reason": "Plans moves ahead",
                                               "affinity": 82, "confidence": 0.7, "evidence_timestamps": [12]}]},
                  "platforms": {"discovery": [{"name": "YouTube", "score": 72, "kind": "predicted_discovery_fit", "reasons": ["Gameplay explanation"]}]},
                  "comparables": [{"name": "Similar Game", "similarity": 54}]}
        conn.execute("INSERT INTO game_analysis_jobs VALUES (?,?,?,?,?)", ("g1", "sample.mp4", service.now(), "completed", json.dumps(report)))
    mapping = service.context("game:g1", {"value_proposition": "Strategic depth"})
    assert mapping["clusters"][0]["id"] == "game:g1:0"
    assert mapping["clusters"][0]["timestamps"] == [12]
    assert "Inferred" in mapping["clusters"][0]["basis"]
    assert mapping["platforms"][0]["kind"] == "predicted_discovery_fit"
    assert mapping["project"]["value_proposition"] == "Strategic depth"
    assert mapping["project_origin"]["value_proposition"] == "project"
    assert mapping["project_origin"]["product_description"] == "align"


def test_game_profile_signals_are_available_as_traceable_evidence(database):
    with db._connect() as conn:
        conn.execute("CREATE TABLE game_analysis_jobs (id TEXT, filename TEXT, updated_at TEXT, status TEXT, report_json TEXT)")
        report = {"profile": {"genres": ["Puzzle"], "core_loop": ["Plan a route"],
                              "observed_actions": ["Player rearranges tiles"],
                              "inferred_mechanics": [{"name": "Timed planning", "evidence_timestamps": [24]}]},
                  "audience": {"archetypes": [{"archetype": "Planners", "reason": "Repeated routing", "affinity": 80}]}}
        conn.execute("INSERT INTO game_analysis_jobs VALUES (?,?,?,?,?)", ("g2", "demo.mp4", service.now(), "completed", json.dumps(report)))
    mapping = service.context("game:g2")
    claims = [item.claim for item in service._base_evidence(mapping)]
    assert mapping["project"]["genre"] == "Puzzle"
    assert any("Plan a route" in claim for claim in claims)
    assert any("Timed planning" in claim for claim in claims)


def test_completed_questionnaire_answers_and_first_party_feedback_feed_synthesis(database):
    doc = {"answers": {"A1": "A cooperative puzzle game", "B3": ["Looks too hard", "Will friends play?"],
                       "D3": ["35-55"], "E2": ["secret credential"],
                       "E5": ["source: spam.example", "topic: medical claims"]},
           "personas": [{"id": "c1", "label": "Cooperative players", "summary": "Solve with friends", "score": 0.82,
                         "evidence_ids": ["unit1"], "pains": [{"text": "Hard to schedule friends", "origin": "evidence"}]}],
           "evidence": [{"id": "unit1", "source_type": "first_party", "source": "Survey",
                         "text": "I need short sessions that fit my friends' schedules"}]}
    with db._connect() as conn:
        conn.execute("INSERT INTO persona_runs VALUES (?,?,?,?,?,?,?)",
                     ("r1", "Puzzle game", 5, "complete", json.dumps(doc), service.now(), service.now()))
    mapping = service.context("persona:r1")
    evidence = service._base_evidence(mapping)
    assert mapping["clusters"][0]["strength"] == 82
    assert mapping["exclusions"]["sources"] == ["spam.example"]
    assert any(item["id"] == "project:answer:B3" for item in mapping["project_details"])
    assert not any(item["id"] == "project:answer:E2" for item in mapping["project_details"])
    assert any(item.type == "project" and "short sessions" in item.claim for item in evidence)
    assert any(item.type == "align" and "Hard to schedule" in item.claim for item in evidence)


def test_writing_fingerprint_reader_signals_are_used(database):
    fp = {"reader": {"motivation": ["thinking", "escape"], "format_habits": ["audiobook"]},
          "feeling_after": [{"tag": "hopeful", "w": 1}], "themes_message": {"tags": ["friendship"]}}
    with db._connect() as conn:
        conn.execute("INSERT INTO book_profile(title,blurb,subgenres,themes,tropes,tone,comps,audience_notes,updated_at,fingerprint_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("A Novel", "A long journey", "[]", "[]", "[]", "warm", "[]", "", service.now(), json.dumps(fp)))
        book_id = conn.execute("SELECT id FROM book_profile").fetchone()["id"]
    mapping = service.context(f"writing:{book_id}")
    assert "thinking" in mapping["clusters"][0]["reason"]
    assert any("audiobook" in item["claim"] for item in mapping["audience_signals"])
    assert any("friendship" in item["claim"] for item in mapping["audience_signals"])


@pytest.mark.parametrize("interruption", [False, True])
def test_four_clusters_generate_in_bounded_calls_and_cache(database, monkeypatch, interruption):
    import httpx
    import huggingface_hub
    clusters = [{"id": f"game:g:{n}", "label": f"Group {n}", "reason": "Observed play",
                 "basis": "Inferred", "strength": 70, "confidence": 0.7} for n in range(4)]
    signals = [{"id": f"game:g:signal:{n}", "claim": f"Signal {n}", "basis": "Observed"} for n in range(4)]
    mapping = {"source_key": "game:g", "project": {"product_description": "Puzzle game"},
               "project_origin": {"product_description": "project"}, "clusters": clusters,
               "audience_signals": signals, "platforms": [], "affinities": []}
    evidence = service._base_evidence(mapping)
    calls = []
    outage = {"active": interruption}
    def persona(n):
        return {"id": "model-id", "name": f"Player {n}", "archetype": f"Group {n}", "description": "Enjoys puzzles",
                "cluster_ids": [clusters[n]["id"]], "evidence_ids": [signals[n]["id"]], "snapshot": {"archetype": f"Group {n}"},
                "jobs_to_be_done": {"functional": ["Solve puzzle"], "emotional": ["Feel clever"], "social": ["Share a solution"]},
                "motivations": [{"name": "Mastery", "rank": 1, "why": "Solve"}, {"name": "Discovery", "rank": 2, "why": "Explore"}],
                "pain_points": [{"text": "Confusing clues", "importance": "High"}, {"text": "Slow setup", "importance": "Medium"}],
                "adoption_triggers": {key: ["Relevant proof"] for key in ("investigate", "try_it", "purchase", "recommend", "return_to_it")},
                "objections": [{"objection": "Too difficult", "response": "Show adjustable difficulty"}],
                "discovery_journey": [{"stage": stage, "touchpoints": ["Video"], "questions": ["Is it fun?"],
                                       "content": ["Demo"], "channels": ["Video"], "proof": "Gameplay", "friction": "Uncertainty"}
                                      for stage in ("Awareness", "Interest", "Evaluation", "Conversion", "Retention", "Advocacy")],
                "channels": [], "content_preferences": {"types": ["Video"], "length": "Short", "hooks": ["Puzzle"],
                    "tone": "Clear", "topics": ["Mechanics"], "visual_style": "Gameplay", "detail_level": "Detailed",
                    "proof": "Demo", "trusted_sources": [], "conversion_examples": ["Demo clip", "Puzzle walkthrough", "Review"]},
                "messaging": {"core_message": "Solve puzzles", "value_proposition": "Clever puzzles", "pillars": ["Depth", "Clarity", "Fun"],
                    "resonant_words": ["Solve"], "avoid_words": ["Easy"], "hooks": ["Try this", "Find a path", "Think ahead", "Play together"]},
                "affinities": [], "reach": [], "strategic_recommendations": ["Test a demo clip"]}
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "https://router.huggingface.co/v1"

        def chat_completion(self, **kwargs):
            model, provider = kwargs["model"].rsplit(":", 1)
            calls.append((provider, model, kwargs["max_tokens"]))
            if provider == "nscale":
                raise _http_error(500)
            if outage["active"] and "Generate only the journey section" in kwargs["messages"][1]["content"]:
                raise httpx.ReadTimeout("Provider stopped responding")
            brief = json.loads(kwargs["messages"][1]["content"].split("\nEvidence:\n", 1)[1])
            index = int(brief["target_cluster"]["label"].rsplit(" ", 1)[1])
            shape = json.loads(kwargs["messages"][1]["content"].split("\nSection schema:\n", 1)[1].split("\nEvidence:\n", 1)[0])
            result = {key: value for key, value in persona(index).items() if key in shape}
            if "evidence_ids" in result:
                result["evidence_ids"] = [brief["align_signals"][index]["id"]]
            assert kwargs["response_format"]["type"] == "json_schema"
            return _completion(json.dumps(result))

    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    if interruption:
        with pytest.raises(service.PersonaModelError, match="journey.*response timed out"):
            service._synthesize(mapping, evidence)
        with db._connect() as conn:
            assert conn.execute("SELECT count(*) FROM buyer_persona2_synthesis_cache").fetchone()[0] == 1
        outage["active"] = False
    report, usage = service._synthesize(mapping, evidence)
    expected_providers = (["nscale", "featherless-ai", "featherless-ai", "deepinfra", "nscale"] +
                          ["featherless-ai"] * 11 if interruption else ["nscale"] + ["featherless-ai"] * 12)
    assert [call[0] for call in calls] == expected_providers
    assert all(call[2] == 1800 for call in calls)
    if interruption:
        assert usage["persona_1"]["profile"]["cached"]
    assert len(report.personas) == 4
    assert {item.id for item in report.personas} == {"p1", "p2", "p3", "p4"}
    assert all(item.signal_ids for item in report.personas)
    assert set(usage) == {"persona_1", "persona_2", "persona_3", "persona_4"}
    _, cached_usage = service._synthesize(mapping, evidence)
    assert len(calls) == len(expected_providers)
    assert all(value["cached"] for value in cached_usage.values())
    assert service._generation_routes(cached_usage) == [(service.MODEL, "featherless-ai")]

    # Complete the persisted job through the real report assembly using cached personas.
    with db._connect() as conn:
        conn.execute("INSERT INTO buyer_persona2_reports(id,source_key,input_hash,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     ("recovered", "game:g", "hash", "queued", service.now(), service.now()))
    monkeypatch.setattr(service, "research", lambda *_args, **_kwargs: ([], [], service.now()))
    service._run("recovered", mapping, False)
    saved = service.get("recovered")
    assert saved["status"] == "complete", saved["error"]
    assert saved["report"]["model"] == f"{service.MODEL} (featherless-ai)"
    assert any("alternate hosted" in warning for warning in saved["report"]["warnings"])
    assert len(calls) == len(expected_providers)


def test_scoring_and_evidence_references_are_deterministic(database):
    persona = Persona.model_validate({
        "id": "x", "name": "Planning player", "archetype": "Planning player", "description": "Seeks tactical choices",
        "snapshot": {}, "jobs_to_be_done": {"functional": ["Plan moves"], "emotional": ["Feel capable"], "social": []},
        "motivations": [{"name": "Mastery", "rank": 1, "why": "Tactical choices"}], "pain_points": [],
        "adoption_triggers": {"investigate": [], "try_it": [], "purchase": ["Demo"], "recommend": [], "return_to_it": ["New challenge"]}, "objections": [],
        "discovery_journey": [], "channels": [{"name": "YouTube", "affinity": "High", "why": "Gameplay proof"}],
        "content_preferences": {"types": ["Video"], "length": "Short", "hooks": [], "tone": "Clear", "topics": [],
                                "visual_style": "Gameplay", "detail_level": "High", "proof": "Demo", "trusted_sources": [],
                                "conversion_examples": ["Mechanics clip"]},
        "messaging": {"core_message": "Plan better", "value_proposition": "Tactical depth", "pillars": ["Depth"],
                      "resonant_words": [], "avoid_words": [], "hooks": ["Choose your move"]},
        "affinities": [], "reach": [], "evidence_ids": ["web:1"], "strategic_recommendations": [],
        "cluster_ids": ["game:g1:0"]})
    mapping = {"project": {"product_description": "Tactical game", "genre": "Strategy"},
               "clusters": [{"id": "game:g1:0", "strength": 82}]}
    evidence = [Evidence(id="web:1", type="external", claim="Tactical players compare mechanics")]
    service._score(persona, mapping, evidence)
    assert persona.score_components["audience_mapping"] == 82
    assert persona.score_components["external_evidence"] == 33
    assert persona.confidence == "Medium"
    assert 0 < persona.relevance_score < 100
    synthesis = Synthesis.model_validate({"personas": [persona.model_dump()],
        "strategy": {key: [] for key in ("audience_priorities", "positioning", "product", "content", "channels",
                                            "community", "launch", "risks")} | {"experiments": ["Test a demo"]},
        "conflicts": [], "warnings": []})
    errors = service._completeness_errors(synthesis, mapping, {"web:1"})
    assert any("six discovery stages" in error for error in errors)
    assert any("three concrete experiments" in error for error in errors)
    mapping["clusters"][0]["confidence"] = "Low"
    service._score(persona, mapping, evidence)
    assert persona.confidence == "Low"


def test_research_cache_deduplicates_queries_and_preserves_retrieval_date(database, monkeypatch):
    monkeypatch.setattr(service, "_search", lambda query: [("https://example.org/report", "Research", "Search snippet")])
    monkeypatch.setattr(service, "_page", lambda url: ("Verified article text about strategy game buyers and their decisions. " * 3, "2026-01-01"))
    mapping = {"project": {"genre": "Strategy game"}, "clusters": [], "source_key": ""}
    first, warnings, researched_at = service.research(mapping)
    assert len(first) == 1 and not warnings
    assert first[0].claim.startswith("Verified article text")
    assert first[0].published_at == "2026-01-01"
    monkeypatch.setattr(service, "_search", lambda query: pytest.fail("cache was missed"))
    second, _, cached_date = service.research(mapping)
    assert len(second) == 1
    assert cached_date and researched_at


def test_research_respects_saved_topic_and_source_exclusions(database, monkeypatch):
    searches = []
    fetched = []
    def search(query):
        searches.append(query)
        return [("https://spam.example/article", "Spam", ""), ("https://valid.example/article", "Study", "")]
    def page(url):
        fetched.append(url)
        return ("Verified article about puzzle game customers and their decisions. " * 3, "")
    monkeypatch.setattr(service, "_search", search)
    monkeypatch.setattr(service, "_page", page)
    mapping = {"project": {"genre": "Puzzle game"}, "clusters": [], "source_key": "",
               "exclusions": {"topics": ["communities discovery"], "sources": ["spam.example"]}}
    service.research(mapping)
    assert searches and all("communities discovery" not in query for query in searches)
    assert fetched and all("spam.example" not in url for url in fetched)
    with db._connect() as conn:
        for query in searches:
            conn.execute("UPDATE buyer_persona2_research_cache SET evidence_json=? WHERE query=?",
                         (json.dumps([Evidence(id="web:blocked", type="external", claim="Spam",
                                               source_url="https://spam.example/article").model_dump()]), query))
    monkeypatch.setattr(service, "_search", lambda query: pytest.fail("cache was missed"))
    cached, _, _ = service.research(mapping)
    assert cached == []


def test_conflict_requires_explicit_nonoverlapping_age_ranges():
    mapping = {"project": {"target_market": "Adults 35-55"}, "project_details": [],
               "clusters": [{"id": "align:1", "label": "Young players", "reason": "Most likely 18-34"}]}
    assert "align:1" in service._verified_conflicts(mapping)[0]
    mapping["clusters"][0]["reason"] = "Most likely 25-44"
    assert service._verified_conflicts(mapping) == []


def test_recent_job_and_last_complete_report_are_separate(database):
    with db._connect() as conn:
        conn.execute("INSERT INTO buyer_persona2_reports(id,source_key,input_hash,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     ("old", "game:g1", "hash-old", "complete", "2026-01-01", "2026-01-01"))
        conn.execute("INSERT INTO buyer_persona2_reports(id,source_key,input_hash,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     ("new", "game:g1", "hash-new", "researching", "2026-01-02", "2026-01-02"))
    assert service.recent("game:g1")["id"] == "new"
    assert service.latest("game:g1")["id"] == "old"


def test_report_contract_rejects_malformed_model_output():
    with pytest.raises(ValueError):
        Synthesis.model_validate({"personas": [{"name": "Unstructured guess"}], "strategy": {}, "conflicts": [], "warnings": []})


def test_saved_music_and_art_feed_the_adapter_without_media(database):
    music = align_saved_reports.save("music", "Demo song", {
        "analysis": {"tempoBpm": 122},
        "audience_context": {"style": "dream pop", "location": "Kolkata", "goal": "listeners", "stage": "released"},
        "audience_report": {"reference_artists": [{"name": "Reference Act", "musicbrainz_url": "https://musicbrainz.org/artist/example"}],
                            "adjacent_artists": [{"name": "Adjacent Act", "musicbrainz_url": "https://musicbrainz.org/artist/other"}]},
        "destinations": [{"name": "Bandcamp", "priority": 84, "reason": "Artist-selected route"}],
    })
    art = align_saved_reports.save("visual_art", "Demo art", {
        "labels": {"group": "landscape", "style": "watercolor", "description": "A mountain scene"},
        "report": {"pamela_evidence": [{"name": "landscape", "admirer_participants": 4, "eligible_participants": 20}],
                   "sources": {"pamela_dataset": "https://huggingface.co/datasets/bethgelab/PAMELA"},
                   "artsy_category_links": {"Landscape": "https://www.artsy.net/genre/landscape"}},
    })
    assert {item["key"] for item in service.sources()} >= {f"music:{music['id']}", f"visual_art:{art['id']}"}
    music_context = service.context(f"music:{music['id']}")
    assert music_context["clusters"][0]["id"].startswith("music:")
    assert music_context["affinities"][0]["name"] == "Adjacent Act"
    assert music_context["source_evidence"][0]["source_url"].startswith("https://musicbrainz.org/")
    art_context = service.context(f"visual_art:{art['id']}")
    assert art_context["clusters"][0]["strength"] <= 35
    assert "other AI-generated images" in art_context["clusters"][0]["basis"]
    assert art_context["project_origin"]["product_description"] == "align"
    assert "image" not in art["document"] and "audio" not in music["document"]


def test_hugging_face_usage_is_serialized_without_a_pydantic_response(monkeypatch):
    from huggingface_hub import InferenceClient

    @dataclass
    class Usage:
        prompt_tokens: int = 12
        completion_tokens: int = 7
        total_tokens: int = 19

    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setattr(InferenceClient, "chat_completion", lambda *args, **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], usage=Usage()))
    result, usage = service._model_call("system", "prompt")
    assert result == {"ok": True}
    assert usage["total_tokens"] == 19
    assert usage["model"] == service.MODEL
    assert usage["provider"] == "nscale"


def _http_error(status):
    import httpx
    from huggingface_hub.utils import HfHubHTTPError

    response = httpx.Response(status, json={"error": "Provider failed; hf-test must never be exposed"},
                              request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions"))
    return HfHubHTTPError(f"HTTP {status}", response=response)


def _completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


def test_http_500_and_unavailable_alternative_use_another_model(monkeypatch):
    import huggingface_hub

    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, **kwargs):
            model, provider = kwargs["model"].rsplit(":", 1)
            calls.append((model, provider))
            if provider == "nscale":
                raise _http_error(500)
            if provider == "featherless-ai":
                raise ValueError("Model is unavailable")
            return _completion('{"ok":true}')

    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    result, usage = service._model_call("system", "prompt")
    assert result == {"ok": True}
    assert calls == service._model_routes()
    assert usage["model"] == "Qwen/Qwen3.5-9B"
    assert usage["provider"] == "deepinfra"
    assert usage["attempts"][0]["status"] == 500
    service._model_call("system", "another persona")
    assert calls == service._model_routes() + [("Qwen/Qwen3.5-9B", "deepinfra")]


@pytest.mark.parametrize("failure", ["500", "timeout", "invalid_json"])
def test_all_routes_failing_has_bounded_attempts_and_safe_error(monkeypatch, failure):
    import httpx
    import huggingface_hub

    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, **kwargs):
            calls.append(kwargs["model"].rsplit(":", 1)[1])
            if failure == "500":
                raise _http_error(500)
            if failure == "timeout":
                raise httpx.ReadTimeout("hf-test; private prompt")
            return _completion("<think>hidden reasoning</think>incomplete JSON")

    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    expected = ValueError if failure == "invalid_json" else service.PersonaModelError
    with pytest.raises(expected) as failure_info:
        service._model_call("system", "private prompt")
    assert len(calls) == 3
    assert "hf-test" not in str(failure_info.value)
    assert "private prompt" not in str(failure_info.value)
    if failure != "invalid_json":
        assert len(failure_info.value.attempts) == 3
    # A new manual retry remains possible even during the outage cooldown.
    with pytest.raises(expected):
        service._model_call("system", "private prompt")
    assert len(calls) == 6


@pytest.mark.parametrize("status", [401, 403, 402])
def test_access_and_billing_failures_do_not_try_other_providers(monkeypatch, status):
    import huggingface_hub

    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, **kwargs):
            calls.append(kwargs["model"].rsplit(":", 1)[1])
            raise _http_error(status)

    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    with pytest.raises(service.PersonaModelError) as failure:
        service._model_call("system", "prompt")
    assert calls == ["nscale"]
    assert "billing" in str(failure.value) if status == 402 else "permission" in str(failure.value)


def test_fallback_configuration_is_deduplicated_and_bounded(monkeypatch):
    monkeypatch.setenv("HF_PERSONA_FALLBACK_ROUTES", "")
    assert service._model_routes() == [(service.MODEL, "nscale")]
    monkeypatch.setenv("HF_PERSONA_FALLBACK_ROUTES",
                       f"{service.MODEL}:nscale,org/a:deepinfra,invalid,org/b:auto,org/c:auto,org/d:auto")
    assert service._model_routes() == [(service.MODEL, "nscale"), ("org/a", "deepinfra"),
                                       ("org/b", "auto"), ("org/c", "auto")]


@pytest.mark.parametrize("provider", ["nscale", "auto"])
def test_installed_sdk_uses_server_router_without_provider_mapping_lookup(monkeypatch, provider):
    from huggingface_hub import InferenceClient
    from huggingface_hub.inference._providers import _common

    def mapping_lookup(*_args, **_kwargs):
        pytest.fail("Server-side routing must not fetch a provider mapping")

    def post(_self, request, *, stream=False):
        assert not stream
        assert request.url == "https://router.huggingface.co/v1/chat/completions"
        assert request.json["model"] == (service.MODEL if provider == "auto" else service.MODEL + ":nscale")
        assert request.json["max_tokens"] == 1800
        assert request.json["chat_template_kwargs"] == {"enable_thinking": False}
        return json.dumps({"id": "test", "created": 0, "model": service.MODEL,
                           "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok":true}'},
                                        "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}}).encode()

    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setattr(service, "PROVIDER", provider)
    monkeypatch.setattr(_common, "_fetch_inference_provider_mapping", mapping_lookup)
    monkeypatch.setattr(InferenceClient, "_inner_post", post)
    result, usage = service._model_call("system", "JSON prompt")
    assert result == {"ok": True}
    assert usage["total_tokens"] == 19
    assert usage["provider"] == provider


def test_section_validation_error_names_fields_and_repairs_with_another_model(database, monkeypatch):
    calls = []
    def call(_system, _prompt, **kwargs):
        calls.append(kwargs)
        return {"discovery_journey": [], "channels": [], "reach": []}, {"model": service.MODEL, "provider": "nscale"}
    monkeypatch.setattr(service, "_model_call", call)
    with pytest.raises(service.PersonaSectionError, match="journey.*six discovery stages"):
        service._generate_section("journey", "system", "prompt", service._journey_errors)
    assert len(calls) == 2
    assert calls[0]["schema"]["properties"]["discovery_journey"]["minItems"] == 6
    assert calls[1]["avoid_model"] == service.MODEL


def test_schema_incompatibility_retries_json_mode_on_same_route(monkeypatch):
    import httpx
    import huggingface_hub
    from huggingface_hub.utils import HfHubHTTPError
    calls = []
    class Client:
        def __init__(self, **_kwargs): pass
        def chat_completion(self, **kwargs):
            calls.append(kwargs)
            if kwargs["response_format"]["type"] == "json_schema":
                raise HfHubHTTPError("unsupported schema", response=httpx.Response(400,
                    json={"error": "response_format json_schema is not supported"},
                    request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions")))
            return _completion('{"ok":true}')
    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    result, usage = service._model_call("system", "JSON prompt", schema={"type": "object"})
    assert result == {"ok": True}
    assert [item["response_format"]["type"] for item in calls] == ["json_schema", "json_object"]
    assert usage["provider"] == "nscale"


def test_model_check_requires_explicit_consent_before_submitting(database, monkeypatch):
    calls = []
    monkeypatch.setattr(service._pool, "submit", lambda *args: calls.append(args))
    monkeypatch.setattr(service, "_running", set())
    with pytest.raises(ValueError, match="Allow"):
        service.submit_model_check(False)
    assert not calls
    check = service.submit_model_check(True)
    assert check["status"] == "queued"
    assert calls[0] == (service._run_model_check, check["id"])
    with pytest.raises(RuntimeError, match="already running"):
        service.submit_model_check(True)


def test_model_check_uses_only_fixed_synthetic_inputs_and_saves_result(database, monkeypatch):
    def forbidden(*_args, **_kwargs): pytest.fail("A synthetic test must not load saved sources or run public research")
    monkeypatch.setattr(service, "context", forbidden)
    monkeypatch.setattr(service, "research", forbidden)
    def synthesize(mapping, evidence, **kwargs):
        assert kwargs["use_cache"] is False
        assert mapping == service._synthetic_music_mapping()
        assert len(mapping["clusters"]) == 2
        assert all("diagnostic:" in item["id"] for item in mapping["audience_signals"])
        assert not any(item.source_url for item in evidence)
        return SimpleNamespace(personas=["one", "two"]), {"persona_1": {"profile": {"initial": {
            "model": service.MODEL, "provider": "nscale"}}}}
    monkeypatch.setattr(service, "_synthesize", synthesize)
    with db._connect() as conn:
        conn.execute("INSERT INTO buyer_persona2_model_checks(id,status,created_at,updated_at) VALUES (?,?,?,?)",
                     ("check", "queued", service.now(), service.now()))
    service._run_model_check("check")
    check = service.get_model_check("check")
    assert check["status"] == "complete"
    assert check["result"]["persona_count"] == 2
    assert check["result"]["models"] == [f"{service.MODEL} (nscale)"]
    assert "No saved song data" in check["result"]["message"]


def test_background_error_preserves_section_and_validation_fields(database, monkeypatch):
    with db._connect() as conn:
        conn.execute("INSERT INTO buyer_persona2_reports(id,source_key,input_hash,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     ("bad", "music:demo", "hash", "queued", service.now(), service.now()))
    monkeypatch.setattr(service, "research", lambda *_args, **_kwargs: ([], [], service.now()))
    def invalid(*_args): raise service.PersonaSectionError("profile", "snapshot.archetype:missing")
    monkeypatch.setattr(service, "_synthesize", invalid)
    service._run("bad", service._synthetic_music_mapping(), False)
    error = service.get("bad")
    assert error["status"] == "error"
    assert "profile" in error["error"] and "snapshot.archetype" in error["error"]
