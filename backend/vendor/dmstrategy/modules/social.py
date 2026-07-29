"""Social media strategy module: organic platform mix, content pillars, and a
posting cadence/calendar sized to manpower. Paid social spend (Meta/TikTok/
LinkedIn ad budget) is handled separately in ads.py — this module is organic
content & community strategy only."""

from __future__ import annotations

import json
from pathlib import Path

from modules import llm, rag

# Creative, platform-voice content generation benefits more from a model tuned
# for writing quality than from raw reasoning-benchmark scores. Qwen3-235B-A22B
# scores strongly on independent creative-writing evaluations, is Apache 2.0,
# and its MoE design (22B active of 235B total) keeps per-post generation cheap
# — useful here since a content calendar means many short generations, not one
# long document.
RECOMMENDED_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"

_BENCHMARKS_PATH = Path(__file__).resolve().parent.parent / "data" / "social_benchmarks.json"
_benchmarks_cache: dict | None = None


def _load_benchmarks() -> dict:
    global _benchmarks_cache
    if _benchmarks_cache is None:
        _benchmarks_cache = json.loads(_BENCHMARKS_PATH.read_text(encoding="utf-8"))
    return _benchmarks_cache


def _benchmarks_block(industry_key: str) -> str:
    data = _load_benchmarks()
    industry_note = data["industry_engagement_notes"].get(industry_key)
    return f"""
## Real platform benchmark data ({", ".join(data["sources"])})
Per-platform engagement rate, posting frequency, best posting windows, audience
demographics, and dominant content format (JSON):
{json.dumps(data["platforms"], indent=2)}
{"Industry-specific engagement signal for this business: " + industry_note if industry_note else "No industry-specific engagement signal for this industry in the benchmark report — treat all platforms as median."}
Use this real data to justify platform prioritization and posting cadence, rather than
generic assumptions about which platforms are "popular."
"""


def build_social_plan(
    hf_token: str,
    product_description: str,
    manpower_summary: str,
    industry: str,
    geo: str,
    industry_key: str = "",
    model: str | None = None,
) -> str:
    model = model or RECOMMENDED_MODEL
    rag_chunks = rag.retrieve(product_description, top_k=6, category=["social_media", "general"])
    rag_context = rag.grounding_block(rag_chunks)
    benchmarks_block = _benchmarks_block(industry_key)

    prompt = f"""You are a senior organic social media strategist (not paid social ads — that's
handled separately). Using the business context below, produce an organic social media plan.

Product/service: {product_description}
Available manpower: {manpower_summary}
Industry: {industry or "not specified"}
Geography: {geo or "not specified"}
{benchmarks_block}
## Grounding context from social media books & industry publications
{rag_context}

Produce, in concise markdown:
1. **Platform mix** — which 2-4 platforms to prioritize (e.g. Instagram, TikTok, LinkedIn,
   YouTube Shorts, X, Pinterest) and why, sized to the available manpower — fewer platforms if
   manpower is limited. Cite the real engagement rate/demographic data above to justify the
   choice, not just general impressions of platform popularity.
2. **Content pillars** — 3-5 recurring content themes tailored to this product/audience, each
   with 2-3 example post/video concepts, matched to each platform's dominant content format.
3. **Posting cadence & first-30-days calendar** — a realistic weekly posting frequency per
   platform (anchored to the real posts/week benchmarks above, adjusted for manpower), including
   the real best-posting-window data, and a first-30-days calendar (week-by-week, not necessarily
   post-by-post) mapping content pillars to platforms.
4. **Engagement & community tactics** — concrete tactics for replies, UGC, collaborations/
   influencer seeding, and community-building appropriate to the team size.
5. **Step-by-step implementation guide** — a numbered, actionable sequence for actually setting
   this plan up in the first two weeks (accounts/profile setup, content batching workflow, tools,
   first posts to publish, what to review after week 1), written so someone with no prior social
   media management experience could follow it.

Keep it concrete and platform-specific rather than generic social media advice.
"""
    return llm.chat(
        hf_token=hf_token,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.5,
    )
