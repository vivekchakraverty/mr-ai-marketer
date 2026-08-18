from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from ..services.genqueue import queue_slot
from pydantic import BaseModel

from .. import config, db
from ..services import (
    keyword_surfer,
    keyword_surfer_collector,
    marketing_plan_space,
    plan_export,
    rag_service,
)
from modules import ads, composer, keywords, llm, rag, seo, social

router = APIRouter(prefix="/marketing-plan", tags=["marketing-plan"])

# The 104k-chunk RAG index (~2.3GB) isn't bundled in the installer — it's pulled from this
# private HF Dataset on first use instead (see modules/rag.py's _maybe_download_private_dataset,
# which reads RAG_DATASET_ID/RAG_DATASET_TOKEN from the environment).
# User-supplied: the RAG index lives in your own HF Dataset. Empty means the plan falls
# back to generating without retrieval rather than reading a stranger's dataset.
RAG_DATASET_ID = config.MARKETING_PLAN_RAG_DATASET
os.environ.setdefault("RAG_DATASET_ID", RAG_DATASET_ID)

_RAG_INDEX_FILE = Path(rag.__file__).resolve().parent.parent / "rag_index" / "chroma.sqlite3"

# Signals that the background warm-up has finished (successfully or not). /generate waits on
# it briefly so a plan requested seconds after launch still gets grounded, instead of racing
# the warm-up and silently falling back to an ungrounded plan — rag._load() latches a
# "already attempted" flag on entry, so a concurrent caller would see an empty collection
# rather than block.
_rag_warm = threading.Event()

INDUSTRY_LABELS = {
    "ecommerce_retail": "Ecommerce / Retail",
    "apparel_fashion": "Apparel / Fashion",
    "b2b_saas": "B2B SaaS",
    "technology_electronics": "Technology / Electronics",
    "education": "Education",
    "finance_insurance": "Finance / Insurance",
    "health_medical": "Health / Medical",
    "home_improvement": "Home Improvement",
    "legal": "Legal",
    "real_estate": "Real Estate",
    "travel_hospitality": "Travel / Hospitality",
    "automotive": "Automotive",
    "beauty_personal_care": "Beauty / Personal Care",
    "restaurants_food": "Restaurants / Food",
    "fitness_wellness": "Fitness / Wellness",
    "nonprofit": "Nonprofit",
    "professional_services": "Professional Services",
    "furniture_home_goods": "Furniture / Home Goods",
    "industrial_manufacturing": "Industrial / Manufacturing",
    "consumer_services": "Consumer Services",
}


def _install_remote_pipeline() -> None:
    """Route the vendored pipeline through the retrieval Space without editing it.

    Two patches that only make sense together:

    * `rag.retrieve` stops returning passages and returns a **marker** instead — a single
      opaque string carrying the query and filters. The vendored modules paste it into their
      prompts exactly where the passages would have gone, and are none the wiser.
    * `llm.chat` notices a marker in the outgoing prompt and sends the whole prompt to the
      Space, which fills the marker in and runs the model there. The passages are assembled
      inside the Space and never travel; what comes back is the finished text.

    Patching the two ends rather than the four call sites keeps vendor/dmstrategy as upstream
    wrote it — the same reason this module configures that package through the environment
    instead of forking it — and it means the composer, SEO, ads and social stages are all
    covered by one change.

    Anything without a marker (there is none today, but the pipeline is free to add one) goes
    to the original local `llm.chat`, so this cannot silently redirect an ungrounded call.
    """
    local_chat = llm.chat

    def _marker(query: str, top_k: int = 8, category=None) -> list[str]:
        # A one-element list: `grounding_block()` joins whatever it gets, so the app keeps
        # owning the framing text around the passages and the Space only supplies the
        # "Source N:" lines themselves.
        return [rag_service.marker(query, top_k=top_k, category=category)]

    def _chat(hf_token: str, model: str, messages: list[dict], max_tokens: int = 2000,
              temperature: float = 0.4) -> str:
        prompt = "\n\n".join(str(m.get("content") or "") for m in messages)
        if not rag_service.has_marker(prompt):
            return local_chat(hf_token, model, messages, max_tokens=max_tokens,
                              temperature=temperature)
        try:
            return rag_service.compose(prompt, hf_token=hf_token, model=model,
                                       max_tokens=max_tokens, temperature=temperature)
        except rag_service.RagServiceError as err:
            # Fall back to generating here, ungrounded. Grounding is an enhancement; a plan
            # written without it beats a 502 in the middle of a ten-section generation the
            # user has already waited minutes for. The marker has to come out first — an
            # unresolved one is a line of base64 in the middle of the prompt.
            print(f"[marketing-plan] remote compose failed, generating ungrounded: {err}")
            cleaned = [
                {**m, "content": rag_service.strip_markers(str(m.get("content") or ""))}
                for m in messages
            ]
            return local_chat(hf_token, model, cleaned, max_tokens=max_tokens,
                              temperature=temperature)

    # All four vendored stages do `from modules import llm, rag` and call `llm.chat(...)` /
    # `rag.retrieve(...)` at call time, so rebinding the attributes here reaches every one of
    # them. (Checked rather than assumed — a `from modules.llm import chat` anywhere would
    # have taken its own reference and quietly escaped this.)
    rag.retrieve = _marker
    llm.chat = _chat


