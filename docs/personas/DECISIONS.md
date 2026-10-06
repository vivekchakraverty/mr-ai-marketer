# Decisions and assumptions

- The repo controls implementation when the pasted prompt's context differs. The active social-post generation provider is Hugging Face, while Brand Studio also offers Modal. Personas use deterministic local analysis first; no model is needed or billed.
- A 150-unit/two-source threshold is too high for small businesses. Thin data still yields clearly marked hypothesis personas, never a claim of validated demand.
- Sources that require credentials stay off until the user enters credentials through the app's encrypted settings. Research-plan approval is a second, explicit gate.
- Do not infer protected demographics. Optional user supplied demographic context is displayed as `user-assumed` and is not used for segmentation.
- The public Hacker News, Stack Exchange and Bluesky endpoints issue no API key. They stay switched off until the user enables them, approves a concrete plan and confirms source terms. Mastodon and YouTube receive keys from Electron's encrypted Settings only for the live job.
- A run with fewer than five usable evidence units stops with a data request. With 5–149 units or one independent source, results are low-confidence hypotheses. Pageviews are a trend signal only and never count toward persona evidence.
- This feature does not call an LLM; TF-IDF, clustering and transparent heuristics keep demo and normal analysis offline and avoid a required model expense. A future optional LLM pass, if added, must use an affordable Hugging Face model with a call cap and spend logging.
- User label and summary edits are kept by evidence overlap on refresh. If the underlying evidence changes so much that no segment overlaps by at least half, the edit remains saved on the old snapshot and is not attached to a different group.
