"""Telegram as *the user*, alongside the bot.

The bot half of the Community section (services/telegram_community.py) cannot do two of
the things people expect a community tool to do, and no amount of code fixes that:

* **A bot cannot create a group or a channel.** There is no Bot API method for it.
* **A bot cannot add anyone to a chat.** It can only hand out invite links and wait.

Both of those are things a *person* can do, so this module signs in as the person — MTProto
(Telethon) rather than the Bot API. That is Telegram's own client protocol; the app becomes
one more logged-in device on the account, listed under Settings → Devices in Telegram, and
revocable from there.

Three consequences worth knowing before reading further:

* **It needs an api_id/api_hash the user gets from my.telegram.org.** Telegram issues those
  per person, and the app ships without any — one baked in would be shared by every install
  and would be rate-limited or revoked for all of them at once.
* **The session string is full account access.** It is handed back to the renderer and kept
  in Electron's DPAPI-encrypted store, not in the app's SQLite file — the same treatment the
  Hugging Face token gets, because it is a credential of the same weight.
* **Adding members is not unconditional.** Telegram lets people refuse being added to groups
  by anyone who isn't a contact, and it rate-limits accounts that add strangers in bulk. The
  add path reports each person's outcome individually and falls back to "send them the invite
  link" rather than pretending a refusal was a success.

Telethon is asyncio; the rest of this backend is synchronous request handlers. So the client
lives on its own event loop in its own thread, and every public function here is an ordinary
blocking call that hands work to it. One loop, one client, one account — this is a desktop
app with a single user.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from dataclasses import dataclass
from typing import Any, Optional

from telethon import TelegramClient, functions, utils
from telethon.errors import (
    ApiIdInvalidError,
    ChatAdminRequiredError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    SessionPasswordNeededError,
    UserChannelsTooMuchError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
)
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, ChatAdminRights, User

# Shown in the user's Telegram "Devices" list, so a session they don't recognise is
# identifiable as this app rather than an anonymous "Unknown device".
DEVICE_MODEL = "Mr. AI Marketer"
APP_VERSION = "1.0"

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()

# The signed-in client, and the (api_id, session) it belongs to. A different session means a
# different account, so the old client is dropped rather than reused.
_client: Optional[TelegramClient] = None
_client_key: Optional[tuple[int, str]] = None

# The half-finished login: created by send_code, consumed by sign_in. Kept separate from
# _client because it is not authorised yet and must not be mistaken for a live session.
_login_client: Optional[TelegramClient] = None
_login_phone = ""
_login_hash = ""


class TelegramUserError(RuntimeError):
    pass


class NeedsPasswordError(TelegramUserError):
    """The account has two-step verification; sign_in needs the password too."""


@dataclass(frozen=True)
class Account:
    api_id: int
    api_hash: str
    session: str


# --------------------------------------------------------------------------- plumbing


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, name="telegram-user", daemon=True).start()
        _loop = loop
        return loop


def _run(coro, timeout: float = 120) -> Any:
    """Run a coroutine on the Telegram loop and block until it answers.

    Everything Telethon touches has to happen on the loop its client was built on, so every
    public function in this module is a thin wrapper around this.
    """
    future = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise TelegramUserError("Telegram took too long to answer. Try again.") from None


def _friendly(err: Exception) -> TelegramUserError:
    """Telegram's errors are precise but not written for people. Translate the ones a user
    can actually act on, and pass the rest through rather than inventing an explanation."""
    if isinstance(err, FloodWaitError):
        wait = int(getattr(err, "seconds", 0))
        mins = max(1, round(wait / 60))
        return TelegramUserError(
            f"Telegram is rate-limiting this account for about {mins} minute(s). "
            "This happens when a lot of requests go out at once — wait it out rather than retrying."
        )
    if isinstance(err, ApiIdInvalidError):
        return TelegramUserError(
            "Telegram rejected that api_id / api_hash pair. Copy both again from my.telegram.org/apps — "
            "they have to come from the same app entry."
        )
    if isinstance(err, PhoneNumberInvalidError):
        return TelegramUserError("Telegram doesn't recognise that phone number. Include the country code.")
    if isinstance(err, PhoneNumberUnoccupiedError):
        return TelegramUserError(
            "There's no Telegram account on that number. Create one in the Telegram app first, then sign in here."
        )
    if isinstance(err, PhoneCodeInvalidError):
        return TelegramUserError("That code isn't right. Check the message Telegram sent you.")
    if isinstance(err, PhoneCodeExpiredError):
        return TelegramUserError("That code has expired. Send a new one.")
    if isinstance(err, PasswordHashInvalidError):
        return TelegramUserError("That two-step verification password isn't right.")
    if isinstance(err, ChatAdminRequiredError):
        return TelegramUserError("You don't have admin rights in that chat.")
    if isinstance(err, TelegramUserError):
        return err
    return TelegramUserError(str(err) or type(err).__name__)


async def _connect(account: Account) -> TelegramClient:
    global _client, _client_key
    if not account.session:
        raise TelegramUserError("Not signed in to Telegram.")
    key = (account.api_id, account.session)
    if _client is not None and _client_key == key and _client.is_connected():
        return _client
    if _client is not None:
        try:
            await _client.disconnect()
        except Exception:  # noqa: BLE001 — a dead client must not block a fresh one
            pass
        _client = None
        _client_key = None

    try:
        stored = StringSession(account.session)
    except ValueError:
        # A session string that isn't one at all — truncated in storage, or from a version
        # that no longer parses. Telethon's own "Not a valid string" says nothing about what
        # to do next.
        raise TelegramUserError("This Telegram session is unreadable — sign in again.") from None

    client = TelegramClient(
        stored, account.api_id, account.api_hash,
        device_model=DEVICE_MODEL, system_version="desktop", app_version=APP_VERSION,
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise TelegramUserError("This Telegram session is no longer valid — sign in again.")
    _client, _client_key = client, key
    return client


def _describe(entity) -> dict:
    """One chat, in the shape the UI and the rest of the app use.

    `id` is the *marked* id (-100… for supergroups and channels) — the same form the Bot API
    uses, so a chat created here can be handed straight to the bot half and to the embed.
    """
    is_channel = isinstance(entity, Channel)
    return {
        "id": str(utils.get_peer_id(entity)),
        "title": getattr(entity, "title", "") or "",
        "username": getattr(entity, "username", "") or "",
        # A "channel" to Telegram is anything with a supergroup-style id; broadcast is the
        # one people mean when they say channel. Report the distinction the user sees.
        "kind": ("channel" if getattr(entity, "broadcast", False) else "group") if is_channel else "group",
        "megagroup": bool(getattr(entity, "megagroup", False)),
        "creator": bool(getattr(entity, "creator", False)),
        "admin": bool(getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)),
        "participants": int(getattr(entity, "participants_count", 0) or 0),
    }


async def _resolve(client: TelegramClient, chat_id: str):
    """Marked chat id → entity.

    Telethon can only resolve a bare id it has seen before, so a cold start falls back to a
    dialog sweep, which is also how the chat list is built.
    """
    try:
        return await client.get_entity(int(chat_id))
    except (ValueError, TypeError):
        pass
    async for dialog in client.iter_dialogs():
        if str(utils.get_peer_id(dialog.entity)) == str(chat_id):
            return dialog.entity
    raise TelegramUserError("That chat isn't on this account any more.")


# --------------------------------------------------------------------------- signing in


async def _send_code(api_id: int, api_hash: str, phone: str) -> dict:
    global _login_client, _login_phone, _login_hash
    if _login_client is not None:
        try:
            await _login_client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    client = TelegramClient(
        StringSession(), api_id, api_hash,
        device_model=DEVICE_MODEL, system_version="desktop", app_version=APP_VERSION,
    )
    await client.connect()
    sent = await client.send_code_request(phone)
    _login_client, _login_phone, _login_hash = client, phone, sent.phone_code_hash
    # Telegram decides where the code goes: its own app if you're signed in elsewhere, SMS
    # if not. Say which, because "check your SMS" when it went to the app wastes people's time.
    return {"sentTo": type(sent.type).__name__.replace("SentCodeType", "").lower() or "app"}


def send_code(api_id: int, api_hash: str, phone: str) -> dict:
    phone = phone.strip()
    if not phone:
        raise TelegramUserError("Enter the phone number your Telegram account is on.")
    try:
        return _run(_send_code(api_id, api_hash, phone), timeout=90)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _sign_in(code: str, password: str) -> dict:
    global _client, _client_key, _login_client, _login_hash
    if _login_client is None:
        raise TelegramUserError("Ask Telegram for a code first.")
    client = _login_client
    try:
        await client.sign_in(phone=_login_phone, code=code, phone_code_hash=_login_hash)
    except SessionPasswordNeededError:
        if not password:
            raise NeedsPasswordError(
                "This account has two-step verification. Enter its password to finish signing in."
            ) from None
        await client.sign_in(password=password)

    me = await client.get_me()
    session = client.session.save()
    # Promote the login client to *the* client: it is already connected and authorised, so
    # reconnecting would only cost a round trip.
    _client, _client_key = client, (client.api_id, session)
    _login_client, _login_hash = None, ""
    return {
        "session": session,
        "userId": str(me.id),
        "username": me.username or "",
        "firstName": me.first_name or "",
        "phone": me.phone or "",
    }


def sign_in(code: str, password: str = "") -> dict:
    try:
        return _run(_sign_in(code.strip(), password), timeout=90)
    except Exception as err:  # noqa: BLE001
        if isinstance(err, NeedsPasswordError):
            raise
        raise _friendly(err) from None


async def _status(account: Account) -> dict:
    client = await _connect(account)
    me = await client.get_me()
    return {
        "connected": True,
        "userId": str(me.id),
        "username": me.username or "",
        "firstName": me.first_name or "",
        "phone": me.phone or "",
    }


def status(account: Account) -> dict:
    if not account.session:
        return {"connected": False, "userId": "", "username": "", "firstName": "", "phone": ""}
    try:
        return _run(_status(account), timeout=45)
    except Exception as err:  # noqa: BLE001
        return {
            "connected": False, "userId": "", "username": "", "firstName": "", "phone": "",
            "detail": str(_friendly(err)),
        }


async def _log_out(account: Account) -> dict:
    global _client, _client_key
    try:
        client = await _connect(account)
        await client.log_out()
    finally:
        _client, _client_key = None, None
    return {"connected": False}


def log_out(account: Account) -> dict:
    """End the session on Telegram's side too, not just locally.

    Dropping the string alone would leave this app listed as a live device on the account
    forever, which is the opposite of what "log out" means to the person clicking it.
    """
    if not account.session:
        return {"connected": False}
    try:
        return _run(_log_out(account), timeout=45)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


# --------------------------------------------------------------------------- chats


async def _list_chats(account: Account) -> list[dict]:
    client = await _connect(account)
    chats: list[dict] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (Chat, Channel)):
            continue
        if not (getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)):
            continue
        chats.append(_describe(entity))
    chats.sort(key=lambda c: (not c["creator"], c["title"].lower()))
    return chats


def list_chats(account: Account) -> list[dict]:
    """Every group and channel the account owns or administers.

    Filtered rather than listed whole on purpose: this screen is for running your own
    communities, and a full dialog list would put the user's private chats in it.
    """
    try:
        return _run(_list_chats(account), timeout=90)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _create_chat(account: Account, title: str, about: str, kind: str) -> dict:
    client = await _connect(account)
    result = await client(
        functions.channels.CreateChannelRequest(
            title=title, about=about,
            # A supergroup rather than a legacy "basic group": only supergroups support
            # granular admin rights, join requests and the member counts the rest of this
            # section depends on, and Telegram migrates basic groups into them anyway.
            megagroup=(kind != "channel"),
            broadcast=(kind == "channel"),
        )
    )
    return _describe(result.chats[0])


def create_chat(account: Account, title: str, about: str = "", kind: str = "group") -> dict:
    title = title.strip()
    if not title:
        raise TelegramUserError("Give the group a name.")
    try:
        return _run(_create_chat(account, title, about.strip(), kind), timeout=60)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _add_members(account: Account, chat_id: str, handles: list[str]) -> list[dict]:
    client = await _connect(account)
    entity = await _resolve(client, chat_id)
    results: list[dict] = []

    for position, raw in enumerate(handles):
        handle = raw.strip().lstrip("@")
        if not handle:
            continue
        try:
            user = await client.get_input_entity(handle)
        except Exception:  # noqa: BLE001 — every resolution failure means the same thing here
            results.append({"handle": raw, "ok": False, "detail": "No Telegram user with that username."})
            continue

        try:
            # One request per person, not one for the batch: Telegram fails the whole call if
            # any single user refuses, and "nobody was added because one person's privacy
            # settings said no" is a miserable way to add ten people.
            if isinstance(entity, Channel):
                await client(functions.channels.InviteToChannelRequest(entity, [user]))
            else:
                await client(functions.messages.AddChatUserRequest(entity.id, user, fwd_limit=0))
            results.append({"handle": raw, "ok": True, "detail": "Added."})
        except UserPrivacyRestrictedError:
            results.append({
                "handle": raw, "ok": False,
                "detail": "Their privacy settings don't allow being added. Send them the invite link instead.",
            })
        except UserNotMutualContactError:
            results.append({
                "handle": raw, "ok": False,
                "detail": "They can only be added by a mutual contact. Send them the invite link instead.",
            })
        except UserChannelsTooMuchError:
            results.append({"handle": raw, "ok": False, "detail": "They're in too many groups already."})
        except FloodWaitError as err:
            # Stop the batch: continuing through a flood wait is what turns a rate limit into
            # a restricted account.
            results.append({"handle": raw, "ok": False, "detail": str(_friendly(err))})
            for remaining in handles[position + 1:]:
                results.append({"handle": remaining, "ok": False, "detail": "Skipped — rate limited."})
            break
        except Exception as err:  # noqa: BLE001
            results.append({"handle": raw, "ok": False, "detail": str(_friendly(err))})
    return results


def add_members(account: Account, chat_id: str, handles: list[str]) -> list[dict]:
    """Add people by @username, one at a time, reporting each outcome.

    Telegram treats adding strangers to groups as spam behaviour, and enforces that both per
    person (privacy settings) and per account (flood waits). Neither is worked around here —
    a refusal is reported as a refusal, and a flood wait stops the batch.
    """
    if not handles:
        raise TelegramUserError("Nobody to add.")
    try:
        return _run(_add_members(account, chat_id, handles), timeout=180)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _list_members(account: Account, chat_id: str, limit: int) -> list[dict]:
    client = await _connect(account)
    entity = await _resolve(client, chat_id)
    people: list[dict] = []
    async for user in client.iter_participants(entity, limit=limit):
        if not isinstance(user, User):
            continue
        people.append({
            "id": str(user.id),
            "username": user.username or "",
            "name": " ".join(filter(None, [user.first_name, user.last_name])) or "",
            "bot": bool(user.bot),
        })
    return people


def list_members(account: Account, chat_id: str, limit: int = 200) -> list[dict]:
    try:
        return _run(_list_members(account, chat_id, limit), timeout=120)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _post(account: Account, chat_id: str, text: str) -> dict:
    client = await _connect(account)
    entity = await _resolve(client, chat_id)
    message = await client.send_message(entity, text)
    return {"messageId": str(message.id)}


def post(account: Account, chat_id: str, text: str) -> dict:
    """Post as yourself. The bot's broadcast posts as the bot — different author, same chat."""
    text = text.strip()
    if not text:
        raise TelegramUserError("Nothing to post.")
    try:
        return _run(_post(account, chat_id, text), timeout=60)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _invite_link(account: Account, chat_id: str) -> str:
    client = await _connect(account)
    entity = await _resolve(client, chat_id)
    result = await client(functions.messages.ExportChatInviteRequest(entity, title="Mr. AI Marketer"))
    return getattr(result, "link", "") or ""


