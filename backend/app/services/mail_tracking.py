"""Email open/click tracking — the local half.

The public half (the actual pixel/redirect endpoints a recipient's mail client
hits) runs on a small free Hugging Face Space this app owns, `mail-tracker`,
built the same way the Email Writer's Space was: the URL and its sync secret
are hardcoded here rather than user-configurable, exactly like Email Writer's
`_SPACE_ID` in services/email_writer.py. That Space is a single deployment
shared by every installation of this app (there's no per-user boundary on it),
so SYNC_SECRET only deters casual scraping of its /events feed, not a
determined reader of this file — the real privacy boundary is client-side:
`sync_from_space()` below discards any event whose token isn't a message this
installation actually sent.

Covers both SMTP send paths in this app: the Mail Composer (called directly
from routers/mail.py) and the Lead Gen Agent's outreach (injected into
vendor/leadgen as a callable, mirroring how the Email Writer itself is already
injected as `draft_writer` — see app/main.py's startup wiring).
"""

from __future__ import annotations

from .. import config

import html
import logging
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .. import db

log = logging.getLogger(__name__)

# Your own tracking Space (see README). Empty disables open/click tracking rather than
# routing anyone's mail through a third party's host.
SPACE_BASE_URL = config.MAIL_TRACKER_URL
# Read from the environment, never hardcoded. The value that used to sit here was a real
# shared secret for one person's Space; committed, it was a secret every reader of the repo
# had. Empty means the sync is skipped rather than sent unauthenticated.
SYNC_SECRET = config.MAIL_TRACKER_SYNC_SECRET

_SYNC_INTERVAL_SECONDS = 180

_URL_RE = re.compile(r'(https?://[^\s<>"\')\]]+)')


def build_tracked_html(plain_body: str, token: str) -> str:
    """Turn a plain-text email body into an HTML alternative with a hidden open
    pixel and every link rewritten through the click-redirect endpoint.

    Splits on the URL regex *before* escaping anything — escaping first would
    turn a `&` in any URL's query string into `&amp;`, which then gets baked
    into the redirect's `u=` param and corrupts the destination for every link
    with more than one query parameter. Only the surrounding text gets escaped.
    """
    parts = _URL_RE.split(plain_body)
    pieces: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # odd indices are the captured URLs themselves
            redirect = f"{SPACE_BASE_URL}/c/{token}?u={quote(part, safe='')}"
            pieces.append(f'<a href="{html.escape(redirect)}">{html.escape(part)}</a>')
        else:
            pieces.append(html.escape(part))
    body_html = "".join(pieces).replace("\n", "<br>\n")
    pixel = (
        f'<img src="{SPACE_BASE_URL}/o/{token}.gif" width="1" height="1" '
        f'alt="" style="display:none" border="0">'
    )
    return f'<!DOCTYPE html><html><body style="font-family:sans-serif">{body_html}{pixel}</body></html>'


def prepare_send(
    source: str,
    to_addrs: list[str],
    subject: str,
    body: str,
    cc_addrs: list[str] | None = None,
    leadgen_deal_id: str | None = None,
) -> tuple[str, str]:
    """Record a pending send and build its tracked HTML body. Returns
    (mail_message_id, html_body) — the id doubles as the pixel/click token."""
    row = db.add_mail_message(source, to_addrs, subject, cc_addrs=cc_addrs, leadgen_deal_id=leadgen_deal_id)
    html_body = build_tracked_html(body, row["id"])
    return row["id"], html_body


def finalize_send(
    mail_message_id: str, message_id: str | None, status: str, error: str | None = None
) -> None:
    """Record the outcome of a send attempt. `status` is 'sent', 'failed', or
    'bounced' (the last for a synchronous SMTP rejection — see mail.py/sender.py's
    SMTPRecipientsRefused handling)."""
    fields: dict = {"status": status, "error": error}
    if message_id:
        fields["message_id"] = message_id
    if status == "sent":
        fields["sent_at"] = datetime.now(timezone.utc).isoformat()
    db.update_mail_message(mail_message_id, **fields)


