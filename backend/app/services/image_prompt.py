"""Suggest an image prompt for a post, then render it. Shared by all three composers.

Two jobs, deliberately split so the user sits between them:

  suggest()  a small language model reads the finished post and proposes an image
             direction, which is handed back as *text the user can edit*.
  render()   whatever prompt they ended up with is what gets drawn.

WHY THE SPLIT MATTERS. The Bluesky composer already generated images, but it built
the prompt internally from a fixed template and never showed it — so the only way to
change the picture was to change the post. A prompt the user cannot see is a prompt
they cannot fix, and image models fail in specific, legible ways ("make it a wide
shot", "lose the desk", "warmer") that a person can correct in seconds and a template
never will. The template is still here, as the fallback, and is now one of two
starting points rather than the only one.

WHY A SMALL MODEL. Writing one paragraph of art direction is not the hard part of
this app. The composers themselves run on a large instruct model because register
and grounding are genuinely difficult; this is a short, well-specified rewrite, and
paying 80B-class prices per image suggestion for it would be waste the user can
see on their bill. IMAGE_PROMPT_MODEL overrides the default, matching how HF_MODEL
already works, because which small models a given token can reach changes over time.

WHY IT NEVER BLOCKS. If the model call fails — unreachable, gated, out of credit —
suggest() returns the template instead and says which one it gave you. An image
prompt the user was going to edit anyway is not worth failing a screen over.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

# Small, ungated, and served by HF's inference providers. Overridable because
# provider catalogues change and a token's access varies; see the module docstring.
DEFAULT_PROMPT_MODEL = "Qwen/Qwen2.5-7B-Instruct"

# One paragraph of art direction is ~120 tokens. The cap is a cost guard, not a
# quality one: a model that wants to write an essay here has misunderstood the task.
MAX_PROMPT_TOKENS = 220

# Long enough for a slow cold start on a shared endpoint, short enough that a stuck
# call does not hold the screen. The image call itself gets brandforge's own budget.
PROMPT_TIMEOUT_SECONDS = 60

#: Native canvas per platform. Bluesky and X crop to roughly 1.91:1 in-feed;
#: LinkedIn and Mastodon show square well; Tumblr's dashboard is a tall single
#: column, so a portrait canvas uses the space a square one wastes.
PLATFORM_DIMENSIONS: dict[str, tuple[int, int]] = {
    "bluesky": (1200, 672),
    "x": (1200, 672),
    "linkedin": (1200, 1200),
    "mastodon": (1024, 1024),
    "tumblr": (1080, 1350),
}

DEFAULT_DIMENSIONS = (1024, 1024)


def dimensions_for(platform: str) -> tuple[int, int]:
    return PLATFORM_DIMENSIONS.get(platform.strip().lower(), DEFAULT_DIMENSIONS)


@dataclass(frozen=True)
class Suggestion:
    prompt: str
    #: "model" when a language model wrote it, "template" when the fallback did.
    source: str
    #: Empty unless the model was tried and failed; shown so the user knows why.
    note: str


def template_prompt(post_text: str, niche: str, platform: str) -> str:
    """A usable image direction with no model call. The fallback, and the floor.

    Kept deterministic on purpose: this is what the screen falls back to when the
    model is unreachable, so it must never itself fail or need a token.
    """
    return " ".join(
        part
        for part in (
            "Create an original editorial social-media image for a marketing post.",
            f"Platform: {platform}.",
            f"Audience niche: {niche}." if niche.strip() else "",
            "Communicate the post's central idea visually with a polished, specific composition.",
            "No lettering, no logo, no watermark, no UI mockup, no collage of screenshots.",
            "Leave a calm, uncluttered focal area suitable for real text to be added later.",
            f"Post to illustrate: {post_text.strip()[:1800]}",
        )
        if part
    )


_SYSTEM = (
    "You write art direction for image generators. Given a social media post, reply "
    "with ONE paragraph describing a single photograph or illustration that would sit "
    "beside it.\n"
    "Rules:\n"
    "- Describe only what is visible: subject, setting, composition, light, colour, mood.\n"
    "- No text, lettering, logos, watermarks, UI mockups or screenshot collages — image "
    "models render text badly and a marketer adds real copy afterwards.\n"
    "- Leave one calm, uncluttered area where copy could go.\n"
    "- Be concrete. 'A worn oak desk under low winter light' beats 'a nice workspace'.\n"
    "- Do not restate the post, do not use quotation marks, and do not explain yourself. "
    "Reply with the description and nothing else."
)


def _model_name() -> str:
    return (os.environ.get("IMAGE_PROMPT_MODEL") or "").strip() or DEFAULT_PROMPT_MODEL


def suggest(post_text: str, niche: str, platform: str, hf_token: str) -> Suggestion:
    """Ask a small model for an image direction. Never raises — falls back instead."""
    fallback = template_prompt(post_text, niche, platform)
    text = post_text.strip()
    if not text:
        return Suggestion(prompt=fallback, source="template", note="")
    if not hf_token.strip():
        return Suggestion(
            prompt=fallback,
            source="template",
            note="Connect your Hugging Face account in Settings for a written suggestion.",
        )

    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(api_key=hf_token.strip(), timeout=PROMPT_TIMEOUT_SECONDS)
        completion = client.chat_completion(
            model=_model_name(),
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Platform: {platform}\n"
                        f"Audience niche: {niche or 'general'}\n\n"
                        f"Post:\n{text[:1800]}"
                    ),
                },
            ],
            max_tokens=MAX_PROMPT_TOKENS,
            temperature=0.8,
        )
        written = (completion.choices[0].message.content or "").strip()
    except Exception as err:  # noqa: BLE001 — any failure means "use the template"
        log.info("[image-prompt] %s unavailable, using the template: %s", _model_name(), str(err)[:160])
        return Suggestion(
            prompt=fallback,
            source="template",
            note=f"Could not reach {_model_name()}, so this is the standard direction. Edit it freely.",
        )

    # A model that returned nothing useful is the same as one that failed.
    if len(written) < 40:
        return Suggestion(
            prompt=fallback,
            source="template",
            note="The model's suggestion came back empty, so this is the standard direction.",
        )

    # Strip a wrapping quote if the model ignored the instruction not to use one.
    if written[0] in "\"'“" and written[-1] in "\"'”":
        written = written[1:-1].strip()

    # The negative constraints are appended rather than trusted to the model: it was
    # asked not to include text, and asking is not the same as enforcing.
    return Suggestion(
        prompt=(
            f"{written}\n\n"
            "No lettering, no logo, no watermark, no UI mockup. Leave a calm, "
            "uncluttered focal area for real text to be added later."
        ),
        source="model",
        note="",
    )


class ImageRenderError(RuntimeError):
    """The image backend refused or returned something unusable. Message is user-facing."""


def _modal_runtime():
    """Indirection so the Modal path stays substitutable in tests.

    The SDK is a heavy optional dependency and is only imported when the user has
    actually provisioned their own GPU; keeping the import behind a named function also
    gives the suite one seam to replace instead of patching a package attribute.
    """
    from ..brandforge import modal_runtime

    return modal_runtime


def render(
    prompt: str,
    platform: str,
    hf_token: str,
    *,
    tool: str,
    use_modal: bool = False,
    modal_token_id: str = "",
    modal_token_secret: str = "",
) -> tuple[str, int, int]:
    """Draw `prompt` and save it. Returns (url, width, height).

    The prompt arrives already decided — whatever the user left in the box — so this
    does no rewriting of its own. Modal is used when the user has provisioned their
    own GPU in Settings, matching how Brand Studio chooses; otherwise the shared HF
    endpoint. Same split for all three composers, which is the point of it living here.
    """
    import io

    from PIL import Image

    from ..brandforge.client import BrandForgeError, text_to_image

    if not prompt.strip():
        raise ImageRenderError("There is no image prompt to draw.")
    if not hf_token.strip():
        raise ImageRenderError("Please connect your Hugging Face account in Settings.")

    width, height = dimensions_for(platform)
    on_modal = bool(use_modal and modal_token_id.strip() and modal_token_secret.strip())

    try:
        if on_modal:
            modal_runtime = _modal_runtime()
            cfg = modal_runtime.ModalConfig(
                token_id=modal_token_id.strip(),
                token_secret=modal_token_secret.strip(),
                hf_token=hf_token.strip(),
            )
            png = modal_runtime.generate_image(cfg, prompt, width, height)
            with Image.open(io.BytesIO(png)) as response_image:
                response_image.load()
                image = response_image.copy()
        else:
            image = text_to_image(hf_token.strip(), prompt)
    except ImportError as err:
        raise ImageRenderError(
            "The Modal SDK isn't installed in this build, so a personal GPU backend "
            "can't be used."
        ) from err
    except BrandForgeError as err:
        raise ImageRenderError(str(err)) from err
    except (OSError, ValueError) as err:
        raise ImageRenderError(f"The image backend returned invalid PNG data: {err}") from err

    path = output_path(tool)
    image.save(path)
    return outputs_url(path), width, height


def output_path(tool: str) -> Path:
    run_dir = config.OUTPUTS_DIR / tool / str(uuid.uuid4())
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / "post-image.png"


def outputs_url(path: Path) -> str:
    return "/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix()
