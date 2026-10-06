import io
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.routers.align_visual_art import router
from app.services import align_visual_art as art
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.align_visual_art.build_pamela_index import build


def test_pamela_index_counts_distinct_participants_and_uses_personal_baseline():
    rows = []
    for person, art_scores, other_scores in (
        ("one", [4.5, 4.4, 4.3], [2.0, 2.0, 2.0]),
        ("two", [4.5, 4.4, 4.3], [4.5, 4.5, 4.5]),
    ):
        for index, score in enumerate(art_scores):
            rows.append({"participant_id": person, "image_id": index,
                         "image_metadata": {"group": "landscape", "style": "Realism", "type": "Art"},
                         "original_score": score})
        for index, score in enumerate(other_scores):
            rows.append({"participant_id": person, "image_id": 10 + index,
                         "image_metadata": {"group": "abstract", "style": None, "type": "Art"},
                         "original_score": score})
    result = build(rows)["dimensions"]["group"]["landscape"]
    assert result["ratings"] == 6
    assert result["images"] == 3
    assert result["eligible_participants"] == 2
    assert result["admirer_participants"] == 1


def test_upload_is_resized_and_malformed_or_oversize_uploads_fail():
    image = Image.new("RGB", (1600, 800), "red")
    output = io.BytesIO()
    image.save(output, format="PNG")
    uri = art.prepare_image(output.getvalue())
    assert uri.startswith("data:image/jpeg;base64,")
    import base64
    processed = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert processed.size == (1024, 512)
    with pytest.raises(art.VisualArtError):
        art.prepare_image(b"not an image")
    with pytest.raises(art.VisualArtError):
        art.prepare_image(b"a" * (art.MAX_IMAGE_BYTES + 1))


def test_classification_rejects_invented_labels_and_match_uses_real_sample():
    labels = art.parse_classification('```json\n{"description":"a painted view","group":"landscape",'
                                      '"style":"Imaginary Style","artsy_categories":["Landscapes","Fake"]}\n```')
    assert labels == {"group": "landscape", "style": "", "artsy_categories": ["Landscapes"],
                      "description": "a painted view"}
    report = art.match(labels["group"], labels["style"], labels["artsy_categories"])
    assert report["pamela_evidence"][0]["ratings"] == 7570
    assert report["pamela_evidence"][0]["eligible_participants"] == 145
    with pytest.raises(art.VisualArtError):
        art.match("fabricated", "", [])


@pytest.mark.parametrize(
    ("provider_status", "api_status", "expected"),
    [(403, 502, "Inference Providers permission"),
     (402, 502, "billing or credits"),
     (429, 429, "rate-limited"),
     (503, 503, "could not serve")],
)
def test_provider_failure_reports_actionable_status_without_leaking_token(
    monkeypatch, provider_status, api_status, expected
):
    token = "fake"
    monkeypatch.setenv("HF_TOKEN", token)
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
    calls = []

    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, **_kwargs):
            calls.append(_kwargs["model"])
            response = httpx.Response(
                provider_status,
                json={"error": f"provider rejected {token}"},
                request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions"),
            )
            raise httpx.HTTPStatusError("provider error", request=response.request, response=response)

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "InferenceClient", FailingClient)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/align/visual-art/classify", files={"file": ("art.png", image.getvalue(), "image/png")}
    )
    assert response.status_code == api_status
    assert expected in response.json()["detail"]
    assert "provider rejected [redacted]" in response.json()["detail"]
    assert token not in response.text
    assert calls == [art.MODEL]


def test_unroutable_model_is_reported_as_provider_unavailable():
    error = art._inference_error(ValueError("No Inference Provider supports this model"), "hf_secret")
    assert error.http_status == 503
    assert "could not route" in str(error)


@pytest.mark.parametrize("fallback_available", [True, False])
def test_unsupported_enabled_providers_tries_another_vision_model(monkeypatch, fallback_available):
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, format="PNG")
    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1 or not fallback_available:
                response = httpx.Response(
                    400,
                    json={"error": f"The requested model '{kwargs['model']}' is not supported by any provider you have enabled."},
                    request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions"),
                )
                raise httpx.HTTPStatusError("unsupported", request=response.request, response=response)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"description":"a blue landscape","group":"landscape","style":null,"artsy_categories":["Landscapes"]}'
            ))])

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "InferenceClient", Client)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/align/visual-art/classify", files={"file": ("art.png", image.getvalue(), "image/png")}
    )
    assert [call["model"] for call in calls] == list(art.VISION_MODELS)
    assert calls[0]["messages"] == calls[1]["messages"]
    assert calls[0]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert "extra_body" not in calls[1]
    if fallback_available:
        assert response.status_code == 200
        assert response.json()["model"] == art.VISION_MODELS[1]
        assert response.json()["group"] == "landscape"
    else:
        assert response.status_code == 503
        assert "Enable a provider" in response.json()["detail"]
        assert "smaller image" not in response.text
