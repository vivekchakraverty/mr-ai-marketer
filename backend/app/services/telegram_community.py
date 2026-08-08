"""Telegram Bot API client and the loop that runs the Community section.

What the bot can and cannot do, because it shapes the whole design:

* **It cannot create the group.** Telegram only lets a human create one — bots have no such
  method. So the setup flow is: the user creates the group, adds this bot, promotes it to
  admin, and the app links it by reading the `my_chat_member` update that arrives when that
  happens. Anything else would be pretending.
* **It cannot see who is in a group it did not watch join.** Telegram gives a bot the member
  list only through join events, so membership is tracked from the moment the bot is added.

Payments are **Telegram Stars** (`XTR`). That is not a preference: Telegram requires digital
goods and subscriptions to be sold in Stars, and it means no payment-provider token, no card
handling, and no money touching this machine — Telegram settles with the group owner.

Updates arrive by **long polling**, not webhooks. A webhook needs a public HTTPS endpoint,
which a desktop app on someone's laptop does not have.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from .. import db

API_ROOT = "https://api.telegram.org"

# Long-poll window. Telegram holds the request open until something happens or this elapses,
# so a big value means near-zero idle traffic rather than a slow reaction.
POLL_TIMEOUT = 45
# Gap after a failure before trying again, so a network blip doesn't become a hot loop.
ERROR_BACKOFF = 15

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_last_error = ""


class TelegramError(RuntimeError):
    pass


def _call(token: str, method: str, http_timeout: int = 30, **params) -> Any:
    """One Bot API call.

    The HTTP timeout is named `http_timeout` rather than `timeout` on purpose: `getUpdates`
    takes its *own* `timeout` parameter for long polling, and a single `timeout` name would
    swallow it — leaving Telegram to default to 0, i.e. short polling in a tight loop.
    """
    if not token:
        raise TelegramError("No bot token configured.")
    try:
        resp = requests.post(f"{API_ROOT}/bot{token}/{method}", json=params, timeout=http_timeout)
    except requests.RequestException as err:
        raise TelegramError(f"{method} failed to connect: {err}") from err
    try:
        body = resp.json()
    except ValueError:
        raise TelegramError(f"{method} returned non-JSON (HTTP {resp.status_code})") from None
    if not body.get("ok"):
        # Telegram's own description is far more useful than the status code.
        raise TelegramError(body.get("description") or f"{method} failed (HTTP {resp.status_code})")
    return body.get("result")


# --------------------------------------------------------------------------- setup


def verify_bot(token: str) -> dict:
    """Confirms a token works and returns the bot's identity."""
    me = _call(token, "getMe")
    return {"id": me.get("id"), "username": me.get("username"), "name": me.get("first_name")}


def create_invite_link(token: str, chat_id: str) -> str:
    """An invite link that creates *join requests* rather than admitting people directly.

    This is what makes gating possible at all: with `creates_join_request`, everyone who
    clicks waits for approval, and the bot approves only those whose subscription is live.
    A plain invite link would let anyone straight in and leave nothing to gate.
    """
    result = _call(
        token, "createChatInviteLink",
        chat_id=chat_id, creates_join_request=True, name="Mr. AI Marketer community",
    )
    return result.get("invite_link", "")


def create_subscription_invite_link(token: str, chat_id: str, stars: int) -> str:
    """A Telegram-native paid subscription link for the gated channel.

    This hands the whole access problem to Telegram: it takes the recurring Star payment,
    admits the member, renews them monthly and removes them when they stop paying. The bot
    does not have to approve anyone, and there is no state here that can drift out of step
    with who has actually paid.

    Telegram only accepts a 30-day period for these today, which is why tiers on any other
    cadence fall back to the bot-managed path instead.
    """
    result = _call(
        token, "createChatSubscriptionInviteLink",
        chat_id=chat_id,
        subscription_period=2592000,
        subscription_price=int(stars),
        name="Members",
    )
    return result.get("invite_link", "")


# --------------------------------------------------------------------------- payments


def send_subscription_invoice(token: str, telegram_id: str, tier: dict) -> None:
    """Send a Stars invoice for a tier.

    `subscription_period` makes it a recurring Star subscription that Telegram renews and
    reports back; without it the charge is one-off and the member silently lapses. Telegram
    only accepts 30 days (2592000s) for this today, so a tier with any other period is billed
    as a one-off rather than quietly charging the wrong cadence.
    """
    payload = {
        "chat_id": telegram_id,
        "title": tier["name"],
        "description": tier.get("description") or f"Access to the community for {tier['period_days']} days",
        "payload": f"tier:{tier['id']}",
        "currency": "XTR",
        "prices": [{"label": tier["name"], "amount": int(tier["stars"])}],
    }
    if int(tier.get("period_days", 30)) == 30:
        payload["subscription_period"] = 2592000
    _call(token, "sendInvoice", **payload)


