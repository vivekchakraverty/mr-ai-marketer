"""Bluesky intent discovery — find people publicly posting about the problem you solve.

For audiences that live on social platforms rather than in business directories (indie
devs, creators, hobbyist communities), searching what people are actually posting surfaces
far warmer leads than Overpass or generic web search. This uses Bluesky's post-search API
(not the raw firehose — same "who's posting about X" outcome, a fraction of the overhead) and
reuses the Bluesky login already configured for the Social Post Generator / Engage
(BLUESKY_HANDLE / BLUESKY_APP_PASSWORD) — no new credentials.

Email outreach still needs an address, so we keep authors who expose a *personal* website in
their profile and derive a domain from it. Authors with only a platform link (itch.io,
Twitter, Discord, …) or no link are skipped — they're warm, but not emailable.
"""

from __future__ import annotations

import logging
import os
import re

from .base import RawLead, clause_terms, domain_of

log = logging.getLogger(__name__)

# Links that aren't a person's own emailable domain — skip them for email derivation.
_PLATFORM_DOMAINS = {
    "itch.io", "twitter.com", "x.com", "youtube.com", "youtu.be", "twitch.tv", "discord.gg",
    "discord.com", "linktr.ee", "bsky.app", "github.com", "gitlab.com", "patreon.com",
    "ko-fi.com", "buymeacoffee.com", "steampowered.com", "store.steampowered.com",
    "steamcommunity.com", "reddit.com", "instagram.com", "tiktok.com", "mastodon.social",
    "facebook.com", "medium.com", "substack.com", "notion.so", "carrd.co", "gumroad.com",
}
_URL_RE = re.compile(r"https?://[^\s)\]]+")


def configured() -> bool:
    return bool(
        (os.environ.get("BLUESKY_HANDLE") or "").strip()
        and (os.environ.get("BLUESKY_APP_PASSWORD") or "").strip()
    )


def _client():
    # Reuse the Social Post Generator's cached, authenticated client (atproto's createSession
    # is rate-limited, so sharing one login matters), exactly as engage.py does.
    from vendor.socialpost.src import bluesky as spg_bluesky

    return spg_bluesky.get_client()


def healthcheck() -> tuple[bool, str]:
    if not configured():
        return (
            False,
            "Connect Bluesky first — set BLUESKY_HANDLE / BLUESKY_APP_PASSWORD in Settings "
            "(shared with the Social Post Generator).",
        )
    try:
        _client()
        return True, f"Bluesky login OK ({os.environ.get('BLUESKY_HANDLE')})."
    except Exception as err:  # noqa: BLE001
        return False, f"Bluesky login failed: {str(err).splitlines()[0][:160]}"


def _personal_domain(url: str) -> str:
    d = domain_of(url)
    if not d or "." not in d:
        return ""
    # Reject platform hosts and their subdomains (e.g. someone.itch.io).
    if any(d == p or d.endswith("." + p) for p in _PLATFORM_DOMAINS):
        return ""
    return d


def _post_web_url(uri: str, handle: str) -> str:
    try:
        return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"
    except Exception:  # noqa: BLE001
        return ""


def search(clauses: dict, limit: int = 25) -> list[RawLead]:
    if not configured():
        return []
    query = clause_terms(clauses)
    try:
        client = _client()
        resp = client.app.bsky.feed.search_posts({"q": query, "limit": limit})
        posts = resp.posts
    except Exception as err:  # noqa: BLE001 — a search hiccup shouldn't crash the daemon tick
        log.warning("[leadgen] bluesky search failed: %s", str(err).splitlines()[0][:160])
        return []

    leads: list[RawLead] = []
    seen_domains: set[str] = set()
    seen_handles: set[str] = set()
    for post in posts:
        author = post.author
        handle = author.handle
        if handle in seen_handles:
            continue
        seen_handles.add(handle)

        try:
            profile = client.app.bsky.actor.get_profile({"actor": author.did})
            bio = getattr(profile, "description", "") or ""
        except Exception:  # noqa: BLE001
            bio = ""

        website = next((m for m in _URL_RE.findall(bio) if _personal_domain(m)), "")
        domain = _personal_domain(website)
        if not domain or domain in seen_domains:
            continue  # can't email without a personal domain (or already have this domain)
        seen_domains.add(domain)

        name = author.display_name or handle
        post_text = getattr(post.record, "text", "") or ""
        leads.append(
            RawLead(
                company=name,
                contact_name=name,
                website=website,
                domain=domain,
                source="bluesky",
                source_url=_post_web_url(post.uri, handle),
                profile_text=f'{name} on Bluesky (@{handle}) posted: "{post_text.strip()}". {bio.strip()}'.strip(),
            )
        )
    log.info("[leadgen] bluesky '%s' -> %d emailable authors", query, len(leads))
    return leads
