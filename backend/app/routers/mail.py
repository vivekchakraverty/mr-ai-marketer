"""Send-only email — a standalone SMTP composer, not the queued/automation-driven
'Email / newsletter' broadcast channel already in Distribute (that one runs
through Activepieces for pushing Library content out later; this is a direct,
immediate send for one-off outreach)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import mail as mailsvc
from ..services import mail_tracking

router = APIRouter(prefix="/mail", tags=["mail"])


class MailStatus(BaseModel):
    configured: bool
    host: str
    port: int
    username: str
    password: str  # masked placeholder or "" — never the real credential
    fromName: str
    fromEmail: str
    useTls: bool
    imapHost: str
    imapPort: int


class MailSettingsRequest(BaseModel):
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    fromName: str | None = None
    fromEmail: str | None = None
    useTls: bool | None = None
    imapHost: str | None = None
    imapPort: int | None = None


class VerifyResponse(BaseModel):
    valid: bool
    detail: str


class SendRequest(BaseModel):
    to: list[str]
    subject: str
    body: str
    cc: list[str] | None = None


class SendResponse(BaseModel):
    sent: bool


def _to_status(cfg: mailsvc.MailConfig) -> MailStatus:
    return MailStatus(
        configured=cfg.configured,
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
        fromName=cfg.from_name,
        fromEmail=cfg.from_email,
        useTls=cfg.use_tls,
        imapHost=cfg.imap_host,
        imapPort=cfg.imap_port,
    )


@router.get("/status", response_model=MailStatus)
def status() -> MailStatus:
    return _to_status(mailsvc.masked())


@router.post("/settings", response_model=MailStatus)
def update_settings(body: MailSettingsRequest) -> MailStatus:
    patch = {
        "host": body.host,
        "port": body.port,
        "username": body.username,
        "password": body.password,
        "from_name": body.fromName,
        "from_email": body.fromEmail,
        "use_tls": body.useTls,
        "imap_host": body.imapHost,
        "imap_port": body.imapPort,
    }
    mailsvc.save(patch)
    return _to_status(mailsvc.masked())


@router.post("/verify", response_model=VerifyResponse)
def verify() -> VerifyResponse:
    ok, detail = mailsvc.verify()
    return VerifyResponse(valid=ok, detail=detail)


@router.post("/verify-imap", response_model=VerifyResponse)
def verify_imap() -> VerifyResponse:
    ok, detail = mailsvc.verify_imap()
    return VerifyResponse(valid=ok, detail=detail)


@router.post("/send", response_model=SendResponse)
def send(body: SendRequest) -> SendResponse:
    mail_message_id, html_body = mail_tracking.prepare_send(
        "composer", body.to, body.subject, body.body, cc_addrs=body.cc
    )
    try:
        message_id = mailsvc.send(body.to, body.subject, body.body, cc=body.cc, html_body=html_body)
    except mailsvc.MailError as err:
        status_value = "bounced" if getattr(err, "refused", None) else "failed"
        mail_tracking.finalize_send(mail_message_id, None, status_value, str(err))
        raise HTTPException(status_code=400, detail=str(err)) from None
    mail_tracking.finalize_send(mail_message_id, message_id, "sent")
    return SendResponse(sent=True)
