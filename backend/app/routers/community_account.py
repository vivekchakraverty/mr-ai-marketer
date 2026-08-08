"""The Telegram account half of the Community section.

Everything here runs as the signed-in person rather than as the bot, which is why it is a
separate router: the bot endpoints in community.py take a bot token the backend stores, and
these take an account session the backend deliberately does *not* store.

**Where the session lives.** Every request carries `apiId`, `apiHash` and `session` in its
body, the same way the Hugging Face token travels in this app. The renderer holds them in
Electron's DPAPI-encrypted store and the backend keeps a live client in memory — nothing is
written to the SQLite file, because a Telegram session string is full access to someone's
account and the database is a plain file on disk.

The one thing not to mistake: signing in here does not replace the bot. The bot still runs
the paid channel, because Stars subscriptions are a Bot API feature and an account cannot
sell them.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..services import telegram_user as tg
from ..services.telegram_user import Account, NeedsPasswordError, TelegramUserError

router = APIRouter(prefix="/community/account", tags=["community"])


class Credentials(BaseModel):
    """The three things every account call needs. `session` is empty only while signing in."""

    apiId: int = Field(ge=1)
    apiHash: str
    session: str = ""

    def account(self) -> Account:
        return Account(api_id=self.apiId, api_hash=self.apiHash.strip(), session=self.session.strip())


class SendCodeRequest(Credentials):
    phone: str


class SignInRequest(BaseModel):
    code: str
    # Two-step verification. Never logged, never stored — it is used once to complete the
    # sign-in and the resulting session string is what persists.
    password: str = ""


class CreateChatRequest(Credentials):
    title: str
    about: str = ""
    kind: str = "group"  # "group" (supergroup) or "channel" (broadcast)
    addBot: bool = True


class AddMembersRequest(Credentials):
    handles: list[str]


class PostRequest(Credentials):
    text: str


class LinkRequest(Credentials):
    role: str = "open"  # "open" | "paid"
    title: str = ""


def _fail(err: TelegramUserError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(err))


@router.post("/send-code")
def send_code(body: SendCodeRequest) -> dict:
    """Ask Telegram to send a login code.

    Telegram picks the channel itself — its own app if the account is signed in somewhere
    else, SMS otherwise — so the response says which, rather than guessing in the UI.
    """
    try:
        return tg.send_code(body.apiId, body.apiHash.strip(), body.phone)
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/sign-in")
def sign_in(body: SignInRequest) -> dict:
    """Finish the login and hand the session string back to the renderer to store."""
    try:
        return tg.sign_in(body.code, body.password)
    except NeedsPasswordError as err:
        # 409 rather than 400: the code was accepted and the flow is mid-way, not wrong. The
        # UI uses this to reveal the password field instead of showing a failure.
        raise HTTPException(status_code=409, detail=str(err)) from None
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/status")
def status(body: Credentials) -> dict:
    """POST, not GET: the session must travel in a body rather than a query string."""
    return tg.status(body.account())


@router.post("/logout")
def logout(body: Credentials) -> dict:
    try:
        return tg.log_out(body.account())
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats")
def list_chats(body: Credentials) -> dict:
    try:
        return {"chats": tg.list_chats(body.account())}
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats/create")
def create_chat(body: CreateChatRequest) -> dict:
    """Create a group or channel, and put the bot in it while we're there.

    Adding the bot is the step that makes the chat usable by the rest of this section: the
    bot poller links it from the `my_chat_member` update, and the paid channel needs the bot
    as an admin before a subscription link can exist. It is skipped silently when no bot is
    connected — a group without one is still a perfectly good group.
    """
    account = body.account()
    try:
        chat = tg.create_chat(account, body.title, body.about, body.kind)
    except TelegramUserError as err:
        raise _fail(err) from None

    bot_username = (db.get_community_config().get("bot_username") or "").strip()
    if body.addBot and bot_username:
        try:
            tg.add_bot(account, chat["id"], bot_username)
            chat["botAdded"] = True
        except TelegramUserError as err:
            # The chat exists either way; report the bot failure without losing it.
            chat["botAdded"] = False
            chat["botDetail"] = str(err)
    else:
        chat["botAdded"] = False
    return {"chat": chat}


@router.post("/chats/{chat_id}/members")
def list_members(chat_id: str, body: Credentials) -> dict:
    try:
        return {"members": tg.list_members(body.account(), chat_id)}
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats/{chat_id}/members/add")
def add_members(chat_id: str, body: AddMembersRequest) -> dict:
    """Add people by @username. Each one succeeds or fails on its own.

    Telegram lets people refuse being added by non-contacts, and rate-limits accounts that
    add strangers in bulk — so the response is a per-person list, not a yes/no.
    """
    try:
        return {"results": tg.add_members(body.account(), chat_id, body.handles)}
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats/{chat_id}/post")
def post(chat_id: str, body: PostRequest) -> dict:
    try:
        return tg.post(body.account(), chat_id, body.text)
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats/{chat_id}/invite")
def invite(chat_id: str, body: Credentials) -> dict:
    try:
        return {"inviteLink": tg.invite_link(body.account(), chat_id)}
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats/{chat_id}/add-bot")
def add_bot(chat_id: str, body: Credentials) -> dict:
    bot_username = (db.get_community_config().get("bot_username") or "").strip()
    if not bot_username:
        raise HTTPException(status_code=400, detail="Connect a bot in Setup first.")
    try:
        return {"chat": tg.add_bot(body.account(), chat_id, bot_username)}
    except TelegramUserError as err:
        raise _fail(err) from None


@router.post("/chats/{chat_id}/link")
def link(chat_id: str, body: LinkRequest) -> dict:
    """Point the section's open-group or paid-channel role at an existing chat.

    The bot links a chat automatically when it is added to one, but only the first of each
    kind — this is how you change your mind, or pick between several groups you own.
    """
    title = body.title.strip()
    if body.role == "paid":
        db.update_community_config(gated_chat_id=chat_id, gated_chat_title=title, gated_invite_link="")
    else:
        db.update_community_config(chat_id=chat_id, chat_title=title, invite_link="")
    return {"linked": body.role}
