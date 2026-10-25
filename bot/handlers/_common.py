"""Shared state and helpers for the bot.handlers package.

All mutating state that tests patch (ledger, _now, _qr_bytes, _money_cmd_last)
lives here so a single monkeypatch on bot.handlers._common works everywhere.
"""

import html
import logging
import re
import time
from decimal import Decimal

from aiogram import Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import base, batch, config, credit, i18n, paymaster, wallets
from .. import qr as qrlib
from ..creator_tokens import registry as creator_tokens
from ..ledger import async_ledger as ledger
from ..recurring import store as recurring

log = logging.getLogger("tipbot.handlers")

__all__ = [
    "AMOUNT_RE",
    "BET_ID_RE",
    "BET_LINK_RE",
    "DEADLINE_RE",
    "DONATE_LINK_RE",
    "KIND_EMOJI",
    "PAYWALL_LINK_RE",
    "QUICK_AMOUNTS",
    "SIG_RE",
    "TX_HASH_RE",
    "USDC_ADDR_RE",
    "_back_row",
    "_bot_username",
    "_default_deadline_label",
    "_edit_menu",
    "_esc",
    "_fmt",
    "_get_bot_username",
    "_menu_kb",
    "_money_cmd_last",
    "_more_kb",
    "_now",
    "_qr_bytes",
    "_throttle",
    "_to_micro",
    "base",
    "batch",
    "config",
    "creator_tokens",
    "credit",
    "ledger",
    "paymaster",
    "qrlib",
    "recurring",
    "require_private",
    "router",
    "wallets",
]

router = Router()

# ----- error safety net -----
_error_notify_ts = 0.0
_ERROR_NOTIFY_COOLDOWN = 60


def _err_context(up) -> str:
    """Short human-readable context of the update that failed."""
    try:
        ev = getattr(up, "event", None)
        if ev is None:
            return f"update_type={type(up).__name__}"
        chat = getattr(ev, "chat", None)
        parts = [f"chat={chat.id}:{chat.type}"] if chat is not None else []
        fu = getattr(ev, "from_user", None)
        if fu is not None:
            parts.append(f"user={fu.username or fu.id}")
        txt = getattr(ev, "text", None) or getattr(ev, "caption", None)
        if txt:
            parts.append(f"text={txt[:120]!r}")
        return " · ".join(parts) or "unknown"
    except Exception:
        return "unknown"


@router.errors()
async def _on_error(event: types.ErrorEvent) -> None:
    """Last-resort safety net: a failed money handler must not stay silent.

    aiogram swallows unhandled exceptions in both polling and webhook modes.
    This logs full context, tells the user (private chats only) something went
    wrong, and pings the admin in a throttled fashion.
    """
    global _error_notify_ts
    up: types.Update = event.update
    ctx = _err_context(up)
    log.error(
        "unhandled update error | context: %s | exception: %r",
        ctx, event.exception,
        exc_info=event.exception,
    )

    # A failed money handler can leave the shared ledger connection in an
    # open/aborted transaction (e.g. an exception raised between a SELECT FOR
    # UPDATE and its commit). Roll it back here so the connection isn't
    # poisoned (`InFailedSqlTransaction`) and doesn't pin row locks that block
    # concurrent DDL/watchers — otherwise every later handler call fails until
    # a process restart. Idempotent: rollback after a healthy commit is a no-op.
    try:
        await ledger.rollback()
    except Exception:
        log.warning("ledger rollback after handler error failed", exc_info=True)

    try:
        ev = getattr(up, "event", None)
    except Exception:
        ev = None
    bot = getattr(up, "bot", None)
    try:
        if bot is not None and ev is not None and getattr(ev, "chat", None) and ev.chat.type == "private":
            fu = getattr(ev, "from_user", None)
            lang = "ru"
            if fu is not None:
                lang = await user_lang(fu.id)
            await bot.send_message(ev.chat.id, i18n.t(lang, "error_generic"))
    except Exception:
        pass

    admin = int(config.ADMIN_TG_ID) if config.ADMIN_TG_ID else 0
    if not admin or bot is None:
        return
    now = time.time()
    if now - _error_notify_ts < _ERROR_NOTIFY_COOLDOWN:
        return
    _error_notify_ts = now
    try:
        await bot.send_message(
            admin,
            f"⚠️ <b>Bot error</b>\n{html.escape(type(event.exception).__name__)}: {html.escape(str(event.exception)[:200])}\n{html.escape(ctx)}",
            parse_mode="HTML",
        )
    except Exception:
        log.exception("failed to notify admin about a handler error")

TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
USDC_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
AMOUNT_RE = re.compile(r"^\d{1,9}(\.\d{1,6})?$")
SIG_RE = re.compile(r"^0x[a-fA-F0-9]{130}$")
BET_ID_RE = re.compile(r"^\d{1,9}$")
DONATE_LINK_RE = re.compile(r"^donate_(\d{1,20})$")
BET_LINK_RE = re.compile(r"^bet_(\d{1,20})$")
PAYWALL_LINK_RE = re.compile(r"^paywall_(\d{1,20})$")
DEADLINE_RE = re.compile(r"^(\d{1,3})([hd])$")

QUICK_AMOUNTS = ("5", "10", "25", "50")


def _default_deadline_label() -> str:
    """The window a market gets when the creator states no deadline, spelled the
    way DEADLINE_RE parses, so help text cannot drift from MARKET_DEFAULT_HOURS."""
    hours = config.MARKET_DEFAULT_HOURS
    return f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"


