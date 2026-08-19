from __future__ import annotations

import io
import pathlib

from PIL import Image

from app.brandforge import modal_image_backend
from app.brandforge.intake import demo_brand_dict
import pytest
from fastapi import HTTPException

from app.routers import brand_forge, mastodon_post, social_post, tumblr_post
from app.services import image_prompt


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


def test_modal_image_worker_loads_the_flux_pipeline():
    assert modal_image_backend.SOURCE_MODEL == "black-forest-labs/FLUX.2-klein-4B"
    # A model repo, not an hf://buckets/ URI. The bucket form was the reason this worker
    # could never deploy: the env var had no default, so it resolved to "hf://buckets/".
    assert "/" in modal_image_backend.MODEL_REPO
    assert not modal_image_backend.MODEL_REPO.startswith("hf://")
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
    # The Modal seam moved into services/image_prompt when the three composers started
    # sharing one render path; the behaviour under test is unchanged.
    monkeypatch.setattr(image_prompt.config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(image_prompt, "_modal_runtime", lambda: _FakeModalRuntime)
    _FakeModalRuntime.calls = []

    approved = image_prompt.template_prompt(
        "We launched a more accessible way to review campaigns.", "B2B SaaS", "linkedin"
    )
    response = social_post.generate_image(
        social_post.GenerateImageRequest(
            postText="We launched a more accessible way to review campaigns.",
            niche="B2B SaaS",
            platform="linkedin",
            hfToken="hf-test",
            modalTokenId="modal-id",
            modalTokenSecret="modal-secret",
            useModal=True,
            prompt=approved,
        )
    )

    assert _FakeModalRuntime.calls[0][1:] == (1200, 1200)
    assert (tmp_path / response.url.removeprefix("/outputs/")).is_file()
    assert "No lettering" in response.promptUsed
    # What was drawn is exactly what was approved, not something rebuilt server-side.
    assert response.promptUsed == approved


def test_no_image_is_drawn_from_a_prompt_the_user_never_approved(monkeypatch):
    """The rule, not just the habit of the current screen.

    Every composer's /images used to build its own prompt and draw immediately, so the
    picture was decided by text the user never saw. Refusing here is what makes review
    mandatory rather than a convention the UI could quietly drop.
    """
    drew: list[str] = []
    monkeypatch.setattr(
        image_prompt, "render", lambda *a, **k: drew.append(a) or ("/outputs/x.png", 1, 1)
    )

    for call in (
        lambda: social_post.generate_image(
            social_post.GenerateImageRequest(
                postText="a real post", platform="bluesky", hfToken="hf-test", prompt="   "
            )
        ),
        lambda: mastodon_post.generate_image(
            mastodon_post.GenerateImageRequest(prompt="", hfToken="hf-test")
        ),
        lambda: tumblr_post.generate_image(
            tumblr_post.GenerateImageRequest(prompt="", hfToken="hf-test")
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            call()
        assert excinfo.value.status_code == 400
        assert "approve" in str(excinfo.value.detail).lower()

    assert drew == [], "nothing may reach the image backend without an approved prompt"


def test_a_suggestion_never_fails_the_screen(monkeypatch):
    """An unreachable model returns the template with a note, rather than raising."""
    monkeypatch.setattr(
        image_prompt, "_model_name", lambda: "definitely/not-a-real-model"
    )
    result = image_prompt.suggest("a post about shipping something", "tech", "bluesky", "hf-bad")
    assert result.source == "template"
    assert result.note
    assert "No lettering" in result.prompt


def test_every_generated_image_lands_on_the_library_shelf(monkeypatch, tmp_path, app_db):
    """Both generators file what they draw, with the prompt beside the file.

    Brand Studio saved its three assets to disk and told nobody: the pictures existed but
    the Library had no row for them, so they could not be reopened, and the compose-box
    picker — which reads the shelf — never offered them. The post creators already filed
    theirs, which is what made the gap easy to miss.
    """
    monkeypatch.setattr(brand_forge.config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(image_prompt.config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(
        brand_forge,
        "text_to_image",
        lambda _token, prompt, model=None: Image.new("RGB", (16, 16), "#126782"),
    )
    monkeypatch.setattr(image_prompt, "_modal_runtime", lambda: _FakeModalRuntime)

    brand_forge.generate_images(
        brand_forge.BrandImagesRequest(
            intake=brand_forge.IntakeIn(**demo_brand_dict()),
            visualBrief="Visual direction for a clear, modern brand.",
            hfToken="hf-test",
        )
    )
    social_post.generate_image(
        social_post.GenerateImageRequest(
            postText="We shipped a thing.",
            platform="bluesky",
            hfToken="hf-test",
            modalTokenId="modal-id",
            modalTokenSecret="modal-secret",
            useModal=True,
            prompt="An approved prompt.",
        )
    )

    rows = app_db.list_items()
    images = [r for r in rows if (r["output_path"] or "").endswith(".png")]
    assert len(images) == 4, "three brand assets and one companion image"
    assert {r["tool"] for r in images} == {"Brand", "Social"}
    # The prompt is the row's text, so opening it later shows what produced the picture.
    assert all(r["content"] for r in images)
    assert all(pathlib.Path(r["output_path"]).is_file() for r in images)
