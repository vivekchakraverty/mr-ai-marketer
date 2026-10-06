"""Content calendar uses saved audience data and keeps cadence aligned to topic slots."""
import json

import pytest
from pydantic import ValidationError

from app import db
from app.services import buyer_personas2, content_calendar


def _report():
    return {
        "id": "report1", "source_key": "music:align1",
        "source_snapshot": {
            "project": {"name": "North Star", "product_description": "Independent music release", "geo": "US"},
            "platforms": [{"name": "YouTube", "score": 72, "kind": "predicted fit"}],
            "audience_signals": [{"claim": "Listeners enjoy live performances", "basis": "Artist interview"}],
            "clusters": [{"label": "Live music seekers", "basis": "Inferred"}],
        },
        "personas": [{"archetype": "Live music seekers", "priority": "Primary", "confidence": "Medium",
                      "pain_points": [{"text": "Few local shows"}], "motivations": [{"name": "Discovery"}],
                      "channels": [{"name": "YouTube", "affinity": "High"}],
                      "content_preferences": {"topics": ["Song stories"], "types": ["Short video"],
                                              "conversion_examples": ["Live clip"], "proof": "Performance footage"},
                      "messaging": {"core_message": "Discover a new live sound"}}],
        "strategy": {"content": ["Show the song's origin"], "channels": ["Test YouTube"]},
    }


def _draft():
    return {"summary": "Test short performance clips and stories with live music seekers.",
            "channels": [{"name": "YouTube", "posts_per_week": 2,
                          "formats": ["Short video"], "reason": "Persona affinity and Align predicted fit support a small test."}],
            "topics": [{"week": week, "theme": f"Song story {week}-{slot}",
                        "angle": "Explain the story behind a verse with a short performance clip.",
                        "persona": "Live music seekers", "channel": "YouTube", "format": "Short video",
                        "goal": "Qualified interest"}
                       for week in range(1, 5) for slot in range(2)],
            "measurement": "Review saves, comments, qualified visits and production effort after four weeks."}


def test_prompt_uses_saved_persona_align_and_both_research_sources(monkeypatch):
    calls = []

    def fake_model(system, prompt, **kwargs):
        calls.append((system, prompt, kwargs))
        return {"ideas": [{"slot": 1, "theme": "A live song origin", "angle": "Show the rehearsal behind a song."}]}, {"model": "Qwen/test", "provider": "test"}

    monkeypatch.setattr(buyer_personas2, "_model_call", fake_model)
    result = content_calendar._generate(_report())
    prompt = calls[0][1]
    assert "Live music seekers" in prompt
    assert "Artist interview" in prompt
    assert "4.21 days per week" in prompt
    assert "52% use Facebook daily" in prompt
    assert "Pew findings only if the project market includes the U.S." in prompt
    assert result["channels"][0]["posts_per_week"] == 2
    assert len(result["topics"]) == 8
    assert result["topics"][0]["theme"] == "A live song origin"
    assert len(result["research"]) == 2


def test_topic_slots_must_match_recommended_frequency():
    draft = _draft()
    draft["topics"].pop()
    draft["topics"].append({**draft["topics"][0]})
    with pytest.raises(ValidationError, match="topic slots"):
        content_calendar.CalendarDraft.model_validate(draft)


def test_model_cannot_override_channels_or_frequency(monkeypatch):
    attempts = []
    invalid = _draft()
    invalid["channels"][0]["name"] = "LinkedIn"
    for topic in invalid["topics"]:
        topic["channel"] = "LinkedIn"

    def fake_model(system, prompt, **kwargs):
        attempts.append(prompt)
        return invalid, {"model": "Qwen/test"}

    monkeypatch.setattr(buyer_personas2, "_model_call", fake_model)
    result = content_calendar._generate(_report())
    assert len(attempts) == 1
    assert result["channels"][0]["name"] == "YouTube"
    assert result["channels"][0]["posts_per_week"] == 2


def test_wrapped_ideas_are_used(monkeypatch):
    attempts = []

    def fake_model(system, prompt, **kwargs):
        attempts.append(prompt)
        return {"content_calendar": {"ideas": [{"slot": 1, "theme": "Show the song's origins",
                                                 "angle": "Connect a lyric to the artist's experience."}]}}, {"model": "Qwen/test"}

    monkeypatch.setattr(buyer_personas2, "_model_call", fake_model)
    result = content_calendar._generate(_report())
    assert len(attempts) == 1
    assert result["topics"][0]["theme"] == "Show the song's origins"


def test_unusable_model_output_still_produces_a_complete_calendar(monkeypatch):
    monkeypatch.setattr(buyer_personas2, "_model_call",
                        lambda *args, **kwargs: ({"content_calendar": {"weekly_frequency": 3}}, {}))
    result = content_calendar._generate(_report())
    assert len(result["topics"]) == 8
    assert "no usable topic ideas" in result["generation_note"]
    content_calendar.CalendarDraft.model_validate(result)


def test_non_json_model_response_uses_persona_topics(monkeypatch):
    def invalid_json(*args, **kwargs):
        raise ValueError("The model returned malformed JSON")

    monkeypatch.setattr(buyer_personas2, "_model_call", invalid_json)
    result = content_calendar._generate(_report())
    assert result["model"] == ""
    assert result["topics"][0]["persona"] == "Live music seekers"


def test_capacity_changes_weekly_frequency():
    context = content_calendar._input(_report())
    context["project"]["marketing_resources"] = "Solo, 2 hours per week"
    assert sum(item["posts_per_week"] for item in content_calendar._channel_mix(context)) == 1
    context["project"]["marketing_resources"] = "Full-time marketing team"
    assert sum(item["posts_per_week"] for item in content_calendar._channel_mix(context)) == 3


def test_completed_persona_reports_are_selectable_and_jobs_persist(app_db):
    buyer_personas2.initialize()
    content_calendar.initialize()
    report = _report()
    with db._connect() as conn:
        conn.execute("INSERT INTO buyer_persona2_reports(id,source_key,input_hash,status,created_at,updated_at,report_json) VALUES (?,?,?,?,?,?,?)",
                     ("report1", "music:align1", "hash", "complete", "2026-10-01", "2026-10-01", json.dumps(report)))
        conn.execute("INSERT INTO content_calendar_jobs(id,persona_report_id,status,created_at,updated_at,result_json) VALUES (?,?,?,?,?,?)",
                     ("job1", "report1", "complete", "2026-10-02", "2026-10-02", json.dumps({"summary": "Saved"})))
    assert content_calendar.persona_reports()[0]["label"] == "North Star"
    assert content_calendar.latest("report1")["result"]["summary"] == "Saved"
