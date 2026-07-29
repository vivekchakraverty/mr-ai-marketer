from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, db  # imports app.config as a side effect, which sets up the vendor sys.path
from .routers import (
    blog_writer,
    brand_forge,
    bluesky_analytics,
    distribution,
    docu_maker,
    email_writer,
    engage,
    guest_post,
    influencer_db,
    leadgen,
    library,
    mail,
    mail_tracking,
    marketing_plan,
    mastodon_post,
    settings,
    social_post,
    topic_scout,
    tutorial_maker,
)

app = FastAPI(title="Mr. AI Marketer backend")

# Backend only ever binds to 127.0.0.1 and is spawned/owned by the Electron app itself,
# so a permissive CORS policy here doesn't expose anything beyond the local machine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
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
    # The Lead Gen Agent's daemon; inert until a campaign is active + credentials set. The
    # Email Writer (app service) is injected as the outreach draft writer, and the mail
    # tracking service is injected the same way for opens/clicks/bounces on its sends.
    from vendor.leadgen import scheduler as leadgen_scheduler

    from .services import email_writer as email_writer_service
    from .services import mail_bounce as mail_bounce_service
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(settings.router)
app.include_router(library.router)
app.include_router(marketing_plan.router)
app.include_router(brand_forge.router)
app.include_router(blog_writer.router)
app.include_router(email_writer.router)
app.include_router(guest_post.router)
app.include_router(tutorial_maker.router)
app.include_router(docu_maker.router)
app.include_router(distribution.router)
app.include_router(social_post.router)
app.include_router(mastodon_post.router)
app.include_router(topic_scout.router)
app.include_router(influencer_db.router)
app.include_router(engage.router)
app.include_router(bluesky_analytics.router)
app.include_router(mail.router)
app.include_router(mail_tracking.router)
app.include_router(leadgen.router)

# Serves generated images/docs (backend/app/config.OUTPUTS_DIR) so the renderer can load
# them over http://127.0.0.1:<port>/outputs/... instead of file:// (unreliable from a
# renderer whose origin is the Vite dev server in dev mode).
app.mount("/outputs", StaticFiles(directory=str(config.OUTPUTS_DIR)), name="outputs")
