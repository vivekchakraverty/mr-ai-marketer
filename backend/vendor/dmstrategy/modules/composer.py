"""Final composer: merges the SEO plan, ads plan, and RAG context into one
client-ready markdown plan. RAG citations are numbered "Source N" only — the
index physically carries no title/author/URL, so the model is instructed
never to invent one."""

from __future__ import annotations

from modules import llm, rag


def compose_plan(
    hf_token: str,
    product_description: str,
    budget_usd_per_month: float,
    manpower_summary: str,
    industry: str,
    geo: str,
    seo_plan: str,
    ads_plan: str,
    social_plan: str,
    model: str | None = None,
) -> str:
    model = model or llm.DEFAULT_MODEL  # composer spans all domains; no single-task model fits best
    rag_chunks = rag.retrieve(product_description, top_k=8)  # no category filter: draws from the whole corpus
    rag_context = rag.grounding_block(rag_chunks)

    prompt = f"""You are a senior digital marketing consultant producing a final, client-ready
digital marketing plan. Combine the inputs below into ONE cohesive markdown document.

## Business context
Product/service: {product_description}
Monthly budget: ${budget_usd_per_month:,.0f} USD
Available manpower: {manpower_summary}
Industry: {industry or "not specified"}
Geography: {geo or "not specified"}

## SEO plan (already drafted)
{seo_plan}

## Paid advertising plan (already drafted)
{ads_plan}

## Organic social media plan (already drafted)
{social_plan}

## Grounding context from digital marketing books & industry publications
{rag_context}

## Output
Write the final plan as one markdown document with these sections:
1. Executive summary
2. Positioning & target audience
3. SEO strategy (synthesize, don't just repeat, the SEO plan above)
4. Content plan
5. Paid advertising strategy (synthesize the ads plan above)
6. Social media & organic channels (synthesize the social plan above)
7. Email / retention (if relevant to the budget/manpower)
8. Measurement & KPIs
9. 90-day roadmap
10. Team task allocation (map tasks to the available manpower)

Where you use grounding context, cite inline as "(Source N)". Keep it concise and actionable.
"""
    return llm.chat(
        hf_token=hf_token,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3500,
        temperature=0.4,
    )