async def user_lang(tg_id: int) -> str:
    try:
        s = await ledger.get_settings(tg_id)
        return i18n.norm(s.get("lang"))
    except Exception:
        return "ru"


async def require_private(message: types.Message) -> bool:
    """Refuse sensitive commands outside a private chat to avoid leaking
    secrets (private keys, seed phrases) into group history. Returns True
    when the command may proceed. On refusal it hints the user to DM the
    bot and best-effort deletes the triggering message."""
    if message.chat.type == "private":
        return True
    lang = await user_lang(message.from_user.id)
    await message.answer(i18n.t(lang, "private_chat_only"))
    try:
        await message.delete()
    except Exception:
        pass
    return False


def help_text(lang: str = "ru") -> str:
    return i18n.t(lang, "help_full")


KIND_EMOJI = {
    "deposit": "⬇️",
    "tip": "💸",
    "tip_in": "💸",
    "rain_in": "🌧️",
    "withdraw": "⬆️",
    "withdraw_refund": "⬇️",
    "fee": "🧾",
    "creator_income": "🧾",
    "dividend": "🧾",
    "bet": "🎲",
    "bet_win": "🏆",
    "bet_cancel": "↩️",
    "x402": "🤖",
    "paywall": "🔐",
    "paywall_earn": "💰",
    "channel_pay": "🔑",
    "channel_earn": "💰",
    "market_create": "🏦",
    "market_buy": "📈",
    "market_sell": "📉",
    "market_win": "🏆",
    "market_cancel": "↩️",
}

_bot_username: str | None = None


def _fmt(amount_micro: int) -> str:
    d = Decimal(amount_micro) / Decimal(10**config.USDC_DECIMALS)
    s = f"{d:,.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _to_micro(amount: Decimal) -> int:
    return int(amount * Decimal(10**config.USDC_DECIMALS))


def _h(s: object) -> str:
    """HTML-escape user content that is interpolated into ParseMode.HTML text.

    Questions, option labels and usernames are user-authored; without escaping
    a `<a href=...>`/`<script>` payload becomes a phishing vector in every
    None-to-telegram HTML-rendered message.
    """
    return html.escape(str(s))


def _esc(s: str, n: int = 4) -> str:
    return html.escape(f"{s[:6]}…{s[-n:]}")


def _now() -> float:
    return time.time()


_money_cmd_last: dict[tuple[int, str], float] = {}


async def _throttle(tg_id: int, action: str) -> str | None:
    cooldown = config.MONEY_CMD_COOLDOWN_SECONDS
    if cooldown <= 0:
        return None
    key = (tg_id, action)
    now = _now()
    last = _money_cmd_last.get(key, 0.0)
    if now - last < cooldown:
        lang = await user_lang(tg_id)
        remaining = max(1, int(cooldown - (now - last)) + 1)
        return i18n.t(lang, "throttle", sec=remaining)
    if len(_money_cmd_last) > 100_000:
        cutoff = now - 3600
        expired = [k for k, v in _money_cmd_last.items() if v < cutoff]
        for k in expired:
            del _money_cmd_last[k]
    _money_cmd_last[key] = now
    return None


async def _get_bot_username(bot) -> str:
    global _bot_username
    if _bot_username is None:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username


def _back_row(lang: str) -> InlineKeyboardButton:
    """Every screen the menu opens has to hand the menu back. Until now ten of
    the fourteen buttons edited the message with `reply_markup=None`, which
    deletes the keyboard: one tap on «Баланс» and the only way back was typing
    /menu again."""
    return InlineKeyboardButton(text=i18n.t(lang, 'btn_back'), callback_data='menu')


def _menu_button(lang: str, key: str, cb: str | None = None) -> InlineKeyboardButton:
    if key == "about":
        return InlineKeyboardButton(text=i18n.t(lang, "btn_about"), callback_data="about")
    return InlineKeyboardButton(text=i18n.t(lang, f"btn_{key}"), callback_data=cb or key)


def _menu_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    """The main screen. Sixteen buttons in ten flat rows buried the Mini App
    under «Мой Basename»; the pairs are now the four things a user came for, and
    everything else is one tap away behind «Ещё»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button(lang, "bal"), _menu_button(lang, "dep")],
            [_menu_button(lang, "tip"), _menu_button(lang, "markets", "markets_amm")],
            [_menu_button(lang, "bets"), _menu_button(lang, "ask")],
            [_menu_button(lang, "top"), _menu_button(lang, "hist")],
            [_menu_button(lang, "settings"), _menu_button(lang, "more", "menu_more")],
            [_menu_button(lang, "mini_app", "miniapp")],
        ]
    )


def _more_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    """The second level: account plumbing and the creator features, none of
    which is the reason a new user opens the bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button(lang, "wallet"), _menu_button(lang, "stats")],
            [_menu_button(lang, "donate"), _menu_button(lang, "paywall", "paywall_list")],
            [_menu_button(lang, "basename")],
            [_menu_button(lang, "about"), _back_row(lang)],
        ]
    )


async def _qr_bytes(data: str) -> bytes | None:
    try:
        return await qrlib.qr_bytes(data)
    except Exception:
        return None


async def _edit_menu(cb: types.CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=reply_markup)
        except Exception:
            pass
