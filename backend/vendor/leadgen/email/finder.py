"""Deterministic email-pattern finder — the free replacement for a paid email-lookup API.

Generates ranked candidate work addresses from a contact name + verified domain, ordered by
how common each pattern is in the wild. If the domain already has a confirmed address (from
an earlier scrape), that pattern is inferred and floated to the front — the same trick
commercial finders use. Candidates are then handed to Reacher (verify.py), stopping at the
first that verifies. No network call happens here; this is pure string logic.
"""

from __future__ import annotations

import re

# Ordered by real-world prevalence for business email patterns.
_PATTERNS = [
    "{first}.{last}",
    "{first}",
    "{f}{last}",
    "{first}{last}",
    "{f}.{last}",
    "{first}_{last}",
    "{last}",
    "{last}.{first}",
    "{first}{l}",
]

# Used only when there is no contact name at all — low-confidence generic mailboxes.
_GENERIC = ["info", "contact", "hello", "sales", "office"]


def _norm(part: str) -> str:
    return re.sub(r"[^a-z]", "", part.lower())


def _split_name(contact_name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", contact_name.strip()) if p]
    if len(parts) >= 2:
        return _norm(parts[0]), _norm(parts[-1])
    if len(parts) == 1:
        return _norm(parts[0]), ""
    return "", ""


def _pattern_of(localpart: str, first: str, last: str) -> str | None:
    """Reverse a known local-part into one of our pattern templates, so a confirmed address
    tells us the domain's convention."""
    if not first or not last:
        return None
    f, l = first[0], last[0]
    table = {
        f"{first}.{last}": "{first}.{last}",
        first: "{first}",
        f"{f}{last}": "{f}{last}",
        f"{first}{last}": "{first}{last}",
        f"{f}.{last}": "{f}.{last}",
        f"{first}_{last}": "{first}_{last}",
        last: "{last}",
        f"{last}.{first}": "{last}.{first}",
        f"{first}{l}": "{first}{l}",
    }
    return table.get(localpart.lower())


def candidates(contact_name: str | None, domain: str, known_localparts: list[str] | None = None) -> list[str]:
    """Ranked candidate emails for a domain. Empty when there's nothing to go on."""
    domain = (domain or "").strip().lower()
    if not domain:
        return []

    first, last = _split_name(contact_name or "")
    if not first:
        # No name — only weak generic guesses.
        return [f"{g}@{domain}" for g in _GENERIC]

    ctx = {"first": first, "last": last, "f": first[:1], "l": last[:1]}
    ordered = list(_PATTERNS)

    # Float a pattern already confirmed for this domain to the front.
    for lp in known_localparts or []:
        pat = _pattern_of(lp, first, last)
        if pat and pat in ordered:
            ordered.remove(pat)
            ordered.insert(0, pat)
            break

    out: list[str] = []
    seen: set[str] = set()
    for pat in ordered:
        local = pat.format(**ctx).strip("._")
        if not local or (not last and "{last}" in pat):
            continue
        addr = f"{local}@{domain}"
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out