# --------------------------------------------------------------------------- gating


def _is_active(member: Optional[dict]) -> bool:
    if not member or member.get("status") != "active":
        return False
    expires = member.get("expires_at")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(expires) > datetime.now(timezone.utc)
    except ValueError:
        return False


def _grant(telegram_id: str, tier: Optional[dict], days: int) -> dict:
    """Extend from whichever is later: now, or an unexpired current period.

    Renewing early should add to the remaining time, not throw it away — otherwise paying
    ahead of expiry costs the member days they already bought.
    """
    existing = db.get_community_member(telegram_id)
    base = datetime.now(timezone.utc)
    if existing and existing.get("expires_at"):
        try:
            current = datetime.fromisoformat(existing["expires_at"])
            base = max(base, current)
        except ValueError:
            pass
    return db.upsert_community_member(
        telegram_id,
        tier_id=(tier or {}).get("id"),
        status="active",
        expires_at=(base + timedelta(days=days)).isoformat(),
    )


def _handle_join_request(token: str, chat_id: str, update: dict) -> None:
    req = update["chat_join_request"]
    user = req.get("from") or {}
    uid = str(user.get("id"))
    db.upsert_community_member(
        uid, username=user.get("username") or "", first_name=user.get("first_name") or ""
    )
    member = db.get_community_member(uid)

    if _is_active(member):
        _call(token, "approveChatJoinRequest", chat_id=chat_id, user_id=user.get("id"))
        db.upsert_community_member(uid, in_group=1, joined_at=datetime.now(timezone.utc).isoformat())
        _say(token, uid, "You're in — welcome to the community.")
        return

    # Not a paying member: leave the request pending rather than declining it. Declining
    # bans re-requesting for a while on Telegram's side, which would punish someone who is
    # about to pay. Instead, tell them how to join and let the approval happen on payment.
    tiers = db.list_community_tiers(active_only=True)
    if tiers:
        lines = "\n".join(f"• {t['name']} — {t['stars']} ⭐ / {t['period_days']} days" for t in tiers)
        _say(token, uid, f"This community is subscriber-only.\n\n{lines}\n\nSend /join to subscribe.")
    else:
        _say(token, uid, "This community isn't open for subscriptions yet.")


def _handle_successful_payment(token: str, chat_id: str, message: dict) -> None:
    payment = message["successful_payment"]
    user = message.get("from") or {}
    uid = str(user.get("id"))
    tier_id = (payment.get("invoice_payload") or "").removeprefix("tier:")
    tier = next((t for t in db.list_community_tiers() if t["id"] == tier_id), None)
    days = int(tier["period_days"]) if tier else 30

    db.add_community_payment(
        uid,
        stars=int(payment.get("total_amount") or 0),
        tier_id=tier_id or None,
        charge_id=payment.get("telegram_payment_charge_id") or "",
        is_recurring=bool(payment.get("is_recurring")),
    )
    _grant(uid, tier, days)

    # Approve any request they left pending before paying, so buying is the last step they
    # have to take rather than "pay, then ask again and wait".
    if chat_id:
        try:
            _call(token, "approveChatJoinRequest", chat_id=chat_id, user_id=user.get("id"))
            db.upsert_community_member(uid, in_group=1, joined_at=datetime.now(timezone.utc).isoformat())
        except TelegramError:
            pass  # no pending request — they may already be in, or will click the link next

    config = db.get_community_config()
    link = config.get("invite_link")
    _say(token, uid, "Payment received — thank you!" + (f"\n\nJoin here: {link}" if link else ""))


def _handle_command(token: str, message: dict) -> None:
    text = (message.get("text") or "").strip().lower()
    user = message.get("from") or {}
    uid = str(user.get("id"))
    db.upsert_community_member(
        uid, username=user.get("username") or "", first_name=user.get("first_name") or ""
    )

    if text.startswith("/start") or text.startswith("/join"):
        tiers = db.list_community_tiers(active_only=True)
        if not tiers:
            _say(token, uid, "No subscription tiers are set up yet.")
            return
        for tier in tiers:
            try:
                send_subscription_invoice(token, uid, tier)
            except TelegramError as err:
                _say(token, uid, f"Couldn't create the invoice for {tier['name']}: {err}")
    elif text.startswith("/status"):
        member = db.get_community_member(uid)
        if _is_active(member):
            _say(token, uid, f"Your subscription is active until {member['expires_at'][:10]}.")
        else:
            _say(token, uid, "You don't have an active subscription. Send /join to subscribe.")


def _say(token: str, chat_id: str, text: str) -> None:
    try:
        _call(token, "sendMessage", chat_id=chat_id, text=text)
    except TelegramError:
        pass  # a member who blocked the bot must not break the loop


