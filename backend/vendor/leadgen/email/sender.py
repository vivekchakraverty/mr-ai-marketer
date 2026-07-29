"""Outbound SMTP — sends approved outreach from the user's own mailbox.

Stdlib only (smtplib + ssl + email), the same approach as app/services/mail.py: no mail
server, no new dependency, TLS to the user's provider. Adds threaded-reply headers
(In-Reply-To / References) for follow-ups and a List-Unsubscribe header for compliance.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .. import config

_IMPLICIT_TLS_PORTS = {465}


class SendError(Exception):
    """A send/connection attempt failed, with a message safe to show the user."""

    def __init__(self, message: str, refused: dict | None = None) -> None:
        super().__init__(message)
        # Set only for a synchronous SMTPRecipientsRefused (e.g. "550 no such
        # user") — a hard bounce known at send time, distinct from a generic
        # connection/auth failure. {address: (code, message)}.
        self.refused = refused


def _cfg() -> dict[str, str]:
    return {
        "host": config.current("SMTP_HOST"),
        "port": config.current("SMTP_PORT") or "587",
        "username": config.current("SMTP_USERNAME"),
        "password": config.current("SMTP_PASSWORD"),
        "from_name": config.current("MAILBOX_FROM_NAME"),
        "from_email": config.current("MAILBOX_FROM_EMAIL"),
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["username"] and c["password"] and c["from_email"])


def _connect(c: dict[str, str]) -> smtplib.SMTP:
    context = ssl.create_default_context()
    port = int(c["port"])
    if port in _IMPLICIT_TLS_PORTS:
        client: smtplib.SMTP = smtplib.SMTP_SSL(c["host"], port, context=context, timeout=20)
    else:
        client = smtplib.SMTP(c["host"], port, timeout=20)
        client.starttls(context=context)
    client.login(c["username"], c["password"])
    return client


def verify() -> tuple[bool, str]:
    """Log in without sending anything (so 'Test connection' can't spam anyone)."""
    c = _cfg()
    if not configured():
        return False, "Fill in SMTP host, username, password and from-address first."
    try:
        with _connect(c) as client:
            client.noop()
        return True, f"Connected to {c['host']} as {c['username']} ✓"
    except smtplib.SMTPException as err:
        return False, str(err)
    except OSError as err:
        return False, f"Could not reach {c['host']}:{c['port']} — {err}"


def send(
    to_email: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    html_body: str | None = None,
) -> str:
    """Send one email. Returns the generated Message-ID (for thread tracking and
    for matching a later bounce back to this send). `html_body`, when given
    (the tracking service builds it — see email/tracking.py), is attached as an
    HTML alternative alongside the plain-text `body` so the open pixel and
    click-tracking links actually reach the recipient's mail client.

    Suppression is enforced by the caller (the task handlers) before we get here, but we
    re-check as a last line of defense — a suppressed address must never receive mail.
    """
    from .. import db

    c = _cfg()
    if not configured():
        raise SendError("Mailbox isn't set up — add your SMTP settings first.")
    to_email = to_email.strip()
    if not to_email:
        raise SendError("No recipient address.")
    if db.is_suppressed(to_email):
        raise SendError(f"{to_email} is on the suppression list — refusing to send.")

    message_id = make_msgid()
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["Subject"] = subject
    msg["From"] = formataddr((c["from_name"], c["from_email"])) if c["from_name"] else c["from_email"]
    msg["To"] = to_email
    # One-click and mailto unsubscribe, honored permanently via the suppression list.
    msg["List-Unsubscribe"] = f"<mailto:{c['from_email']}?subject=unsubscribe>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with _connect(c) as client:
            client.send_message(msg)
    except smtplib.SMTPRecipientsRefused as err:
        # A hard bounce known synchronously (e.g. "550 no such user") — no async
        # DSN will ever arrive for the inbox poller to catch this one.
        raise SendError(str(err), refused=err.recipients) from err
    except smtplib.SMTPException as err:
        raise SendError(str(err)) from err
    except OSError as err:
        raise SendError(f"Could not reach {c['host']}:{c['port']} — {err}") from err
    return message_id
