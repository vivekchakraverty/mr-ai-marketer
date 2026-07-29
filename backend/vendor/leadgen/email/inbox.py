"""Inbound IMAP — reads replies so the follow-up agent can react to them.

Stdlib only (imaplib + email). Fetches recent messages, extracts the plain-text body and the
threading headers, and lets the follow-up handler match each reply back to a deal via the
In-Reply-To / References that point at a Message-ID we sent.
"""

from __future__ import annotations

import email
import imaplib
import logging
from email.header import decode_header, make_header
from email.message import Message

from .. import config

log = logging.getLogger(__name__)


class InboxError(Exception):
    pass


def _cfg() -> dict[str, str]:
    return {
        "host": config.current("IMAP_HOST"),
        "port": config.current("IMAP_PORT") or "993",
        "username": config.current("IMAP_USERNAME"),
        "password": config.current("IMAP_PASSWORD"),
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["username"] and c["password"])


def _connect(c: dict[str, str]) -> imaplib.IMAP4:
    client = imaplib.IMAP4_SSL(c["host"], int(c["port"]))
    client.login(c["username"], c["password"])
    return client


def verify() -> tuple[bool, str]:
    c = _cfg()
    if not configured():
        return False, "Fill in IMAP host, username and password first."
    try:
        client = _connect(c)
        try:
            client.select("INBOX", readonly=True)
        finally:
            client.logout()
        return True, f"Connected to {c['host']} as {c['username']} ✓"
    except imaplib.IMAP4.error as err:
        return False, str(err)
    except OSError as err:
        return False, f"Could not reach {c['host']}:{c['port']} — {err}"


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _plain_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""


def _dsn_fields(msg: Message) -> dict:
    """Pull the RFC 3464 delivery-status fields a bounce notification carries, if
    this message has any. `_plain_body()` above only ever surfaces the first
    text/plain part — for a standards-compliant bounce that's just the
    human-readable prose, never the sibling message/delivery-status part that
    actually carries Final-Recipient/Action/Diagnostic-Code, nor the
    message/rfc822 part carrying the original Message-ID. This is purely
    additive: every key here is new, nothing `fetch_recent()` already returns
    changes shape.
    """
    is_report = (
        msg.get_content_type() == "multipart/report"
        and "delivery-status" in (msg.get_param("report-type") or "")
    )
    fields = {
        "is_report": is_report,
        "dsn_action": "",
        "dsn_final_recipient": "",
        "dsn_diagnostic": "",
        "dsn_original_message_id": "",
    }
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            payload = part.get_payload()
            # The payload is a list: one per-message fields group (Reporting-MTA,
            # ...) followed by one or more per-recipient groups — Action/
            # Final-Recipient/Diagnostic-Code only ever live on a per-recipient
            # group, never the first one, so it has to be found by field
            # presence rather than assumed to be payload[0]. (A multi-recipient
            # bounce has more than one per-recipient group; taking the first
            # match is a documented simplification.)
            blocks = payload if isinstance(payload, list) else [part]
            for block in blocks:
                if hasattr(block, "get") and block.get("Action"):
                    fields["dsn_action"] = (block.get("Action", "") or "").strip().lower()
                    fields["dsn_diagnostic"] = block.get("Diagnostic-Code", "") or block.get("Status", "") or ""
                    fields["dsn_final_recipient"] = block.get("Final-Recipient", "") or ""
                    break
        elif ctype in ("message/rfc822", "text/rfc822-headers"):
            payload = part.get_payload()
            inner = payload[0] if isinstance(payload, list) and payload else None
            if inner is not None and hasattr(inner, "get"):
                fields["dsn_original_message_id"] = inner.get("Message-ID", "") or ""
            elif isinstance(payload, str):
                for line in payload.splitlines():
                    if line.lower().startswith("message-id:"):
                        fields["dsn_original_message_id"] = line.split(":", 1)[1].strip()
                        break
    return fields


def fetch_recent(limit: int = 30) -> list[dict]:
    """Return recent inbox messages as dicts with from/subject/body + threading headers.

    Read-only. The follow-up handler decides which of these belong to a deal (by matching
    In-Reply-To/References to Message-IDs we sent) and what to do about them.
    """
    if not configured():
        return []
    c = _cfg()
    try:
        client = _connect(c)
    except Exception as err:  # noqa: BLE001
        log.warning("[leadgen] IMAP connect failed: %s", str(err).splitlines()[0][:160])
        return []

    out: list[dict] = []
    try:
        client.select("INBOX", readonly=True)
        typ, data = client.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()[-limit:]
        for msg_id in reversed(ids):
            typ, msg_data = client.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            row = {
                "from": _decode(msg.get("From")),
                "subject": _decode(msg.get("Subject")),
                "message_id": (msg.get("Message-ID") or "").strip(),
                "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                "references": (msg.get("References") or "").strip(),
                "body": _plain_body(msg).strip(),
            }
            row.update(_dsn_fields(msg))
            out.append(row)
    except Exception as err:  # noqa: BLE001
        log.warning("[leadgen] IMAP fetch failed: %s", str(err).splitlines()[0][:160])
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass
    return out
