"""Self-hosted Reacher email verification — the open-source replacement for a paid verifier.

Calls the local Reacher backend (started by Electron alongside SearXNG). We never send to an
address Reacher doesn't consider deliverable.
"""

from __future__ import annotations

import logging

import httpx

from .. import config

log = logging.getLogger(__name__)

_TIMEOUT = 30

SAFE = "safe"
RISKY = "risky"
INVALID = "invalid"
UNKNOWN = "unknown"


def healthcheck() -> tuple[bool, str]:
    """Confirm the local Reacher answers. It has no dedicated health route, so a real (tiny)
    check against an obviously-invalid address doubles as the probe."""
    try:
        result, _ = verify("probe@example.com")
        return True, f"Reacher responding (probe -> {result})."
    except Exception as err:  # noqa: BLE001
        return False, f"Could not reach Reacher: {str(err).splitlines()[0][:160]}"


def verify(email: str) -> tuple[str, str]:
    """Return (result, detail) where result is safe|risky|invalid|unknown.

    Maps Reacher's `is_reachable` field. Any transport error surfaces as `unknown` with a
    detail string rather than raising, so a flaky verifier never crashes the daemon tick.
    """
    try:
        resp = httpx.post(
            f"{config.reacher_url()}/v0/check_email",
            json={"to_email": email},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as err:  # noqa: BLE001
        return UNKNOWN, f"verifier error: {str(err).splitlines()[0][:160]}"

    reachable = str(data.get("is_reachable", "unknown")).lower()
    if reachable == "safe":
        return SAFE, "deliverable"
    if reachable == "risky":
        return RISKY, "risky (accept-all/low-quality)"
    if reachable == "invalid":
        return INVALID, "undeliverable"
    return UNKNOWN, "verifier could not determine"


def find_and_verify(
    contact_name: str | None,
    domain: str,
    known_localparts: list[str] | None = None,
    scraped_email: str | None = None,
    accept_risky: bool = False,
) -> tuple[str | None, str]:
    """Resolve a deliverable address for a lead. If a scraped email exists, verify that
    first; otherwise walk ranked pattern candidates until one verifies. Returns (email, note)
    or (None, reason)."""
    from . import finder

    to_try: list[str] = []
    if scraped_email:
        to_try.append(scraped_email.strip().lower())
    to_try += [c for c in finder.candidates(contact_name, domain, known_localparts) if c not in to_try]

    if not to_try:
        return None, "no candidate addresses (missing name and domain)"

    first_risky: str | None = None
    for addr in to_try:
        result, _ = verify(addr)
        if result == SAFE:
            return addr, "verified safe"
        if result == RISKY and first_risky is None:
            first_risky = addr

    if accept_risky and first_risky:
        return first_risky, "accepted risky per config"
    return None, "no address passed verification"
