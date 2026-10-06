# Align → Games: Game Audience Analyzer

The Games sub-tab accepts a gameplay video and builds an evidence-led report without asking for a title. It works with unknown games and prototypes. The application is still usable when IGDB is unconfigured, but comparable games and catalog-backed platform signals are then unavailable.

## Architecture

`AlignGames.tsx` uploads to the token-protected local FastAPI router. The router checks extension, MIME type and byte limit, sanitizes the display filename, hashes the upload, and puts the raw file in a private temporary directory. A bounded background worker records progress in the app's SQLite database. FFprobe reads duration, resolution, FPS, codec, audio presence and aspect ratio. FFmpeg samples JPEG frames from overlapping chunks. Only sampled frames are sent to a private Modal function; the raw video is never published. A text-only Qwen pass synthesizes the full recording after all chunk observations are merged. The local backend queries IGDB v4, ranks structural candidates, and estimates behavioral audience and platform fit. The report and evidence remain local.

The SQLite table `game_analysis_jobs` stores status, metadata, chunk observations, final report and cost metrics separately from the temporary source file. Incomplete jobs become failed on app restart. The startup sweep removes abandoned private temporary uploads. A byte-identical upload reuses an existing completed or running analysis.

## Setup

From `backend/`, install the pinned dependencies with `python -m pip install -r requirements.txt`. FFmpeg and FFprobe must be on `PATH`; packaged builds already prepend the bundled binaries. For a development run, set the environment variables below, then run `python -m uvicorn app.main:app --host 127.0.0.1 --port 8756`. Run the Electron app from `electron/` with `npm run dev`.

When Modal credentials are present, the first analysis deploys the private Games function into your Modal workspace if it is missing. This first run can take several minutes. You can also deploy it ahead of time from `backend/`:

```powershell
$env:MODAL_TOKEN_ID = '<your Modal token ID>'
$env:MODAL_TOKEN_SECRET = '<your Modal token secret>'
python -m modal deploy -m app.game_analysis.modal_backend
```

The GPU image includes both PyTorch and torchvision; Qwen's processor requires torchvision even when the app sends still images. The first GPU call downloads `Qwen/Qwen3-VL-2B-Instruct` into the persistent Modal Volume `mr-ai-marketer-qwen-vl-cache`; later container starts reuse the cache. The default GPU is L4. To change model, GPU or image dependencies, redeploy; an existing deployment is not rebuilt automatically. Set `GAME_ANALYSIS_QWEN_MODEL` or `GAME_ANALYSIS_MODAL_GPU` **before deployment** when changing them. Keep the local backend's model setting in sync for documentation and future runtime swaps. The local backend invokes the named Modal function through the SDK, without a public inference URL or a local GPU. The app can reuse Modal credentials saved under Brand Studio or Email Writer; explicit game-specific or standard Modal environment credentials take precedence. Restart the desktop app after changing saved credentials so the backend process receives them.

Enter your Twitch developer application's Client ID and Client Secret under **Settings > Games — IGDB**, save, then restart the desktop app. They are stored in the existing encrypted settings store and passed to the local backend at startup. Alternatively, set `IGDB_CLIENT_ID` and `IGDB_CLIENT_SECRET` in the backend process environment. The backend obtains and caches a Twitch client-credentials OAuth token; the access token never reaches the renderer. On a completed Games report, use **Refresh comparable games** to add catalog matches and update platform estimates without reuploading footage or repeating GPU inference. Check the [IGDB API terms](https://api-docs.igdb.com/) for your commercial usage before shipping a product that relies on its data.

Optional controls: `GAME_ANALYSIS_MAX_BYTES` (default 524288000), `GAME_ANALYSIS_MAX_DURATION_SECONDS` (600), `GAME_ANALYSIS_SAMPLE_FPS` (1), `GAME_ANALYSIS_CHUNK_SECONDS` (30), `GAME_ANALYSIS_CHUNK_OVERLAP_SECONDS` (2), `GAME_ANALYSIS_MAX_FRAMES_PER_CHUNK` (32), `GAME_ANALYSIS_MAX_ACTIVE` (1), and `GAME_ANALYSIS_RETAIN_UPLOADS` (0). The UI allows 1 or 2 FPS; frame count per chunk remains capped. With retention enabled, raw uploads move to a private data directory and can be removed with the analysis delete endpoint.

