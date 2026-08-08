"""Generate a social-media post and a matching FLUX.2 visual on Modal."""

from __future__ import annotations

from functools import lru_cache
import io
import os
import re

import gradio as gr
import modal
from PIL import Image
import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    T5ForConditionalGeneration,
    T5Tokenizer,
)


CAPTION_MODEL_ID = "Abdelmageed95/caption_model"
POST_MODEL_ID = "bigscience/bloom-560m"
MODAL_APP_NAME = "mr-ai-marketer-image-generator"
MODAL_FUNCTION_NAME = "generate_image"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _model_kwargs() -> dict[str, str]:
    token = os.getenv("HF_TOKEN")
    return {"token": token} if token else {}


@lru_cache(maxsize=1)
def load_models():
    """Load CPU text models once so the Space starts quickly."""
    kwargs = _model_kwargs()
    try:
        caption_model = T5ForConditionalGeneration.from_pretrained(CAPTION_MODEL_ID, **kwargs).to(DEVICE)
        caption_tokenizer = T5Tokenizer.from_pretrained("google-t5/t5-base", **kwargs)
        post_model = AutoModelForCausalLM.from_pretrained(POST_MODEL_ID, **kwargs).to(DEVICE)
        post_tokenizer = AutoTokenizer.from_pretrained(POST_MODEL_ID, **kwargs)
    except Exception as error:
        raise gr.Error(
            "The text models could not be loaded. Add an HF_TOKEN Space secret with access to the caption model."
        ) from error

    caption_model.eval()
    post_model.eval()
    if post_tokenizer.pad_token is None:
        post_tokenizer.pad_token = post_tokenizer.eos_token
    return caption_model, caption_tokenizer, post_model, post_tokenizer


@lru_cache(maxsize=1)
def modal_image_function():
    """Authenticate the Space to the owner's non-public Modal function."""
    token_id = (os.getenv("MODAL_TOKEN_ID") or "").strip()
    token_secret = (os.getenv("MODAL_TOKEN_SECRET") or "").strip()
    if not token_id or not token_secret:
        raise gr.Error(
            "Add MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in the Space Settings before generating images."
        )
    try:
        client = modal.Client.from_credentials(token_id, token_secret)
        return modal.Function.from_name(MODAL_APP_NAME, MODAL_FUNCTION_NAME, client=client)
    except modal.exception.AuthError as error:
        raise gr.Error("Modal rejected the Space credentials. Check its two Modal secrets.") from error
    except Exception as error:
        raise gr.Error(f"Could not connect the Space to Modal: {error}") from error


def clean_post(text: str) -> str:
    text = re.sub(r"#\w+", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def generate_image(prompt: str) -> Image.Image:
    """Render one 1:1 social visual through the warm FLUX.2 Modal worker."""
    try:
        png = modal_image_function().remote(prompt, 1024, 1024, None, 4)
        with Image.open(io.BytesIO(png)) as response:
            response.load()
            return response.convert("RGB")
    except gr.Error:
        raise
    except modal.exception.NotFoundError as error:
        raise gr.Error(
            "The Modal image worker is not deployed yet. Run Set up my GPU in the desktop app Settings."
        ) from error
    except Exception as error:
        raise gr.Error(f"Modal image generation failed: {error}") from error


def generate_post(
    company_name: str,
    description: str,
    example1: str,
    example2: str,
    example3: str,
):
    if not all(value and value.strip() for value in (company_name, description)):
        raise gr.Error("Company name and description are required.")

    caption_model, caption_tokenizer, post_model, post_tokenizer = load_models()
    prompt = "\n".join(
        (
            f"{company_name} {description}, {example1}.",
            f"{company_name} {description}, {example2}.",
            f"{company_name} {description}, {example3}.",
            f"{company_name} {description},",
        )
    )

    with torch.inference_mode():
        post_inputs = post_tokenizer(prompt, return_tensors="pt").to(DEVICE)
        generated_ids = post_model.generate(
            **post_inputs,
            do_sample=True,
            temperature=0.7,
            max_new_tokens=70,
            repetition_penalty=5.4,
            pad_token_id=post_tokenizer.eos_token_id,
        )
        new_tokens = generated_ids[0, post_inputs["input_ids"].shape[1] :]
        post = clean_post(post_tokenizer.decode(new_tokens, skip_special_tokens=True))

        caption_inputs = caption_tokenizer(f"generate: {post}", return_tensors="pt").to(DEVICE)
        caption_ids = caption_model.generate(
            **caption_inputs,
            do_sample=True,
            temperature=0.7,
            min_new_tokens=8,
            max_new_tokens=30,
            repetition_penalty=5.4,
            top_p=1.0,
            top_k=50,
        )
        image_prompt = caption_tokenizer.decode(
            caption_ids[0], skip_special_tokens=True, clean_up_tokenization_spaces=True
        )

    image_prompt = (
        f"{image_prompt}, polished editorial social media visual, no lettering, no logo, no watermark, "
        "leave uncluttered space for a real headline"
    )
    return post, image_prompt, generate_image(image_prompt)


demo = gr.Interface(
    fn=generate_post,
    inputs=[
        gr.Textbox(label="Company name"),
        gr.Textbox(label="Company description", lines=3),
        gr.Textbox(label="Example post 1", lines=3),
        gr.Textbox(label="Example post 2", lines=3),
        gr.Textbox(label="Example post 3", lines=3),
    ],
    outputs=[
        gr.Textbox(label="Generated social post"),
        gr.Textbox(label="Image prompt"),
        gr.Image(label="Generated FLUX.2 image"),
    ],
    title="Social Media Post Generation",
    description="Generate a post and a matching FLUX.2 klein image through the owner's Modal GPU.",
)


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch()