def initialize() -> None:
    """Warm the RAG index (embedding model + Chroma collection) once at startup —
    but only if it's already present locally.

    rag._load() sets a module-level "already attempted" flag the first time it's called,
    regardless of outcome, and never retries after a failure. Calling it here unconditionally
    would be fine on a dev machine that already has rag_index/ (fast local warm-up), but on a
    fresh install the index doesn't exist yet and downloading it needs a valid HF token — which
    isn't available yet at backend startup (it only arrives per-request from the connected
    account). Calling rag.is_available() here in that case would burn the one-shot load attempt
    with no token and permanently disable RAG grounding for the rest of the process's life.
    So: warm up eagerly only when the local file already exists; otherwise defer to the first
    real /generate request, which sets RAG_DATASET_TOKEN from the caller's HF token first.
    """
    # A configured plan Space replaces the whole pipeline, retrieval included — it does
    # its own grounding on its own index. Checked first because it subsumes the retrieval
    # service below: warming anything here would be work for a code path that no longer
    # runs.
    if marketing_plan_space.is_configured():
        print(
            f"[marketing-plan] generating via {config.MARKETING_PLAN_SPACE} — "
            "no local pipeline or index needed."
        )
        _rag_warm.set()
        return

    # A configured retrieval service replaces the local index entirely: no download, no
    # warm-up, nothing on disk. It also moves generation, not just lookup — see
    # _install_remote_pipeline for why the passages have to be assembled on the far side.
    if rag_service.is_configured():
        _install_remote_pipeline()
        print(f"[marketing-plan] retrieval and generation via {config.RAG_SERVICE_URL} — "
              "no local index needed.")
        _rag_warm.set()
        return

    if not _RAG_INDEX_FILE.exists():
        print("[marketing-plan] RAG index not present locally — will attempt to download from "
              f"{RAG_DATASET_ID or '(no dataset configured)'} on first plan generation.")
        _rag_warm.set()  # nothing to wait for
        return

    # Warm up on a background thread rather than inline.
    #
    # Loading the index means an embedding model plus a multi-GB Chroma collection, which
    # measured ~100s on a normal dev machine — past the 90s the desktop app waits for the
    # backend to report healthy, so doing it here killed startup outright and the whole app
    # failed to launch. Nothing else needs RAG to serve a request, so the API comes up
    # immediately and this finishes behind it.
    def _warm() -> None:
        try:
            if rag.is_available():
                print(f"[marketing-plan] RAG index loaded: {rag.chunk_count()} chunks.")
            else:
                print("[marketing-plan] RAG index file present but failed to load — plans will "
                      "be generated without RAG grounding.")
        except Exception as err:  # noqa: BLE001 — a warm-up must never take the backend down
            print(f"[marketing-plan] RAG warm-up failed: {err}")
        finally:
            # Set even on failure: waiters want "the attempt is over", not "it worked".
            _rag_warm.set()

    threading.Thread(target=_warm, name="marketing-plan-rag-warmup", daemon=True).start()


