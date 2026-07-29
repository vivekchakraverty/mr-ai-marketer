"""OpenStreetMap / Overpass discovery backend.

Free, keyless, structured. We look for businesses that (a) sit inside the clause's named
area and (b) carry a website tag and a name matching the category/keyword terms — the OSM
analog of "firmographic profiles matching an ICP filter". Only publicly-mapped business
data is read; nothing is scraped here.
"""

from __future__ import annotations

import logging
import re

import httpx

from .. import config
from .base import RawLead, clause_terms, domain_of

log = logging.getLogger(__name__)

_TIMEOUT = 40


def _terms_regex(clauses: dict) -> str:
    """A case-insensitive alternation of the meaningful words in category + keyword, used to
    match business names. Locations are handled by the area filter, not the name regex."""
    words: list[str] = []
    for key in ("category", "keyword"):
        val = str(clauses.get(key, "")).strip()
        words += [w for w in re.split(r"[^A-Za-z0-9]+", val) if len(w) > 2]
    # De-dup, cap, and escape for the Overpass regex.
    seen: list[str] = []
    for w in words:
        if w.lower() not in [s.lower() for s in seen]:
            seen.append(w)
    return "|".join(re.escape(w) for w in seen[:6])


def build_query(clauses: dict, limit: int = 60) -> str | None:
    """Compose Overpass QL. Returns None when there is no usable location (an unbounded
    Overpass query would time out), so the caller can skip this backend for that clause."""
    location = str(clauses.get("location", "")).strip()
    if not location:
        return None
    regex = _terms_regex(clauses)
    name_filter = f'["name"~"{regex}",i]' if regex else ""
    # Businesses with a website inside the named area whose name matches the terms.
    return (
        "[out:json][timeout:25];\n"
        f'area["name"~"{re.escape(location)}",i]->.a;\n'
        "(\n"
        f'  nwr["website"]{name_filter}(area.a);\n'
        f'  nwr["contact:website"]{name_filter}(area.a);\n'
        ");\n"
        f"out center tags {limit};"
    )


def _to_lead(element: dict, clauses: dict) -> RawLead | None:
    tags = element.get("tags", {})
    website = tags.get("website") or tags.get("contact:website") or ""
    domain = domain_of(website)
    if not domain:
        return None
    name = tags.get("name", "").strip()
    city = tags.get("addr:city", "")
    state = tags.get("addr:state", "")
    country = tags.get("addr:country", "")
    region = ", ".join([p for p in (city, state, country) if p]) or str(clauses.get("location", ""))
    category = (
        tags.get("shop")
        or tags.get("office")
        or tags.get("craft")
        or tags.get("amenity")
        or tags.get("healthcare")
        or str(clauses.get("category", ""))
    )
    profile = f"{name} — {category} in {region}." if name else f"{category} in {region}."
    return RawLead(
        company=name or domain,
        website=website,
        domain=domain,
        email=tags.get("contact:email") or tags.get("email"),
        region=region,
        source="overpass",
        source_url=website,
        profile_text=profile.strip(),
        extra={"osm_category": category},
    )


def search(clauses: dict, limit: int = 60) -> list[RawLead]:
    query = build_query(clauses, limit=limit)
    if not query:
        return []
    try:
        resp = httpx.post(config.overpass_url(), data={"data": query}, timeout=_TIMEOUT)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception as err:  # noqa: BLE001 — a backend hiccup shouldn't crash the daemon tick
        log.warning("[leadgen] overpass query failed: %s", str(err).splitlines()[0][:160])
        return []

    leads: list[RawLead] = []
    seen: set[str] = set()
    for el in elements:
        lead = _to_lead(el, clauses)
        if lead and lead.domain not in seen:
            seen.add(lead.domain)
            leads.append(lead)
    log.info("[leadgen] overpass '%s' -> %d businesses", clause_terms(clauses), len(leads))
    return leads
