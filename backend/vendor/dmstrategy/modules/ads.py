"""Ads plan module: looks up static industry ad benchmarks (and real per-keyword
CPC data when the Google Ads API tier produced the keyword research) and asks
the LLM to allocate the user's budget across channels, sized to their manpower."""

from __future__ import annotations

import json
from pathlib import Path

from modules import llm, rag
from modules.keywords import KeywordData

# Ads planning is fundamentally quantitative — allocating a budget across channels
# and projecting clicks/conversions/CAC from CPC/CTR/CVR benchmarks means the
# arithmetic actually has to be right. DeepSeek's line is consistently the
# strongest open-weight performer on math/quantitative-reasoning benchmarks,
# which matters more here than general planning ability (seo.py) or creative
# voice (social.py).
RECOMMENDED_MODEL = "deepseek-ai/DeepSeek-V4-Pro"

_BENCHMARKS_PATH = Path(__file__).resolve().parent.parent / "data" / "ad_benchmarks.json"
_CHANNELS = ["search", "display", "meta", "linkedin", "tiktok", "youtube"]
_METRICS = ["cpc_usd", "ctr_pct", "cvr_pct"]

_benchmarks_cache: dict | None = None


def _load_benchmarks() -> dict:
    global _benchmarks_cache
    if _benchmarks_cache is None:
        _benchmarks_cache = json.loads(_BENCHMARKS_PATH.read_text(encoding="utf-8"))
    return _benchmarks_cache


def available_industries() -> list[str]:
    return sorted(_load_benchmarks()["industries"].keys())


def _average_benchmarks(all_industries: dict) -> dict:
    avg = {}
    for ch in _CHANNELS:
        avg[ch] = {}
        for m in _METRICS:
            values = [ind[ch][m] for ind in all_industries.values() if ch in ind]
            avg[ch][m] = round(sum(values) / len(values), 2) if values else None
    return avg


def get_industry_benchmarks(industry_key: str) -> dict:
    industries = _load_benchmarks()["industries"]
    if industry_key in industries:
        return industries[industry_key]
    return _average_benchmarks(industries)  # unknown industry: fall back to an overall average


def _real_keyword_cpc_block(keyword_data: list[KeywordData] | None) -> str:
    """Real per-keyword Search CPC bid ranges from the Google Ads API tier, if
    that's what produced the keyword research — a more accurate signal for
    this specific product than the generic industry benchmark, which is
    always included regardless as a fallback for the other channels."""
    if not keyword_data:
        return ""
    real = [kd for kd in keyword_data if kd.source == "google_ads_api" and kd.cpc]
    if not real:
        return ""
    rows = [{"keyword": kd.keyword, "volume": kd.volume, "cpc_bid_range": kd.cpc} for kd in real[:20]]
    return f"""
## Real per-keyword Search CPC data (Google Ads API, this product's actual keywords)
Prefer this over the generic industry Search CPC benchmark above when projecting Search
channel costs — it reflects this product's actual keywords, not an industry average.
{json.dumps(rows, indent=2)}
"""


def build_ads_plan(
    hf_token: str,
    product_description: str,
    budget_usd_per_month: float,
    manpower_summary: str,
    industry_key: str,
    geo: str,
    keyword_data: list[KeywordData] | None = None,
    model: str | None = None,
) -> str:
    model = model or RECOMMENDED_MODEL
    benchmarks = get_industry_benchmarks(industry_key)
    rag_chunks = rag.retrieve(product_description, top_k=6, category=["online_ads", "general"])
    rag_context = rag.grounding_block(rag_chunks)
    real_cpc_block = _real_keyword_cpc_block(keyword_data)

    prompt = f"""You are a senior digital advertising strategist. Using the industry benchmark
data below (CPC/CTR/CVR per channel — directional industry averages, not guarantees), propose a
paid advertising plan.

Product/service: {product_description}
Monthly budget: ${budget_usd_per_month:,.0f} USD
Available manpower: {manpower_summary}
Geography: {geo or "not specified"}

Industry benchmark data (JSON, per channel: cpc_usd, ctr_pct, cvr_pct):
{json.dumps(benchmarks, indent=2)}
{real_cpc_block}
## Grounding context from paid advertising books & industry publications
{rag_context}

Produce:
1. A recommended channel mix (which of Search, Display, Meta, LinkedIn, TikTok, YouTube to use
   and why), sized to the available manpower — fewer channels if manpower is limited.
2. A budget split across the chosen channels (USD/month and % of budget).
3. Projected monthly clicks, leads/conversions, and estimated CAC per channel, computed from the
   benchmark CPC/CTR/CVR figures and the allocated budget (for Search, use the real per-keyword
   CPC data above instead of the benchmark if it's present). Show the math briefly.
4. Note explicitly which figures are real (Google Ads API keyword data, if present) versus
   industry-average estimates.
5. **Step-by-step implementation guide** — a numbered, actionable sequence for launching this in
   the first 2 weeks (account/pixel setup per platform, campaign structure to create first,
   targeting/budget settings for the first campaign, what to check at the day-3 and day-14
   check-ins), written so someone with no prior paid-ads experience could follow it.

Format as concise markdown with a summary table.
"""
    return llm.chat(
        hf_token=hf_token,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1800,
        temperature=0.4,
    )