@router.get("/industries")
def list_industries() -> dict:
    return {"industries": [{"key": k, "label": v} for k, v in INDUSTRY_LABELS.items()]}


@router.get("/models")
def list_models() -> dict:
    """Whichever engine will actually run the plan decides what the dropdown offers.

    The Space enforces its own model policy, so listing the local pipeline's models while
    generating on the Space would offer options the Space rejects.
    """
    if marketing_plan_space.is_configured():
        return {"models": ["Auto"] + marketing_plan_space.available_models()}
    return {"models": ["Auto"] + llm.AVAILABLE_MODELS}


def _derive_seed_keywords(hf_token: str, model: str, product_description: str) -> list[str]:
    prompt = f"""Given this product/service description, list 8-12 seed keywords a potential
customer might search for. Respond ONLY with a JSON array of strings, no other text.

Product/service: {product_description}
"""
    raw = llm.chat(hf_token=hf_token, model=model, messages=[{"role": "user", "content": prompt}], max_tokens=400, temperature=0.3)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise llm.LLMError("Could not parse seed keywords from the model's response.")
    return json.loads(match.group(0))


class SurferProxy(BaseModel):
    """Proxy for the Space's Keyword Surfer tier.

    Empty is the normal state and simply leaves that tier blocked: Google shows an
    automated browser a captcha from ordinary addresses, so without a residential proxy
    the scrape returns nothing and the remaining keyword tiers carry the plan.
    """

    proxyServer: str = ""
    proxyUsername: str = ""
    proxyPassword: str = ""


class GoogleAdsCreds(BaseModel):
    developerToken: str = ""
    clientId: str = ""
    clientSecret: str = ""
    refreshToken: str = ""
    loginCustomerId: str = ""


class GeneratePlanRequest(BaseModel):
    name: str = ""
    productDescription: str
    budgetUsdPerMonth: float = 0
    manpowerSummary: str = ""
    industryKey: str = "ecommerce_retail"
    geo: str = ""
    hfToken: str
    model: str = "Auto"
    googleAds: GoogleAdsCreds = GoogleAdsCreds()
    keywordSurfer: SurferProxy = SurferProxy()


class KeywordRow(BaseModel):
    keyword: str
    volume: str = ""
    cpc: str = ""
    related: list[str] = []
    source: str = ""
    sourceLabel: str = ""


class PlanFile(BaseModel):
    """One exported file. `aspect` is which part of the plan it holds; "bundle" is all of them."""

    aspect: str
    label: str
    format: str
    path: str
    url: str


class GeneratePlanResponse(BaseModel):
    markdown: str
    seoMarkdown: str
    socialMarkdown: str
    adsMarkdown: str
    keywordsMarkdown: str = ""
    keywordRows: list[KeywordRow] = []
    keywordSourceNote: str
    libraryId: str
    files: list[PlanFile] = []


_SOURCE_LABELS = {
    "google_ads_api": "Google Ads API (official)",
    "keyword_surfer": "live Keyword Surfer scrape",
    "autocomplete_trends": "Google Autocomplete + Trends (estimated)",
    "llm_estimate": "LLM estimate (no live data available)",
}


class SurferTestResponse(BaseModel):
    ok: bool
    detail: str
    exitIp: str = ""
    usingProxy: bool = False
    sample: KeywordRow | None = None


@router.post("/keyword-surfer/test", response_model=SurferTestResponse)
def test_keyword_surfer(body: SurferProxy) -> SurferTestResponse:
    """One real lookup, so a proxy can be checked without generating a whole plan.

    Reports the exit address as well as the verdict. "Blocked" and "blocked from an
    address that isn't the proxy I configured" read identically otherwise, and only one of
    them means the settings are wrong.
    """
    result = keyword_surfer.probe(proxy=body.model_dump())
    sample = result.get("sample")
    return SurferTestResponse(
        ok=bool(result.get("ok")),
        detail=str(result.get("detail") or ""),
        exitIp=str(result.get("exitIp") or ""),
        usingProxy=bool(result.get("usingProxy")),
        sample=KeywordRow(**sample) if isinstance(sample, dict) else None,
    )


