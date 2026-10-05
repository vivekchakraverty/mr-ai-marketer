"""Provider failover and actionable errors for manuscript analysis."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import huggingface_hub
import pytest
from huggingface_hub.utils import HfHubHTTPError

from app.services import align_writing


def _http_error(status: int, request_id: str) -> HfHubHTTPError:
    response = httpx.Response(
        status,
        json={"error": "The model worker is unavailable."},
        headers={"x-request-id": request_id},
        request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions"),
    )
    return HfHubHTTPError(f"HTTP {status}", response=response)


def _completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")]
    )


def test_503_retries_cheapest_then_uses_next_provider(monkeypatch):
    calls: list[str] = []

    class Client:
        def __init__(self, *, provider, **_kwargs):
            self.provider = provider

        def chat_completion(self, **_kwargs):
            calls.append(self.provider)
            if self.provider == "deepinfra":
                raise _http_error(503, "worker-down")
            return _completion('{"summary":"A story of discovery"}')

    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    monkeypatch.setattr(align_writing.time, "sleep", lambda _delay: None)

    result = align_writing._call_model("hf-test", "system", "excerpt", stage="section 1 of 3")

    assert result["summary"] == "A story of discovery"
    assert calls == ["deepinfra", "deepinfra", "ovhcloud"]


def test_all_503s_report_stage_provider_status_and_request_ids(monkeypatch):
    calls: list[str] = []

    class Client:
        def __init__(self, *, provider, **_kwargs):
            self.provider = provider

        def chat_completion(self, **_kwargs):
            calls.append(self.provider)
            raise _http_error(503, f"{self.provider}-request")

    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    monkeypatch.setattr(align_writing.time, "sleep", lambda _delay: None)

    with pytest.raises(align_writing.WritingModelError) as failure:
        align_writing._call_model("hf-test", "system", "excerpt", stage="section 2 of 3")

    detail = failure.value.detail()
    assert failure.value.http_status == 503
    assert detail["code"] == "provider_unavailable"
    assert detail["stage"] == "section 2 of 3"
    assert [attempt["status"] for attempt in detail["attempts"]] == [503] * 4
    assert detail["attempts"][-1]["requestId"] == "together-request"
    assert calls == ["deepinfra", "deepinfra", "ovhcloud", "together"]


def test_access_denial_does_not_retry_or_call_it_a_service_outage(monkeypatch):
    calls: list[str] = []

    class Client:
        def __init__(self, *, provider, **_kwargs):
            self.provider = provider

        def chat_completion(self, **_kwargs):
            calls.append(self.provider)
            raise _http_error(403, "access-denied")

    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)

    with pytest.raises(align_writing.WritingModelError) as failure:
        align_writing._call_model("hf-test", "system", "excerpt", stage="final fingerprint")

    assert failure.value.detail()["code"] == "provider_access_denied"
    assert failure.value.detail()["attempts"][0]["status"] == 403
    assert calls == ["deepinfra"]


def test_unusable_json_tries_another_provider(monkeypatch):
    calls: list[str] = []

    class Client:
        def __init__(self, *, provider, **_kwargs):
            self.provider = provider

        def chat_completion(self, **_kwargs):
            calls.append(self.provider)
            return _completion("No JSON") if self.provider == "deepinfra" else _completion('{"themes":["hope"]}')

    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    monkeypatch.setattr(align_writing.time, "sleep", lambda _delay: None)

    result = align_writing._call_model("hf-test", "system", "excerpt")

    assert result == {"themes": ["hope"]}
    assert calls == ["deepinfra", "deepinfra", "ovhcloud"]
