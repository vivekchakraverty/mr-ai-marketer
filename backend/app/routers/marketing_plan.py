from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db
from ..services import rag_service
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


class GeneratePlanResponse(BaseModel):
    markdown: str
    seoMarkdown: str
    socialMarkdown: str
    adsMarkdown: str
    keywordSourceNote: str
    libraryId: str


_SOURCE_LABELS = {
    "google_ads_api": "Google Ads API (official)",
    "keyword_surfer": "live Keyword Surfer scrape",
    "autocomplete_trends": "Google Autocomplete + Trends (estimated)",
    "llm_estimate": "LLM estimate (no live data available)",
}


@router.post("/generate", response_model=GeneratePlanResponse)
def generate_plan(body: GeneratePlanRequest) -> GeneratePlanResponse:
    if not body.productDescription.strip():
        raise HTTPException(status_code=400, detail="Please describe your product or service.")
    if not body.hfToken.strip():
        raise HTTPException(status_code=400, detail="Please connect your Hugging Face account.")

    selected_model = None if body.model in ("Auto", "") else body.model
    utility_model = selected_model or llm.DEFAULT_MODEL
    industry_label = INDUSTRY_LABELS.get(body.industryKey, body.industryKey)

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

    title = body.name or "Your business"
    item = db.add_item(tool="Plan", title=f"{title} — Growth Plan", subtitle="Marketing plan", content=full_md)
    return GeneratePlanResponse(
        markdown=full_md,
        seoMarkdown=seo_md,
        socialMarkdown=social_md,
        adsMarkdown=ads_md,
        keywordSourceNote=keyword_source_note,
        libraryId=item["id"],
    )
