"""Outbound mail — send only, stdlib only, no server process.

Deliberately not a hosted mail server: this app runs on a user's own desktop,
where residential ISPs commonly block inbound port 25, there's no static IP or
reverse DNS to anchor SPF/DKIM/DMARC against, and a misconfigured relay is a real
spam/security liability. Instead this logs into the user's own mailbox (an app
password, the same idea as the Bluesky credential already used elsewhere in this
app) and sends through it over TLS — `smtplib` + `ssl` + `email`, all standard
library, so there is nothing new to install and nothing listening on any port.

Settings persist to a small JSON file under DATA_DIR, mirroring where the rest of
this app's per-user local state already lives.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .. import config

CONFIG_PATH = config.DATA_DIR / "mail_settings.json"

SECRET_PLACEHOLDER = "•••••••• (set)"

# Ports that speak TLS from the first byte (implicit TLS). Anything else — 587,
# 25, custom ports — starts in plaintext and upgrades via STARTTLS when use_tls
# is on, which is what every mainstream provider's submission port expects.
_IMPLICIT_TLS_PORTS = {465}


class MailError(Exception):
    """A send or connection attempt failed, with a message safe to show the user."""

    def __init__(self, message: str, refused: dict | None = None) -> None:
        super().__init__(message)
        # Populated only for a synchronous SMTP rejection (SMTPRecipientsRefused) —
        # {address: (code, message)} for each recipient the server refused outright,
        # e.g. "550 no such user". Distinguishes an immediate hard bounce from a
        # generic connection/auth failure, for callers that want to record one.
        self.refused = refused


@dataclass
class MailConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_name: str = ""
    from_email: str = ""
    use_tls: bool = True
    # Bounce detection only — reused with the SMTP username/password above rather
    # than adding separate IMAP credentials (the common case for one mailbox app
    # password). Blank host means bounce polling is simply off.
    imap_host: str = ""
    imap_port: int = 993

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.password and self.from_email)


def load() -> MailConfig:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return MailConfig(**{k: v for k, v in data.items() if k in MailConfig.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            pass
    return MailConfig()


def save(patch: dict[str, object]) -> MailConfig:
    """Merge `patch` over the stored config. A blank password means 'unchanged' —
    the settings screen never has the real password to send back, only a masked
    placeholder, so blank is the only way it can say "leave this alone".
    """
    current = asdict(load())
    for key, value in patch.items():
        if key not in current or value is None:
            continue
        if key == "password" and value == "":
            continue
        current[key] = value
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return MailConfig(**current)


def masked() -> MailConfig:
    """The config with the password replaced — safe to send to the renderer."""
    cfg = load()
    return MailConfig(**{**asdict(cfg), "password": SECRET_PLACEHOLDER if cfg.password else ""})


def _connect(cfg: MailConfig) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if cfg.port in _IMPLICIT_TLS_PORTS:
        client = smtplib.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=20)
    else:
        client = smtplib.SMTP(cfg.host, cfg.port, timeout=20)
        if cfg.use_tls:
            client.starttls(context=context)
    client.login(cfg.username, cfg.password)
    return client


def verify() -> tuple[bool, str]:
    """Log in without sending anything, so 'Test connection' can't spam anyone."""
    cfg = load()
    if not cfg.configured:
        return False, "Fill in host, username, password and from-address first."
    try:
        with _connect(cfg) as client:
            client.noop()
        return True, f"Connected to {cfg.host} as {cfg.username} ✓"
    except smtplib.SMTPException as err:
        return False, str(err)
    except OSError as err:
        return False, f"Could not reach {cfg.host}:{cfg.port} — {err}"


def verify_imap() -> tuple[bool, str]:
    """Log into the IMAP side read-only, so bounce polling can be confirmed
    working without touching the inbox. Mirrors vendor/leadgen/email/inbox.py's
    own verify() shape for the same kind of check."""
    import imaplib

    cfg = load()
    if not cfg.imap_host:
        return False, "Fill in an IMAP host first."
    if not (cfg.username and cfg.password):
        return False, "Needs the SMTP username/password above — IMAP reuses them."
    try:
        client = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        try:
            client.login(cfg.username, cfg.password)
            client.select("INBOX", readonly=True)
        finally:
            client.logout()
        return True, f"Connected to {cfg.imap_host} as {cfg.username} ✓"
    except OSError as err:
        return False, f"Could not reach {cfg.imap_host}:{cfg.imap_port} — {err}"
    except Exception as err:  # noqa: BLE001 — imaplib raises its own error classes per step
        return False, str(err)


def send(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    html_body: str | None = None,
) -> str:
    """Send one email. Returns the generated Message-ID (used to match a later
    IMAP bounce notification back to this send). `html_body`, when given, is
    attached as an HTML alternative alongside the plain-text `body` — this is
    how the open pixel and click-tracking links actually reach the recipient's
    mail client, since a plain-text part can't render an <img> or style a link."""
    cfg = load()
    if not cfg.configured:
        raise MailError("Mail isn't set up yet — add your SMTP settings first.")
    to = [a.strip() for a in to if a.strip()]
    cc = [a.strip() for a in (cc or []) if a.strip()]
    if not to:
        raise MailError("Needs at least one recipient.")
    if not subject.strip():
        raise MailError("Needs a subject.")

    message_id = make_msgid()
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.from_name, cfg.from_email)) if cfg.from_name else cfg.from_email
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with _connect(cfg) as client:
            client.send_message(msg, to_addrs=[*to, *cc])
    except smtplib.SMTPRecipientsRefused as err:
        # The receiving server rejected one or more recipients outright during
        # the SMTP session itself (e.g. "550 no such user") — a hard bounce,
        # known synchronously, with no async Delivery Status Notification ever
        # generated for the IMAP poller to find later.
        raise MailError(str(err), refused=err.recipients) from err
    except smtplib.SMTPException as err:
        raise MailError(str(err)) from err
    except OSError as err:
        raise MailError(f"Could not reach {cfg.host}:{cfg.port} — {err}") from err
    return message_id
