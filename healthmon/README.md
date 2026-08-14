# healthmon

A standalone monitor for Mr. AI Marketer. **Once a day** it exercises every module and every
capability the app exposes — **without generating anything**. A separate on-demand mode runs
real generation when you actually want to pay for it.

It is deliberately separate from the app: nothing here imports the app's code. Every check
probes the same surfaces a user's machine does — HTTP endpoints, the WSL/Docker runtime,
the Hugging Face API — so a green run means the dependencies actually answer, rather than
that the modules import cleanly.

## Running it

```bash
python -m healthmon health     # the daily set — every module, no generation
```

```bash
python -m healthmon e2e        # real generation through real endpoints, on demand
```

Other modes: `report` re-renders the HTML from history without running anything,
`install` / `uninstall` manage the scheduled tasks.

Add `--open` to open the report when it finishes.

Exit codes are meant for schedulers: **0** all good, **1** something degraded, **2**
something is down.

## What it checks

**Infrastructure** — backend API, the WSL2 VM, the Docker daemon, all three containers by
name, the distribution engine, Lead Gen's SearXNG and Reacher, and both video-search tiers
(yt-dlp and the Piped instance network).

**Hugging Face Spaces** — BrandForge, Blog Writer, Email Writer, the third-party YouTube
search Space, and the mail-tracking Space.

**In-app modules** — one probe per *capability*, not per module, across every router the app
registers. Read surfaces (statuses, lists, schemas, catalogues, the posting-time curve for
both networks) are called for real.

**Work-only endpoints** — generation, crawling and signed-in fediverse calls have nothing to
GET, and calling them for real would spend inference on every run. They are held to their
**request contract** instead: the probe sends a body the request model must reject and
requires a `422` back. FastAPI validates before entering the handler, so nothing is
generated, nothing is crawled and no token is spent — while still proving the router loaded,
its request model is intact and auth let the call through. That is strictly more than the
old "is this path in `openapi.json`" check proved, at about a millisecond each.

If one of those probes ever returns a **2xx**, it is reported as degraded rather than
passing: a success means the payload it sends as invalid was accepted, so the model changed
underneath it and the handler just ran for real. Left as a pass, that would be a wasted
inference call on every scheduled run wearing the greenest badge on the page.

## Configuration

Everything is optional. Copy `.env.example` to `.env` if you need any of it.

| Variable | Why |
| --- | --- |
| `HF_TOKEN` | Required for `e2e`. Also lets the health run see the **private** BrandForge Space, which otherwise reports as skipped. |
| `YOUTUBE_API_KEY` | Reserved for e2e checks that need Data API quota. |
| `HEALTHMON_BACKEND_URL` | Defaults to `http://127.0.0.1:8756`. |
| `HEALTHMON_STATE_DIR` | Where history and the report are written. Defaults to `healthmon/state`. |

The monitor cannot read the app's own settings: Mr. AI Marketer stores them in `config.enc`,
encrypted with Electron's safeStorage (DPAPI on Windows), which nothing outside Electron can
decrypt. That is why the token is configured separately here.

## Scheduling

```bash
python -m healthmon install
```

Registers one Windows scheduled task:

| Task | Cadence | Generates? |
| --- | --- | --- |
| `MrAIMarketer-HealthCheck` | daily at 09:00 | no |
| `MrAIMarketer-E2E` | weekly, Sunday 03:00 — only with `--with-e2e` | **yes, spends inference** |

The daily run now covers every module and capability, so a second pass would re-establish
the same facts. The generation run is the only thing here that costs money, which is why it
is opt-in:

```bash
python -m healthmon install --with-e2e
```

Installing without that flag also *removes* an existing E2E task, so upgrading from the old
two-task setup doesn't quietly leave a weekly generation job behind.

Task Scheduler rather than a resident daemon, because a daemon would have to survive
reboots, sleep and its own crashes to be trusted daily — and schtasks already does. The task
runs whether or not the app is open.

Both are registered against the same interpreter that ran `install`, so the scheduled run
can't drift onto a different Python. If creation fails on permissions, run it from an
elevated prompt. `python -m healthmon uninstall` removes both.

## Building the .exe

```bash
pyinstaller healthmon/healthmon.spec --noconfirm --distpath healthmon/dist --workpath healthmon/build
```

Produces `healthmon/dist/SpaceHealthMonitor.exe` (~45 MB, onefile, no console). It carries
its own Python, so it runs on a machine with none — point a shortcut at it and nothing else
is needed.

Two things the spec has to get right, both of which fail quietly:

- **The excludes.** It is built from `backend/.venv`, which holds torch, transformers and
  chromadb for the app itself. Without cutting them explicitly, PyInstaller follows imports
  transitively and bundles gigabytes of model stack into a tray icon that makes HTTP
  requests. Nothing in `healthmon/` touches any of it.
- **Where state goes.** Frozen, `__file__` resolves inside the onefile extraction directory,
  which is deleted on exit — so `config.py` switches `STATE_DIR` to
  `%APPDATA%\mr-ai-marketer\healthmon` when `sys.frozen` is set. Otherwise history would be
  thrown away every run, silently, since each run would just find an empty file. The `.env`
  is read from beside the `.exe` for the same reason.

Running from source is unchanged: state stays in `healthmon/state/`.

## Output

- `state/history.jsonl` — one line per run, append-only. Keeping history is the point: "the
  engine was down at 09:00 and up at 21:00" can only be answered from it.
- `state/report.html` — the latest run plus a strip of the last 24, self-contained (no CDN,
  no fonts, no scripts) so it stays readable on a machine having the network problems it is
  reporting on.

## Reading the results

`SLEEPING` on a Space is **ok**, not an outage — free Spaces rest and wake on the first
request. Treating that as a failure would raise an alarm twice a day, forever. Genuine
Space failures are `RUNTIME_ERROR`, `BUILD_ERROR`, `CONFIG_ERROR` and `NO_APP_FILE`.

A stopped Lead Gen container is normal when you aren't using the Lead Gen Agent — the app
starts those on demand. The signal to act on is something being down that you expect to be
up.