def record_bounce(mail_message_id: str, address: str, detail: str, dedupe_key: str) -> None:
    text = f"{address}: {detail}" if address and detail else (address or detail or "")
    db.add_mail_event(mail_message_id, "bounce", dedupe_key, detail=text)


# --- Leadgen-facing adapters (injected via vendor/leadgen/scheduler.py) -----
# Thin wrappers matching the DI shape vendor/leadgen/email/tracking.py expects,
# kept separate from the functions above so the leadgen call sites stay in
# their own single-recipient, deal-scoped vocabulary.


def prepare_for_leadgen(to_email: str, subject: str, body: str, deal_id: str) -> tuple[str, str]:
    return prepare_send("leadgen", [to_email], subject, body, leadgen_deal_id=deal_id)


def finalize_for_leadgen(
    mail_message_id: str | None, message_id: str | None, status: str, error: str | None = None
) -> None:
    if mail_message_id is None:
        return
    finalize_send(mail_message_id, message_id, status, error)


def record_bounce_for_leadgen(payload: dict) -> None:
    """`payload` comes from vendor/leadgen/email/tracking.py's is_bounce(), merged
    with the original inbox reply dict — see that module for the exact keys."""
    resolved_message_id = payload.get("dsn_original_message_id") or payload.get("in_reply_to") or ""
    mm = db.get_mail_message_by_message_id(resolved_message_id) if resolved_message_id else None
    if not mm:
        return
    bounce_own_id = payload.get("message_id") or resolved_message_id
    record_bounce(
        mm["id"],
        payload.get("dsn_final_recipient", ""),
        payload.get("dsn_diagnostic") or payload.get("subject", ""),
        dedupe_key=f"bounce:{bounce_own_id}",
    )


# --- Sync from the Space ----------------------------------------------------


def sync_from_space() -> dict:
    """Pull new open/click events since the last sync. Never raises — a
    network blip or a sleeping Space must not take down the caller (the
    background loop below, or the on-demand /mail-tracking/sync endpoint)."""
    if not SPACE_BASE_URL or not SYNC_SECRET:
        # Nothing to sync against, or no credential for it. Skipped quietly rather than
        # polling an unconfigured host every three minutes, and never sent without the
        # secret — an unauthenticated /events call is a request to be refused, or worse,
        # answered by someone else's Space.
        return {"synced": 0, "ok": False, "detail": "tracking not configured"}

    cursor = db.max_space_event_id()
    try:
        resp = httpx.get(
            f"{SPACE_BASE_URL}/events",
            params={"since_id": cursor, "secret": SYNC_SECRET, "limit": 500},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as err:  # noqa: BLE001
        log.warning("[mail_tracking] space sync failed: %s", str(err).splitlines()[0][:160])
        return {"synced": 0, "ok": False}

    inserted = 0
    for ev in events:
        mm = db.get_mail_message(ev.get("token", ""))
        if not mm:
            continue  # not this installation's token — discard, see module docstring
        recorded = db.add_mail_event(
            mm["id"],
            ev["type"],
            f"space:{ev['id']}",
            url=ev.get("url"),
            space_event_id=ev["id"],
            occurred_at=ev.get("occurred_at"),
        )
        if recorded:
            inserted += 1
    return {"synced": inserted, "ok": True}


def start_sync_loop() -> None:
    """Started once at app startup. 3 minutes is frequent enough to also double
    as a keep-warm ping against the Space's free-tier cold starts."""
    threading.Thread(target=_sync_loop, name="mail-tracking-sync", daemon=True).start()


def _sync_loop() -> None:
    while True:
        try:
            sync_from_space()
        except Exception:  # noqa: BLE001 — one bad tick must never stop the loop
            log.exception("[mail_tracking] sync loop tick failed")
        time.sleep(_SYNC_INTERVAL_SECONDS)
