"""Bridge to the app's mail-tracking service — records opens/clicks/bounces for
outreach this agent sends.

Mirrors email/writer.py's exact shape: the app injects plain callables once at
startup (app/main.py -> scheduler.start_scheduler -> set_tracking_hooks here),
so this vendored package stays decoupled from app.* and is trivially testable
with fakes. Deliberately **fail-soft**, unlike writer.py's fail-hard
_require_writer() — drafting has no fallback, but tracking does: if a hook is
unset or raises, sending must proceed exactly as it did before this feature
existed (no HTML alternative, no recorded event), never regress or block a
real send.

is_bounce() is a pure classifier, independent of the hooks above — it just
decides whether an inbox message (from email/inbox.py's fetch_recent(), now
carrying the extra dsn_* fields that module adds) is a bounce notification.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# (to_email, subject, body, deal_id) -> (mail_message_id, html_body) | None
PrepareHook = Callable[[str, str, str, str], Optional[tuple[str, str]]]
# (mail_message_id, message_id, status, error) -> None
FinalizeHook = Callable[[Optional[str], Optional[str], str, Optional[str]], None]
# (payload dict) -> None
BounceHook = Callable[[dict], None]

_prepare: Optional[PrepareHook] = None
_finalize: Optional[FinalizeHook] = None
_bounce: Optional[BounceHook] = None

_MAILER_DAEMON_HINTS = ("mailer-daemon", "postmaster", "mail delivery subsystem")
_BOUNCE_SUBJECT_HINTS = (
    "undelivered mail returned to sender",
    "delivery status notification (failure)",
    "mail delivery failed",
    "returned mail",
    "undeliverable",
)


def set_tracking_hooks(
    prepare: Optional[PrepareHook] = None,
    finalize: Optional[FinalizeHook] = None,
    bounce: Optional[BounceHook] = None,
) -> None:
    """Injected once by app/main.py (the mail-tracking service). Tests inject
    fakes, or nothing at all — every consumer below tolerates that."""
    global _prepare, _finalize, _bounce
    if prepare is not None:
        _prepare = prepare
    if finalize is not None:
        _finalize = finalize
    if bounce is not None:
        _bounce = bounce


def prepare(to_email: str, subject: str, body: str, deal_id: str) -> Optional[tuple[str, str]]:
    """Returns (mail_message_id, html_body), or None if tracking isn't wired up
    or fails for any reason — the caller sends the plain body as-is either way."""
    if _prepare is None:
        return None
    try:
        return _prepare(to_email, subject, body, deal_id)
    except Exception:  # noqa: BLE001 — tracking must never block a real send
        return None


def finalize(mail_message_id: Optional[str], message_id: Optional[str], status: str, error: Optional[str] = None) -> None:
    if _finalize is None or mail_message_id is None:
        return
    try:
        _finalize(mail_message_id, message_id, status, error)
    except Exception:  # noqa: BLE001
        pass


def is_bounce(reply: dict) -> tuple[bool, dict]:
    """Classify one inbox message (email/inbox.py's fetch_recent() shape) as a
    bounce or not. A well-formed RFC 3464 report is trusted structurally
    (its own Action field, 'failed' only — 'delayed' is a transient retry
    notice, not a bounce); anything else falls back to loose From/Subject
    heuristics for MTAs that don't send a spec-compliant report."""
    if reply.get("is_report"):
        return reply.get("dsn_action") == "failed", reply

    from_addr = (reply.get("from") or "").lower()
    subject = (reply.get("subject") or "").lower()
    if any(h in from_addr for h in _MAILER_DAEMON_HINTS):
        return True, reply
    if any(h in subject for h in _BOUNCE_SUBJECT_HINTS):
        return True, reply
    return False, reply


def handle_possible_bounce(reply: dict) -> bool:
    """If `reply` is a bounce, hand it to the injected bounce hook and report
    True so the caller skips normal reply-handling for it. Fail-soft: an
    unwired or raising hook still returns True (it *was* a bounce, just not
    recorded), so a bounce never gets mistaken for an unmatched human reply."""
    is_b, dsn = is_bounce(reply)
    if not is_b:
        return False
    if _bounce is not None:
        try:
            _bounce({**reply, **dsn})
        except Exception:  # noqa: BLE001
            pass
    return True
