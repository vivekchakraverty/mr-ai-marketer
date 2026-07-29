"""Content-gap analysis: given a site's existing title library and a user topic, ask an
LLM to suggest ~5 guest-post ideas that fit the site's niche/audience but aren't already
covered. Billed to the user's HF token via pipeline.llm.chat().
"""
from __future__ import annotations

from typing import List

from . import cache, config, llm

_SYSTEM_TEMPLATE = """You are a guest-post content strategist. Given a website's existing
blog post titles and a topic area the writer wants to pitch, suggest {n} NEW guest-post
topic ideas that:
- clearly fit the site's existing niche, tone, and audience (inferred from its titles)
- relate to the requested topic area
- are NOT already covered by an existing title (avoid near-duplicates)
- are specific and pitchable (not generic like "Tips for Beginners")

Return exactly {n} ideas as a numbered list, each a single line: a short punchy title
followed by a one-sentence angle in parentheses. No preamble, no extra commentary."""


def suggest_topics(site, topic: str, title_library: List[str], hf_token: str) -> List[str]:
    key = cache.suggestions_key(site.domain, topic)
    cached = cache.get(key, "result")
    if cached:
        return cached["suggestions"]

    client = llm.make_client(hf_token)
    niche_info = f"Niche: {site.niche}. " if site.niche else ""
    user = (
        f"{niche_info}Site: {site.domain}\n"
        f"Requested topic area: {topic}\n\n"
        f"Existing post titles ({len(title_library)} total, sample below):\n"
        + "\n".join(f"- {t}" for t in title_library[:200])
    )
    reply = llm.chat(
        client, config.MODEL_SUGGEST,
        _SYSTEM_TEMPLATE.format(n=config.N_SUGGESTIONS), user,
        max_tokens=800, temperature=0.8,
        fallback_model=config.MODEL_SUGGEST_FALLBACK,
    )
    suggestions = [line.strip() for line in reply.splitlines() if line.strip()]

    cache.put(key, "result", {"suggestions": suggestions})
    return suggestions