# --------------------------------------------------------------------------- collector
#
# The Keyword Surfer collector: a visible browser the user can reach into, driven from a
# tab in the Marketing Plan screen. Separate from the enrichment pass above — that one
# runs unattended and gives up quietly when Google objects, whereas this one exists
# precisely so somebody can answer Google when it does.


class SurferRunRequest(BaseModel):
    keywords: list[str] = []
    country: str = "us"
    delayMs: int = 7000
    maxSuggestions: int = 25


@router.get("/keyword-surfer/config")
def surfer_config() -> dict:
    return {
        "countries": keyword_surfer_collector.COUNTRIES,
        "minDelayMs": keyword_surfer_collector.MIN_DELAY_MS,
        "maxKeywordsPerRun": keyword_surfer_collector.MAX_KEYWORDS_PER_RUN,
        "storeUrl": keyword_surfer_collector.SURFER_STORE_URL,
    }


@router.get("/keyword-surfer/status")
def surfer_status() -> dict:
    """Browser state plus the active run, in one call — the tab polls this while a run is on."""
    return {
        "browser": keyword_surfer_collector.SESSION.status(),
        "run": keyword_surfer_collector.SESSION.current_run(),
    }


@router.post("/keyword-surfer/browser/open")
def surfer_open_browser(body: dict | None = None) -> dict:
    target = (body or {}).get("target")
    url = (
        keyword_surfer_collector.SURFER_STORE_URL
        if target == "store"
        else "https://www.google.com/"
    )
    try:
        return keyword_surfer_collector.SESSION.open_browser(url)
    except Exception as err:  # noqa: BLE001 — a browser that won't start is a 502, not a crash
        raise HTTPException(status_code=502, detail=f"Could not open the collector browser: {err}") from err


@router.post("/keyword-surfer/browser/close")
def surfer_close_browser() -> dict:
    return keyword_surfer_collector.SESSION.close_browser()


@router.post("/keyword-surfer/runs")
def surfer_start_run(body: SurferRunRequest) -> dict:
    try:
        return keyword_surfer_collector.SESSION.start_run(
            body.keywords, body.country, body.delayMs, body.maxSuggestions
        )
    except ValueError as err:
        # Empty input, too many keywords, or a run already going — all the caller's to fix.
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/keyword-surfer/runs/cancel")
def surfer_cancel_run() -> dict:
    run = keyword_surfer_collector.SESSION.cancel_run()
    if run is None:
        raise HTTPException(status_code=404, detail="No run to cancel.")
    return run


@router.get("/keyword-surfer/runs")
def surfer_list_runs() -> dict:
    return {"runs": keyword_surfer_collector.list_runs()}


@router.get("/keyword-surfer/runs/{run_id}")
def surfer_get_run(run_id: str) -> dict:
    run = keyword_surfer_collector.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/keyword-surfer/runs/{run_id}/csv")
def surfer_run_csv(run_id: str) -> Response:
    run = keyword_surfer_collector.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    csv_text = keyword_surfer_collector.run_csv(run)
    return Response(
        # utf-8-sig: without the BOM Excel reads the file as the local ANSI codepage and
        # turns every non-ASCII keyword into mojibake.
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="keyword-surfer-{run_id}.csv"'},
    )


class SurferExport(BaseModel):
    path: str
    url: str


@router.post("/keyword-surfer/runs/{run_id}/export", response_model=SurferExport)
def surfer_export_csv(run_id: str) -> SurferExport:
    """Write the run's CSV into the outputs tree and hand back its path.

    The renderer runs from file:// and the API needs its token, so a plain link to the
    endpoint above cannot be clicked from the UI. Every other export in this app works by
    writing the file and opening it, and this follows that.
    """
    run = keyword_surfer_collector.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    directory = config.OUTPUTS_DIR / "keyword-surfer"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"keyword-surfer-{run_id}.csv"
    # utf-8-sig, as bytes: csv rows already end in CRLF and text mode on Windows would
    # translate the LF again, putting a blank line between every row.
    path.write_bytes(keyword_surfer_collector.run_csv(run).encode("utf-8-sig"))
    return SurferExport(
        path=str(path),
        url="/outputs/" + path.resolve().relative_to(config.OUTPUTS_DIR.resolve()).as_posix(),
    )