def _handle_my_chat_member(update: dict) -> None:
    """Links the group automatically when the bot is added to one.

    This is the only moment the app learns the chat id — the user never has to find and copy
    it, which is the part of every Telegram-bot setup guide that people get wrong.
    """
    change = update["my_chat_member"]
    chat = change.get("chat") or {}
    status = (change.get("new_chat_member") or {}).get("status")
    if status not in ("member", "administrator"):
        return

    kind = chat.get("type")
    cfg = db.get_community_config()
    # A channel is the gated side, a group is the open side. Telegram makes that distinction
    # for us, so the user never has to say which chat they just added the bot to — and the
    # two roles are genuinely different chat types, not a setting.
    if kind == "channel" and not cfg.get("gated_chat_id"):
        db.update_community_config(
            gated_chat_id=str(chat.get("id")), gated_chat_title=chat.get("title") or ""
        )
    elif kind in ("group", "supergroup") and not cfg.get("chat_id"):
        db.update_community_config(
            chat_id=str(chat.get("id")), chat_title=chat.get("title") or ""
        )


def process_updates(token: str, chat_id: str, offset: int) -> int:
    """One long-poll cycle. Returns the new offset."""
    updates = _call(
        token, "getUpdates",
        http_timeout=POLL_TIMEOUT + 10,   # outlive Telegram's own hold
        offset=offset + 1,
        timeout=POLL_TIMEOUT,             # Telegram's long-poll window
        allowed_updates=["message", "chat_join_request", "my_chat_member", "pre_checkout_query"],
    ) or []

    for update in updates:
        offset = max(offset, int(update.get("update_id", 0)))
        try:
            if "chat_join_request" in update:
                _handle_join_request(token, chat_id, update)
            elif "my_chat_member" in update:
                _handle_my_chat_member(update)
            elif "pre_checkout_query" in update:
                # Telegram gives ~10 seconds to accept or the payment fails. There is nothing
                # to validate here — the invoice was ours — so answer immediately.
                _call(token, "answerPreCheckoutQuery",
                      pre_checkout_query_id=update["pre_checkout_query"]["id"], ok=True)
            elif "message" in update:
                message = update["message"]
                if "successful_payment" in message:
                    _handle_successful_payment(token, chat_id, message)
                elif (message.get("text") or "").startswith("/"):
                    _handle_command(token, message)
        except Exception as err:  # noqa: BLE001 — one bad update must not stall the rest
            print(f"[community] update {update.get('update_id')} failed: {err}")
    return offset


def sweep_expired(token: str, chat_id: str) -> int:
    """Remove members whose subscription has lapsed. Returns how many were removed."""
    if not chat_id:
        return 0
    removed = 0
    for member in db.list_expired_members():
        db.upsert_community_member(member["telegram_id"], status="expired")
        if not member.get("in_group"):
            continue
        try:
            # Ban then unban: Telegram has no plain "remove", and leaving them banned would
            # stop them rejoining when they resubscribe.
            _call(token, "banChatMember", chat_id=chat_id, user_id=int(member["telegram_id"]))
            _call(token, "unbanChatMember", chat_id=chat_id,
                  user_id=int(member["telegram_id"]), only_if_banned=True)
            db.upsert_community_member(member["telegram_id"], in_group=0)
            removed += 1
            _say(token, member["telegram_id"], "Your subscription has ended. Send /join to renew.")
        except TelegramError as err:
            print(f"[community] could not remove {member['telegram_id']}: {err}")
    return removed


# --------------------------------------------------------------------------- loop


def _loop() -> None:
    global _last_error
    last_sweep = 0.0
    while not _stop.is_set():
        config = db.get_community_config()
        token = config.get("bot_token") or ""
        if not token:
            _stop.wait(30)  # nothing configured yet; check back rather than spin
            continue
        try:
            offset = process_updates(token, config.get("chat_id") or "", int(config.get("last_update_id") or 0))
            if offset != int(config.get("last_update_id") or 0):
                db.update_community_config(last_update_id=offset)
            _last_error = ""
            if time.time() - last_sweep > 3600:
                sweep_expired(token, config.get("chat_id") or "")
                last_sweep = time.time()
        except TelegramError as err:
            _last_error = str(err)
            print(f"[community] {err}")
            _stop.wait(ERROR_BACKOFF)
        except Exception as err:  # noqa: BLE001
            _last_error = f"{type(err).__name__}: {err}"
            print(f"[community] loop error: {err}")
            _stop.wait(ERROR_BACKOFF)


def start_poller() -> None:
    """Starts the update loop. Inert until a bot token is configured."""
    global _thread
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="community-poller", daemon=True)
    _thread.start()
    print("[community] Telegram poller started")


def last_error() -> str:
    return _last_error
