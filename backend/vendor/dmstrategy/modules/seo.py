"""SEO plan module: turns keyword research into clusters, a content calendar,
an on-page/technical checklist, and a link-building plan sized to manpower."""

from __future__ import annotations

import json

from modules import llm, rag
from modules.keywords import KeywordData

# SEO planning here is mostly structured decomposition (cluster keywords, build a
# calendar, prioritize a checklist, size a link plan to manpower) — closer to
# planning/synthesis than to raw factual QA or creative writing. GLM-5.2's scale
# and long-horizon planning specialization fit that better than a model picked
# for creative voice (social.py) or quantitative precision (ads.py).
RECOMMENDED_MODEL = "zai-org/GLM-5.2"


def _keyword_summary(keyword_data: list[KeywordData]) -> str:
    rows = []
    for kd in keyword_data:
        rows.append(
            {
                "keyword": kd.keyword,
                "volume": kd.volume,
                "cpc": kd.cpc,
                "related": kd.related[:8],
                "data_source": kd.source,
            }
        )
    return json.dumps(rows, indent=2)


def build_seo_plan(
    hf_token: str,
    product_description: str,
    manpower_summary: str,
    keyword_data: list[KeywordData],
    model: str | None = None,
) -> str:
    model = model or RECOMMENDED_MODEL
    sources_used = sorted({kd.source for kd in keyword_data}) or ["none"]
    rag_chunks = rag.retrieve(product_description, top_k=6, category=["seo", "general"])
    rag_context = rag.grounding_block(rag_chunks)

    prompt = f"""You are a senior SEO strategist. Using the keyword research data below, produce
an SEO plan.

Product/service: {product_description}
Available manpower: {manpower_summary}

Keyword research data (JSON — volume/CPC come from: {", ".join(sources_used)}; treat
"LLM estimate" or "relative interest (est.)" values as rough directional estimates, not
verified search data):
{_keyword_summary(keyword_data)}

## Grounding context from SEO books & industry publications
{rag_context}

Produce, in concise markdown:
1. **Keyword clusters** — group the keywords (and related terms) into 3-6 topical clusters,
   each with a primary target keyword and search intent (informational/commercial/transactional).
2. **Content calendar** — a first-90-days content calendar sized to the available manpower
   (fewer pieces/week if manpower is limited), one row per piece: title, target cluster,
   content type (blog/landing page/guide/video), and week number.
3. **On-page & technical SEO checklist** — a prioritized checklist appropriate for the team size.
4. **Link-building plan** — tactics sized to manpower (e.g. digital PR, guest posts, resource
   link building), with a realistic monthly link target.
5. **Step-by-step implementation guide** — a numbered, actionable sequence for executing this
   plan in the first 30 days (tooling/account setup, the first 3 content pieces to write and in
   what order, first technical fixes to make, first outreach to send), written so someone with no
   prior SEO experience could follow it.
6. State clearly which keyword data came from live/estimated sources per the tagging above.
"""
    return llm.chat(
        hf_token=hf_token,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.4,
    )
