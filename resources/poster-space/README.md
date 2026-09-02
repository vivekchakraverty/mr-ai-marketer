---
title: Mr AI Marketer Poster
emoji: 📮
colorFrom: pink
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

Posts your scheduled Mastodon and Bluesky updates at the time you scheduled them,
so they go out whether or not the desktop app is running.

This is your own copy. The app duplicates it into your account and pushes your
credentials in as Space secrets — nothing here is shared with anyone else, and
nobody else's credentials are ever in it.

## How it works

Your queue is **not** in this Space. It lives in a private Hugging Face dataset in
your account (`OUTBOX_REPO`), and this Space reads it, posts what is due, and writes
the outcome back. Nothing confidential ever crosses this Space's HTTP surface, which
is why `/tick` can be open to anyone: waking it is all it does, and everything it
then acts on is already yours.

## Endpoints

| Route | Auth | What it does |
| --- | --- | --- |
| `GET /health` | none | Liveness. |
| `GET /tick` | none | Run one pass over the queue. Rate-limited; answers `{"ok": true}` and nothing else, so hitting it reveals nothing. |
| `GET /status` | `X-Poster-Key` | Queue depth, last tick, and whether Bluesky needs reconnecting. |

## Timing, honestly

On free hardware a Space sleeps after a period of inactivity, and Hugging Face does
not let a free Space configure that. This one keeps itself awake while it is running
and the desktop app wakes it when it is open, but a Space that has been asleep posts
when it is next woken.

**So: normally on time, worst case late, never lost.** Every pass fires everything
whose time has passed, not only what is due this minute. If exact timing matters,
upgrade this Space to paid hardware and it will not sleep at all.

To narrow the window for free, point a scheduler at `/tick`. **cron-job.org** is
free and does exactly this — add the address, set it to every 5 minutes, and turn
notifications off, since a sleeping Space is normal here and an uptime monitor will
otherwise mail you about it. This is a backstop rather than the mechanism: the Space
already pings itself while awake, and the desktop app wakes it when a post is nearly
due.

## Secrets and variables

Set for you by the app. Listed so you can see what is here.

| Name | Kind | What it is |
| --- | --- | --- |
| `OUTBOX_REPO` | variable | The private dataset holding your queue. |
| `SELF_URL` | variable | This Space's own URL, used for the keep-awake ping. |
| `MASTODON_HOST` | variable | Your instance, e.g. `mastodon.social`. |
| `BLUESKY_PDS`, `BLUESKY_DID` | variable | Your Bluesky server and account id. |
| `HF_TOKEN` | secret | Fine-grained, scoped to the outbox dataset alone. |
| `POSTER_KEY` | secret | Guards `/status`. |
| `MASTODON_TOKEN` | secret | Scoped to `write:statuses` and `write:media` — it cannot read your account. |
| `BLUESKY_REFRESH_JWT` | secret | A refresh session, not your app password. Rotates itself; revoke it by revoking the app password it came from. |
