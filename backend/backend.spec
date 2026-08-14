# PyInstaller spec for the FastAPI backend. Built per-OS (PyInstaller can't cross-compile);
# run from backend/ via: pyinstaller backend.spec --noconfirm
#
# This dependency tree (torch, chromadb, transformers, opencv, google-ads, yt-dlp, ...) does
# a lot of dynamic/plugin-style importing that PyInstaller's static analysis can't see, hence
# the broad collect_all() list below rather than hand-picked hidden imports.
from PyInstaller.utils.hooks import collect_all

# Datasets and models are NOT bundled. They live in Hugging Face repos and are fetched on
# first use into the user's data directory — see app/services/hf_assets.py for why (a file
# inside a distributed app is a file every user has, and cannot be revoked). What stays here
# is configuration and code that the packages resolve relative to themselves.
datas = [
    ("vendor/dmstrategy/data/ad_benchmarks.json", "vendor/dmstrategy/data"),
    ("vendor/dmstrategy/data/social_benchmarks.json", "vendor/dmstrategy/data"),
    # vendor/socialpost resolves these at runtime relative to its own package root,
    # so they are load-bearing, not documentation: the migrations create its schema
    # on first connect, niches.yaml seeds the default niches, and .env.example is
    # parsed into the Settings screen's field list.
    ("vendor/socialpost/migrations/001_init_sqlite.sql", "vendor/socialpost/migrations"),
    ("vendor/socialpost/migrations/001_init.sql", "vendor/socialpost/migrations"),
    ("vendor/socialpost/config/niches.yaml", "vendor/socialpost/config"),
    ("vendor/socialpost/config/rss_sources.yaml", "vendor/socialpost/config"),
    ("vendor/socialpost/.env.example", "vendor/socialpost"),
    # vendor/leadgen parses its .env.example into the Settings screen's field list at runtime,
    # exactly like socialpost above — load-bearing, not documentation.
    ("vendor/leadgen/.env.example", "vendor/leadgen"),
]
binaries = []
hiddenimports = [
    "app.main",
    "app.routers.settings",
    "app.services.genqueue",
    "app.routers.library",
    "app.routers.marketing_plan",
    "app.routers.blog_writer",
    "app.routers.email_writer",
    "app.services.ctr_predictor",
    "app.routers.guest_post",
    "app.routers.tutorial_maker",
    "app.routers.docu_maker",
    "app.routers.social_post",
    # The Mastodon Post Creator. Its service module is imported at router scope,
    # but the vendored socialpost modules it reaches are resolved lazily inside
    # handlers, so they are already covered by the social_post entries below.
    "app.routers.mastodon_post",
    "app.services.mastodon",
    "app.services.mastodon_gate",
    # Engage's Mastodon panel — same service + gate, different verbs.
    "app.routers.mastodon_engage",
    # Engage's Tumblr panel. requests_oauthlib is reached only from the service
    # module, and oauthlib picks its signature method up by name at sign time.
    "app.routers.tumblr_engage",
    "app.services.tumblr",
    "requests_oauthlib",
    "oauthlib.oauth1",
    "app.routers.hashtags",
    "app.routers.posting_time",
    "app.services.hashtags",
    "app.routers.topic_scout",
    # vendor/topicscout is imported lazily inside the router, so static analysis
    # never sees it. Its sub-modules are reached only through engine.py.
    "vendor.topicscout.engine",
    "vendor.topicscout.models",
    "vendor.topicscout.sources",
    "vendor.topicscout.social",
    "vendor.topicscout.signals",
    "vendor.topicscout.sentiment",
    # Brand Studio's bring-your-own-Modal path. brand_forge.py imports these
    # inside the handlers (so the hosted-Space path still works in a build with
    # no modal SDK), which hides them from static analysis.
    "app.brandforge.modal_runtime",
    "app.brandforge.modal_backend",
    "app.brandforge.modal_image_backend",
    "app.routers.influencer_db",
    # The Community section. Both halves are listed: the bot poller and the Telethon account
    # client are reached through routers rather than imported at module scope anywhere on the
    # static path, and Telethon builds its request classes dynamically, so its generated
    # `tl` packages have to be collected wholesale further down.
    "app.routers.community",
    "app.routers.community_account",
    "app.services.telegram_community",
    "app.services.telegram_user",
    "app.routers.engage",
    "app.routers.mail",
    "app.routers.mail_tracking",
    "app.routers.leadgen",
    "app.routers.tracker",
    "app.services.email_writer",
    "app.services.mail_tracking",
    "app.services.mail_bounce",
    "app.services.posting_time_corpus",
    "app.services.posting_time_cli",
    # vendor/leadgen (the Lead Gen Agent) is imported lazily inside router/startup functions
    # to keep it off the fast path, which hides it from PyInstaller's static analysis. List its
    # entry points + the modules reached only through the lazily-imported scheduler chain.
    "vendor.leadgen.config",
    "vendor.leadgen.db",
    "vendor.leadgen.llm",
    "vendor.leadgen.scheduler",
    "vendor.leadgen.daemon",
    "vendor.leadgen.cli",
    "vendor.leadgen.ml.embedder",
    "vendor.leadgen.ml.qualifier",
    "vendor.leadgen.ml.labels",
    "vendor.leadgen.discovery.overpass",
    "vendor.leadgen.discovery.searxng",
    "vendor.leadgen.discovery.bluesky",
    "vendor.leadgen.discovery.enrich",
    "vendor.leadgen.discovery.icp",
    "vendor.leadgen.email.finder",
    "vendor.leadgen.email.verify",
    "vendor.leadgen.email.writer",
    "vendor.leadgen.email.sender",
    "vendor.leadgen.email.inbox",
    "vendor.leadgen.email.tracking",
    "vendor.leadgen.tasks.common",
    "vendor.leadgen.tasks.discover_qualify",
    "vendor.leadgen.tasks.find_email",
    "vendor.leadgen.tasks.email",
    "vendor.leadgen.tasks.follow_up",
    "vendor.leadgen.prompts.reasoning",
    "vendor.leadgen.prompts.outreach",
    # The router imports the vendored package lazily (inside functions) to keep it
    # off the startup path, which also hides it from PyInstaller's static analysis.
    "vendor.socialpost.src.generation",
    "vendor.socialpost.src.config",
    "vendor.socialpost.src.telemetry",
    "vendor.socialpost.src.llm",
    "vendor.socialpost.src.bluesky",
    "vendor.socialpost.src.embeddings",
    "vendor.socialpost.src.backends.sqlite_backend",
    # The scheduler resolves job modules by name via importlib, which is invisible
    # to static analysis for exactly the same reason.
    "vendor.socialpost.src.jobs.ingest",
    "vendor.socialpost.src.jobs.snapshot",
    "vendor.socialpost.src.jobs.refresh_exemplars",
    "vendor.socialpost.src.jobs.ingest_kb",
    "vendor.socialpost.src.jobs.telemetry",
    "vendor.socialpost.src.jobs.cleanup",
    "vendor.socialpost.src.jobs.authors",
    "pytrends",
    # vendor/dmstrategy/modules/*.py import each other as a top-level `modules` package
    # via a runtime sys.path.insert (see app/config.py) rather than a normal dotted
    # subpackage import — PyInstaller's static analysis can't see that trick, so list
    # the submodules explicitly and add the real directory to pathex below.
    "modules",
    "modules.ads",
    "modules.composer",
    "modules.keywords",
    "modules.llm",
    "modules.rag",
    "modules.seo",
    "modules.social",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# Packages with heavy dynamic imports / data files — collect everything rather than guessing.
_COLLECT_ALL = [
    "chromadb",
    "sentence_transformers",
    "transformers",
    "tldextract",
    "google.ads.googleads",
    "yt_dlp",
    "faster_whisper",
    "gradio_client",
    "huggingface_hub",
    "scenedetect",
    # vendor/socialpost: atproto builds its lexicon models dynamically, which defeats
    # static analysis in the same way the packages above do.
    "atproto",
    # sklearn's compiled Cython extensions (tree/_tree, utils/_typedefs, etc.) are a
    # well-known PyInstaller static-analysis blind spot; joblib.load()ing the CTR
    # model needs the exact class it was pickled with to be importable at runtime.
    "sklearn",
    "joblib",
    # Brand Studio's own-GPU path. modal ships a vendored cloudpickle and wires
    # its gRPC stubs up at import time; it also has to be fully present because
    # provisioning cloudpickles a function *out* of this build and into a
    # container (see app/brandforge/modal_backend.py).
    "modal",
    # Community's account half. Telethon generates its whole `tl` layer — hundreds of
    # request and type classes — and resolves them by name at call time, so listing the
    # handful this app calls would still miss everything they construct underneath.
    "telethon",
]
for pkg in _COLLECT_ALL:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run.py"],
    pathex=[".", "vendor/dmstrategy"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mr-ai-marketer-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="mr-ai-marketer-backend",
)
