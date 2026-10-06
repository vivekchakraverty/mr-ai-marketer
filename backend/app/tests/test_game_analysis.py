"""The expensive providers are mocked; media decoding uses a tiny real FFmpeg fixture."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.game_analysis import igdb, jobs, modal_runtime, observer, report, video
from app.routers import game_analysis as game_router


@pytest.fixture()
def clip(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is unavailable")
    path = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=5",
                    "-t", "2", "-an", "-c:v", "mpeg4", str(path)], check=True, timeout=30)
    return path


def test_video_probe_and_sampling_with_no_audio(clip, monkeypatch):
    info = video.probe(clip)
    assert info["duration"] == 2
    assert info["has_audio"] is False
    assert info["width"] == 320 and info["fps"] == 5
    assert len(video.sample(clip, 0, 2, 2)) <= 4
    monkeypatch.setattr(video.config, "MAX_DURATION", 1)
    with pytest.raises(video.InvalidVideo, match="no longer"):
        video.probe(clip)


def test_invalid_video_and_chunk_overlap(tmp_path, monkeypatch):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    with pytest.raises(video.InvalidVideo):
        video.probe(path)
    monkeypatch.setattr(video.config, "CHUNK_SECONDS", 30)
    monkeypatch.setattr(video.config, "CHUNK_OVERLAP", 2)
    assert video.chunks(65) == [(0.0, 30.0), (28.0, 58.0), (56.0, 65)]
    assert video.chunks(35, 2) == [(0.0, 16.0), (14.0, 30.0), (28.0, 35)]


def _chunk(start, end, pace="slow"):
    return {"start": start, "end": end, "description": "The player adjusts a visible resource panel.",
            "observations": [{"timestamp": start + 1, "observation": "Resource panel opened", "confidence": .9}],
            "events": [{"start": start + 1, "end": start + 2, "type": "management", "description": "Panel opened", "confidence": .9}],
            "observed_actions": ["open panel"], "inferred_mechanics": [{"name": "resource management", "confidence": .8,
                                                                     "evidence_timestamps": [start + 1]}],
            "genres": ["Simulation"], "subgenres": [], "themes": ["Sandbox"], "keywords": ["resources"],
            "player_perspective": ["Bird view"], "game_modes": [], "core_loop": ["inspect", "adjust"],
            "uncertainties": [], "pace": pace, "complexity": "high", "dimensions": {"management_depth": 80}}


def test_qwen_output_validation_and_grounded_merge(monkeypatch):
    response = _chunk(0, 30)
    response["inferred_mechanics"].append({"name": "multiplayer", "confidence": .99, "evidence_timestamps": [999]})
    monkeypatch.setattr(observer.modal_runtime, "observe", lambda *_: json.dumps(response))
    part = observer.observe_chunk([(1, b"fake")], 0, 30)
    assert [m["name"] for m in part["inferred_mechanics"]] == ["resource management"]
    another = _chunk(0, 30, "fast")
    merged = observer.merge([part, another])
    assert len(merged["events"]) == 1
    assert any("pace" in note for note in merged["uncertainties"])
    assert merged["claims"][0]["evidence"][0]["timestamp"] == 1
    monkeypatch.setattr(observer.modal_runtime, "observe", lambda *_: "{bad}")
    with pytest.raises(ValueError, match="malformed"):
        observer.observe_chunk([], 0, 30)
    monkeypatch.setattr(observer.modal_runtime, "observe", lambda *_: '{"observations":null,"events":null,"inferred_mechanics":null}')
    partial = observer.observe_chunk([], 0, 30)
    assert partial["observations"] == [] and partial["genres"] == []


def test_igdb_oauth_cache_query_and_rate_limit(monkeypatch):
    monkeypatch.setenv("IGDB_CLIENT_ID", "client")
    monkeypatch.setenv("IGDB_CLIENT_SECRET", "secret")
    monkeypatch.setattr(igdb, "_token", "")
    monkeypatch.setattr(igdb, "_expiry", 0)
    calls = []

    class Response:
        status_code = 200
        def __init__(self, body): self.body = body
        def raise_for_status(self): pass
        def json(self): return self.body

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"access_token": "token", "expires_in": 3600}) if "twitch" in url else Response([{"id": 1}])

    monkeypatch.setattr(igdb.httpx, "post", fake_post)
    assert igdb.query("games", "fields name; limit 1;") == [{"id": 1}]
    assert igdb.query("games", "fields name; limit 1;") == [{"id": 1}]
    assert len([url for url, _ in calls if "twitch" in url]) == 1
    assert calls[1][1]["headers"]["Authorization"] == "Bearer token"
    monkeypatch.setattr(igdb.httpx, "post", lambda *_a, **_kw: type("Limited", (), {"status_code": 429})())
    with pytest.raises(igdb.IGDBError, match="rate limit"):
        igdb.query("games", "fields name;")


def test_structural_ranking_ignores_rating_and_platforms_explain_confidence():
    profile = observer.merge([_chunk(0, 30)])
    profile["audience_archetypes"] = []
    similar = {"id": 1, "name": "Similar", "genres": [{"name": "Simulation"}],
               "themes": [{"name": "Sandbox"}], "keywords": [{"name": "resources"}],
               "player_perspectives": [{"name": "Bird view"}], "platforms": [{"name": "PC (Microsoft Windows)"}],
               "summary": "Inspect and adjust resources", "rating": 30, "rating_count": 200}
    popular = {"id": 2, "name": "Popular", "genres": [{"name": "Action"}],
               "themes": [{"name": "War"}], "keywords": [{"name": "shooting"}],
               "player_perspectives": [{"name": "First person"}], "platforms": [{"name": "PlayStation 5"}],
               "summary": "Shooter", "rating": 99, "rating_count": 10000}
    ranked = report.rank(profile, [popular, similar])
    assert ranked[0]["name"] == "Similar"
    assert ranked[0]["rating"] == 30
    recs = report.platform_recommendations(profile, ranked)
    pc = next(item for item in recs["hardware"] if item["name"] == "Windows PC")
    assert pc["comparable_presence"] == 1
    assert pc["reasons"] and 0 <= pc["confidence"] <= 1
    assert recs["distribution"][0]["publishing_feasibility"] == "Not assessed"
    assert "not sales" in recs["caveat"]


def test_upload_validates_extension_mime_size_and_deletes_temp_file(tmp_path, monkeypatch):
    from app import config as app_config
    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(game_router.config, "MAX_BYTES", 5)
    captured = []

    def fake_submit(path, filename, digest, fps):
        captured.append((path, filename, digest, fps))
        assert path.exists()
        return "job123", False

    monkeypatch.setattr(game_router.jobs, "submit", fake_submit)
    app = FastAPI()
    app.include_router(game_router.router)
    client = TestClient(app)
    assert client.post("/align/games/analyze", files={"file": ("bad.txt", b"abc", "text/plain")}).status_code == 400
    assert client.post("/align/games/analyze", files={"file": ("bad.mp4", b"abc", "text/plain")}).status_code == 400
    assert client.post("/align/games/analyze", files={"file": ("large.mp4", b"123456", "video/mp4")}).status_code == 413
    assert client.post("/align/games/analyze", files={"file": ("empty.mp4", b"", "video/mp4")}).status_code == 400
    assert not list((tmp_path / "private-game-analysis-tmp").iterdir())
    response = client.post("/align/games/analyze", files={"file": ("../../game.mp4", b"1234", "video/mp4")})
    assert response.status_code == 202
    assert captured[0][1] == "game.mp4"
    assert captured[0][3] == 1
    # The fake queue did not take ownership; the API leaves no server path in its response.
    assert "private-game" not in response.text
    captured[0][0].unlink()


def test_provider_failure_and_partial_synthesis(monkeypatch):
    profile = observer.merge([_chunk(0, 30)])
    monkeypatch.setattr(observer.modal_runtime, "observe", lambda *_: (_ for _ in ()).throw(TimeoutError("timed out")))
    # A failed optional synthesis still leaves grounded observations available.
    result = observer.synthesize(profile)
    assert result["description"] == profile["description"]
    assert result["audience_archetypes"] == []


def test_igdb_empty_results_and_concept_query(monkeypatch):
    monkeypatch.setattr(igdb, "configured", lambda: True)
    calls = []
    def fake_query(endpoint, body):
        calls.append((endpoint, body))
        if endpoint == "genres":
            return [{"id": 13, "name": "Simulation"}]
        if endpoint == "games":
            return [{"id": 8, "name": "Example"}]
        return []
    monkeypatch.setattr(igdb, "query", fake_query)
    result = igdb.candidates({"genres": ["Simulation"], "themes": [], "keywords": [], "player_perspective": []})
    assert result == [{"id": 8, "name": "Example"}]
    assert ("games", f"fields {igdb.FIELDS}; where genres = 13; limit 50;") in calls
    monkeypatch.setattr(igdb, "query", lambda *_: [])
    assert igdb.candidates({"genres": ["Unknown"], "themes": [], "keywords": [], "player_perspective": []}) == []


def test_igdb_keywords_use_supported_name_filter(monkeypatch):
    calls = []
    def fake_query(endpoint, body):
        calls.append((endpoint, body))
        if 'search ' in body:
            raise igdb.IGDBError('IGDB keywords do not support search')
        return [{"id": 87, "name": "Combat"}]
    monkeypatch.setattr(igdb, "query", fake_query)
    assert igdb._match_ids("keywords", ["combat"]) == [87]
    assert calls == [("keywords", 'fields id,name; where name ~ "combat"; limit 20;')]


def test_shooter_taxonomy_aliases_match_candidates_and_similarity(monkeypatch):
    monkeypatch.setattr(igdb, "query", lambda *_: [{"id": 5, "name": "Shooter"}, {"id": 13, "name": "Simulator"}])
    assert igdb._match_ids("genres", ["first-person shooter", "tactical shooter", "movement"]) == [5]
    profile = observer.merge([_chunk(0, 30)])
    profile.update(genres=["tactical shooter", "first-person shooter", "combat", "movement"],
                   themes=[], player_perspective=["first-person", "first-person perspective", "enemy"])
    shooter = {"id": 1, "name": "Shooter game", "genres": [{"name": "Shooter"}],
               "player_perspectives": [{"name": "First person"}], "rating": 20}
    unrelated = {"id": 2, "name": "Simulation game", "genres": [{"name": "Simulator"}],
                 "player_perspectives": [{"name": "Bird view / Isometric"}], "rating": 99}
    ranked = report.rank(profile, [unrelated, shooter])
    assert ranked[0]["igdb_id"] == 1
    assert ranked[0]["score_factors"]["genres"] == 1
    assert ranked[0]["score_factors"]["perspective"] == 1


def test_local_pipeline_persists_report_without_raw_path(clip, tmp_path, monkeypatch):
    from app import config as app_config
    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    jobs.initialize()
    with jobs.db._connect() as conn:
        conn.execute("INSERT INTO game_analysis_jobs(id,filename,sha256,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     ("sample", "short.mp4", "hash", "queued", "now", "now"))
    def fake_modal(prompt, frames):
        if "Use ONLY this merged" in prompt:
            return json.dumps({"description": "The player inspects a resource panel and adjusts a system.",
                               "audience_archetypes": [{"archetype": "Optimizer", "affinity": 80,
                                                        "reason": "A resource panel is visible.", "evidence_timestamps": [1]}],
                               "audience_summary": "Players who enjoy adjusting systems may like this."})
        return json.dumps(_chunk(0, 2))
    monkeypatch.setattr(observer.modal_runtime, "observe", fake_modal)
    monkeypatch.setattr(igdb, "configured", lambda: True)
    monkeypatch.setattr(igdb, "candidates", lambda _profile: [{"id": 1, "name": "Analog", "genres": [{"name": "Simulation"}],
                                                               "platforms": [{"name": "PC (Microsoft Windows)"}], "rating": 70,
                                                               "rating_count": 50, "summary": "Adjust resources in a simulation"}])
    jobs._analyze("sample", clip, 1)
    saved = jobs.get("sample")
    assert saved and saved["status"] == "completed"
    assert saved["report"]["audience"]["archetypes"][0]["archetype"] == "Optimizer"
    assert saved["report"]["comparables"][0]["rating"] == 70
    assert saved["metrics"]["frames_processed"] > 0
    assert str(clip) not in json.dumps(saved)


def test_modal_first_use_deploys_once_and_errors_are_actionable(monkeypatch):
    import modal.exception
    client = object()
    monkeypatch.setattr(modal_runtime, "_client", lambda: client)
    calls = []

    def invoke(_client, _prompt, _frames):
        calls.append("invoke")
        if len(calls) < 3:
            raise modal.exception.NotFoundError("missing")
        return "observed"

    monkeypatch.setattr(modal_runtime, "_invoke", invoke)
    monkeypatch.setattr(modal_runtime, "_deploy", lambda _client: calls.append("deploy"))
    assert modal_runtime.observe("prompt", []) == "observed"
    assert calls == ["invoke", "invoke", "deploy", "invoke"]
    monkeypatch.setattr(modal_runtime, "_invoke", lambda *_: (_ for _ in ()).throw(RuntimeError("CUDA out of memory")))
    with pytest.raises(modal_runtime.GameInferenceError, match="out of memory"):
        modal_runtime.observe("prompt", [])


def test_missing_modal_credentials_are_named_without_exposing_values(monkeypatch):
    for key in ("GAME_ANALYSIS_MODAL_TOKEN_ID", "GAME_ANALYSIS_MODAL_TOKEN_SECRET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert modal_runtime.credentials_configured() is False
    with pytest.raises(modal_runtime.GameInferenceError, match="Settings > Brand Studio GPU"):
        modal_runtime.observe("prompt", [])


def test_gpu_decoder_blocks_repeated_observations_and_missing_confidence():
    # The GPU-only dependency is optional in ordinary local backend installs.
    enforcer = pytest.importorskip("lmformatenforcer")
    from app.game_analysis.modal_backend import _output_schema
    schema = _output_schema(False)["properties"]["observations"]
    parser = enforcer.JsonSchemaParser(schema)
    repeated = [{"timestamp": i, "observation": "Player runs", "confidence": .8} for i in range(6)]
    for char in json.dumps(repeated)[:-1]:
        assert char in parser.get_allowed_characters()
        parser = parser.add_character(char)
    assert "]" in parser.get_allowed_characters()
    assert "," not in parser.get_allowed_characters()  # A seventh repeated entry is impossible.
    assert parser.add_character("]").can_end()

    parser = enforcer.JsonSchemaParser(schema["items"])
    for char in '{"timestamp":1,"observation":"Player runs"':
        assert char in parser.get_allowed_characters()
        parser = parser.add_character(char)
    assert "}" not in parser.get_allowed_characters()  # Confidence cannot be omitted.


def test_refresh_comparables_preserves_gpu_report_and_failed_refresh(tmp_path, monkeypatch):
    from app import config as app_config
    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    jobs.initialize()
    profile = observer.merge([_chunk(0, 30)])
    result = {"description": "Existing gameplay description", "profile": profile, "comparables": [],
              "audience": {"archetypes": [{"archetype": "Optimizer"}], "summary": "Existing audience", "basis": "",
                           "comparable_titles": []}, "platforms": {}, "igdb_note": "Credentials missing"}
    metrics = {"frames_processed": 100, "model_calls": 4, "igdb_candidates": 0}
    with jobs.db._connect() as conn:
        conn.execute("INSERT INTO game_analysis_jobs(id,filename,sha256,status,created_at,updated_at,report_json,metrics_json) VALUES (?,?,?,?,?,?,?,?)",
                     ("refresh", "game.mp4", "hash", "completed", "now", "now", json.dumps(result), json.dumps(metrics)))
    monkeypatch.setattr(modal_runtime, "observe", lambda *_: pytest.fail("Catalog refresh must not call the GPU"))
    monkeypatch.setattr(igdb, "configured", lambda: False)
    app = FastAPI()
    app.include_router(game_router.router)
    client = TestClient(app)
    assert client.post("/align/games/missing/refresh-comparables").status_code == 404
    assert client.post("/align/games/refresh/refresh-comparables").status_code == 409
    monkeypatch.setattr(igdb, "configured", lambda: True)
    monkeypatch.setattr(igdb, "candidates", lambda _: [{"id": 31, "name": "Comparable", "genres": [{"name": "Simulation"}],
                                                       "platforms": [{"name": "PC (Microsoft Windows)"}], "summary": "Adjust resources"}])
    response = client.post("/align/games/refresh/refresh-comparables")
    assert response.status_code == 200
    updated = response.json()
    assert updated["report"]["comparables"][0]["igdb_id"] == 31
    assert updated["report"]["audience"]["comparable_titles"] == ["Comparable"]
    assert updated["report"]["audience"]["archetypes"] == result["audience"]["archetypes"]
    assert updated["report"]["description"] == result["description"]
    assert updated["metrics"]["model_calls"] == 4 and updated["metrics"]["frames_processed"] == 100
    assert updated["metrics"]["igdb_candidates"] == 1 and not updated["report"]["igdb_note"]
    monkeypatch.setattr(igdb, "candidates", lambda _: (_ for _ in ()).throw(igdb.IGDBError("Authentication failed")))
    assert client.post("/align/games/refresh/refresh-comparables").status_code == 502
    assert jobs.get("refresh")["report"] == updated["report"]