@router.post("/keyword-surfer/runs/{run_id}/to-plan", response_model=list[KeywordRow])
def surfer_run_as_rows(run_id: str) -> list[KeywordRow]:
    """A finished run as keyword rows, so collected figures can feed a plan's sheet.

    Seeds and their suggestions are flattened into one table and de-duplicated, which is
    the shape the rest of the keyword pipeline already speaks.
    """
    run = keyword_surfer_collector.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    rows: dict[str, dict] = {}
    for result in run.get("results") or []:
        entries = [
            {
                "keyword": result.get("query") or "",
                "volume": result.get("volume"),
                "cpc": result.get("cpcDisplay") or result.get("cpc"),
                "related": [s.get("keyword", "") for s in (result.get("suggestions") or [])],
            }
        ]
        entries += [
            {
                "keyword": s.get("keyword") or "",
                "volume": s.get("volume"),
                "cpc": s.get("cpcDisplay") or s.get("cpc"),
                "related": [],
            }
            for s in (result.get("suggestions") or [])
        ]
        for entry in entries:
            key = entry["keyword"].strip().lower()
            if not key or key in rows:
                continue
            volume = entry["volume"]
            rows[key] = {
                "keyword": entry["keyword"],
                "volume": f"{volume:,}/mo" if isinstance(volume, (int, float)) and volume else "",
                "cpc": str(entry["cpc"]) if entry["cpc"] not in (None, "") else "",
                "related": entry["related"],
                "source": "keyword_surfer",
                "sourceLabel": "live Keyword Surfer scrape",
            }
    return [KeywordRow(**row) for row in rows.values()]


@router.post("/generate", response_model=GeneratePlanResponse, dependencies=[Depends(queue_slot("model"))])
def generate_plan(body: GeneratePlanRequest) -> GeneratePlanResponse:
    if not body.productDescription.strip():
        raise HTTPException(status_code=400, detail="Please describe your product or service.")
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account.")

    selected_model = None if body.model in ("Auto", "") else body.model
    utility_model = selected_model or llm.DEFAULT_MODEL
    industry_label = INDUSTRY_LABELS.get(body.industryKey, body.industryKey)

    if marketing_plan_space.is_configured():
        return _generate_via_space(body, industry_label)

    # First-ever call (no local rag_index/ yet): rag._load() downloads the private dataset
    # using RAG_DATASET_TOKEN, read at call time — set it from the caller's own connected HF
    # token so the one-shot download has a valid token available. No-op once the index is
    # already loaded (rag._load() short-circuits on its own "already attempted" flag).
    os.environ["RAG_DATASET_TOKEN"] = body.hfToken

    # Give a warm-up that started at launch time to finish. Bounded: if it is somehow still
    # going, generating without grounding beats making the user stare at a spinner.
    if not _rag_warm.wait(timeout=180):
        print("[marketing-plan] RAG warm-up still running — generating without grounding.")

    # keywords._google_ads_config() reads these env vars at call time (not import time),
    # so setting them fresh per-request is enough — it already no-ops the whole tier
    # unless every field is present, so partially-filled Settings just fall through to
    # the pipeline's next tier with no special-casing needed here.
    ga = body.googleAds
    os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"] = ga.developerToken
    os.environ["GOOGLE_ADS_CLIENT_ID"] = ga.clientId
    os.environ["GOOGLE_ADS_CLIENT_SECRET"] = ga.clientSecret
    os.environ["GOOGLE_ADS_REFRESH_TOKEN"] = ga.refreshToken
    os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = ga.loginCustomerId

    try:
        seed_keywords = _derive_seed_keywords(body.hfToken, utility_model, body.productDescription)
        keyword_data = keywords.research_keywords(seed_keywords, body.hfToken, utility_model, geo=body.geo)
        keyword_source_note = ", ".join(
            sorted({_SOURCE_LABELS.get(kd.source, kd.source) for kd in keyword_data})
        ) or "no keyword data available"

        seo_md = seo.build_seo_plan(body.hfToken, body.productDescription, body.manpowerSummary, keyword_data, model=selected_model)
        social_md = social.build_social_plan(
            body.hfToken,
            body.productDescription,
            body.manpowerSummary,
            industry_label,
            body.geo,
            industry_key=body.industryKey,
            model=selected_model,
        )
        ads_md = ads.build_ads_plan(
            body.hfToken,
            body.productDescription,
            body.budgetUsdPerMonth,
            body.manpowerSummary,
            body.industryKey,
            body.geo,
            keyword_data=keyword_data,
            model=selected_model,
        )
        full_md = composer.compose_plan(
            body.hfToken,
            body.productDescription,
            body.budgetUsdPerMonth,
            body.manpowerSummary,
            industry_label,
            body.geo,
            seo_md,
            ads_md,
            social_md,
            model=selected_model,
        )
    except llm.LLMError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err

    # Enriched on both paths, so the keyword table is the same table however the plan was
    # generated. The vendored pipeline has a Surfer tier of its own, but it is an
    # all-or-nothing fallback that never runs once an earlier tier returns anything.
    keyword_rows, keyword_source_note = _with_surfer(
        _local_keyword_rows(keyword_data), keyword_source_note, body
    )
    return _finish(
        body,
        industry_label,
        full_md=full_md,
        seo_md=seo_md,
        social_md=social_md,
        ads_md=ads_md,
        keywords_md=_keyword_markdown(keyword_rows, body.geo, keyword_source_note),
        keyword_rows=keyword_rows,
        keyword_source_note=keyword_source_note,
    )