## API

- `GET /align/games/status` reports whether Modal and IGDB credentials reached the backend, without exposing their values.
- `POST /align/games/analyze` accepts multipart `file` and `sampling_fps`, returns `analysis_id` and status (202).
- `GET /align/games` lists recent reports; `GET /align/games/{id}` polls status and returns the full report.
- `GET /align/games/{id}/events`, `/comparables`, `/audience`, and `/platforms` return report sections.
- `DELETE /align/games/{id}` removes the derived report and any retained raw upload.
- `POST /align/games/{id}/refresh-comparables` updates catalog matches and platform estimates on a completed report using its stored gameplay profile.

Progress states are queued, preprocessing_video, analyzing_gameplay, merging_analysis, querying_igdb, ranking_comparables, modeling_audience, generating_recommendations, completed, and failed.

## How scores are formed

Qwen chunk outputs store timestamped visible observations and events separately from inferred mechanics and uncertain claims. Structured responses have a 4,096-token budget. A JSON schema constrains decoding to valid field types, required confidence values, bounded prose, six observations and four events per chunk; this prevents repeated entries from consuming the response budget. The GPU image pins LM Format Enforcer to revision `817f944fcc8851917c40300a47f62d1defc3ffc3`, which includes Transformers 5 compatibility. Generation uses [Qwen's published sampling settings](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct/blob/main/generation_config.json) (temperature 0.7, top-p 0.8, top-k 20). The merger deduplicates overlapping events and collects traits across every chunk. A synthesis pass writes the description and behavioral archetypes from that merged evidence. A claim or archetype is displayed only if it links to a stored observation timestamp.

IGDB taxonomy names from the profile are mapped to genre, theme, keyword and perspective IDs. Keyword lookups use case-insensitive `where name ~` filters because the keyword endpoint does not support `search`. Genre aliases such as “first-person shooter” and “tactical shooter” map to “Shooter”; perspective punctuation and common aliases are normalized for retrieval and scoring. Free-form labels without a corresponding catalog taxonomy are excluded from taxonomy comparisons. The backend retrieves candidates through those structural concepts and requests names, summaries, storylines, tags, modes, perspectives, platforms, release dates, similar-game IDs, ratings and counts, companies, media, websites and hypes where present. Ranking uses configurable weights: mechanics 35%, genre 15%, theme 10%, perspective 10%, core loop 15%, pace/complexity 5%, and audience words 10%. Missing profile dimensions are excluded from the denominator. IGDB ratings and counts are displayed as source data and do not raise similarity scores.

Hardware predictions combine visible input/UI suitability with how often top structural comparables list that hardware. Storefront predictions inherit the hardware fit and flag publication feasibility as unassessed. Community predictions estimate how easily the visible play might be communicated through each format. These are **predicted fit**, never proven demand. IGDB website presence is not treated as success evidence. A count-adjusted rating signal is recorded separately and does not set the fit score.

## Limits and verification

The model observes sampled still frames, so sound, very brief actions and events between frames can be missed. FFmpeg timestamps approximate sampled frame positions. The text synthesis can still misinterpret evidence; inspect the report's claim and timeline panels. The in-process queue survives as a failed record after restart but does not resume interrupted GPU work. IGDB's metadata does not measure buyers or community performance. Live Modal verification completed on 2026-10-05 with the original 50.71-second gameplay clip at 2 FPS: 113 sampled frames, four chunks and five model calls, with the report saved through the running app. Live IGDB verification completed on 2026-10-06 using the saved Twitch credentials: the corrected keyword filter succeeded, 143 candidates were retrieved, and 10 ranked entries were saved in the existing report without another GPU call.

Run `backend/.venv/Scripts/python.exe -m pytest app/tests/test_game_analysis.py -q` from `backend/`, and `npm run typecheck` from `electron/`. The tests cover decodable, no-audio and corrupt media, duration and chunk bounds, grounded Qwen output, malformed output, merge deduplication, OAuth caching, IGDB query/rate-limit behavior, taxonomy aliases, structural ranking, platform explanations, upload validation and catalog refresh without GPU calls. Future work includes tuning the similarity weights against human-rated pairs and adding durable queue recovery for long analyses.
