"""/tip  handlers."""
import logging
from decimal import Decimal

from aiogram import Bot, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import i18n, tip_targets

from . import _common as common

__all__ = ['_notify_tip_received', '_resolve_in_chat', 'cb_tip_pick', 'cmd_rain', 'cmd_tip']

log = logging.getLogger("tipbot.tips")


def _amount_kb() -> InlineKeyboardMarkup:
    """Quick amounts, the same set the bet and trade cards use."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'{a} USDC', callback_data=f'tip:a:{a}') for a in common.QUICK_AMOUNTS]
    ])


async def _recipient_kb(tg_id: int, amount: str, lang: str) -> InlineKeyboardMarkup | None:
    """Buttons for the people this user tips most often; None when there is no
    history to offer, which is the only case where a typed @username is the
    shortest path."""
    ids = await common.ledger.recent_tip_targets(tg_id, 6)
    if not ids:
        return None
    names = await common.ledger.usernames_bulk(ids)
    rows = [[InlineKeyboardButton(text=f'@{names[i] or f"id{i}"}', callback_data=f'tip:r:{amount}:{i}')]
            for i in ids]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_tip_target(message: types.Message, lang: str, amount: Decimal) -> None:
    """Ask for the missing half of the command with buttons instead of a hint."""
    amt = common._fmt(common._to_micro(amount))
    kb = await _recipient_kb(message.from_user.id, f'{amount}', lang)
    if kb is None:
        await message.answer(i18n.t(lang, 'tip_pick_recipient_first', amt=amt))
        return
    await message.answer(i18n.t(lang, 'tip_pick_recipient', amt=amt), reply_markup=kb)


async def _send_tip(bot: Bot, lang: str, from_id: int, from_name: str | None,
                    to_id: int, to_name: str, amount_micro: int) -> str:
    """Move the money and write the receipt — one path for a typed /tip and for
    a tap on a chip, so a button can never be a weaker operation than typing.

    The payer is passed in, not read off a message: on the callback path the
    message's author is the bot, and a receipt naming the bot as the sender is
    exactly what this would have produced.
    """
    wait = await common._throttle(from_id, 'tip')
    if wait:
        return wait
    if not await common.ledger.transfer(from_id, to_id, amount_micro):
        return i18n.t(lang, 'tip_no_balance')
    sender_name = from_name or f'id{from_id}'
    safe_to = common._h(to_name or str(to_id))
    mention = f"<a href='tg://user?id={to_id}'>@{safe_to}</a>"
    bal = await common.ledger.balance(from_id)
    bal_str = f'{bal:.4f}'.rstrip('0').rstrip('.')
    await _notify_tip_received(bot, from_id, to_id, amount_micro, sender_name)
    return i18n.t(lang, 'tip_sent', sender=common._h(sender_name), mention=mention,
                  amount=common._fmt(amount_micro), bal=bal_str)


@common.router.callback_query(F.data.startswith('tip:'))
async def cb_tip_pick(cb: types.CallbackQuery) -> None:
    user = cb.from_user
    if not user:
        return
    lang = await common.user_lang(user.id)
    parts = cb.data.split(':')
    if len(parts) == 3 and parts[1] == 'a':
        amount = parts[2]
        if not common.AMOUNT_RE.match(amount):
            await cb.answer(i18n.t(lang, 'bad_amount'), show_alert=True)
            return
        kb = await _recipient_kb(user.id, amount, lang)
        text = i18n.t(lang, 'tip_pick_recipient', amt=amount) if kb else \
            i18n.t(lang, 'tip_pick_recipient_first', amt=amount)
        if kb is None:
            await cb.message.answer(text)
        else:
            await common._edit_menu(cb, text, kb)
        await cb.answer()
        return
    if len(parts) == 4 and parts[1] == 'r':
        _, _, amount, to_raw = parts
        if not common.AMOUNT_RE.match(amount) or not to_raw.isdigit() or len(to_raw) > 20:
            await cb.answer(i18n.t(lang, 'bad_amount'), show_alert=True)
            return
        to_id = int(to_raw)
        if to_id == user.id:
            await cb.answer(i18n.t(lang, 'tip_self'), show_alert=True)
            return
        to_name = (await common.ledger.usernames_bulk([to_id])).get(to_id) or str(to_id)
        text = await _send_tip(cb.message.bot, lang, user.id, user.username, to_id, to_name,
                               common._to_micro(Decimal(amount)))
        await common._edit_menu(cb, text)
        await cb.answer()
        return
    await cb.answer(i18n.t(lang, 'market_not_found'), show_alert=True)

@common.router.message(Command('rain'))
async def cmd_rain(message: types.Message) -> None:
    chat = message.chat
    lang = await common.user_lang(message.from_user.id)
    if chat.type not in ('group', 'supergroup'):
        await message.answer(i18n.t(lang, 'rain_only_groups'))
        return
    parts = message.text.strip().split()
    if len(parts) not in (2, 3) or not common.AMOUNT_RE.match(parts[1]):
        await message.answer(i18n.t(lang, 'rain_format'))
        return
    amount = Decimal(parts[1])
    if amount <= 0:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if amount > common.config.RAIN_MAX_USDC:
        await message.answer(i18n.t(lang, 'rain_max', n=f'{common.config.RAIN_MAX_USDC:.0f}'))
        return
    count = int(parts[2]) if len(parts) == 3 else 5
    if count < 1:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if count > common.config.RAIN_MAX_RECIPIENTS:
        await message.answer(i18n.t(lang, 'rain_max_participants', n=str(common.config.RAIN_MAX_RECIPIENTS)))
        return
    wait = await common._throttle(message.from_user.id, 'rain')
    if wait:
        await message.answer(wait)
        return
    amount_micro = common._to_micro(amount)
    ok, text, chosen = await common.ledger.rain(chat.id, message.from_user.id, amount_micro, count)
    if not ok:
        await message.answer(text)
        return
    names = []
    # One read for the whole line-up: username_of() per recipient put up to eight
    # queries behind the ledger lock on a single /rain.
    shown = chosen[:8]
    display = await common.ledger.usernames_bulk(shown)
    for tid in shown:
        uname = display.get(tid) or f'id{tid}'
        names.append(f'@{common._h(uname)}')
    tail = i18n.t(lang, 'rain_and_more', n=len(chosen) - 8) if len(chosen) > 8 else ''
    names_str = ', '.join(names)
    await message.answer(i18n.t(lang, 'rain_recipients', names=names_str, tail=tail))

@common.router.message(Command('tip'))
async def cmd_tip(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    parts = message.text.strip().split()
    if len(parts) == 1:
        # No amount at all: chips first, recipients next. The old answer was a
        # sentence describing a command nobody types from memory.
        await message.answer(i18n.t(lang, 'tip_pick_amount'), reply_markup=_amount_kb())
        return
    if common.AMOUNT_RE.match(parts[1]):
        amount = Decimal(parts[1])
        rest = parts[2:]
    else:
        await message.answer(i18n.t(lang, 'tip_need_amount'))
        return
    if amount <= 0:
        await message.answer(i18n.t(lang, 'rain_need_positive'))
        return
    if amount > common.config.MAX_TIP_USDC:
        await message.answer(i18n.t(lang, 'tip_max', n=f'{common.config.MAX_TIP_USDC:.0f}'))
        return
    amount_micro = common._to_micro(amount)
    if message.reply_to_message and message.reply_to_message.from_user:
        to_id = message.reply_to_message.from_user.id
        to_name = message.reply_to_message.from_user.username
    elif rest:
        target = rest[0]
        # Basenames (`name.base.eth`) resolve on-chain to an address, then to
        # the Tippy user that owns the address (linked or custodial wallet).
        bn_id, bn_err = await tip_targets.resolve_tip_target(target)
        if bn_err == 'basename_unknown':
            await message.answer(i18n.t(lang, 'tip_basename_unknown',
                                        name=common._h(target.lstrip('@').lower())))
            return
        if bn_id is not None:
            to_id = bn_id
            to_name = target.lstrip('@').lower()
        else:
            if not target.startswith('@'):
                await message.answer(i18n.t(lang, 'tip_need_recipient'))
                return
            username = target[1:]
            to_id = await common.ledger.find_by_username(username)
            if to_id is None:
                to_id = await _resolve_in_chat(message, username)
            if to_id is None:
                await message.answer(i18n.t(lang, 'tip_user_not_found', user=common._h(username)))
                return
            to_name = username
    else:
        # Amount known, recipient missing: offer the people they already tip.
        await _ask_tip_target(message, lang, amount)
        return
    if to_id == message.from_user.id:
        await message.answer(i18n.t(lang, 'tip_self'))
        return
    await message.answer(await _send_tip(message.bot, lang, message.from_user.id,
                                         message.from_user.username, to_id, to_name, amount_micro))

async def _notify_tip_received(bot: Bot, from_id: int, to_id: int,
                               amount_micro: int, sender: str) -> None:
    if to_id == from_id:
        return
    try:
        lang = await common.user_lang(to_id)
        await bot.send_message(to_id, i18n.t(lang, 'tip_received', amount=common._fmt(amount_micro), sender=common._h(sender)))
    except Exception:
        log.warning("tip notification to user %d failed", to_id, exc_info=True)

async def _resolve_in_chat(message: types.Message, username: str) -> int | None:
    try:
        async for member in message.chat.get_members(limit=200):
            user = member.user
            if user.username and user.username.lower() == username.lower():
                await common.ledger.ensure_user(user.id, user.username)
                return user.id
    except Exception:
        log.warning("chat member scan for @%s failed", username, exc_info=True)
        return None
    return None
