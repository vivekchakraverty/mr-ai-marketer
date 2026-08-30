"""Choosing between the free Space and the user's own Modal GPU.

The Space is the default and costs nothing; Modal is faster and spends credits. Which one
runs is decided entirely by whether Modal credentials are present, so these pin that down —
including that a configured GPU failing is reported rather than quietly served from the
Space at a minute and a half a go.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.services import email_writer


def _install_ctr(monkeypatch, predict):
    """Stand in for the scoring module.

    Substituted in sys.modules rather than patched on the real one: the predictor imports
    numpy, scikit-learn and joblib, and the point of the service importing it lazily is that
    those need not be present. Reaching for the real module here would put them straight back
    into the test's requirements — which is exactly what broke CI.
    """
    module = types.ModuleType("app.services.ctr_predictor")
    module.predict_ctr = predict
    monkeypatch.setitem(sys.modules, "app.services.ctr_predictor", module)
    return module


@pytest.fixture(autouse=True)
def no_ctr(monkeypatch):
    """A score is irrelevant to which backend generated the text."""
    return _install_ctr(
        monkeypatch,
        lambda text, hf_token=None: types.SimpleNamespace(predictedClickRate=0.1, bucket="mid"),
    )


@pytest.fixture()
def modal_runtime(monkeypatch):
    """A stand-in for the Modal runtime, which imports the modal SDK for real."""
    calls: list[tuple] = []

    class EmailWriterModalError(RuntimeError):
        pass

    class ModalConfig:
        def __init__(self, token_id, token_secret, hf_token=""):
            self.token_id, self.token_secret, self.hf_token = token_id, token_secret, hf_token

    module = types.ModuleType("app.emailwriter.modal_runtime")
    module.EmailWriterModalError = EmailWriterModalError
    module.ModalConfig = ModalConfig
    module.generate_email = lambda cfg, instruction: (
        calls.append((cfg.token_id, cfg.hf_token, instruction)) or "Subject: from the GPU"
    )
    package = types.ModuleType("app.emailwriter")
    package.modal_runtime = module
    monkeypatch.setitem(sys.modules, "app.emailwriter", package)
    monkeypatch.setitem(sys.modules, "app.emailwriter.modal_runtime", module)
    return module, calls


@pytest.fixture()
def space(monkeypatch):
    """A stand-in for the Hugging Face Space client."""
    calls: list[str] = []

    class FakeClient:
        def predict(self, instruction, api_name=None):
            calls.append(instruction)
            return "Subject: from the Space"

    monkeypatch.setattr(email_writer, "_get_client", lambda hf_token=None: FakeClient())
    return calls


def _no_modal(monkeypatch):
    for name in ("EMAIL_WRITER_MODAL_TOKEN_ID", "EMAIL_WRITER_MODAL_TOKEN_SECRET"):
        monkeypatch.delenv(name, raising=False)


def _with_modal(monkeypatch):
    monkeypatch.setenv("EMAIL_WRITER_MODAL_TOKEN_ID", "ak-123")
    monkeypatch.setenv("EMAIL_WRITER_MODAL_TOKEN_SECRET", "as-456")


def test_without_modal_credentials_the_space_writes_it(monkeypatch, space, modal_runtime):
    _no_modal(monkeypatch)
    result = email_writer.generate_marketing_email("A summer sale email")
    assert result["text"] == "Subject: from the Space"
    assert space == ["A summer sale email"]
    assert modal_runtime[1] == []


def test_with_modal_credentials_the_gpu_writes_it(monkeypatch, space, modal_runtime):
    _with_modal(monkeypatch)
    result = email_writer.generate_marketing_email("A summer sale email", hf_token="hf_abc")
    assert result["text"] == "Subject: from the GPU"
    assert modal_runtime[1] == [("ak-123", "hf_abc", "A summer sale email")]
    # The Space must not also be called: that would be paying twice for one email.
    assert space == []


def test_half_a_credential_pair_is_not_a_configuration(monkeypatch, space, modal_runtime):
    """A token id with no secret cannot authenticate, so it is the Space, not an error."""
    monkeypatch.setenv("EMAIL_WRITER_MODAL_TOKEN_ID", "ak-123")
    monkeypatch.delenv("EMAIL_WRITER_MODAL_TOKEN_SECRET", raising=False)
    assert email_writer.generate_marketing_email("x")["text"] == "Subject: from the Space"
    assert modal_runtime[1] == []


def test_a_configured_gpu_that_fails_is_reported_not_silently_replaced(
    monkeypatch, space, modal_runtime
):
    """Falling back would look like the tool got slower for no reason. Someone who set a GPU
    up wants to hear that it stopped working."""
    module, _ = modal_runtime

    def boom(cfg, instruction):
        raise module.EmailWriterModalError("Modal generation failed: out of credit")

    module.generate_email = boom
    _with_modal(monkeypatch)

    with pytest.raises(module.EmailWriterModalError, match="out of credit"):
        email_writer.generate_marketing_email("A summer sale email")
    assert space == []


def test_the_hf_token_falls_back_to_the_environment(monkeypatch, space, modal_runtime):
    """The Lead Gen Agent drafts through here with an instruction and no token; the image
    build needs one that can read the private weights repo."""
    _with_modal(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    email_writer.generate_marketing_email("A summer sale email")
    assert modal_runtime[1][0][1] == "hf_from_env"


def test_an_empty_instruction_never_reaches_either_backend(monkeypatch, space, modal_runtime):
    _with_modal(monkeypatch)
    with pytest.raises(ValueError):
        email_writer.generate_marketing_email("   ")
    assert modal_runtime[1] == []
    assert space == []


def test_a_missing_ctr_model_does_not_throw_the_email_away(monkeypatch, space, modal_runtime):
    """The estimate is an extra, and it is absent on any install not pointed at a model
    repo — the model is deliberately not shipped in the public repo. Failing the request
    over it discarded an email that had already been written."""
    _no_modal(monkeypatch)

    def unavailable(text, hf_token=None):
        raise RuntimeError(
            "the click-through-rate model isn't available: no local copy, and "
            "HF_ASSETS_CTR_MODEL_REPO is not set."
        )

    _install_ctr(monkeypatch, unavailable)

    result = email_writer.generate_marketing_email("A summer sale email")
    assert result["text"] == "Subject: from the Space"
    assert result["predictedClickRate"] is None
    assert result["ctrBucket"] == ""
