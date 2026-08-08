from __future__ import annotations

import io

from PIL import Image

from app.brandforge import modal_image_backend
from app.brandforge.intake import demo_brand_dict
from app.routers import brand_forge, social_post


def _png_bytes() -> bytes:
    image = Image.new("RGB", (16, 16), "#126782")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _FakeModalRuntime:
    class ModalConfig:
        def __init__(self, **values):
            self.values = values

    calls: list[tuple[str, int, int]] = []

    @classmethod
    def generate_image(cls, _config, prompt: str, width: int, height: int) -> bytes:
        cls.calls.append((prompt, width, height))
        return _png_bytes()


def test_modal_image_worker_uses_the_flux_bucket():
    assert modal_image_backend.SOURCE_MODEL == "black-forest-labs/FLUX.2-klein-4B"
    # The bucket is user-configured, so assert the URI shape rather than one account's id.
    assert modal_image_backend.BUCKET_URI.startswith("hf://buckets/")
    assert modal_image_backend.MODEL_DIR == "/models/image-generator-marketer"


def test_brand_images_use_modal_only_after_successful_provision(monkeypatch, tmp_path):
    monkeypatch.setattr(brand_forge.config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(brand_forge, "_modal_runtime", lambda: _FakeModalRuntime)
    _FakeModalRuntime.calls = []

    response = brand_forge.generate_images(
        brand_forge.BrandImagesRequest(
            intake=brand_forge.IntakeIn(**demo_brand_dict()),
            visualBrief="Visual direction for a clear, modern brand.",
            hfToken="hf-test",
            modalTokenId="modal-id",
            modalTokenSecret="modal-secret",
            useModal=True,
        )
    )

    assert len(response.images) == 3
    assert [call[1:] for call in _FakeModalRuntime.calls] == [(1024, 1024), (1024, 1024), (1536, 512)]
    assert all(image.url and (tmp_path / image.url.removeprefix("/outputs/")).is_file() for image in response.images)


def test_brand_images_fall_back_to_hf_before_modal_setup(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(brand_forge.config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(
        brand_forge,
        "text_to_image",
        lambda _token, prompt, model=None: calls.append(prompt) or Image.new("RGB", (16, 16), "#126782"),
    )

    response = brand_forge.generate_images(
        brand_forge.BrandImagesRequest(
            intake=brand_forge.IntakeIn(**demo_brand_dict()),
            visualBrief="Visual direction for a clear, modern brand.",
            hfToken="hf-test",
            modalTokenId="modal-id",
            modalTokenSecret="modal-secret",
            useModal=False,
        )
    )

    assert len(calls) == 3
    assert len(response.images) == 3


def test_social_post_image_uses_provisioned_modal_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(social_post.config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(social_post, "_modal_runtime", lambda: _FakeModalRuntime)
    _FakeModalRuntime.calls = []

    response = social_post.generate_image(
        social_post.GenerateImageRequest(
            postText="We launched a more accessible way to review campaigns.",
            niche="B2B SaaS",
            platform="linkedin",
            hfToken="hf-test",
            modalTokenId="modal-id",
            modalTokenSecret="modal-secret",
            useModal=True,
        )
    )

    assert _FakeModalRuntime.calls[0][1:] == (1200, 1200)
    assert (tmp_path / response.url.removeprefix("/outputs/")).is_file()
    assert "No lettering" in response.promptUsed
