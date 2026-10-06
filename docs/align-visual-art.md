# Align → Visual Art

The Visual Art subtab accepts a PNG, JPEG or WebP upload (20 MB maximum). The backend
validates it with Pillow, corrects EXIF orientation, downsizes it to at most 1024 px,
and sends that resized JPEG to a Hugging Face vision model for optional label
suggestions. The upload is not retained. Users can edit every label or choose them
manually when image inference is unavailable.

Label suggestions use `Qwen/Qwen3.5-9B` with thinking disabled. If Hugging Face
reports that no enabled provider serves it, the app tries `google/gemma-3-27b-it`.
Both are vision models; routing respects the user's enabled Inference Providers.
Access, billing, rate limit and other request failures are reported without retrying
another model. If neither model is available, enable a supporting provider at
[Hugging Face Inference Providers settings](https://huggingface.co/settings/inference-providers)
or choose labels manually. Model routing availability is distinct from token validity.

The matching step uses two distinct sources:

1. [Artsy's Art Genome categories](https://www.artsy.net/categories) provide a
   controlled vocabulary of visible subject and style. Artsy category links are
   discovery research paths; the app does not access Artsy user profiles, saves,
   sales, or engagement data.
2. The public [PAMELA archive](https://huggingface.co/buckets/vivekchakraverty/images)
   contains AI-generated images rated by human participants. Its
   [dataset card](https://huggingface.co/datasets/bethgelab/PAMELA) describes the
   study and CC BY 4.0 license. `pamela_affinity.json` contains only category-level
   aggregates from the `pamela_train` annotations. It contains no images, person
   IDs, or demographics. Regenerate it with
   `backend/.venv/Scripts/python.exe scripts/align_visual_art/build_pamela_index.py --remote`.
   The script reads ZIP byte ranges, downloading about 8 MB of compressed annotation
   data rather than the full 2.25 GB archive.

For a selected PAMELA group or style, a benchmark "admirer" is a training-set
participant with at least three ratings in that category, a mean of at least 4/5,
and a category mean at least 0.2 points above their own training-set average.
The report shows both the qualifying count and the eligible participant count,
plus mean rating, number of ratings, and number of images. This rule is a transparent
descriptive heuristic, not the PAMELA predictor model. The group and style rows
may contain many of the same participants and must not be added together.

The ratings were for **other AI-generated images**, not for the user's artwork.
The participant sample is not representative of the public, and these aggregates
cannot identify individual admirers, buyers, or a probability of purchase. The
vision model's category assignments can also be wrong, especially for mixed media
or work whose art-historical context cannot be established from pixels alone.

API: `GET /align/visual-art/choices`, `POST /align/visual-art/classify` (multipart
image), and `POST /align/visual-art/match` (reviewed labels). The match endpoint
works locally without a Hugging Face token.
