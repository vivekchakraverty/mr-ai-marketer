"""The Community section — an open group plus a paid channel.

Thin translation layer over services/telegram_community.py: HTTP in, Bot API out.

**Two chats, because Telegram gives no third option.** A group shows every message to every
member; there is no per-post visibility and no way to hide a post from some of the people in
the room. So "everyone sees general posts, only paying members see gated ones" has to mean
two places:

* an **open group** — admins add whoever they like, everything posted there is for everyone;
* a **paid channel** — joined through a Telegram subscription link, which Telegram itself
  charges for, renews, and removes people from when they stop paying.

Both are chats the user creates: a bot cannot create either. The app links each one
automatically from the `my_chat_member` update when the bot is added, telling them apart by
chat type — a channel is the gated side, a group is the open side.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..services import telegram_community as tg
from ..services.telegram_community import TelegramError

router = APIRouter(prefix="/community", tags=["community"])


class ConnectBotRequest(BaseModel):
    token: str


class TierRequest(BaseModel):
    id: str | None = None
    name: str
    description: str = ""
    stars: int = Field(ge=1, le=100000)
    periodDays: int = Field(default=30, ge=1, le=365)
    active: bool = True


class BroadcastRequest(BaseModel):
    text: str
    gated: bool = True


def _public_config() -> dict:
    """Config for the UI, minus the bot token — it never leaves the backend."""
    cfg = db.get_community_config()
    return {
        "botConnected": bool(cfg.get("bot_token")),
        "botUsername": cfg.get("bot_username") or "",
        "chatId": cfg.get("chat_id") or "",
        "chatTitle": cfg.get("chat_title") or "",
        "inviteLink": cfg.get("invite_link") or "",
        "gatedChatId": cfg.get("gated_chat_id") or "",
        "gatedChatTitle": cfg.get("gated_chat_title") or "",
        "gatedInviteLink": cfg.get("gated_invite_link") or "",
    }


@router.get("/status")
def status() -> dict:
    cfg = _public_config()
    return {
        **cfg,
        "groupLinked": bool(cfg["chatId"]),
        "gatedLinked": bool(cfg["gatedChatId"]),
        "tiers": db.list_community_tiers(),
        "revenue": db.community_revenue(),
        "lastError": tg.last_error(),
    }


@router.post("/bot")
def connect_bot(body: ConnectBotRequest) -> dict:
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Paste the token @BotFather gave you.")
    try:
        me = tg.verify_bot(token)
    except TelegramError as err:
        raise HTTPException(status_code=400, detail=f"Telegram rejected that token: {err}") from None
    db.update_community_config(bot_token=token, bot_username=me.get("username") or "")
    return {"botUsername": me.get("username"), "name": me.get("name")}


@router.delete("/bot")
def disconnect_bot() -> dict:
    # Clears both chat links too: a chat id from a bot that is no longer connected is a
    # reference to something this app can no longer act on.
    db.update_community_config(
        bot_token="", bot_username="",
        chat_id="", chat_title="", invite_link="",
        gated_chat_id="", gated_chat_title="", gated_invite_link="",
    )
    return {"botConnected": False}


@router.post("/invite-link")
def invite_link() -> dict:
    cfg = db.get_community_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=400, detail="Connect a bot first.")
    if not cfg.get("chat_id"):
        raise HTTPException(
            status_code=400,
            detail="No group linked yet. Create a Telegram group, add your bot to it and make "
                   "it an admin — the app links it automatically once that happens.",
        )
    try:
        link = tg.create_invite_link(cfg["bot_token"], cfg["chat_id"])
    except TelegramError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    db.update_community_config(invite_link=link)
    return {"inviteLink": link}


@router.get("/tiers")
def list_tiers() -> dict:
    return {"tiers": db.list_community_tiers()}


@router.post("/tiers")
def save_tier(body: TierRequest) -> dict:
    tier = db.save_community_tier(
        name=body.name.strip() or "Membership",
        stars=body.stars,
        description=body.description.strip(),
        period_days=body.periodDays,
        tier_id=body.id,
        active=body.active,
    )
    return {"tier": tier}


@router.delete("/tiers/{tier_id}")
def delete_tier(tier_id: str) -> dict:
    db.delete_community_tier(tier_id)
    return {"deleted": True}


@router.get("/members")
def list_members() -> dict:
    return {"members": db.list_community_members()}


@router.post("/sweep")
def sweep() -> dict:
    """Remove lapsed members now rather than waiting for the hourly pass."""
    cfg = db.get_community_config()
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        raise HTTPException(status_code=400, detail="Connect a bot and link a group first.")
    try:
        removed = tg.sweep_expired(cfg["bot_token"], cfg["chat_id"])
    except TelegramError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    return {"removed": removed}


@router.post("/broadcast")
def broadcast(body: BroadcastRequest) -> dict:
    """Post to the open group, or to the paid channel.

    These are two different chats on purpose. A Telegram group shows every message to every
    member — there is no per-post visibility — so a "subscribers only" post has to live
    somewhere only subscribers can be, which is the gated channel.
    """
    cfg = db.get_community_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=400, detail="Connect a bot first.")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to send.")

    target = cfg.get("gated_chat_id") if body.gated else cfg.get("chat_id")
    if not target:
        raise HTTPException(
            status_code=400,
            detail=("No paid channel linked yet — add the bot to a Telegram channel."
                    if body.gated else
                    "No group linked yet — add the bot to your Telegram group."),
        )
    try:
        tg._call(cfg["bot_token"], "sendMessage", chat_id=target, text=text)
    except TelegramError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    return {"sentTo": "gated" if body.gated else "group"}


@router.post("/gated-invite")
def gated_invite() -> dict:
    """A paid subscription link for the gated channel.

    Telegram runs the subscription itself once this exists: it charges the Stars, admits the
    member, renews monthly and removes them if they stop paying. That is why the paid side
    needs no join-request approval, unlike a bot-gated group.
    """
    cfg = db.get_community_config()
    if not cfg.get("bot_token"):
        raise HTTPException(status_code=400, detail="Connect a bot first.")
    if not cfg.get("gated_chat_id"):
        raise HTTPException(
            status_code=400,
            detail="No paid channel linked yet. Create a Telegram channel, add your bot and "
                   "make it an admin — the app links it automatically.",
        )
    tiers = [t for t in db.list_community_tiers(active_only=True) if int(t["period_days"]) == 30]
    if not tiers:
        raise HTTPException(
            status_code=400,
            detail="Add a 30-day tier first — Telegram only supports monthly subscription links.",
        )
    price = min(int(t["stars"]) for t in tiers)
    try:
        link = tg.create_subscription_invite_link(cfg["bot_token"], cfg["gated_chat_id"], price)
    except TelegramError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    db.update_community_config(gated_invite_link=link)
    return {"gatedInviteLink": link, "stars": price}
