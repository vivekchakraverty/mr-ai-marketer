---
title: Social Media Post Generation
emoji: "🖼️"
colorFrom: blue
colorTo: gray
sdk: gradio
sdk_version: 6.18.0
app_file: app.py
python_version: "3.11"
pinned: false
---

## Setup

The Space keeps post-writing models on its CPU runtime and generates the companion
image through the owner's Modal GPU. Add these Space secrets in **Settings >
Variables and secrets**:

- `HF_TOKEN`: Hugging Face read token with access to `Abdelmageed95/caption_model`.
- `MODAL_TOKEN_ID`: Modal API token ID used to invoke `mr-ai-marketer-image-generator`.
- `MODAL_TOKEN_SECRET`: Modal API token secret used to invoke that function.

Before generating an image, deploy the Modal worker from the desktop app's
**Settings > Brand Studio GPU > Set up my GPU**. The worker syncs the FLUX.2
klein 4B Diffusers model from `hf://buckets/<your-username>/image-generator`
when Modal builds the image.
