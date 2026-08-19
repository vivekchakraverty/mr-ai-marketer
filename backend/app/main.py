import os
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .services.genqueue import QueueFull as GenQueueFull

from . import config, db  # imports app.config as a side effect, which sets up the vendor sys.path
from .routers import (
    backup,
    blog_writer,
    community,
    community_account,
    brand_forge,
    bluesky_analytics,
    distribution,
    docu_maker,
    email_writer,
    engage,
    guest_post,
    hashtags,
    influencer_db,
    leadgen,
    library,
    mail,
    mail_tracking,
    marketing_plan,
    mastodon_engage,
    mastodon_post,
    posting_time,
    settings,
    social_post,
    topic_scout,
    tracker,
    tumblr_engage,
    tumblr_post,
    tutorial_maker,
)

app = FastAPI(title="Mr. AI Marketer backend")

# Endpoints that answer without a token. `/health` is how Electron knows the backend has
# finished starting, and it returns nothing but the word "ok"; the docs routes describe the
# API surface, which is public in this repo anyway.
_OPEN_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

# The one prefix that answers without the session token, because the caller cannot send it:
# the Distribute engine runs in its own container and fetches an image by URL to attach it
# to a post. Everything under it is signed and expiring and names exactly one file inside
# OUTPUTS_DIR — see services/share_links.py. It is a prefix rather than a member of
# _OPEN_PATHS only because the token is part of the path.
_OPEN_PREFIXES = ("/shared/",)

# The header the renderer sends. A custom name rather than Authorization so it can never be
# confused with a credential for one of the upstream services this app talks to.
API_TOKEN_HEADER = "x-mraim-token"


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    """Reject anything that cannot prove it is this app.

    Binding to 127.0.0.1 is not access control. Any web page the user has open can fetch
    http://127.0.0.1:8756/… , and because the packaged renderer runs from file:// the CORS
    policy below has to stay permissive — so the browser will hand that page the response.
    Confirmed against a running backend: a request carrying `Origin: https://evil.example`
    got 200 and `access-control-allow-origin: *` from /library, and a POST preflight to
    /community/broadcast was approved.

    A shared per-session token closes it, because a page on another site has no way to read
    one. Preflights pass through untouched — the browser sends them without custom headers by
    definition, and CORSMiddleware answers them before the real request is allowed to run.
    """
    if not config.API_TOKEN:
        return await call_next(request)  # dev mode; see the warning printed at startup
    if (
        request.method == "OPTIONS"
        or request.url.path in _OPEN_PATHS
        or request.url.path.startswith(_OPEN_PREFIXES)
    ):
        return await call_next(request)
    supplied = request.headers.get(API_TOKEN_HEADER, "")
    # compare_digest, not ==, so a wrong token cannot be narrowed down by timing.
    if not supplied or not secrets.compare_digest(supplied, config.API_TOKEN):
        return JSONResponse(
            status_code=401,
            content={"detail": "This API only answers the app that started it."},
        )
    return await call_next(request)


# Permissive by necessity, not by choice: the packaged renderer loads from file:// and sends
# `Origin: null`, so an origin allowlist would have to accept a value any sandboxed iframe can
# also send. The token middleware above is the actual boundary — this only decides which
# browser reads are *attempted*, not which succeed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _publish_token() -> None:
    """Write the session token where local tools can find it.

    The health monitor and the maintenance scripts are not the renderer and never get handed
    a token, but they are entitled to one — they run on this machine with the user's own
    permissions. A file is the correct channel precisely because the attacker this protects
    against is a web page, and a web page cannot read files.

    Rewritten every start: the token changes each session, and a stale one would send local
    tools into a confusing 401 instead of a clean reconnect.
    """
    try:
        if config.API_TOKEN:
            config.API_TOKEN_FILE.write_text(config.API_TOKEN, encoding="utf-8")
        elif config.API_TOKEN_FILE.exists():
            # No token this run: remove the old one rather than leave a file implying a
            # protection that is not in force.
            config.API_TOKEN_FILE.unlink()
    except OSError as err:
        print(f"[auth] could not publish the API token for local tools: {err}")