def invite_link(account: Account, chat_id: str) -> str:
    """A plain invite link, for the people Telegram won't let you add directly."""
    try:
        return _run(_invite_link(account, chat_id), timeout=60)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None


async def _add_bot(account: Account, chat_id: str, bot_username: str) -> dict:
    client = await _connect(account)
    entity = await _resolve(client, chat_id)
    if not isinstance(entity, Channel):
        raise TelegramUserError("Only supergroups and channels can have bot admins.")
    bot = await client.get_input_entity(bot_username.lstrip("@"))

    try:
        await client(functions.channels.InviteToChannelRequest(entity, [bot]))
    except Exception as err:  # noqa: BLE001 — already a member is the common case, not a failure
        if "USER_ALREADY_PARTICIPANT" not in str(err).upper():
            raise

    # The rights the Community features actually use, and nothing else: post, invite (for the
    # subscription link), and ban/unban (for removing lapsed members). No add_admins, so the
    # bot cannot widen its own reach.
    await client(
        functions.channels.EditAdminRequest(
            entity, bot,
            ChatAdminRights(
                post_messages=True, edit_messages=False, delete_messages=True,
                ban_users=True, invite_users=True, pin_messages=True,
                change_info=False, add_admins=False, anonymous=False, manage_call=False,
            ),
            rank="app",
        )
    )
    return _describe(entity)


def add_bot(account: Account, chat_id: str, bot_username: str) -> dict:
    """Put the Community bot into a chat as an admin.

    This is the step that connects the two halves: once the bot is an admin, Telegram sends it
    the `my_chat_member` update the bot poller links the chat from, and the paid-subscription
    link becomes possible. Doing it here saves the user the fiddliest part of every
    Telegram-bot setup guide.
    """
    if not bot_username:
        raise TelegramUserError("Connect a bot first.")
    try:
        return _run(_add_bot(account, chat_id, bot_username), timeout=90)
    except Exception as err:  # noqa: BLE001
        raise _friendly(err) from None
