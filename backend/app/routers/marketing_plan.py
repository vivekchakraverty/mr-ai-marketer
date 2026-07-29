from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from modules import ads, composer, keywords, llm, rag, seo, social

router = APIRouter(prefix="/marketing-plan", tags=["marketing-plan"])

# The 104k-chunk RAG index (~2.3GB) isn't bundled in the installer — it's pulled from this
# private HF Dataset on first use instead (see modules/rag.py's _maybe_download_private_dataset,
# which reads RAG_DATASET_ID/RAG_DATASET_TOKEN from the environment).
RAG_DATASET_ID = "vivekchakraverty/digital-marketer-rag-index"
os.environ.setdefault("RAG_DATASET_ID", RAG_DATASET_ID)

_RAG_INDEX_FILE = Path(rag.__file__).resolve().parent.parent / "rag_index" / "chroma.sqlite3"

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
    if _RAG_INDEX_FILE.exists():
        if rag.is_available():
            print(f"[marketing-plan] RAG index loaded: {rag.chunk_count()} chunks.")
        else:
            print("[marketing-plan] RAG index file present but failed to load — plans will be generated without RAG grounding.")
    else:
        print("[marketing-plan] RAG index not present locally — will attempt to download from "
              f"{RAG_DATASET_ID} on first plan generation.")


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