def _local_keyword_rows(keyword_data) -> list[dict]:
    """The vendored pipeline's KeywordData as the same row shape the Space returns.

    Built here rather than in the vendored module so vendor/dmstrategy stays as upstream
    wrote it — the same reason this router configures that package through the environment
    instead of forking it.
    """
    return [
        {
            "keyword": kd.keyword,
            "volume": kd.volume or "",
            "cpc": kd.cpc or "",
            "related": list(kd.related or []),
            "source": kd.source,
            "sourceLabel": _SOURCE_LABELS.get(kd.source, kd.source),
        }
        for kd in keyword_data or []
    ]


def _keyword_markdown(rows: list[dict], geo: str, source_note: str) -> str:
    """The keyword table as a section, for the tab and the exported documents."""
    if not rows:
        return ""
    scope = f" · {geo.upper()}" if geo and geo.strip() else " · worldwide"
    columns = plan_export.SHEET_COLUMNS
    lines = [
        "# Keyword Research",
        "",
        f"**Data source:** {source_note or 'unknown'}{scope}",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        # A pipe inside a cell would end it early and shear the rest of the row into
        # phantom columns; related keywords are scraped text and do contain them.
        cells = [
            row.get("keyword", ""),
            row.get("volume") or "—",
            row.get("cpc") or "—",
            "; ".join(row.get("related") or []) or "—",
            row.get("sourceLabel") or row.get("source", ""),
        ]
        lines.append("| " + " | ".join(c.replace("|", r"\|").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines)


def _generate_via_space(body: GeneratePlanRequest, industry_label: str) -> GeneratePlanResponse:
    """Generate on the configured Space instead of running the pipeline here.

    The user's Google Ads credentials from Settings travel with the request so the official
    keyword tier runs against their own account; without them the Space falls through to
    its free tiers (headless-Chromium Keyword Surfer, then Autocomplete/Trends, then
    labelled LLM estimates) and says which one it used.
    """
    directory = plan_export.run_dir()
    try:
        result = marketing_plan_space.generate(
            product_description=body.productDescription,
            budget_usd_per_month=body.budgetUsdPerMonth,
            manpower_summary=body.manpowerSummary,
            industry_key=body.industryKey,
            geo=body.geo,
            model=marketing_plan_space.AUTO_MODEL_LABEL if body.model in ("Auto", "") else body.model,
            hf_token=body.hfToken,
            google_ads=body.googleAds.model_dump(),
            copy_files_to=directory,
        )
    except marketing_plan_space.PlanSpaceError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err

    rows, source_note = _with_surfer(
        result.keyword_rows,
        result.keyword_source_note or "no keyword data available",
        body,
    )
    return _finish(
        body,
        industry_label,
        full_md=result.full_markdown,
        seo_md=result.seo_markdown,
        social_md=result.social_markdown,
        ads_md=result.ads_markdown,
        keywords_md=_keyword_markdown(rows, body.geo, source_note),
        keyword_rows=rows,
        keyword_source_note=source_note,
        directory=directory,
    )


def _with_surfer(
    rows: list[dict], source_note: str, body: GeneratePlanRequest
) -> tuple[list[dict], str]:
    """Fold in a local Keyword Surfer scrape, if this machine can manage one.

    Deliberately done here rather than on the plan Space. Surfer's numbers only exist once
    its extension has run on a real Google results page, and Google answers an automated
    browser from a datacenter address with a captcha and no results — so the Space could
    never obtain them. This machine has an ordinary consumer network and, where that is
    not enough, the proxy configured in Settings.

    Enrichment only: any failure leaves the caller's rows exactly as they were.
    """
    if not rows:
        return rows, source_note

    try:
        surfer_rows = keyword_surfer.scrape(
            [str(r.get("keyword", "")) for r in rows],
            geo=body.geo,
            proxy=body.keywordSurfer.model_dump(),
        )
    except keyword_surfer.SurferUnavailable as err:
        print(f"[marketing-plan] Keyword Surfer unavailable, keeping existing data: {err}")
        return rows, source_note
    except Exception as err:  # noqa: BLE001 — enrichment must never fail a finished plan
        print(f"[marketing-plan] Keyword Surfer errored, keeping existing data: {err}")
        return rows, source_note

    if not surfer_rows:
        return rows, source_note

    merged = keyword_surfer.merge_into(rows, surfer_rows)
    print(f"[marketing-plan] Keyword Surfer enriched {len(surfer_rows)} of {len(merged)} keywords")

    # Rebuilt from the merged rows rather than appended to, so the note lists exactly the
    # tiers present in the table and cannot claim one that contributed nothing.
    tiers: list[str] = []
    for row in merged:
        for part in str(row.get("source") or "").split("+"):
            label = _SOURCE_LABELS.get(part, part)
            if part and label not in tiers:
                tiers.append(label)
    return merged, ", ".join(sorted(tiers)) or source_note


def _finish(
    body: GeneratePlanRequest,
    industry_label: str,
    *,
    full_md: str,
    seo_md: str,
    social_md: str,
    ads_md: str,
    keywords_md: str,
    keyword_rows: list[dict],
    keyword_source_note: str,
    directory: Path | None = None,
) -> GeneratePlanResponse:
    """Save the plan to the Library, write every aspect to disk, and answer.

    Shared by both engines so a plan is exported the same way however it was generated —
    the alternative was two copies of the save/export step that would drift.
    """
    title = f"{body.name or 'Your business'} — Growth Plan"
    bundle = plan_export.PlanBundle(
        title=title,
        markdown={
            "full": full_md,
            "keywords": keywords_md,
            "seo": seo_md,
            "social": social_md,
            "ads": ads_md,
        },
        keyword_rows=keyword_rows,
        keyword_source_note=keyword_source_note,
        geo=body.geo,
        budget_usd_per_month=body.budgetUsdPerMonth,
        industry_label=industry_label,
        manpower_summary=body.manpowerSummary,
    )
    try:
        written = plan_export.write_bundle(bundle, directory=directory)
    except Exception as err:  # noqa: BLE001 — a failed export must not lose the plan
        print(f"[marketing-plan] export failed, returning the plan without files: {err}")
        written = []

    # output_path points at the combined Word document so the Library's "open" action has
    # something to open, matching Blog Writer and Brand Studio.
    docx = next((f for f in written if f.aspect == "bundle" and f.fmt == "docx"), None)
    item = db.add_item(
        tool="Plan",
        title=title,
        subtitle="Marketing plan",
        content=full_md,
        output_path=str(docx.path) if docx else None,
    )
    return GeneratePlanResponse(
        markdown=full_md,
        seoMarkdown=seo_md,
        socialMarkdown=social_md,
        adsMarkdown=ads_md,
        keywordsMarkdown=keywords_md,
        keywordRows=[KeywordRow(**row) for row in keyword_rows],
        keywordSourceNote=keyword_source_note,
        libraryId=item["id"],
        files=[
            PlanFile(aspect=f.aspect, label=f.label, format=f.fmt, path=str(f.path), url=f.url)
            for f in written
        ],
    )
