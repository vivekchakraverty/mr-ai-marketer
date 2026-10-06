# Buyer Persona

Open **Research / Strategy → Buyer Persona**. Select a saved Align analysis, review the available inputs, then generate.

## Evidence sources

- **Project information:** the Marketing Plan fields currently in the app, plus details in the selected saved analysis. The app does not have a general project entity, so selecting the correct source is important.
- **Align → Games:** timestamp-backed audience archetypes, observed actions, inferred mechanics, core loop, structural comparables and predicted discovery fit from the saved game analysis.
- **Align → Writing:** reviewed writing fingerprint, reader motivations, emotional and theme signals, comparable titles and rule-based channel plan.
- **Align → Music:** user-supplied style and release context, local Essentia measurements, optional MusicBrainz/ListenBrainz research and suggested destinations. These are audience overlap leads, not measured fans of the song.
- **Align → Visual Art:** reviewed labels and the PAMELA aggregate benchmark for other images with similar labels. These are neither responses to the user's artwork nor representative buyer data.
- **Saved persona research:** completed runs saved before the Audience Personas wizard was removed remain available as source data. Relevant answers, persona claims and linked imported first-party feedback are reused. Credential answers are excluded. Saved topic and source exclusions are honored during research. Marketing Plan can also use these saved personas as optional targets.
- **External research:** bounded public web search and page extraction. Search uses `BRAVE_SEARCH_API_KEY` when configured and otherwise uses public DuckDuckGo HTML search. Retrieved pages must pass the existing public HTTPS and robots checks. Research is cached in SQLite for seven days by default (`PERSONA_RESEARCH_TTL_DAYS`); empty searches are cached for one day. `PERSONA_RESEARCH_QUERIES` changes the default four-query limit.

Music and Visual Art derived reports now save automatically in local SQLite and can be reopened in Align. The original audio and image files are not stored. Saved Music and Visual Art reports appear in the Buyer Persona source selector.

## Generation and storage

The app uses the Hugging Face token already configured in Settings. `HF_PERSONA_MODEL` can override the default `Qwen/Qwen3-4B-Instruct-2507`; `HF_PERSONA_PROVIDER` selects its provider (default `nscale`, or automatic routing for a custom model). Calls use the server-side router at `https://router.huggingface.co/v1/chat/completions` with provider suffixes in the model name, avoiding the SDK's provider-mapping lookup. If a provider returns HTTP 500, is unavailable, times out, or returns unusable JSON, generation automatically tries the same 4B model through `featherless-ai`, then `Qwen/Qwen3.5-9B` through `deepinfra`. These routes are bounded to one attempt each per call. Failed routes are skipped for five minutes during subsequent personas. If all routes fail, a manual retry can try them again. Access-denial and billing errors stop immediately. `HF_PERSONA_FALLBACK_ROUTES` overrides the comma-separated `model:provider` alternatives; an empty value disables them. Changes apply when the backend restarts.

Each persona is generated in three short sections: profile, discovery journey, and activation. Each call has a 1,800-token output cap, and the prompt requests fewer than 1,300 tokens. Each section is validated and permits one repair for malformed or substantively incomplete output. Successful sections and complete personas are cached by route configuration and prompt, so a retry can reuse a profile even if its journey call timed out. Section errors identify the failed section and each attempted provider's status or failure category. Cached usage is labelled separately from new calls. Reports record the actual successful models and providers, including cached results. The cross-persona strategy is assembled from the validated personas in code. The background job and finished report live in the app's local SQLite database. The UI resumes polling an unfinished job when reopened. A restart marks unfinished jobs as errored; the last completed report remains available.

Sections request JSON Schema output with required fields and minimum item counts. Providers that reject this format can use JSON object mode, followed by the same application validation. Persona IDs and selected cluster links are assigned in code. Short evidence aliases in model inputs are resolved back to the canonical saved source IDs. A repair prefers an available different model, so an incomplete response is not repeatedly sent to the same small model. Failed repairs retain the section and validation fields in the job error.

## Notifications and visible permission requests

The top-bar bell opens the app's notification center. It retains app errors, persona completion notices and explicit model-test requests in local storage. Unresolved requests remain badged until answered. Escape and outside clicks close the panel; notifications survive navigation and restart.

A failed persona job creates **Allow a Hugging Face model test?** with **Allow and run test** and **Decline** actions. Opening the bell or declining sends no inference request. Allow starts a background check using the app's configured token and two fixed fictional music audiences. It does not load saved songs, Align analyses, project inputs or original media. It skips generation caches so a successful check reflects fresh provider calls, and the result appears in the bell. The test can consume inference credits. The API requires explicit consent and shares the persona service's single-job limit. Unfinished checks are marked stopped after a restart.

This notification center manages requests created by Mr. AI Marketer. It does not mirror or approve Codex's separate chat/tool permission controls.

Relevance components and confidence are calculated in application code. Scores are heuristic fit indicators. Music and Visual Art confidence is capped because their saved signals do not observe buyers of the user's work. Every report records its input snapshot, source links, research date, model, warnings and evidence IDs. Regenerate reuses recent research; Refresh Research fetches again while keeping the same saved project inputs.