@app.on_event("startup")
def on_startup() -> None:
    if config.API_TOKEN:
        _publish_token()
    else:
        print("[auth] MRAIM_API_TOKEN is not set — this API will answer ANY local caller, "
              "including any web page open in a browser. Expected for `uvicorn app.main:app` "
              "during development; a packaged app always sets it.")
    # The container that posts on the user's behalf cannot reach 127.0.0.1 here, so a
    # second listener carries signed image links to it — bound to the WSL adapter only,
    # and only when the app supplies that address. See services/share_server.py.
    share_host = os.environ.get("MRAIM_SHARE_HOST", "").strip()
    if share_host:
        from .services import share_server

        share_server.start(share_host, int(os.environ.get("MRAIM_SHARE_PORT", "8756")))

    db.init_db()
    guest_post.initialize()
    marketing_plan.initialize()
    distribution.start_scheduler()
    # The Social Post Generator only improves if its collect/measure/rebuild loop
    # keeps running; the thread self-skips until the user configures credentials.
    social_post.start_scheduler()
    # Same deal for the Mastodon Post Creator: it measures the posts the user
    # published and tops its corpus up. Inert until an instance's rules have been
    # accepted, so an untouched install never talks to anyone's server.
    mastodon_post.start_scheduler()
    # The Tumblr Post Creator re-reads the standalone collector's corpus daily, since that
    # crawl is resumable and keeps growing. Inert when no corpus file is present, so it
    # makes no noise on an install that does not have one.
    tumblr_post.start_scheduler()
    # The Lead Gen Agent's daemon; inert until a campaign is active + credentials set. The
    # Email Writer (app service) is injected as the outreach draft writer, and the mail
    # tracking service is injected the same way for opens/clicks/bounces on its sends.
    from vendor.leadgen import scheduler as leadgen_scheduler

    from .services import email_writer as email_writer_service
    from .services import mail_bounce as mail_bounce_service
    from .services import telegram_community
    from .services import mail_tracking as mail_tracking_service

    leadgen_scheduler.start_scheduler(
        draft_writer=email_writer_service.generate_marketing_email,
        tracking_prepare=mail_tracking_service.prepare_for_leadgen,
        tracking_finalize=mail_tracking_service.finalize_for_leadgen,
        tracking_bounce=mail_tracking_service.record_bounce_for_leadgen,
    )
    # Pulls opens/clicks from the mail-tracker Space, and polls the Mail Composer's
    # own mailbox for bounce notifications — both inert (or a cheap no-op) until
    # there's anything sent/configured to check.
    mail_tracking_service.start_sync_loop()
    mail_bounce_service.start_bounce_poller()
    bluesky_analytics.start_scheduler()
    # The Community section's Telegram bot. Long-polls for join requests and payments;
    # inert until a bot token is configured in that screen.
    telegram_community.start_poller()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/queue")
def queue() -> dict:
    """How much generation work is in flight. Polled by the queue indicator.

    Deliberately outside the token-protected tool routers and as cheap as /health: the
    renderer polls this while anything is running, and it must never be the thing that is
    slow or contended.
    """
    from .services import genqueue

    return genqueue.status()


@app.exception_handler(GenQueueFull)
async def _queue_full(_request: Request, exc: GenQueueFull) -> JSONResponse:
    """429 rather than 500: the request was refused because the queue is full, which is a
    "come back shortly", not a fault. The message is already user-facing — the queue raises
    it with the wait described in plain words — so it passes straight through as `detail`,
    matching the shape every other error in this app uses.
    """
    return JSONResponse(status_code=429, content={"detail": str(exc)})


app.include_router(settings.router)
app.include_router(backup.router)
app.include_router(library.router)
app.include_router(marketing_plan.router)
app.include_router(brand_forge.router)
app.include_router(blog_writer.router)
app.include_router(community.router)
app.include_router(community_account.router)
app.include_router(email_writer.router)
app.include_router(guest_post.router)
app.include_router(tutorial_maker.router)
app.include_router(docu_maker.router)
app.include_router(distribution.router)
app.include_router(social_post.router)
app.include_router(mastodon_post.router)
app.include_router(hashtags.router)
app.include_router(posting_time.router)
app.include_router(topic_scout.router)
app.include_router(influencer_db.router)
app.include_router(engage.router)
app.include_router(mastodon_engage.router)
app.include_router(tumblr_engage.router)
app.include_router(tumblr_post.router)
app.include_router(bluesky_analytics.router)
app.include_router(mail.router)
app.include_router(mail_tracking.router)
app.include_router(leadgen.router)
app.include_router(tracker.router)

# Serves generated images/docs (backend/app/config.OUTPUTS_DIR) so the renderer can load
# them over http://127.0.0.1:<port>/outputs/... instead of file:// (unreliable from a
# renderer whose origin is the Vite dev server in dev mode).
app.mount("/outputs", StaticFiles(directory=str(config.OUTPUTS_DIR)), name="outputs")


@app.get("/shared/{token:path}")
def shared_file(token: str):
    """One generated file, by signed link, without the session token.

    This is how the Distribute engine attaches an image: it runs in its own container, so
    it cannot send the header the renderer sends, and a post with a picture needs a URL
    the engine itself can fetch. The link is signed, expiring and names one path inside
    OUTPUTS_DIR — see services/share_links.py for why it is shaped that way.

    A bad, expired, forged or out-of-bounds token is all one answer: 404. Distinguishing
    them would tell an unauthenticated caller which files exist.
    """
    from fastapi.responses import FileResponse

    from .services import share_links

    path = share_links.resolve(token)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path)
