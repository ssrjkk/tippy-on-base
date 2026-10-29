"""Owner's admin panel: /admin.

One screen answers "what happened while I was away" — users, volume, fees, the
treasury against its own liabilities, the deposit scanner's lag, the AI chain and
its budget — and one tap per subsystem goes deeper.

Read-only by construction. Everything here is a SELECT: resolving a market,
cancelling one, refunding or broadcasting stays with the commands that already do
it with their own validation, idempotency keys and audit rows. A panel that can
move money is a panel that can be talked into moving money, and this one is
reachable from a chat window.
"""

import asyncio

from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import ai, i18n
from bot.ledger import async_ledger as ledger

from . import _common as common

SECTIONS = ("ai", "treasury", "markets", "agent")


def _owner(tg_id: int | None) -> bool:
    """One account, one answer. OWNER_ID falls back to ADMIN_TG_ID so an existing
    deployment keeps its operator; with neither set, nobody is in."""
    owner = common.config.OWNER_ID
    return owner is not None and tg_id == owner


def _usd(micro: int | float) -> str:
    return common._fmt(int(micro))


def _clip(text: object, n: int) -> str:
    """Escaped and cut to at most n characters, marked with an ellipsis — a
    question that stops mid-word reads as a bug, not as a summary. Cut first,
    escape after: slicing an escaped string can split `&amp;` in half."""
    s = str(text)
    if len(s) > n:
        s = s[: n - 1].rstrip() + "…"
    return common._h(s)


def _kb(lang: str) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.t(lang, "admin_btn_summary"), callback_data="adm:main")
    for section in SECTIONS:
        kb.button(text=i18n.t(lang, f"admin_btn_{section}"), callback_data=f"adm:{section}")
    kb.adjust(2)
    kb.row(common._back_row(lang))
    return kb.as_markup()


async def _summary(lang: str) -> str:
    s = await ledger.global_stats()
    owed = await ledger.total_liabilities()
    pending = await ledger.pending_deposit_total()
    answers = await ledger.ai_usage_today()
    lines = [
        f"<b>{i18n.t(lang, 'admin_title')}</b>",
        "",
        i18n.t(lang, "admin_users", n=s["users"]),
        i18n.t(lang, "admin_volume", v=_usd(s["volume_micro"]), v30=_usd(s["volume_30d_micro"])),
        i18n.t(lang, "admin_tips_fees", tips=_usd(s["tips_micro"]), fees=_usd(s["fees_micro"])),
        i18n.t(lang, "admin_txs", n=s["transactions"], markets=s["open_markets"]),
        i18n.t(lang, "admin_owed", v=_usd(owed), pending=_usd(pending)),
        i18n.t(lang, "admin_ai_budget", n=answers, limit=common.config.AI_DAILY_ANSWERS),
    ]
    lag = await _deposit_lag()
    if lag is not None:
        lines.append(i18n.t(lang, "admin_lag", n=lag))
    return "\n".join(lines)


async def _deposit_lag() -> int | None:
    """Blocks between the chain head and the last scanned one — the number that
    says whether deposits are arriving, without opening the RPC by hand."""
    from bot import base

    try:
        head = await asyncio.to_thread(lambda: base.w3.eth.block_number)
    except Exception:
        return None
    last = await ledger.last_block()
    return head - last if head and last is not None else None


async def _ai_screen(lang: str) -> str:
    state = await asyncio.to_thread(ai.status)
    lines = [f"<b>{i18n.t(lang, 'admin_btn_ai')}</b>", i18n.t(lang, "admin_ai_mode", mode=state["mode"]), ""]
    for p in state["providers"]:
        mark = "✅" if p["usable"] and not p["parked"] else ("⏸" if p["parked"] else "⛔")
        lines.append(
            f"{mark} <code>{common._h(p['name'])}</code> · {common._h(p['kind'])} · "
            + (
                i18n.t(lang, "admin_parked", err=_clip(p.get("last_error") or "", 60))
                if p["parked"]
                else _clip(p["model"], 40)
            )
        )
    lines += [
        "",
        i18n.t(lang, "admin_ai_budget", n=state["answers_today"], limit=state["daily_limit"]),
    ]
    return "\n".join(lines)


async def _treasury_screen(lang: str) -> str:
    from bot import base

    owed = await ledger.total_liabilities()
    pending = await ledger.pending_deposit_total()
    lines = [f"<b>{i18n.t(lang, 'admin_btn_treasury')}</b>"]
    try:
        hot = await base.hot_balance()
    except Exception:
        hot = None
    try:
        vault = await base.vault_balance()
    except Exception:
        vault = None
    if hot is not None:
        lines.append(i18n.t(lang, "admin_hot_wallet", v=f"{hot:,.2f}"))
    if vault is not None:
        lines.append(i18n.t(lang, "admin_vault", v=f"{vault:,.2f}"))
    lines.append(i18n.t(lang, "admin_owed", v=_usd(owed), pending=_usd(pending)))
    reserves = vault if vault is not None else hot
    if reserves is not None:
        diff = reserves - float(owed) / 10**6
        lines.append(
            i18n.t(lang, "admin_solvent_yes", v=f"{diff:,.2f}")
            if diff >= -0.01
            else i18n.t(lang, "admin_solvent_no", v=f"{diff:,.2f}")
        )
    top = await ledger.top_balances(5)
    if top:
        lines += ["", f"<b>{i18n.t(lang, 'admin_top_balances')}</b>"]
        for row in top:
            handle = row.get("username") or f"id{row.get('tg_id')}"
            lines.append(f"· {_clip(handle, 24)} — {_usd(int(row.get('balance_micro', 0)))}")
    wd = await ledger.pending_withdraws()
    if wd:
        lines += ["", i18n.t(lang, "admin_pending_withdraws", n=len(wd))]
    return "\n".join(lines)


