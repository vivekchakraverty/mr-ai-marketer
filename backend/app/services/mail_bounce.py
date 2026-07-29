"""Mail Composer's own bounce detection — IMAP polling of the same mailbox it
sends from.

Deliberately independent of the Lead Gen Agent's own IMAP poller
(vendor/leadgen/email/inbox.py): app/services/mail.py and
vendor/leadgen/email/sender.py already treat SMTP as two unrelated mailbox
stacks (each duplicates its own ~100 lines of near-identical SMTP rather than
share a mailbox config), and this extends the same choice to IMAP rather than
quietly coupling this app's core to the vendored package.

A bounce reaches us one of two ways: asynchronously, as a Delivery Status
Notification (RFC 3464) landing back in the inbox some time after the send —
that's what this file polls for; or synchronously, as an SMTP-time rejection
(`550 no such user`) raised directly by mail.py's send() — see that module's
SMTPRecipientsRefused handling, which never touches IMAP at all.
"""

from __future__ import annotations

import email
import imaplib
import logging
import threading
import time
from email.message import Message

from .. import db
from . import mail as mailsvc
from . import mail_tracking

log = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 120
_FETCH_LIMIT = 30

_MAILER_DAEMON_HINTS = ("mailer-daemon", "postmaster", "mail delivery subsystem")
_BOUNCE_SUBJECT_HINTS = (
    "undelivered mail returned to sender",
    "delivery status notification (failure)",
    "mail delivery failed",
    "returned mail",
    "undeliverable",
)


def start_bounce_poller() -> None:
    """Started once at app startup. Inert until Mail Composer's Settings has an
    IMAP host configured — checked fresh every tick, so turning it on takes
    effect on the next poll with no restart needed."""
    threading.Thread(target=_poll_loop, name="mail-bounce-poll", daemon=True).start()


def _poll_loop() -> None:
    while True:
        try:
            cfg = mailsvc.load()
            if cfg.imap_host:
                _poll_once(cfg)
        except Exception:  # noqa: BLE001 — one bad tick must never kill the poller
            log.exception("[mail_bounce] poll tick failed")
        time.sleep(_POLL_INTERVAL_SECONDS)


def _poll_once(cfg: "mailsvc.MailConfig") -> int:
    client = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        client.login(cfg.username, cfg.password)
        client.select("INBOX", readonly=True)
        _, data = client.search(None, "ALL")
        uids = (data[0] or b"").split()[-_FETCH_LIMIT:]
        recorded = 0
        for uid in uids:
            _, msg_data = client.fetch(uid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            if _handle_if_bounce(msg):
                recorded += 1
        return recorded
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


def _parse_dsn(msg: Message) -> dict:
    """One walk over a possible DSN's parts, pulling out the fields that
    matter. Any value may be empty if this isn't a well-formed RFC 3464 report —
    real-world MTAs vary in how closely they follow the spec."""
    result = {"action": "", "diagnostic": "", "final_recipient": "", "original_message_id": ""}
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            payload = part.get_payload()
            # RFC 3464: this payload is a *list* — one per-message fields group
            # (Reporting-MTA, ...) followed by one or more per-recipient groups.
            # Action/Final-Recipient/Diagnostic-Code only ever live on a
            # per-recipient group, never the first (per-message) one, so the
            # right block has to be found by field presence, not by index.
            # (A multi-recipient bounce has more than one per-recipient group;
            # taking the first match is a documented simplification.)
            blocks = payload if isinstance(payload, list) else [part]
            for block in blocks:
                if hasattr(block, "get") and block.get("Action"):
                    result["action"] = (block.get("Action", "") or "").strip().lower()
                    result["diagnostic"] = block.get("Diagnostic-Code", "") or block.get("Status", "") or ""
                    result["final_recipient"] = block.get("Final-Recipient", "") or ""
                    break
        elif ctype in ("message/rfc822", "text/rfc822-headers"):
            payload = part.get_payload()
            inner = payload[0] if isinstance(payload, list) and payload else None
            if inner is not None and hasattr(inner, "get"):
                result["original_message_id"] = inner.get("Message-ID", "") or ""
            elif isinstance(payload, str):
                for line in payload.splitlines():
                    if line.lower().startswith("message-id:"):
                        result["original_message_id"] = line.split(":", 1)[1].strip()
                        break
    return result


def _is_bounce(msg: Message, dsn: dict) -> bool:
    # A well-formed DSN is unambiguous: trust its own Action field, and
    # distinguish a real failure from a transient 'delayed' retry notice.
    if msg.get_content_type() == "multipart/report" and "delivery-status" in (msg.get_param("report-type") or ""):
        return dsn["action"] == "failed"
    # Otherwise fall back to loose heuristics for MTAs that don't send a
    # spec-compliant report.
    from_addr = (msg.get("From") or "").lower()
    subject = (msg.get("Subject") or "").lower()
    if any(h in from_addr for h in _MAILER_DAEMON_HINTS):
        return True
    return any(h in subject for h in _BOUNCE_SUBJECT_HINTS)


def _handle_if_bounce(msg: Message) -> bool:
    dsn = _parse_dsn(msg)
    if not _is_bounce(msg, dsn):
        return False
    original_message_id = dsn["original_message_id"] or msg.get("In-Reply-To", "")
    mm = db.get_mail_message_by_message_id(original_message_id) if original_message_id else None
    if not mm:
        return False  # a bounce we can't tie back to a message we sent — skip it
    bounce_own_id = msg.get("Message-ID", "") or f"no-id:{msg.get('Date', '')}:{mm['id']}"
    mail_tracking.record_bounce(
        mm["id"],
        dsn["final_recipient"],
        dsn["diagnostic"] or msg.get("Subject", ""),
        dedupe_key=f"bounce:{bounce_own_id}",
    )
    return True
