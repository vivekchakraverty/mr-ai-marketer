"""Config schema parsing + the required missing-HF_TOKEN fail-fast path."""

import pytest

from vendor.leadgen import config, llm


def test_schema_parses_expected_settings():
    names = {s.name for s in config.load_schema()}
    for expected in ("LLM_BACKEND", "HF_TOKEN", "LLM_MODEL", "SMTP_HOST", "IMAP_HOST", "DAILY_CAP"):
        assert expected in names


def test_secrets_are_flagged_and_masked(monkeypatch):
    hf = config.get_setting("HF_TOKEN")
    assert hf is not None and hf.is_secret
    # A non-secret free-text setting is not masked.
    model = config.get_setting("LLM_MODEL")
    assert model is not None and not model.is_secret


def test_missing_hf_token_fails_fast(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "hf")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    config.load_schema.cache_clear()

    with pytest.raises(llm.LLMError):
        llm.hf_token()

    ok, detail = config.verify_hf()
    assert ok is False
    assert "HF_TOKEN" in detail


def test_ollama_backend_needs_no_hf_token(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    ok, _ = config.verify_hf()
    assert ok is True


def test_extract_json_from_fenced_reply():
    assert llm._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm._extract_json('sure! {"b": 2} done') == {"b": 2}
