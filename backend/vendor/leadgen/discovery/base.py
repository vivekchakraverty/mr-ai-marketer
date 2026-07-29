"""Shared types for discovery backends."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class RawLead:
    """A candidate business as returned by a discovery backend, before enrichment/embedding."""

    company: str = ""
    website: str = ""
    domain: str = ""
    email: str | None = None
    contact_name: str | None = None
    region: str | None = None
    source: str = ""  # overpass | searxng
    source_url: str = ""
    profile_text: str = ""
    extra: dict = field(default_factory=dict)


def clause_key(clauses: dict) -> str:
    """A stable hash of a clause set, so the same query is never enqueued twice."""
    return hashlib.sha256(json.dumps(clauses, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def clause_terms(clauses: dict) -> str:
    """The clause values as a single string — used both as the search query and as the
    keyword-injection terms appended to a lead's embedding."""
    return " ".join(str(v).strip() for v in clauses.values() if str(v).strip())


def domain_of(url: str) -> str:
    """Registered domain from a URL/website, lowercased. Best-effort; returns '' on junk."""
    if not url:
        return ""
    candidate = url if "//" in url else f"http://{url}"
    host = (urlparse(candidate).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host
