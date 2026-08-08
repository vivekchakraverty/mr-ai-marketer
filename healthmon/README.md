# healthmon

A standalone monitor for Mr. AI Marketer. It checks the app's infrastructure, its Hugging
Face Spaces and its in-app modules **twice a day**, and runs **end-to-end tests once a
week**.

It is deliberately separate from the app: nothing here imports the app's code. Every check
probes the same surfaces a user's machine does — HTTP endpoints, the WSL/Docker runtime,
the Hugging Face API — so a green run means the dependencies actually answer, rather than
that the modules import cleanly.

## Running it

```bash
python -m healthmon health     # the twice-daily set — everything, cheaply
```

```bash
python -m healthmon e2e        # the weekly set — real generation through real endpoints
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

**In-app modules** — a cheap parameterless GET against each of the 15 modules that has one.
The six that are POST-only generation endpoints (Blog Writer, Email Writer, Guest Post,
Tutorial Maker, DocuMaker, Hashtags) are verified as *registered* in the health run — a
router that fails to import silently disappears from the API — and exercised for real in
the weekly run.

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

Registers two Windows scheduled tasks:

| Task | Cadence |
| --- | --- |
| `MrAIMarketer-HealthCheck` | daily at 09:00, repeating every 12h — i.e. 09:00 and 21:00 |
| `MrAIMarketer-E2E` | weekly, Sunday 03:00 |

Task Scheduler rather than a resident daemon, because a daemon would have to survive
reboots, sleep and its own crashes to be trusted twice a day — and schtasks already does.
The tasks run whether or not the app is open.

Both are registered against the same interpreter that ran `install`, so the scheduled run
can't drift onto a different Python. If creation fails on permissions, run it from an
elevated prompt. `python -m healthmon uninstall` removes both.

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
