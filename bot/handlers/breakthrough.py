"""Handler for breakthrough Base features — gasless, recurring, batch, credit, creator tokens."""

import time

from aiogram import types
from aiogram.filters import Command

from bot import i18n

from . import _common as common
from .menu import _lang


async def cmd_gasless(message: types.Message):
    """Check gasless transaction eligibility."""
    lang = await _lang(message.from_user.id)
    addr = await common.wallets.get_address(message.from_user.id)
    if not addr:
        await message.answer(i18n.t(lang, 'no_wallet'))
        return

    info = await common.paymaster.check_eligibility(addr)
    if info["eligible"]:
        text = i18n.t(
            lang,
            'gasless_eligible',
            remaining=info["remaining"],
            total=info["total"],
        )
    else:
        text = i18n.t(lang, 'gasless_not_eligible')

    await message.answer(text)


async def cmd_subscribe(message: types.Message):
    """Set up recurring payment: /subscribe @user amount interval"""
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()

    if len(parts) < 4:
        await message.answer(i18n.t(lang, 'subscribe_format'))
        return

    try:
        to_username = parts[1].lstrip('@')
        amount = float(parts[2])
        interval_str = parts[3].lower()

        from ..recurring import RecurrenceInterval
        interval_map = {
            'daily': RecurrenceInterval.DAILY,
            'weekly': RecurrenceInterval.WEEKLY,
            'biweekly': RecurrenceInterval.BIWEEKLY,
            'monthly': RecurrenceInterval.MONTHLY,
        }

        if interval_str not in interval_map:
            await message.answer(i18n.t(lang, 'subscribe_invalid_interval'))
            return

        interval = interval_map[interval_str]
        amount_micro = int(amount * 1e6)

        to_user = await common.ledger.get_user_by_username(to_username)
        if not to_user:
            await message.answer(i18n.t(lang, 'user_not_found'))
            return

        to_tg_id = to_user['tg_id']
        from_tg_id = message.from_user.id

        payment_id = f"sub_{from_tg_id}_{to_tg_id}_{int(time.time())}"
        await common.recurring.create(
            payment_id=payment_id,
            from_tg_id=from_tg_id,
            to_tg_id=to_tg_id,
            amount_micro=amount_micro,
            interval=interval,
        )

        await message.answer(
            i18n.t(
                lang,
                'subscribe_created',
                to_username=to_username,
                amount=amount,
                interval=interval_str,
            )
        )
    except (ValueError, IndexError):
        await message.answer(i18n.t(lang, 'subscribe_format'))


async def cmd_subscriptions(message: types.Message):
    """List active subscriptions: /subscriptions"""
    lang = await _lang(message.from_user.id)
    uid = message.from_user.id

    payments = await common.recurring.list_for_user(uid)
    if not payments:
        await message.answer(i18n.t(lang, 'no_subscriptions'))
        return

    lines = []
    for p in payments:
        direction = "→" if p.from_tg_id == uid else "←"
        other_id = p.to_tg_id if p.from_tg_id == uid else p.from_tg_id
        other_user = await common.ledger.get_user_by_tg_id(other_id)
        other_name = other_user.get('username', f"ID{other_id}") if other_user else f"ID{other_id}"

        status = "✅" if p.active else "❌"
        amount = p.amount_micro / 1e6
        lines.append(
            f"{status} {direction} @{other_name}: ${amount:.2f} {p.interval.value}"
        )

    text = i18n.t(lang, 'subscriptions_list', list="\n".join(lines))
    await message.answer(text)


async def cmd_cancel_sub(message: types.Message):
    """Cancel subscription: /cancelsub <id>"""
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()

    if len(parts) < 2:
        await message.answer(i18n.t(lang, 'cancel_sub_format'))
        return

    payment_id = parts[1]
    uid = message.from_user.id

    success = await common.recurring.cancel(payment_id, uid)
    if success:
        await message.answer(i18n.t(lang, 'subscription_cancelled'))
    else:
        await message.answer(i18n.t(lang, 'subscription_not_found'))


async def cmd_credit(message: types.Message):
    """Check credit score: /credit"""
    lang = await _lang(message.from_user.id)
    uid = message.from_user.id

    from ..credit import CreditScorer
    scorer = CreditScorer(common.ledger)
    credit = await scorer.calculate(uid)

    max_loan = await scorer.get_max_loan(uid)

    text = i18n.t(
        lang,
        'credit_score',
        score=credit.score,
        grade=credit.grade,
        confidence=int(credit.confidence * 100),
        max_loan=max_loan,
    )
    await message.answer(text)


async def cmd_create_token(message: types.Message):
    """Create creator token: /createtoken <name> <symbol> <supply> <price>"""
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()

    if len(parts) < 5:
        await message.answer(i18n.t(lang, 'create_token_format'))
        return

    try:
        name = parts[1]
        symbol = parts[2].upper()
        supply = int(parts[3])
        price = float(parts[4])
        price_micro = int(price * 1e6)

        uid = message.from_user.id
        token = await common.creator_tokens.create_token(
            creator_tg_id=uid,
            name=name,
            symbol=symbol,
            total_supply=supply,
            initial_price_micro=price_micro,
        )

        await message.answer(
            i18n.t(
                lang,
                'token_created',
                name=name,
                symbol=symbol,
                token_id=token.token_id,
            )
        )
    except (ValueError, IndexError):
        await message.answer(i18n.t(lang, 'create_token_format'))


async def cmd_buy_token(message: types.Message):
    """Buy creator tokens: /buytoken <token_id> <amount>"""
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()

    if len(parts) < 3:
        await message.answer(i18n.t(lang, 'buy_token_format'))
        return

    try:
        token_id = parts[1]
        amount = int(parts[2])
        uid = message.from_user.id

        success, cost_micro = await common.creator_tokens.buy_tokens(
            token_id=token_id,
            buyer_tg_id=uid,
            amount=amount,
        )

        if not success:
            await message.answer(i18n.t(lang, 'token_buy_failed'))
            return

        cost = cost_micro / 1e6
        await common.ledger.debit(uid, cost_micro, f"Buy {amount} tokens {token_id}")

        await message.answer(
            i18n.t(lang, 'token_bought', amount=amount, cost=cost)
        )
    except (ValueError, IndexError):
        await message.answer(i18n.t(lang, 'buy_token_format'))


async def cmd_claim_dividends(message: types.Message):
    """Claim pending dividends: /claim <token_id>"""
    lang = await _lang(message.from_user.id)
    parts = message.text.strip().split()

    if len(parts) < 2:
        await message.answer(i18n.t(lang, 'claim_format'))
        return

    token_id = parts[1]
    uid = message.from_user.id

    amount_micro = await common.creator_tokens.claim_dividends(token_id, uid)
    if amount_micro == 0:
        await message.answer(i18n.t(lang, 'no_dividends'))
        return

    amount = amount_micro / 1e6
    await common.ledger.credit(uid, amount_micro, f"Dividends from {token_id}")

    await message.answer(i18n.t(lang, 'dividends_claimed', amount=amount))


def register(dp):
    """Register breakthrough feature handlers."""
    dp.message.register(cmd_gasless, Command('gasless'))
    dp.message.register(cmd_subscribe, Command('subscribe'))
    dp.message.register(cmd_subscriptions, Command('subscriptions'))
    dp.message.register(cmd_cancel_sub, Command('cancelsub'))
    dp.message.register(cmd_credit, Command('credit'))
    dp.message.register(cmd_create_token, Command('createtoken'))
    dp.message.register(cmd_buy_token, Command('buytoken'))
    dp.message.register(cmd_claim_dividends, Command('claim'))