async def _markets_screen(lang: str) -> str:
    markets = await ledger.open_amm_markets(10)
    bets = await ledger.bets_by_status("open", 10)
    lines = [f"<b>{i18n.t(lang, 'admin_btn_markets')}</b>"]
    if markets:
        lines.append(f"<b>{i18n.t(lang, 'admin_amm_head')}</b>")
        for m in markets:
            view = await ledger.amm_market_view(int(m["id"])) or m
            lines.append(
                f"#{m['id']} {_clip(view.get('question', ''), 44)} · "
                + i18n.t(lang, "admin_liquidity", v=_usd(int(view.get("liquidity_micro", 0))))
            )
    if bets:
        lines.append(f"<b>{i18n.t(lang, 'admin_pool_head')}</b>")
        for b in bets:
            view = await ledger.market_view(int(b["id"])) or b
            lines.append(
                f"#{b['id']} {_clip(view.get('question', ''), 44)} · "
                + i18n.t(lang, "admin_pot", v=_usd(int(view.get("pot", 0))))
            )
    overdue = await ledger.open_markets_past_deadline()
    if overdue:
        lines += ["", i18n.t(lang, "admin_overdue", n=len(overdue))]
        for m in overdue[:5]:
            lines.append(f"· #{m['id']} {_clip(m.get('question', ''), 40)} → /resolve {m['id']}")
    if not markets and not bets:
        lines.append(i18n.t(lang, "admin_nothing_open"))
    return "\n".join(lines)


async def _agent_screen(lang: str) -> str:
    from .agent import _audit_tail

    lines = [f"<b>{i18n.t(lang, 'admin_btn_agent')}</b>"]
    state = await asyncio.to_thread(_agent_state)
    if not state:
        lines.append(i18n.t(lang, "admin_agent_off"))
        return "\n".join(lines)
    lines.append(i18n.t(lang, "admin_agent_spend", v=state.get("spent_today", 0), limit=state.get("daily_max", 0)))
    lines.append(i18n.t(lang, "admin_agent_actions", n=state.get("actions_today", 0)))
    if state.get("breaker"):
        lines.append(i18n.t(lang, "admin_agent_breaker"))
    tail = _audit_tail(3)
    if tail:
        lines += ["", f"<b>{i18n.t(lang, 'admin_agent_last')}</b>"]
        for row in tail:
            lines.append(f"· {_clip(row.get('action', '?'), 40)} — {_clip(row.get('detail', ''), 60)}")
    return "\n".join(lines)


def _agent_state() -> dict | None:
    """Caps counters as the agent writes them; None when the agent has never run."""
    import json
    from pathlib import Path

    from agent.config import STATE_DIR

    path = Path(STATE_DIR) / "agent_state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def _text(screen: str, lang: str) -> str:
    if screen == "ai":
        return await _ai_screen(lang)
    if screen == "treasury":
        return await _treasury_screen(lang)
    if screen == "markets":
        return await _markets_screen(lang)
    if screen == "agent":
        return await _agent_screen(lang)
    return await _summary(lang)


async def _render(screen: str, lang: str) -> str:
    try:
        return await _text(screen, lang)
    except Exception as e:  # an operator needs the failure, not a frozen button
        return i18n.t(lang, "admin_error", err=type(e).__name__)
    finally:
        # Every screen is a batch of SELECTs on the one shared connection, and a
        # read transaction left open pins table locks until some later write
        # commits — an aborted one poisons the connection outright. The web
        # layer rolls back after every request for the same reason; a rollback
        # after a healthy commit is a no-op.
        try:
            await ledger.rollback()
        except Exception:
            pass


@common.router.message(Command("admin"))
async def cmd_admin(message: types.Message) -> None:
    lang = await common.user_lang(message.from_user.id)
    if not _owner(message.from_user.id):
        # One sentence, no numbers: a refusal that leaks "3 users, 17 USDC" is a
        # status endpoint for whoever guesses the command.
        await message.answer(i18n.t(lang, "admin_denied"))
        return
    # The owner may type /admin anywhere; the treasury, the liabilities and the
    # top balances must not land in a group's history because of it.
    if not await common.require_private(message):
        return
    await message.answer(await _render("main", lang), reply_markup=_kb(lang))


@common.router.callback_query(lambda cb: cb.data and cb.data.startswith("adm:"))
async def cb_admin(cb: types.CallbackQuery) -> None:
    lang = await common.user_lang(cb.from_user.id)
    if not _owner(cb.from_user.id):
        await cb.answer(i18n.t(lang, "admin_denied"), show_alert=True)
        return
    chat = cb.message.chat if isinstance(cb.message, types.Message) else None
    if chat is None or chat.type != "private":
        await cb.answer(i18n.t(lang, "private_chat_only"), show_alert=True)
        return
    screen = (cb.data or "").split(":", 1)[1]
    if screen not in ("main", *SECTIONS):
        await cb.answer()
        return
    await cb.message.edit_text(await _render(screen, lang), reply_markup=_kb(lang))
    await cb.answer()
