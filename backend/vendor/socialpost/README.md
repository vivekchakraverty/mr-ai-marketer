# vendor/socialpost — Social Post Generator

The standalone project, vendored unmodified. Source lives at
`C:\social media post generator`; re-sync with:

```bash
python backend/vendor/socialpost/sync.py [SOURCE_DIR]
```

Do **not** hand-edit files here — changes belong upstream, or the next sync
silently reverts them.

## How it plugs in

| Piece | Where |
|---|---|
| HTTP surface | `backend/app/routers/social_post.py` |
| Settings surface | `backend/app/routers/settings.py` (`/settings/social-post/*`) |
| Per-user config | `DATA_DIR/social-post.env`, seeded by `app/config.py` |
| Per-user corpus | `DATA_DIR/social-post.sqlite3` |
| UI | `electron/src/renderer/src/routes/SocialPost.tsx` |

Imported as `vendor.socialpost.src.*`, the same shape as `vendor/docmaker`. That
works because the package uses **relative imports internally** and resolves its
own `config/` and `migrations/` relative to the package root — so it runs at any
nesting depth without modification.

## The one thing that is genuinely different

Every other vendored tool takes credentials **per request** (`hfToken` in the
body). This one reads them from the **process environment**, because it also runs
background jobs that have no request to read from.

The bridge: the renderer pushes settings to `POST /settings/social-post/env`, on
boot and on every save. That applies them to `os.environ` *and* persists them to
`DATA_DIR/social-post.env`, so a backend restart keeps working before the renderer
has pushed anything.

Mutating `os.environ` process-wide is safe here specifically because this is a
single-user desktop app — one user, one backend, spawned by their own Electron
session. It would not be safe in a multi-tenant server.

## Defaults chosen for the desktop context

`app/config.py` seeds the env file on first run with:

- `DB_BACKEND=sqlite` — per-user local data, no cloud account to create. (The
  Supabase backend still works if a user configures it.)
- `LLM_PROVIDER=hf` — the app is already Hugging Face-centric, and the HF provider
  needs no extra SDK.

## Background learning loop

`social_post.start_scheduler()` runs on backend startup. It calls jobs
**in-process** rather than as subprocesses (a packaged build has no interpreter to
shell out to — `sys.executable` is the bundled backend exe), on the same catch-up
cadence as the standalone scheduler, and self-skips entirely until the user has
configured credentials.

## Packaging

`backend.spec` must keep listing this package's data files (`migrations/*.sql`,
`config/*.yaml`, `.env.example`) — they are read at runtime, not documentation —
plus the lazily/dynamically imported modules under `hiddenimports`. A new job
module or data file needs a matching spec entry or it will only fail once packaged.
