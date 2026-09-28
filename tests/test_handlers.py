"""Handler-level tests: commands, inline bet flow, reactions.

Commands are invoked directly (not through the aiogram router) with mocked
message/bot objects; signatures use real local crypto (eth_account).
"""

import asyncio
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.filters import CommandObject
from aiogram.types import ReactionTypeEmoji
from eth_account import Account
from eth_account.messages import encode_defunct

from bot import handlers
from bot.handlers import (
    _bet_card,
    _edit_menu,
    _history_text,
    _index_message,
    _notify_bet_cancelled,
    _notify_bet_result,
    _notify_tip_received,
    _parse_deadline,
    _to_micro,
    cb_bet_amount,
    cb_bet_place,
    cb_market,
    cb_menu,
    cb_res,
    cb_settings,
    cb_withdraw_confirm,
    cmd_balance,
    cmd_bet,
    cmd_bets,
    cmd_broadcast,
    cmd_cancel,
    cmd_claim,
    cmd_confirm,
    cmd_deposit,
    cmd_donate,
    cmd_export,
    cmd_history,
    cmd_import,
    cmd_link,
    cmd_menu,
    cmd_mybets,
    cmd_paywall,
    cmd_rain,
    cmd_resolve,
    cmd_settings,
    cmd_start,
    cmd_stats,
    cmd_tip,
    cmd_top,
    cmd_wallet,
    cmd_withdraw,
    on_menu,
    on_reaction,
)

ALICE, BOB = 2001, 2002
ACC = Account.from_key("0x" + "22" * 32)
ACC2 = Account.from_key("0x" + "33" * 32)


# ---------- mocks ----------


class User:
    def __init__(self, id, username=None, is_bot=False):
        self.id = id
        self.username = username
        self.is_bot = is_bot


class Chat:
    def __init__(self, id=-1000, members=(), type="private"):
        self.id = id
        self.members = list(members)
        self.type = type

    async def get_members(self, limit=200):
        for m in self.members[:limit]:
            yield m


class Bot:
    def __init__(self):
        self.sent = []
        self.username = "base_tipbot"

    async def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))

    async def get_me(self):
        return SimpleNamespace(username=self.username)


class Message:
    def __init__(self, text="", from_id=ALICE, username="alice", bot=None, chat=None, reply_to=None, message_id=1):
        self.text = text
        self.from_user = User(from_id, username)
        self.bot = bot or Bot()
        self.chat = chat or Chat()
        self.reply_to_message = reply_to
        self.message_id = message_id
        self.answers = []
        self.photos = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))

    async def answer_photo(self, photo, caption=None, reply_markup=None, **kw):
        self.photos.append((photo, caption, reply_markup))


class ChannelBot(Bot):
    """Bot mock with Telegram channel APIs for /paywall channel tests."""

    def __init__(self, bot_id=123456, members=None, title="Paid Channel"):
        super().__init__()
        self.id = bot_id
        self.title = title
        self.members = members or {}  # tg_id -> status
        self.invites = []
        self.chat_calls = []  # chat_ids get_chat was asked about

    async def get_chat_member(self, chat_id, user_id):
        status = self.members.get(user_id, "left")
        return SimpleNamespace(status=status)

    async def get_chat(self, chat_id):
        self.chat_calls.append(chat_id)
        if isinstance(chat_id, int):
            cid = chat_id
        else:
            s = str(chat_id)
            cid = int(s) if s.lstrip("-").isdigit() else CHANNEL_ID
        return SimpleNamespace(id=cid, title=self.title, username="paid_channel")

    async def create_chat_invite_link(self, chat_id, member_limit=None, expire_date=None):
        link = f"https://t.me/+INVITE{len(self.invites)}"
        self.invites.append((chat_id, member_limit, expire_date))
        return SimpleNamespace(invite_link=link)


class AnswerRecorder:
    def __init__(self, bot=None):
        self.text = None
        self.markup = None
        self.caption = None
        self.bot = bot or Bot()

    async def edit_text(self, text, reply_markup=None, **kw):
        self.text = text
        self.markup = reply_markup

    async def edit_caption(self, caption=None, reply_markup=None, **kw):
        self.caption = caption
        self.markup = reply_markup


class Callback:
    def __init__(self, data, from_id, bot=None):
        self.data = data
        self.from_user = User(from_id)
        self.message = AnswerRecorder(bot)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def run(coro):
    return asyncio.run(coro)


def statements_used(ledger, coro_factory):
    """(result, the SQL statements that run issued).

    A list renderer that reads one row per item and one that reads them in bulk
    answer with byte-identical text, so the count is the only thing that can
    tell them apart. The spy is installed and removed by hand instead of through
    monkeypatch: monkeypatch.undo() would also tear down the `ledger` fixture's
    rebinding of every module-level singleton, and the handler would then run
    against whatever DATABASE_URL points at — a developer's live database, not
    tipbot_test.
    """
    seen = []
    real = ledger._conn.execute

    def spy(query, *args, **kwargs):
        seen.append(query)
        return real(query, *args, **kwargs)

    ledger._conn.execute = spy
    try:
        out = run(coro_factory())
    finally:
        del ledger._conn.execute
    return out, seen


# ---------- parsing ----------


@pytest.mark.parametrize("s,days", [("24h", 1), ("7d", 7), ("1h", 0), ("48h", 2), ("120h", 5)])
def test_parse_deadline(s, days):
    assert _parse_deadline(s) >= days * 86400


def test_parse_deadline_invalid():
    assert _parse_deadline("abc") is None
    assert _parse_deadline("24") is None
    assert _parse_deadline("1000h") is None


def test_to_micro_rounding_ceiling():
    assert _to_micro(Decimal("1.000001")) == 1_000_001
    assert _to_micro(Decimal("1.000000")) == 1_000_000
    assert _to_micro(Decimal("0.000001")) == 1


# ---------- start / menu ----------


def test_cmd_start_help(ledger):
    m = Message("/start")
    run(cmd_start(m, CommandObject(command="start", args=None)))
    assert m.answers and "Tippy" in m.answers[0][0]
    assert ledger.user_exists(ALICE)


def test_cmd_start_donate_landing(ledger, monkeypatch):
    ledger.ensure_user(BOB, "bob")

    async def fake_qr(data):
        return b"\x89PNG"

    monkeypatch.setattr(handlers._common, "_qr_bytes", fake_qr)
    m = Message("/start", from_id=ALICE, username="alice")
    run(cmd_start(m, CommandObject(command="start", args="donate_2002")))
    assert m.photos, "expected a photo with QR"
    caption = m.photos[0][1]
    assert "bob" in caption


def test_cmd_start_non_donate_args(ledger):
    m = Message("/start", from_id=ALICE)
    run(cmd_start(m, CommandObject(command="start", args="garbage")))
    assert "Tippy" in m.answers[0][0]


def test_cmd_start_market_deep_link(ledger):
    bid = ledger.create_bet(ALICE, "Кто победит?", ["А", "Б"])
    ledger.credit(ALICE, 1_000_000, "deposit")
    ledger.place_bet(ALICE, bid, 0, 100_000)
    m = Message("/start", from_id=BOB, username="bob")
    run(cmd_start(m, CommandObject(command="start", args=f"bet_{bid}")))
    assert any("Кто победит?" in a[0] for a in m.answers)
    kb = m.answers[0][1]
    assert kb is not None
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert f"betq:{bid}:0" in data


def test_cmd_start_market_deep_link_unknown(ledger):
    m = Message("/start", from_id=BOB, username="bob")
    run(cmd_start(m, CommandObject(command="start", args="bet_999999")))
    assert "не найден" in m.answers[0][0]


def test_cmd_start_paywall_deep_link_and_buy(ledger):
    ledger.ensure_user(BOB, "bob")
    ledger.credit(ALICE, 10_000_000, "deposit")
    item_id = ledger.create_paywall(BOB, "Альфа-сигнал", 1_000_000, "секретный контент")
    m = Message("/start", from_id=ALICE, username="alice")
    run(cmd_start(m, CommandObject(command="start", args=f"paywall_{item_id}")))
    text, kb = m.answers[0]
    assert "Альфа-сигнал" in text
    assert "1 USDC" in text
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert f"paywall_buy:{item_id}" in data
    # one-tap purchase via the callback button
    cb = Callback(f"paywall_buy:{item_id}", ALICE)
    run(handlers.cb_paywall_buy(cb))
    assert "секретный контент" in cb.message.text
    assert ledger.user_view(ALICE)["balance_micro"] == 9_000_000
    # already bought -> content shown again
    cb2 = Callback(f"paywall_buy:{item_id}", ALICE)
    run(handlers.cb_paywall_buy(cb2))
    assert "Уже куплено" in cb2.message.text
    # unknown item
    cb3 = Callback("paywall_buy:999", ALICE)
    run(handlers.cb_paywall_buy(cb3))
    assert "не найден" in cb3.message.text


def test_cmd_start_greeting_shows_balance(ledger):
    ledger.credit(ALICE, 5_000_000, "deposit")
    m = Message("/start", from_id=ALICE, username="alice")
    run(cmd_start(m, CommandObject(command="start", args=None)))
    assert "Привет" in m.answers[0][0]
    assert "5" in m.answers[0][0]


def test_cmd_menu_shows_keyboard(ledger):
    ledger.credit(ALICE, 5_000_000, "deposit")
    m = Message("/menu", from_id=ALICE)
    run(cmd_menu(m))
    assert "Баланс" in m.answers[0][0]
    assert m.answers[0][1] is not None  # menu keyboard attached


def test_menu_callbacks(ledger):
    for data in ("bal", "dep", "top", "hist", "bets", "donate", "stats"):
        cb = Callback(data, ALICE)
        run(handlers.on_menu(cb))
        assert cb.message.text is not None or cb.message.caption is not None


# ---------- wallet commands ----------


def test_cmd_balance(ledger):
    ledger.credit(ALICE, 5_000_000, "deposit")
    m = Message("/balance")
    run(cmd_balance(m))
    assert "5" in m.answers[0][0] and "USDC" in m.answers[0][0]


def test_cmd_deposit(ledger, monkeypatch):
    monkeypatch.setattr(handlers.base, "hot_wallet", lambda: "0x" + "ab" * 20)
    m = Message("/deposit")
    run(cmd_deposit(m))
    assert m.photos and "0x" in m.photos[0][1]


def test_cmd_donate_shows_link(ledger):
    m = Message("/donate")
    run(cmd_donate(m))
    assert f"donate_{ALICE}" in m.answers[0][0]


def test_cmd_claim_bad_format(ledger):
    m = Message("/claim notahash")
    run(cmd_claim(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_claim_ok(ledger, monkeypatch):
    async def _cutoff():
        return 999_999_999

    monkeypatch.setattr(handlers.base, "deposit_cutoff", _cutoff)
    ledger.record_pending("0x" + "1" * 64, ACC.address, 5_000_000)
    nonce = ledger.new_link_nonce(ALICE, ACC.address)
    ledger.confirm_link(ALICE, ACC.address, nonce)
    m = Message("/claim 0x" + "1" * 64)
    run(cmd_claim(m))
    assert "Депозит зачислен" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("5.000000")


def test_cmd_claim_other_users_deposit_rejected(ledger, monkeypatch):
    """BOB cannot claim ALICE's deposit even knowing the tx hash."""
    async def _cutoff():
        return 999_999_999

    monkeypatch.setattr(handlers.base, "deposit_cutoff", _cutoff)
    ledger.record_pending("0x" + "1" * 64, ACC.address, 5_000_000)
    nonce = ledger.new_link_nonce(ALICE, ACC.address)
    ledger.confirm_link(ALICE, ACC.address, nonce)
    m = Message("/claim 0x" + "1" * 64, from_id=BOB, username="bob")
    run(cmd_claim(m))
    assert "Привяжи" in m.answers[0][0]
    assert ledger.balance(BOB) == Decimal("0")
    assert ledger.balance(ALICE) == Decimal("0")


def test_cmd_claim_unknown(ledger, monkeypatch):
    async def _cutoff():
        return 999_999_999

    monkeypatch.setattr(handlers.base, "deposit_cutoff", _cutoff)
    m = Message("/claim 0x" + "2" * 64)
    run(cmd_claim(m))
    assert "Транзакция не найдена" in m.answers[0][0]


def test_cmd_claim_not_mature(ledger, monkeypatch):
    """A still-immature pending deposit must NOT be credited via /claim."""
    async def _cutoff():
        return 10_000

    monkeypatch.setattr(handlers.base, "deposit_cutoff", _cutoff)
    ledger.record_pending("0x" + "1" * 64, ACC.address, 5_000_000, block=2_000_000)
    nonce = ledger.new_link_nonce(ALICE, ACC.address)
    ledger.confirm_link(ALICE, ACC.address, nonce)
    m = Message("/claim 0x" + "1" * 64)
    run(cmd_claim(m))
    assert ledger.balance(ALICE) == Decimal("0")
    assert not m.answers[0][0].startswith("Депозит зачислен")


def _sign_text_from_answer(text):
    import re

    m = re.search(r"<code>(Tippy: link \d+:[0-9a-f]+)</code>", text)
    assert m, f"sign text not found in: {text!r}"
    return m.group(1)


def test_cmd_link_and_confirm_real_signature(ledger):
    m = Message("/link " + ACC.address)
    run(cmd_link(m))
    sign_msg = _sign_text_from_answer(m.answers[0][0])
    sig = "0x" + ACC.sign_message(encode_defunct(text=sign_msg)).signature.hex()
    assert sig.startswith("0x")

    m2 = Message("/confirm " + sig)
    run(cmd_confirm(m2))
    assert "привязан" in m2.answers[0][0]
    assert ledger.linked_address(ALICE).lower() == ACC.address.lower()


def test_cmd_confirm_wrong_signature(ledger):
    m = Message("/link " + ACC.address)
    run(cmd_link(m))
    sign_msg = _sign_text_from_answer(m.answers[0][0])
    sig = "0x" + ACC2.sign_message(encode_defunct(text=sign_msg)).signature.hex()  # wrong key
    m2 = Message("/confirm " + sig)
    run(cmd_confirm(m2))
    assert "не совпадает" in m2.answers[0][0]
    assert ledger.linked_address(ALICE) is None


def test_cmd_link_bad_format(ledger):
    m = Message("/link nope")
    run(cmd_link(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_confirm_no_pending(ledger):
    m = Message("/confirm 0x" + "f" * 130)
    run(cmd_confirm(m))
    assert "Сначала начни привязку" in m.answers[0][0]


def test_cmd_confirm_bad_signature_format(ledger):
    m = Message("/confirm garbage")
    run(cmd_confirm(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_confirm_stale_nonce(ledger):
    m = Message("/link " + ACC.address)
    run(cmd_link(m))
    sign_msg = _sign_text_from_answer(m.answers[0][0])
    sig = "0x" + ACC.sign_message(encode_defunct(text=sign_msg)).signature.hex()
    from bot import config

    ledger._conn.execute(
        "UPDATE link_nonces SET created_at = %s WHERE tg_id = %s",
        (int(time.time()) - config.LINK_NONCE_TTL_SECONDS - 60, ALICE),
    )
    ledger._conn.commit()
    m2 = Message("/confirm " + sig)
    run(cmd_confirm(m2))
    assert "устарел" in m2.answers[0][0]
    assert ledger.linked_address(ALICE) is None


# ---------- tips ----------


def test_cmd_tip_reply(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    reply = Message("/tip", from_id=BOB, username="bob")
    m = Message("/tip 5", from_id=ALICE, username="alice", reply_to=reply)
    run(cmd_tip(m))
    assert "5 USDC" in m.answers[0][0] or "5.000000" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("5.000000")
    assert ledger.balance(BOB) == Decimal("5.000000")
    assert any(chat_id == BOB for chat_id, _ in m.bot.sent)


def test_cmd_tip_by_username(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    m = Message("/tip 2 @Bob", from_id=ALICE, username="alice")
    run(cmd_tip(m))
    assert ledger.balance(BOB) == Decimal("2.000000")


def test_cmd_tip_default_amount(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    m = Message("/tip", from_id=ALICE, username="alice")
    run(cmd_tip(m))
    assert "Формат" in m.answers[0][0] or "Кому кидаем" in m.answers[0][0]


def test_cmd_tip_self_rejected(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message("/tip 1", from_id=ALICE, username="alice", reply_to=Message("/hi", from_id=ALICE))
    run(cmd_tip(m))
    assert "Себе" in m.answers[0][0]


def test_cmd_tip_insufficient(ledger):
    ledger.ensure_user(BOB, "bob")
    m = Message("/tip 5 @bob", from_id=ALICE, username="alice")
    run(cmd_tip(m))
    assert "Недостаточно" in m.answers[0][0]


def test_cmd_tip_unknown_user_falls_back_to_chat(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bob_member = SimpleNamespace(user=User(BOB, "bob"))
    m = Message("/tip 1 @bob", from_id=ALICE, username="alice", chat=Chat(members=[bob_member]))
    run(cmd_tip(m))
    assert ledger.balance(BOB) == Decimal("1.000000")


def test_cmd_tip_zero_rejected(ledger):
    m = Message("/tip 0 @bob", from_id=ALICE)
    run(cmd_tip(m))
    assert "больше нуля" in m.answers[0][0]


def test_cmd_tip_over_max(ledger):
    ledger.credit(ALICE, 10_000_000_000, "deposit")
    from bot import config

    m = Message(f"/tip {int(config.MAX_TIP_USDC) + 1} @bob", from_id=ALICE)
    run(cmd_tip(m))
    assert "Максимум" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("10000.000000")


def test_cmd_tip_throttled(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    m1 = Message("/tip 1 @bob", from_id=ALICE)
    run(cmd_tip(m1))
    assert "USDC" in m1.answers[0][0]
    m2 = Message("/tip 1 @bob", from_id=ALICE)
    run(cmd_tip(m2))
    assert "Слишком часто" in m2.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("9.000000")


# ---------- withdraw ----------


def _wd_token(m):
    """Pull the one-shot confirmation token out of a /withdraw prompt."""
    confirm, cancel = m.answers[0][1].inline_keyboard[0]
    assert confirm.callback_data.startswith("wd:y:")
    assert cancel.callback_data.startswith("wd:n:")
    return confirm.callback_data.split(":", 2)[2]


def _wd_tap(token, verdict="y", from_id=ALICE):
    cb = Callback(f"wd:{verdict}:{token}", from_id)
    run(cb_withdraw_confirm(cb))
    return cb


def test_cmd_withdraw_stages_before_debiting(ledger):
    """/withdraw only asks; the money moves on the confirm tap."""
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    token = _wd_token(m)
    assert "5.05" in m.answers[0][0]  # the exact total is what gets confirmed
    assert ledger.balance(ALICE) == Decimal("10.000000")
    assert ledger.withdraw_queue() == []

    cb = _wd_tap(token)
    # Batching: the withdrawal is QUEUED (not instantly sent), flushed later.
    assert "очередь" in cb.message.text
    # 5 USDC enqueued + 1% fee (50_000 micro) debited.
    assert ledger.balance(ALICE) == Decimal("4.950000")
    q = ledger.withdraw_queue()
    assert len(q) == 1
    assert q[0]["status"] == "queued"
    assert q[0]["counterparty"].lower() == ACC.address.lower()
    assert q[0]["amount"] == 5_000_000
    assert ledger.pending_withdraws() == []  # not yet broadcast


def test_cmd_withdraw_cancel_tap_moves_nothing(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    cb = _wd_tap(_wd_token(m), verdict="n")
    assert "отмен" in cb.message.text
    assert ledger.balance(ALICE) == Decimal("10.000000")
    assert ledger.withdraw_queue() == []


def test_cmd_withdraw_confirm_is_single_use(ledger):
    """A double-click must not reserve the payout twice."""
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    token = _wd_token(m)
    _wd_tap(token)
    second = _wd_tap(token)
    assert "устарел" in second.message.text
    assert ledger.balance(ALICE) == Decimal("4.950000")
    assert len(ledger.withdraw_queue()) == 1


def test_cmd_withdraw_stale_prompt_cannot_be_confirmed(ledger, monkeypatch):
    """Staging a newer request retires the older prompt's buttons."""
    monkeypatch.setattr(handlers.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    ledger.credit(ALICE, 20_000_000, "deposit")
    m1 = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m1))
    old = _wd_token(m1)
    m2 = Message(f"/withdraw {ACC.address} 7", from_id=ALICE)
    run(cmd_withdraw(m2))
    assert _wd_token(m2) != old
    cb = _wd_tap(old)
    assert "устарел" in cb.message.text
    assert ledger.balance(ALICE) == Decimal("20.000000")
    assert ledger.withdraw_queue() == []


def test_cmd_withdraw_queued_row_is_not_refunded(ledger):
    """A confirmed withdrawal leaves exactly one queued row.

    The handler never sends to the chain, so it can never refund either: the
    batch flush (watcher) owns success and refund, which is what keeps a crash
    between enqueue and flush from silently losing the user's debited USDC.
    """
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    _wd_tap(_wd_token(m))
    rows = ledger._conn.execute(
        "SELECT kind, status FROM tx_log WHERE tg_id = %s AND kind = 'withdraw' ORDER BY id",
        (ALICE,),
    ).fetchall()
    assert [(r["kind"], r["status"]) for r in rows] == [("withdraw", "queued")]
    assert ledger.balance(ALICE) == Decimal("4.950000")


def test_cmd_withdraw_bad_format(ledger):
    m = Message("/withdraw nope 5", from_id=ALICE)
    run(cmd_withdraw(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_withdraw_insufficient(ledger):
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    assert "Недостаточно" in m.answers[0][0]
    assert m.answers[0][1] is None  # a refusal never offers a confirm button


def test_cmd_withdraw_below_min(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    from bot import config

    m = Message(f"/withdraw {ACC.address} {config.MIN_WITHDRAW_USDC / 2}", from_id=ALICE)
    run(cmd_withdraw(m))
    assert "Минимум" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("10.000000")


def test_cmd_withdraw_refuses_first_party_addresses(ledger, monkeypatch):
    """Each refused destination gets its own reason.

    The handler used to keep an allow-list that contradicted the ledger's
    block-list, so a funded user who typed the bot's own address was told they
    had no balance.
    """
    from bot.base import hot_wallet

    monkeypatch.setattr(handlers.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(handlers.config, "VAULT_ADDRESS", "0x" + "a" * 40)
    monkeypatch.setattr(handlers.config, "X402_RECEIVE_ADDRESS", "0x" + "c" * 40)
    ledger.credit(ALICE, 10_000_000, "deposit")
    for addr, reason in (
        ("0x" + "0" * 40, "Нулевой адрес"),
        (str(hot_wallet()).lower(), "самого бота"),
        ("0x" + "a" * 40, "хранилище бота"),
        ("0x" + "c" * 40, "приёмный пул x402"),
    ):
        m = Message(f"/withdraw {addr} 5", from_id=ALICE)
        run(cmd_withdraw(m))
        assert reason in m.answers[0][0], addr
        assert "Недостаточно" not in m.answers[0][0], addr
    assert ledger.balance(ALICE) == Decimal("10.000000")
    assert ledger.withdraw_queue() == []


def test_cmd_withdraw_blocks_contract_destination(ledger, monkeypatch):
    """A withdrawal to a smart contract (e.g. the USDC token, a router) must be
    refused up front — funds sent to a contract can be burned permanently."""
    ledger.credit(ALICE, 10_000_000, "deposit")
    from bot.chain import network

    contract_addr = "0x" + "c0" * 20  # arbitrary contract-looking address

    async def _contract(addr):
        return addr.lower() == contract_addr.lower()

    monkeypatch.setattr(handlers.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(network, "is_contract", _contract)

    # EOA address (is_contract == False) -> withdrawal is staged for confirm.
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    assert "Подтверди" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("10.000000")

    # Contract address (is_contract == True) -> refused up front, no stage.
    m2 = Message(f"/withdraw {contract_addr} 1", from_id=ALICE)
    run(cmd_withdraw(m2))
    assert "заблокирован" in m2.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("10.000000")


def test_cmd_withdraw_throttled(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    m1 = Message(f"/withdraw {ACC.address} 1", from_id=ALICE)
    run(cmd_withdraw(m1))
    assert "Подтверди" in m1.answers[0][0]
    m2 = Message(f"/withdraw {ACC.address} 1", from_id=ALICE)
    run(cmd_withdraw(m2))
    assert "Слишком часто" in m2.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("10.000000")  # nothing debited yet


def test_cmd_withdraw_daily_limit(ledger, monkeypatch):
    from bot import config

    monkeypatch.setattr(handlers.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    ledger.credit(ALICE, 100_000_000, "deposit")

    for _ in range(config.MAX_WITHDRAWS_PER_DAY):
        m = Message(f"/withdraw {ACC.address} 1", from_id=ALICE)
        run(cmd_withdraw(m))
        _wd_tap(_wd_token(m))
    assert len(ledger.withdraw_queue()) == config.MAX_WITHDRAWS_PER_DAY
    m = Message(f"/withdraw {ACC.address} 1", from_id=ALICE)
    run(cmd_withdraw(m))
    assert "Лимит" in m.answers[0][0]


def test_withdraw_refusal_names_each_reason(ledger):
    """A refused payout never claims the user is broke unless that is why."""
    from bot.handlers.wallet import _withdraw_refusal

    ledger.credit(ALICE, 10_000_000, "deposit")
    assert "Лимит" in run(_withdraw_refusal(ALICE, "ru", "cap", 5_000_000, 50_000))
    text = run(_withdraw_refusal(ALICE, "ru", "blocked", 5_000_000, 50_000))
    assert "служебный" in text and "Недостаточно" not in text
    assert "Недостаточно" in run(
        _withdraw_refusal(ALICE, "ru", "balance", 5_000_000, 50_000)
    )
    assert ledger.balance(ALICE) == Decimal("10.000000")


def test_cmd_withdraw_balance_gone_by_confirm_tap(ledger):
    """Staging holds no funds — if the balance moved on, the confirm tap says
    'not enough' rather than queueing a payout that cannot be paid."""
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message(f"/withdraw {ACC.address} 5", from_id=ALICE)
    run(cmd_withdraw(m))
    assert ledger.debit(ALICE, 6_000_000)  # spent elsewhere while prompting
    cb = _wd_tap(_wd_token(m))
    assert "Недостаточно" in cb.message.text
    assert ledger.balance(ALICE) == Decimal("4.000000")
    assert ledger.withdraw_queue() == []


# ---------- bets ----------


def test_cmd_bet_create_with_deadline(ledger):
    m = Message("/bet create Кто победит? | А | Б 24h", from_id=ALICE)
    run(cmd_bet(m))
    bid = ledger.open_bets(1)[0]
    assert bid["close_at"] is not None
    assert "создана" in m.answers[0][0]


def test_cmd_bet_create_too_many_options(ledger):
    m = Message("/bet create Вопрос? | 1 | 2 | 3 | 4 | 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "Максимум 4" in m.answers[0][0]


def test_cmd_bet_create_format_error(ledger):
    m = Message("/bet create Маловато вариантов", from_id=ALICE)
    run(cmd_bet(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_bet_place(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    m = Message(f"/bet {bid} 1 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "Ставка принята" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("5.000000")


def test_cmd_bet_place_bad_option(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    m = Message(f"/bet {bid} 9 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "Неверный номер" in m.answers[0][0]


def test_cmd_bet_place_over_max(ledger):
    from bot import config

    ledger.credit(ALICE, 1_000_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    m = Message(f"/bet {bid} 1 {int(config.MAX_BET_USDC) + 1}", from_id=ALICE)
    run(cmd_bet(m))
    assert "Максимум" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("1000.000000")


def test_cmd_bet_create_long_option(ledger):
    from bot import config

    long_opt = "в" * (config.MAX_OPTION_LEN + 1)
    m = Message(f"/bet create Вопрос? | А | {long_opt}", from_id=ALICE)
    run(cmd_bet(m))
    assert "Вариант длиннее" in m.answers[0][0]
    assert ledger.open_bets(1) == []


def test_cmd_bet_place_throttled(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    m1 = Message(f"/bet {bid} 1 1", from_id=ALICE)
    run(cmd_bet(m1))
    assert "принята" in m1.answers[0][0]
    m2 = Message(f"/bet {bid} 1 1", from_id=ALICE)
    run(cmd_bet(m2))
    assert "Слишком часто" in m2.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("9.000000")


def test_cb_bet_place_throttled(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    cb1 = Callback(f"bets:{bid}:0:5", ALICE)
    run(cb_bet_place(cb1))
    assert cb1.message.text and "принята" in cb1.message.text
    cb2 = Callback(f"bets:{bid}:0:5", ALICE)
    run(cb_bet_place(cb2))
    assert cb2.answers and "Слишком часто" in cb2.answers[0][0]


def test_cmd_resolve(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 1, 2_000_000)
    m = Message(f"/resolve {bid} 2", from_id=ALICE)
    run(cmd_resolve(m))
    assert "закрыта" in m.answers[0][0]
    assert ledger.market_view(bid)["status"] == "resolved"
    assert any(chat_id == BOB for chat_id, _ in m.bot.sent)  # bettor notified


def test_cmd_resolve_not_creator(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    m = Message(f"/resolve {bid} 1", from_id=BOB)
    run(cmd_resolve(m))
    assert "только" in m.answers[0][0]


def test_cmd_resolve_bad_format(ledger):
    m = Message("/resolve 1", from_id=ALICE)
    run(cmd_resolve(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_cancel_notifies(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    m = Message(f"/cancel {bid}", from_id=ALICE)
    run(cmd_cancel(m))
    assert "возвращены" in m.answers[0][0]
    assert any(chat_id == BOB for chat_id, _ in m.bot.sent)


def test_cmd_cancel_bad_format(ledger):
    m = Message("/cancel", from_id=ALICE)
    run(cmd_cancel(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_mybets(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 3_000_000)
    m = Message("/mybets", from_id=ALICE)
    run(cmd_mybets(m))
    assert "3 USDC" in m.answers[0][0] or "3.000000" in m.answers[0][0]


def test_cmd_mybets_empty(ledger):
    m = Message("/mybets", from_id=ALICE)
    run(cmd_mybets(m))
    assert "нет открытых позиций" in m.answers[0][0]


# ---------- stats / top / history ----------


def test_cmd_stats(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message("/stats", from_id=ALICE)
    run(cmd_stats(m))
    assert m.answers[0][0]


def test_cmd_top(ledger):
    ledger.ensure_user(ALICE, "alice")
    ledger.ensure_user(BOB, "bob")
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.transfer(ALICE, BOB, 2_000_000)
    m = Message("/top", from_id=ALICE)
    run(cmd_top(m))
    assert "alice" in m.answers[0][0]


def test_cmd_top_reads_names_once_whatever_the_leaderboard_size(ledger):
    """/top called username_of() for every row, so ten names cost ten statements on
    top of the ranking query — each one waiting on the ledger lock.

    Every tipper here has a username on purpose: a nameless one makes the handler
    fall back to tip_targets.display_name_for(), whose ENSIP reverse lookup costs
    queries of its own, and the count would then measure the fixture's network
    state rather than the row count."""

    def top_statements():
        return statements_used(ledger, lambda: cmd_top(Message("/top", from_id=ALICE)))[1]

    def tipper(tg_id, username, amount):
        ledger.ensure_user(tg_id, username)
        ledger.credit(tg_id, 10_000_000, "deposit")
        ledger.transfer(tg_id, ALICE, amount)

    tipper(ALICE, "alice", 3_000_000)
    tipper(BOB, "bob", 2_000_000)
    first = top_statements()
    for i in range(4):
        tipper(3001 + i, f"tipper{i}", 1_000_000 - i)  # distinct totals, all inside top-10
    second = top_statements()
    assert len(second) == len(first), (
        f"/top cost {len(second) - len(first)} more statements for 4 extra tippers"
    )
    assert any("IN (" in q for q in second), "the leaderboard reads usernames one by one"

    m = Message("/top", from_id=ALICE)
    run(cmd_top(m))
    text = m.answers[0][0]
    for name in ("alice", "bob", "tipper0", "tipper1", "tipper2", "tipper3"):
        assert f"@{name}" in text
    assert text.count("USDC") == 6


def test_cmd_history(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    m = Message("/history", from_id=ALICE)
    run(cmd_history(m))
    assert "операции" in m.answers[0][0]


def test_cmd_history_reads_tipper_names_once_whatever_the_row_count(ledger):
    """Each tip line used to resolve its @username with a statement of its own.
    The plain page and the `/history tip` filtered page have separate render
    loops, so both are measured: fixing only one leaves the other paying per row."""

    def history_statements(text):
        return statements_used(
            ledger, lambda: cmd_history(Message(text, from_id=ALICE))
        )[1]

    ledger.credit(ALICE, 50_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    ledger.transfer(ALICE, BOB, 1_000_000)
    plain = history_statements("/history")
    filtered = history_statements("/history tip")

    anonymous = 3005  # tipped, but never given a username
    for i, tg in enumerate((3001, 3002, 3003, anonymous)):
        if tg != anonymous:
            ledger.ensure_user(tg, f"payer{i}")
        ledger.transfer(ALICE, tg, 500_000 + i)
    plain2 = history_statements("/history")
    filtered2 = history_statements("/history tip")

    assert len(plain2) == len(plain), (
        f"/history cost {len(plain2) - len(plain)} more statements for 4 extra tips"
    )
    assert len(filtered2) == len(filtered), (
        f"/history tip cost {len(filtered2) - len(filtered)} more statements for 4 extra tips"
    )
    assert any("IN (" in q for q in plain2), "the page resolves names row by row again"
    assert any("IN (" in q for q in filtered2), "the filtered page resolves names row by row"

    m = Message("/history", from_id=ALICE)
    run(cmd_history(m))
    text = m.answers[0][0]
    assert "@bob" in text
    assert "@payer0" in text
    assert str(anonymous) in text  # no username -> the bare id, as before
    assert text.count("→") == 5  # one line per tip, none lost or duplicated


# ---------- inline bet flow ----------


def test_market_callback_shows_options(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Кто победит?", ["Алиса", "Боб"])
    cb = Callback(f"market:{bid}", ALICE)
    run(cb_market(cb))
    assert "Алиса" in cb.message.text
    rows = cb.message.markup.inline_keyboard
    assert len(rows) == 4  # options + creator's "close" + back to list
    assert rows[0][0].callback_data == f"betq:{bid}:0"


def test_market_callback_creator_gets_close_button(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    cb = Callback(f"market:{bid}", ALICE)
    run(cb_market(cb))
    rows = cb.message.markup.inline_keyboard
    assert rows[2][0].callback_data == f"res:{bid}"


def test_market_callback_backer_gets_no_close_button(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    ledger.credit(BOB, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    cb = Callback(f"market:{bid}", BOB)
    run(cb_market(cb))
    rows = cb.message.markup.inline_keyboard
    assert len(rows) == 3
    assert not any(b.callback_data.startswith("res:") for row in rows for b in row)


def test_bet_amount_callback(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    cb = Callback(f"betq:{bid}:1", ALICE)
    run(cb_bet_amount(cb))
    rows = cb.message.markup.inline_keyboard
    assert [b.callback_data for b in rows[0]] == [f"bets:{bid}:1:{a}" for a in ("5", "10", "25", "50")]
    assert len(rows) == 2
    assert rows[1][0].callback_data == f"market:{bid}"


def test_bet_place_callback_full_flow(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    ledger.credit(BOB, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Сыграем?", ["Да", "Нет"])
    cb = Callback(f"bets:{bid}:0:10", BOB)
    run(cb_bet_place(cb))
    assert "Ставка принята" in cb.message.text
    assert ledger.balance(BOB) == Decimal("90.000000")

    cb2 = Callback(f"bets:{bid}:1:95", BOB)
    run(cb_bet_place(cb2))
    assert cb2.answers and cb2.answers[0][1] is True  # alert shown
    assert ledger.balance(BOB) == Decimal("90.000000")


def test_bet_place_after_deadline_alert(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Скоро закроем?", ["Да", "Нет"], close_at=0)
    cb = Callback(f"bets:{bid}:0:5", ALICE)
    run(cb_bet_place(cb))
    assert cb.answers and "истекло" in cb.answers[0][0]


def test_cb_market_unknown(ledger):
    cb = Callback("market:99999", ALICE)
    run(cb_market(cb))
    assert cb.answers and cb.answers[0][1] is True  # alert


# ---------- reactions ----------


def _reaction_update(chat_id, message_id, reactor, emojis, bot):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        message_id=message_id,
        user=reactor,
        new_reaction=[ReactionTypeEmoji(emoji=e) for e in emojis],
        bot=bot,
    )


def test_reaction_tip_end_to_end(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    bot = Bot()
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🔥"], bot)
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("1.000000")  # 🔥 = 1 USDC
    assert ledger.balance(BOB) == Decimal("9.000000")
    assert any(chat_id == ALICE for chat_id, _ in bot.sent)  # author notified


def test_reaction_tip_notifies_reactor(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    bot = Bot()
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🔥"], bot)
    run(on_reaction(upd))
    sent_to = [chat_id for chat_id, _ in bot.sent]
    assert ALICE in sent_to  # author notified
    assert BOB in sent_to    # reactor gets a confirmation too


def test_reaction_tip_throttled_silently(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🔥"], Bot())
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("1.000000")
    run(on_reaction(upd))  # second reaction within cooldown
    assert ledger.balance(ALICE) == Decimal("1.000000")  # throttled, no double tip


def test_reaction_unsupported_emoji_ignored(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["👍"], Bot())
    run(on_reaction(upd))
    assert ledger.balance(BOB) == Decimal("10.000000")


def test_reaction_insufficient_balance_warns(ledger):
    ledger.record_message(-100, 7, ALICE)
    bot = Bot()
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🎉"], bot)
    run(on_reaction(upd))
    assert any(chat_id == BOB for chat_id, _ in bot.sent)
    assert ledger.balance(ALICE) == Decimal("0")


def test_reaction_bot_reactor_ignored(ledger):
    ledger.record_message(-100, 7, ALICE)
    upd = _reaction_update(-100, 7, User(999, "bot", is_bot=True), ["🔥"], Bot())
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("0")


# ---------- edge coverage ----------


def test_qr_error_falls_back_to_text(ledger, monkeypatch):
    ledger.ensure_user(BOB, "bob")

    async def boom(*a, **k):
        raise RuntimeError("qr backend broken")

    monkeypatch.setattr(handlers.qrlib, "qr_bytes", boom)
    m = Message("/start", from_id=ALICE, username="alice")
    run(cmd_start(m, CommandObject(command="start", args="donate_2002")))
    assert not m.photos
    assert m.answers and "Поддержать" in m.answers[0][0]


def test_edit_menu_fallback_to_caption():
    class NoEdit(AnswerRecorder):
        async def edit_text(self, text, reply_markup=None, **kw):
            raise RuntimeError("not editable")

    cb = Callback("x", ALICE)
    cb.message = NoEdit()
    run(_edit_menu(cb, "hello"))
    assert cb.message.caption == "hello"


def test_edit_menu_both_fail_silently():
    class NoEdit(AnswerRecorder):
        async def edit_text(self, text, reply_markup=None, **kw):
            raise RuntimeError("1")

        async def edit_caption(self, caption=None, reply_markup=None, **kw):
            raise RuntimeError("2")

    cb = Callback("x", ALICE)
    cb.message = NoEdit()
    run(_edit_menu(cb, "hello"))
    assert cb.message.text is None and cb.message.caption is None


def test_on_menu_no_user():
    cb = Callback("bal", ALICE)
    cb.from_user = None
    run(on_menu(cb))
    assert cb.answers == []


def test_on_menu_bets_with_markets(ledger):
    ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    cb = Callback("bets", ALICE)
    run(on_menu(cb))
    assert "Открытые ставки" in cb.message.text


def test_cmd_deposit_linked(ledger):
    m = Message("/link " + ACC.address)
    run(cmd_link(m))
    nonce = ledger._conn.execute(
        "SELECT nonce FROM link_nonces WHERE tg_id = %s", (ALICE,)
    ).fetchone()["nonce"]
    ledger.confirm_link(ALICE, ACC.address, nonce)
    m2 = Message("/deposit", from_id=ALICE)
    run(cmd_deposit(m2))
    assert "привязанного кошелька" in m2.photos[0][1]


def test_cmd_confirm_parse_error(ledger):
    """Real invalid signature: v=0x20 -> eth_account raises BadSignature."""
    m = Message("/link " + ACC.address)
    run(cmd_link(m))
    m2 = Message("/confirm " + "0x20" + "ff" * 32 + "00" * 32)
    run(cmd_confirm(m2))
    assert "Не удалось разобрать" in m2.answers[0][0]
    assert ledger.linked_address(ALICE) is None


def test_cmd_tip_bad_format(ledger):
    m = Message("/tip abc def", from_id=ALICE)
    run(cmd_tip(m))
    assert "Формат: /tip" in m.answers[0][0]


def test_cmd_tip_target_no_at(ledger):
    m = Message("/tip 5 bob", from_id=ALICE)
    run(cmd_tip(m))
    assert "Укажи получателя" in m.answers[0][0]


def test_cmd_tip_user_not_found(ledger):
    m = Message("/tip 5 @nobody", from_id=ALICE)
    run(cmd_tip(m))
    assert "Не нашёл" in m.answers[0][0]


def test_cmd_tip_resolve_in_chat_exception(ledger):
    class BoomChat(Chat):
        async def get_members(self, limit=200):
            raise RuntimeError("forbidden")
            yield  # pragma: no cover

    m = Message("/tip 5 @bob", from_id=ALICE, chat=BoomChat())
    run(cmd_tip(m))
    assert "Не нашёл" in m.answers[0][0]


def test_cmd_tip_self(ledger):
    ledger.ensure_user(ALICE, "alice")
    m = Message("/tip 5 @alice", from_id=ALICE)
    run(cmd_tip(m))
    assert "Себе" in m.answers[0][0]


def test_notify_tip_received_self_is_noop():
    m = Message("/tip 1 @alice", from_id=ALICE)
    run(_notify_tip_received(m, ALICE, 5, "alice"))
    assert m.bot.sent == []


def test_tip_notification_failure_silent(ledger):
    class BoomBot(Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("blocked")

    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    m = Message("/tip 5 @bob", from_id=ALICE, bot=BoomBot())
    run(cmd_tip(m))
    assert "Остаток" in m.answers[0][0]


def test_cmd_withdraw_zero(ledger):
    m = Message(f"/withdraw {ACC.address} 0", from_id=ALICE)
    run(cmd_withdraw(m))
    assert "больше нуля" in m.answers[0][0]


def test_cmd_bet_no_args(ledger):
    m = Message("/bet", from_id=ALICE)
    run(cmd_bet(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_bet_create_long_question(ledger):
    m = Message("/bet create " + "в" * 201 + " | А | Б", from_id=ALICE)
    run(cmd_bet(m))
    assert "длинный вопрос" in m.answers[0][0]


def test_cmd_bet_place_wrong_count(ledger):
    m = Message("/bet 1 2", from_id=ALICE)
    run(cmd_bet(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_bet_place_non_numeric_option(ledger):
    m = Message("/bet 1 abc 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_bet_place_zero(ledger):
    m = Message("/bet 1 1 0", from_id=ALICE)
    run(cmd_bet(m))
    assert "больше нуля" in m.answers[0][0]


def test_cmd_bet_place_not_found(ledger):
    m = Message("/bet 999 1 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "не найдена" in m.answers[0][0]


def test_cmd_bet_place_closed(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    m = Message(f"/bet {bid} 1 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "уже закрыта" in m.answers[0][0]


def test_cmd_bet_place_deadline_expired(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"], close_at=0)
    m = Message(f"/bet {bid} 1 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "истекло" in m.answers[0][0]


def test_cmd_bet_place_insufficient(ledger):
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    m = Message(f"/bet {bid} 1 5", from_id=ALICE)
    run(cmd_bet(m))
    assert "Недостаточно" in m.answers[0][0]


def test_bet_card_unknown():
    assert run(_bet_card({"id": 99999})) == ""


def test_cmd_bets_empty(ledger):
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    assert "Открытых ставок нет" in m.answers[0][0]


def test_cmd_bets_with_markets(ledger):
    ledger.create_bet(ALICE, "Кто победит?", ["А", "Б"])
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    assert "Открытые ставки" in m.answers[0][0]
    assert m.answers[0][1] is not None  # keyboard attached


def test_cb_bet_amount_unknown(ledger):
    cb = Callback("betq:99999:0", ALICE)
    run(cb_bet_amount(cb))
    assert cb.answers and cb.answers[0][1] is True


def test_cb_bet_place_invalid_amount(ledger):
    cb = Callback("bets:1:0:abc", ALICE)
    run(cb_bet_place(cb))
    assert cb.answers and "Неверная сумма" in cb.answers[0][0]


def test_cb_bet_place_unknown_market(ledger):
    cb = Callback("bets:99999:0:5", ALICE)
    run(cb_bet_place(cb))
    assert cb.answers and "не найден" in cb.answers[0][0].lower()


def test_cb_bet_place_closed_alert(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    cb = Callback(f"bets:{bid}:0:5", ALICE)
    run(cb_bet_place(cb))
    assert cb.answers and "закрыт" in cb.answers[0][0]


def test_cb_bet_place_insufficient_alert(ledger):
    ledger.credit(BOB, 5_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    cb = Callback(f"bets:{bid}:0:10", BOB)
    run(cb_bet_place(cb))
    assert cb.answers and "Недостаточно баланса" in cb.answers[0][0]
    assert ledger.balance(BOB) == Decimal("5.000000")


def test_cb_bet_place_no_user():
    cb = Callback("bets:1:0:5", ALICE)
    cb.from_user = None
    run(cb_bet_place(cb))
    assert cb.answers == []


def test_cmd_resolve_non_numeric_option(ledger):
    m = Message("/resolve 1 abc", from_id=ALICE)
    run(cmd_resolve(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_cancel_not_open(ledger):
    m = Message("/cancel 999", from_id=ALICE)
    run(cmd_cancel(m))
    assert "не найдена" in m.answers[0][0]


def test_history_all_kinds(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    ledger.transfer(ALICE, BOB, 1_000_000)
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 1_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    bid2 = ledger.create_bet(ALICE, "Вопрос2?", ["А", "Б"])
    ledger.place_bet(bid2, ALICE, 0, 1_000_000)
    ledger.cancel_bet(bid2, ALICE)
    ledger._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note) VALUES "
        "('mystery', %s, 'x', 1, 'n')",
        (ALICE,),
    )
    ledger._conn.execute(
        "INSERT INTO tx_log (kind, tg_id, counterparty, amount, tx_hash) VALUES "
        "('withdraw', %s, %s, %s, %s)",
        (ALICE, ACC.address, 100, "0xabc"),
    )
    ledger._conn.commit()
    txt = run(_history_text(ALICE))
    assert "операции" in txt
    assert "mystery" in txt
    assert "−0.0001" in txt  # withdraw row rendered


def test_index_message_records(ledger):
    m = Message("hello", from_id=ALICE, username="alice")
    run(_index_message(m))
    ledger.credit(BOB, 10_000_000, "deposit")
    bot = Bot()
    upd = _reaction_update(m.chat.id, m.message_id, User(BOB, "bob"), ["❤️"], bot)
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("2.000000")


def test_index_message_skips_bot_and_missing_user(ledger):
    m = Message("hi", from_id=999)
    m.from_user.is_bot = True
    run(_index_message(m))
    m2 = Message("hi", from_id=999)
    m2.from_user = None
    run(_index_message(m2))
    rows = ledger._conn.execute(
        "SELECT COUNT(*) AS c FROM message_authors", ()
    ).fetchone()["c"]
    assert rows == 0


def test_reaction_reactor_none(ledger):
    upd = _reaction_update(-100, 7, None, ["🔥"], Bot())
    run(on_reaction(upd))


def test_reaction_notification_failure_silent(ledger):
    class BoomBot(Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("blocked")

    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🔥"], BoomBot())
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("1.000000")


def test_reaction_balance_warn_failure_silent(ledger):
    class BoomBot(Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("blocked")

    ledger.record_message(-100, 7, ALICE)
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🎉"], BoomBot())
    run(on_reaction(upd))  # must not raise


def test_qr_success_path(ledger, monkeypatch):
    async def _qr(*a, **k):
        return b"\x89PNG\r\n\x1a\n"
    monkeypatch.setattr(handlers.qrlib, "qr_bytes", _qr)
    m = Message("/start", from_id=ALICE, username="alice")
    run(cmd_start(m, CommandObject(command="start", args="donate_2002")))
    assert m.photos


def test_bet_create_deadline_only_option(ledger):
    # "| А | 24h" — deadline eats the missing option, still a format error.
    m = Message("/bet create Вопрос? | А | 24h", from_id=ALICE)
    run(cmd_bet(m))
    assert "Формат" in m.answers[0][0]


def test_bet_create_deadline_in_trailing_word(ledger):
    m = Message("/bet create Вопрос? | А | Б и 24h", from_id=ALICE)
    run(cmd_bet(m))
    assert "создана" in m.answers[0][0]
    assert "Б и" in m.answers[0][0]
    assert ledger.open_bets(1)[0]["close_at"] is not None


def test_cmd_bets_shows_expired_badge(ledger):
    ledger.create_bet(ALICE, "Просроченный?", ["А", "Б"], close_at=0)
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    assert "истёк" in m.answers[0][0]


def test_cmd_bets_shows_deadline(ledger):
    import time

    ledger.create_bet(ALICE, "С дедлайном?", ["А", "Б"], close_at=int(time.time()) + 3600)
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    assert "⏰" in m.answers[0][0] and ("h" in m.answers[0][0] or "m" in m.answers[0][0])


def test_cb_market_expired_text(ledger):
    bid = ledger.create_bet(ALICE, "Истёк?", ["А", "Б"], close_at=0)
    cb = Callback(f"market:{bid}", ALICE)
    run(cb_market(cb))
    assert "истёк" in cb.message.text


def test_cb_market_deadline_text(ledger):
    import time

    bid = ledger.create_bet(ALICE, "Срок?", ["А", "Б"], close_at=int(time.time()) + 3600)
    cb = Callback(f"market:{bid}", ALICE)
    run(cb_market(cb))
    assert "⏰" in cb.message.text and ("h" in cb.message.text or "m" in cb.message.text)


def test_notify_bet_result_dedup(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    ledger.credit(BOB, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 2_000_000)
    ledger.place_bet(bid, BOB, 1, 2_000_000)  # BOB on both options
    ledger.resolve_bet(bid, 0, ALICE)
    m = Message(f"/resolve {bid} 1", from_id=ALICE)
    run(_notify_bet_result(m, bid))
    assert sum(1 for chat_id, _ in m.bot.sent if chat_id == BOB) == 1
    text = next(t for c, t in m.bot.sent if c == BOB)
    assert "Ты выиграл" in text and "3.96" in text


def test_notify_bet_result_bot_failure(ledger):
    class BoomBot(Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("blocked")

    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    m = Message(f"/resolve {bid} 1", from_id=ALICE, bot=BoomBot())
    run(_notify_bet_result(m, bid))


def test_notify_bet_cancelled_dedup(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    ledger.credit(BOB, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    ledger.place_bet(bid, BOB, 1, 1_000_000)
    m = Message(f"/cancel {bid}", from_id=ALICE)
    run(_notify_bet_cancelled(m, bid))
    assert sum(1 for chat_id, _ in m.bot.sent if chat_id == BOB) == 1


def test_notify_bet_cancelled_bot_failure(ledger):
    class BoomBot(Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("blocked")

    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.credit(BOB, 10_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    m = Message(f"/cancel {bid}", from_id=ALICE, bot=BoomBot())
    run(_notify_bet_cancelled(m, bid))


# ---------- rain (group giveaway) ----------


def test_cmd_rain_private_chat(ledger):
    m = Message("/rain 10")
    run(cmd_rain(m))
    assert "только в группах" in m.answers[0][0]


def test_cmd_rain_bad_format(ledger):
    m = Message("/rain abc", chat=Chat(type="group"))
    run(cmd_rain(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_rain_over_max_amount(ledger):
    m = Message("/rain 500", chat=Chat(type="group"))
    run(cmd_rain(m))
    assert "Максимум за один дождь" in m.answers[0][0]


def test_cmd_rain_over_max_recipients(ledger):
    m = Message("/rain 10 100", chat=Chat(type="group"))
    run(cmd_rain(m))
    assert "Максимум участников" in m.answers[0][0]


def test_cmd_rain_zero_amount(ledger):
    m = Message("/rain 0", chat=Chat(type="group"))
    run(cmd_rain(m))
    assert "больше нуля" in m.answers[0][0]


def test_cmd_rain_zero_count(ledger):
    m = Message("/rain 10 0", chat=Chat(type="group"))
    run(cmd_rain(m))
    assert "больше нуля" in m.answers[0][0]


def test_cmd_rain_success(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    for u in (BOB, 2003, 2004, 2005):
        ledger.record_message(-1000, u, u)
    m = Message("/rain 5 3", from_id=ALICE, chat=Chat(id=-1000, type="group"))
    run(cmd_rain(m))
    assert "Дождь закончился" in m.answers[0][0]
    assert "Получили:" in m.answers[0][0]
    assert ledger.balance(ALICE) == Decimal("95.000002")  # 3 x 1.666666, remainder stays


def test_cmd_rain_no_members(ledger):
    m = Message("/rain 5 3", from_id=BOB, chat=Chat(id=-1000, type="group"))
    run(cmd_rain(m))
    assert "мало активных" in m.answers[0][0]


def test_cmd_rain_reads_the_recipient_names_in_one_go(ledger):
    """/rain resolved each recipient's @name with a query of its own, so the
    eight-name line-up it prints cost eight round trips on the one serialized
    ledger connection — before the message rendered at all."""
    ledger.credit(ALICE, 100_000_000, "deposit")
    # rain() excludes the sender from the pool, so the eight candidates below are
    # deliberately ids other than ALICE; eight candidates for count=8 makes the
    # draw deterministic and the whole line-up visible.
    for i in range(7):
        ledger.ensure_user(2100 + i, f"payer{i}")
        ledger.record_message(-1000, 2100 + i, 2100 + i)
    ledger.ensure_user(2200, None)  # tipped, but no Telegram username on file
    ledger.record_message(-1000, 2200, 2200)

    m = Message("/rain 5 8", from_id=ALICE, chat=Chat(id=-1000, type="group"))
    _, queries = statements_used(ledger, lambda: cmd_rain(m))

    # Checked first: if the handler short-circuited before the line-up, the name
    # queries below are absent and this test proves nothing.
    text = m.answers[0][0]
    assert "Получили:" in text
    for i in range(7):
        assert f"@payer{i}" in text
    assert "id2200" in text  # the same fallback the per-row reader gave

    assert sum("SELECT username FROM users WHERE tg_id = %s" in q for q in queries) == 0, (
        "the line-up resolves names one recipient at a time"
    )
    assert sum("FROM users WHERE tg_id IN (" in q for q in queries) == 1


# ---------- settings ----------


def test_cmd_settings_shows_toggles(ledger):
    m = Message("/settings")
    run(cmd_settings(m))
    assert "Реакции-чаевые" in m.answers[0][0]
    assert "включены" in m.answers[0][0]


def test_cb_settings_toggles_and_menu(ledger):
    cb = Callback("set:react", ALICE)
    run(cb_settings(cb))
    assert "выключены" in cb.message.text
    assert not ledger.get_settings(ALICE)["reaction_tips"]
    cb = Callback("set:notif", ALICE)
    run(cb_settings(cb))
    assert not ledger.get_settings(ALICE)["notify_deposits"]
    cb = Callback("menu", ALICE)
    run(cb_menu(cb))
    assert "Баланс" in cb.message.text


def test_reaction_disabled_via_settings(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    ledger.set_setting(BOB, "reaction_tips", False)
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🔥"], Bot())
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("0")
    assert ledger.balance(BOB) == Decimal("10.000000")


def test_reaction_enabled_via_settings_again(ledger):
    ledger.credit(BOB, 10_000_000, "deposit")
    ledger.record_message(-100, 7, ALICE)
    ledger.set_setting(BOB, "reaction_tips", False)
    ledger.set_setting(BOB, "reaction_tips", True)
    upd = _reaction_update(-100, 7, User(BOB, "bob"), ["🔥"], Bot())
    run(on_reaction(upd))
    assert ledger.balance(ALICE) == Decimal("1.000000")


# ---------- broadcast ----------


def test_cmd_broadcast_non_admin(ledger):
    m = Message("/broadcast всем привет")
    run(cmd_broadcast(m))
    assert "Только для владельца" in m.answers[0][0]


def test_cmd_broadcast_admin(ledger, monkeypatch):
    monkeypatch.setattr(handlers.config, "ADMIN_TG_ID", ALICE)
    ledger.credit(BOB, 5_000_000, "deposit")
    m = Message("/broadcast всем привет", from_id=ALICE)
    run(cmd_broadcast(m))
    assert m.answers[0][0].startswith("📣 Разослано: 1")
    assert m.bot.sent == [(BOB, "всем привет")]


def test_cmd_broadcast_admin_no_text(ledger, monkeypatch):
    monkeypatch.setattr(handlers.config, "ADMIN_TG_ID", ALICE)
    m = Message("/broadcast", from_id=ALICE)
    run(cmd_broadcast(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_broadcast_send_failure_silent(ledger, monkeypatch):
    class BoomBot(Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("blocked")

    monkeypatch.setattr(handlers.config, "ADMIN_TG_ID", ALICE)
    ledger.credit(BOB, 5_000_000, "deposit")
    m = Message("/broadcast всем привет", from_id=ALICE, bot=BoomBot())
    run(cmd_broadcast(m))
    assert "Разослано: 0" in m.answers[0][0]


# ---------- inline resolution (creator flow) ----------


def test_cb_res_shows_options_for_creator(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    cb = Callback(f"res:{bid}", ALICE)
    run(cb_res(cb))
    assert "Кто победил?" in cb.message.text
    rows = cb.message.markup.inline_keyboard
    assert rows[0][0].callback_data == f"res:{bid}:0"
    assert rows[-1][0].callback_data == f"market:{bid}"


def test_cb_res_non_creator_alerted(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    cb = Callback(f"res:{bid}", BOB)
    run(cb_res(cb))
    assert cb.answers and cb.answers[0][1] is True  # alert shown
    assert ledger.get_bet(bid)["status"] == "open"


def test_cb_res_closed_market_alerted(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    ledger.credit(BOB, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    ledger.place_bet(bid, BOB, 0, 1_000_000)
    ledger.resolve_bet(bid, 0, ALICE)
    cb = Callback(f"res:{bid}", ALICE)
    run(cb_res(cb))
    assert cb.answers and cb.answers[0][1] is True


def test_cb_res_choose_resolves_and_dms(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    ledger.credit(BOB, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    ledger.place_bet(bid, BOB, 0, 10_000_000)
    bot = Bot()
    cb = Callback(f"res:{bid}:0", ALICE, bot=bot)
    run(cb_res(cb))
    assert ledger.get_bet(bid)["status"] == "resolved"
    assert ledger.get_bet(bid)["winner"] == 0
    assert "закрыта" in cb.message.text
    assert any(cid == BOB and "Ты выиграл" in text for cid, text in bot.sent)


def test_cb_res_choose_bad_index_alerted(ledger):
    ledger.credit(ALICE, 50_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    cb = Callback(f"res:{bid}:7", ALICE)
    run(cb_res(cb))
    assert cb.answers and cb.answers[0][1] is True
    assert ledger.get_bet(bid)["status"] == "open"


def test_cb_res_bet_not_found_alerted(ledger):
    cb = Callback("res:999", ALICE)
    run(cb_res(cb))
    assert cb.answers and cb.answers[0][1] is True


# ---------- compact /bets ----------


def test_cmd_bets_compact_listing(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    ledger.credit(BOB, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Вопрос?", ["Да", "Нет"])
    ledger.place_bet(bid, BOB, 0, 10_000_000)
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    text = m.answers[0][0]
    assert "Вопрос?" in text and "10 USDC" in text
    assert "Комиссия на выигрыш" not in text  # no full cards, just the listing
    rows = m.answers[0][1].inline_keyboard
    assert rows[0][0].callback_data == f"market:{bid}"
    assert rows[-1][0].callback_data == "betcreate"


def test_cmd_bets_no_markets_still_has_create_button(ledger):
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    rows = m.answers[0][1].inline_keyboard
    assert rows[-1][0].callback_data == "betcreate"


def test_cmd_bets_keeps_the_newest_first_order_of_open_bets(ledger):
    """bulk_market_views() returns views in the id order it was handed, which is
    the order open_bets() chose (id DESC). A batched read that reordered the
    list would look correct in every other test and silently shuffle the UI."""
    old = ledger.create_bet(ALICE, "Старый рынок?", ["Да", "Нет"])
    new = ledger.create_bet(ALICE, "Новый рынок?", ["Да", "Нет"])
    m = Message("/bets", from_id=ALICE)
    run(cmd_bets(m))
    text = m.answers[0][0]
    assert text.index("Новый рынок?") < text.index("Старый рынок?")
    rows = m.answers[0][1].inline_keyboard
    assert [r[0].callback_data for r in rows[:2]] == [f"market:{new}", f"market:{old}"]


def test_cmd_bets_queries_do_not_grow_with_the_market_count(ledger):
    """/bets used to call market_view() once per row — four statements each, so a
    ten-market list cost forty queries on every open. bulk_market_views()
    replaces that with a fixed few. Counting statements is the only way to pin
    this down: the rendered text is identical either way."""

    def run_bets():
        return statements_used(ledger, lambda: cmd_bets(Message("/bets", from_id=ALICE)))[1]

    # A market must exist for both runs: with an empty list the handler returns
    # before reading positions, so comparing empty-vs-full would only measure the
    # branch, not the batch.
    ledger.create_bet(ALICE, "Первый рынок?", ["Да", "Нет"])
    first = run_bets()
    for i in range(4):
        ledger.create_bet(ALICE, f"Рынок {i}?", ["Да", "Нет"])
    second = run_bets()
    assert len(second) == len(first), (
        f"/bets cost {len(second) - len(first)} more statements for 4 extra markets"
    )
    assert any("IN (" in q for q in second), "the list reads markets one by one again"


def test_cb_settings_unknown_key_ignored(ledger):
    cb = Callback("set:bogus", ALICE)
    run(cb_settings(cb))  # must not raise, nothing toggled
    assert ledger.get_settings(ALICE)["reaction_tips"]


def test_cb_settings_no_user_ignored(ledger):
    cb = Callback("set:react", ALICE)
    cb.from_user = None
    run(cb_settings(cb))


def test_cb_menu_no_user_ignored(ledger):
    cb = Callback("menu", ALICE)
    cb.from_user = None
    run(cb_menu(cb))


def test_cmd_rain_throttled(ledger, monkeypatch):
    monkeypatch.setattr(handlers._common, "_now", lambda: 1000.0)
    m = Message("/rain 10", from_id=BOB, chat=Chat(id=-1000, type="group"))
    run(cmd_rain(m))
    m2 = Message("/rain 10", from_id=BOB, chat=Chat(id=-1000, type="group"))
    run(cmd_rain(m2))
    assert "Слишком часто" in m2.answers[0][0]


def test_notify_bet_result_unknown_bet(ledger):
    m = Message("/resolve 999 1", from_id=ALICE)
    run(_notify_bet_result(m, 999))  # must not raise
    assert m.bot.sent == []


def test_balance_shows_open_positions(ledger):
    ledger.credit(ALICE, 100_000_000, "deposit")
    bid = ledger.create_bet(ALICE, "Q", ["А", "Б"])
    ledger.place_bet(bid, ALICE, 0, 10_000_000)
    m = Message("/balance")
    run(cmd_balance(m))
    assert "В игре" in m.answers[0][0]
    assert "Потенциальный выигрыш" in m.answers[0][0]


# ---------- paywall ----------


def test_cmd_paywall_help(ledger):
    m = Message("/paywall")
    run(cmd_paywall(m, CommandObject(command="paywall", args=None)))
    assert "Платный контент" in m.answers[0][0]
    assert "/paywall create" in m.answers[0][0]


def test_cmd_paywall_create_asks_for_content(ledger):
    m = Message("/paywall create 5 Мой отчёт")
    run(cmd_paywall(m, CommandObject(command="paywall", args="create 5 Мой отчёт")))
    assert "пришли" in m.answers[0][0].lower()
    assert (ALICE, 5_000_000, "Мой отчёт") in [(k, v[0], v[1]) for k, v in handlers._paywall_draft.items()]
    # malformed price
    m2 = Message("/paywall create abc Заголовок")
    run(cmd_paywall(m2, CommandObject(command="paywall", args="create abc Заголовок")))
    assert "Формат" in m2.answers[0][0]
    # cancel
    m3 = Message("/paywall cancel")
    run(cmd_paywall(m3, CommandObject(command="paywall", args="cancel")))
    assert ALICE not in handlers._paywall_draft


def test_cmd_paywall_draft_captures_content(ledger):
    m = Message("/paywall create 5 Мой отчёт")
    run(cmd_paywall(m, CommandObject(command="paywall", args="create 5 Мой отчёт")))
    msg = Message("вот секретный текст контента", from_id=ALICE)
    run(_index_message(msg))
    items = ledger.paywall_items_list()
    assert len(items) == 1
    assert items[0]["title"] == "Мой отчёт"
    assert items[0]["price_micro"] == 5_000_000
    # The list reader no longer carries `content`, so the stored text is checked
    # through the single-item reader the buy path uses.
    assert "content" not in items[0]
    assert ledger.paywall_item(items[0]["id"])["content"] == "вот секретный текст контента"
    # a command cancels the draft
    m = Message("/paywall create 5 Ещё")
    run(cmd_paywall(m, CommandObject(command="paywall", args="create 5 Ещё")))
    cmd = Message("/balance", from_id=ALICE)
    run(_index_message(cmd))
    assert ALICE not in handlers._paywall_draft
    assert len(ledger.paywall_items_list()) == 1


def test_cmd_paywall_draft_rejected_at_item_cap(ledger, monkeypatch):
    from bot import config as cfg

    monkeypatch.setattr(cfg, "PAYWALL_MAX_ITEMS_PER_USER", 1)
    ledger.create_paywall(ALICE, "Первый", 500_000, "x")
    m = Message("/paywall create 5 Второй")
    run(cmd_paywall(m, CommandObject(command="paywall", args="create 5 Второй")))
    msg = Message("контент второго", from_id=ALICE)
    run(_index_message(msg))
    assert "Лимит" in msg.answers[0][0]
    assert ALICE not in handlers._paywall_draft
    assert len(ledger.paywall_items_list()) == 1


def test_cmd_paywall_list_and_buy(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    ledger.credit(BOB, 10_000_000, "deposit")
    item_id = ledger.create_paywall(BOB, "Пост", 400_000, "секретный контент")

    m = Message("/paywall list", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args="list")))
    assert f"#{item_id}" in m.answers[0][0]
    assert "0.4 USDC" in m.answers[0][0]

    m2 = Message(f"/paywall buy {item_id}", from_id=ALICE)
    run(cmd_paywall(m2, CommandObject(command="paywall", args=f"buy {item_id}")))
    assert "секретный контент" in m2.answers[0][0]
    assert ledger.user_view(ALICE)["balance_micro"] == 9_600_000

    # already bought -> content shown again for free
    m3 = Message(f"/paywall buy {item_id}", from_id=ALICE)
    run(cmd_paywall(m3, CommandObject(command="paywall", args=f"buy {item_id}")))
    assert "Уже куплено" in m3.answers[0][0]
    assert ledger.user_view(ALICE)["balance_micro"] == 9_600_000

    # unknown item
    m4 = Message("/paywall buy 999", from_id=ALICE)
    run(cmd_paywall(m4, CommandObject(command="paywall", args="buy 999")))
    assert "не найден" in m4.answers[0][0]


def test_cmd_paywall_list_sends_one_message(ledger):
    ledger.ensure_user(BOB, "bob")
    item_id = ledger.create_paywall(BOB, "Пост", 400_000, "секретный контент")
    m = Message("/paywall list", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args="list")))
    assert len(m.answers) == 1, "list must send exactly one message"
    text = m.answers[0][0]
    assert f"#{item_id}" in text
    assert "0.4 USDC" in text
    assert "Купить: /paywall buy" in text


def test_cmd_paywall_list_reads_ownership_once_whatever_the_post_count(ledger):
    """The ✅ marker used to cost one paywall_purchased() per post — so opening the
    list was a query for every post any user ever put up for sale, each one behind
    the ledger lock."""

    def list_statements():
        return statements_used(
            ledger,
            lambda: cmd_paywall(
                Message("/paywall list", from_id=ALICE),
                CommandObject(command="paywall", args="list"),
            ),
        )[1]

    ledger.ensure_user(BOB, "bob")
    ledger.credit(ALICE, 10_000_000, "deposit")
    bought = ledger.create_paywall(BOB, "Купленный пост", 400_000, "контент")
    assert ledger.buy_paywall(ALICE, bought) == "ok"
    first = list_statements()
    for i in range(4):
        assert ledger.create_paywall(BOB, f"Пост {i}", 100_000 + i, "контент")
    second = list_statements()
    assert len(second) == len(first), (
        f"/paywall list cost {len(second) - len(first)} more statements for 4 extra posts"
    )
    assert any("IN (" in q for q in second), "the list checks ownership post by post"

    m = Message("/paywall list", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args="list")))
    text = m.answers[0][0]
    lines = {l.split(" ")[0]: l for l in text.split("\n") if l.startswith("#")}
    assert len(lines) == 5
    # Parity with the single-row reader the batch replaced, post by post.
    for r in ledger.paywall_items_list():
        assert ("✅" in lines[f"#{r['id']}"]) == bool(ledger.paywall_purchased(r["id"], ALICE))
    assert sum("✅" in l for l in lines.values()) == 1


def test_cmd_paywall_list_shows_a_page_and_says_so(ledger):
    """/paywall list used to render every post on sale. Telegram hard-caps a text
    message at 4096 characters and an over-long answer is not trimmed by the server —
    the send is refused and the user receives nothing at all. So the list is a page
    of the newest posts, and it says how many it shows."""
    from bot import config as cfg
    from bot.handlers.paywall import TG_MESSAGE_MAX

    ledger.ensure_user(BOB, "bob")
    for i in range(cfg.PAYWALL_LIST_MAX + 5):
        assert ledger.create_paywall(BOB, f"Пост {i}", 100_000 + i, "контент")

    m = Message("/paywall list", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args="list")))
    assert len(m.answers) == 1
    text = m.answers[0][0]
    shown = {int(l.split(" ")[0][1:]) for l in text.split("\n") if l.startswith("#")}
    assert len(shown) == cfg.PAYWALL_LIST_MAX
    all_ids = sorted(int(r["id"]) for r in ledger.paywall_items_list())
    assert shown == set(all_ids[-cfg.PAYWALL_LIST_MAX:])  # the newest, none older
    assert str(cfg.PAYWALL_LIST_MAX) in text
    assert len(text) <= TG_MESSAGE_MAX


def test_cmd_paywall_list_trims_to_what_telegram_accepts(ledger):
    """The row cap alone does not guarantee the page fits: titles are user-written
    and html.escape can turn one 120-character title into 600 characters, so a page
    well inside PAYWALL_LIST_MAX can still cross 4096 and be rejected outright."""
    from bot.handlers.paywall import TG_MESSAGE_MAX

    ledger.ensure_user(BOB, "bob")
    ids = [ledger.create_paywall(BOB, "&" * 120, 100_000 + i, "контент") for i in range(8)]

    m = Message("/paywall list", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args="list")))
    text = m.answers[0][0]
    assert len(text) <= TG_MESSAGE_MAX
    shown = {int(l.split(" ")[0][1:]) for l in text.split("\n") if l.startswith("#")}
    assert 1 <= len(shown) < 8, "the page was not trimmed to fit"
    assert max(ids) in shown  # trimming drops the older posts, never the newest
    assert min(ids) not in shown


def test_paywall_list_shown_renders_in_every_language():
    from bot import i18n

    for lang in ("ru", "en", "zh"):
        msg = i18n.t(lang, "paywall_list_shown", n=20)
        assert "20" in msg
        assert "{n}" not in msg


def test_on_menu_paywall_list_shows_posts(ledger):
    ledger.ensure_user(BOB, "bob")
    item_id = ledger.create_paywall(BOB, "Пост", 400_000, "секретный контент")
    cb = Callback("paywall_list", ALICE)
    run(on_menu(cb))
    text = cb.message.text
    assert f"#{item_id}" in text
    assert "0.4 USDC" in text
    assert "Купить: /paywall buy" in text


def test_oc_registry_warn_placeholders():
    from bot import i18n
    for lang in ("ru", "en", "zh"):
        msg = i18n.t(lang, "oc_registry_warn", err="db down", id2=42)
        assert "db down" in msg
        assert "42" in msg


def test_cmd_paywall_buy_insufficient(ledger):
    ledger.credit(ALICE, 100_000, "deposit")
    item_id = ledger.create_paywall(BOB, "Дорого", 500_000, "x")
    m = Message(f"/paywall buy {item_id}", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"buy {item_id}")))
    assert "Недостаточно" in m.answers[0][0]
    assert ledger.user_view(ALICE)["balance_micro"] == 100_000


def test_cmd_paywall_rejects_overlong_title(ledger):
    m = Message(f"/paywall create 5 {'x' * 200}")
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"create 5 {'x' * 200}")))
    assert "слишком длинный" in m.answers[0][0]
    assert ALICE not in handlers._paywall_draft


def test_cmd_paywall_rejects_overlong_content(ledger):
    m = Message("/paywall create 5 Мой отчёт")
    run(cmd_paywall(m, CommandObject(command="paywall", args="create 5 Мой отчёт")))
    msg = Message("y" * 5000, from_id=ALICE)
    run(_index_message(msg))
    assert "слишком длинный" in msg.answers[0][0]
    assert ALICE not in handlers._paywall_draft
    assert ledger.paywall_items_list() == []


def test_cmd_paywall_owner_cannot_buy_own_post(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    item_id = ledger.create_paywall(ALICE, "Мой пост", 500_000, "секрет")
    m = Message(f"/paywall buy {item_id}", from_id=ALICE)
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"buy {item_id}")))
    assert "твой пост" in m.answers[0][0]
    assert ledger.user_view(ALICE)["balance_micro"] == 10_000_000


def test_paywall_kinds_in_history_text(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    ledger.credit(BOB, 10_000_000, "deposit")
    item_id = ledger.create_paywall(BOB, "Пост", 400_000, "секретный контент")
    ledger.buy_paywall(ALICE, item_id)
    txt = run(_history_text(ALICE))
    assert "платный контент" in txt
    txt_bob = run(_history_text(BOB))
    assert "продажа" in txt_bob


# ---------- channel paywall ----------

CHANNEL_ID = -100123


def _channel_message(text, chat_type="channel", from_id=ALICE, bot=None):
    return Message(text, from_id=from_id, bot=bot, chat=Chat(id=CHANNEL_ID, type=chat_type))


def test_paywall_channel_requires_channel_and_admin(ledger):
    bot = ChannelBot(members={ALICE: "creator", 123456: "administrator"})
    # not inside a channel -> refused
    m = Message("/paywall channel 5", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="channel 5")))
    assert "в самом канале" in m.answers[0][0]
    # bot is not an admin -> refused
    bot2 = ChannelBot(members={ALICE: "creator"})
    m2 = _channel_message("/paywall channel 5", bot=bot2)
    run(cmd_paywall(m2, CommandObject(command="paywall", args="channel 5")))
    assert "админом канала" in m2.answers[0][0]
    # user is not an admin -> refused
    bot3 = ChannelBot(members={ALICE: "member", 123456: "administrator"})
    m3 = _channel_message("/paywall channel 5", bot=bot3)
    run(cmd_paywall(m3, CommandObject(command="paywall", args="channel 5")))
    assert "Только админ" in m3.answers[0][0]


def test_paywall_channel_enable_and_disable(ledger):
    bot = ChannelBot(members={ALICE: "creator", 123456: "administrator"})
    m = _channel_message("/paywall channel 5", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="channel 5")))
    assert "продаётся" in m.answers[0][0]
    ch = ledger.paywall_channel(CHANNEL_ID)
    assert ch["owner_tg"] == ALICE
    assert ch["price_micro"] == 5_000_000
    # malformed price
    m2 = _channel_message("/paywall channel abc", bot=bot)
    run(cmd_paywall(m2, CommandObject(command="paywall", args="channel abc")))
    assert "Формат" in m2.answers[0][0]
    # disable
    m3 = _channel_message("/paywall channel off", bot=bot)
    run(cmd_paywall(m3, CommandObject(command="paywall", args="channel off")))
    assert "выключена" in m3.answers[0][0]
    assert ledger.paywall_channel(CHANNEL_ID) is None


def test_paywall_subscribe_new_member_gets_invite(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000)
    bot = ChannelBot(members={BOB: "creator", 123456: "administrator"})  # ALICE is 'left'
    m = Message(f"/paywall subscribe {CHANNEL_ID}", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"subscribe {CHANNEL_ID}")))
    assert "Жми ссылку" in m.answers[0][0]
    assert bot.invites and bot.invites[0][0] == CHANNEL_ID and bot.invites[0][1] == 1
    assert ledger.user_view(ALICE)["balance_micro"] == 5_000_000
    assert ledger.user_view(BOB)["balance_micro"] == 5_000_000
    assert ledger.channel_subscription(CHANNEL_ID, ALICE) is not None
    # owner got a sale notification
    assert any("подписка" in t for _, t in bot.sent)


def test_paywall_subscribe_member_extends(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000)
    bot = ChannelBot(members={ALICE: "member", BOB: "creator", 123456: "administrator"})
    m = Message(f"/paywall subscribe {CHANNEL_ID}", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"subscribe {CHANNEL_ID}")))
    assert "продлён" in m.answers[0][0]
    assert bot.invites == []  # already inside — no invite link
    assert ledger.channel_subscription(CHANNEL_ID, ALICE) is not None


def test_paywall_subscribe_admin_and_unknown(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, ALICE, 5_000_000)
    bot = ChannelBot(members={ALICE: "creator", 123456: "administrator"})
    # admin of the channel does not need a subscription
    m = Message(f"/paywall subscribe {CHANNEL_ID}", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"subscribe {CHANNEL_ID}")))
    assert "админ" in m.answers[0][0]
    # channel not for sale
    m2 = Message("/paywall subscribe -999", bot=bot)
    run(cmd_paywall(m2, CommandObject(command="paywall", args="subscribe -999")))
    assert "не продаётся" in m2.answers[0][0]
    # unknown username
    m3 = Message("/paywall subscribe nonexistent", bot=bot)
    bot3 = ChannelBot(members={ALICE: "member"})
    run(cmd_paywall(m3, CommandObject(command="paywall", args="subscribe nonexistent")))
    # insufficient funds
    ledger.credit(BOB, 100_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000)
    m4 = Message("/paywall subscribe nonexistent2", from_id=BOB, bot=ChannelBot())
    run(cmd_paywall(m4, CommandObject(command="paywall", args="subscribe nonexistent2")))


def test_paywall_subscribe_insufficient(ledger):
    ledger.credit(ALICE, 100_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000)
    bot = ChannelBot(members={ALICE: "left", BOB: "creator", 123456: "administrator"})
    m = Message(f"/paywall subscribe {CHANNEL_ID}", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"subscribe {CHANNEL_ID}")))
    assert "Недостаточно" in m.answers[0][0]
    assert ledger.user_view(ALICE)["balance_micro"] == 100_000


def test_paywall_subscribe_owner_self_blocked(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, ALICE, 5_000_000)

    class BrokenBot(ChannelBot):
        async def get_chat_member(self, chat_id, user_id):
            raise Exception("api down")  # member check fails -> falls through to ledger

    m = Message(f"/paywall subscribe {CHANNEL_ID}", from_id=ALICE, bot=BrokenBot())
    run(cmd_paywall(m, CommandObject(command="paywall", args=f"subscribe {CHANNEL_ID}")))
    assert "твой канал" in m.answers[0][0]
    assert ledger.user_view(ALICE)["balance_micro"] == 10_000_000
    assert ledger.channel_subscription(CHANNEL_ID, ALICE) is None


def test_paywall_channels_lists_my_subs(ledger):
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000)
    bot = ChannelBot(members={BOB: "creator", 123456: "administrator"})
    m = Message("/paywall channels", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="channels")))
    assert "Paid Channel" in m.answers[0][0]
    assert "5 USDC/30д" in m.answers[0][0]
    m2 = Message("/paywall subscribe " + str(CHANNEL_ID), bot=bot)
    run(cmd_paywall(m2, CommandObject(command="paywall", args="subscribe " + str(CHANNEL_ID))))
    m3 = Message("/paywall channels", bot=bot)
    run(cmd_paywall(m3, CommandObject(command="paywall", args="channels")))
    assert "🔑" in m3.answers[0][0]


def test_paywall_owner_notification_uses_the_owner_language(ledger):
    """The seller's receipt was built with a hardcoded 'ru', so an English or Chinese
    owner was told about money they had earned in a language they never picked. The
    buyer's language cannot decide it either — the message goes to the seller."""
    ledger.credit(ALICE, 10_000_000, "deposit")
    ledger.set_setting(BOB, "lang", "en")
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000)
    bot = ChannelBot(members={BOB: "creator", 123456: "administrator"})
    m = Message("/paywall subscribe " + str(CHANNEL_ID), bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="subscribe " + str(CHANNEL_ID))))
    notice = [text for cid, text in bot.sent if cid == BOB]
    assert len(notice) == 1
    assert "subscription to channel" in notice[0]
    assert "подписка" not in notice[0]


def test_paywall_channels_advertise_the_real_period(ledger):
    """The listing line read "30д" whatever the channel actually sold access for, so
    a weekly channel advertised a monthly price."""
    ledger.set_paywall_channel(CHANNEL_ID, BOB, 5_000_000, 7)
    bot = ChannelBot()
    m = Message("/paywall channels", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="channels")))
    text = m.answers[0][0]
    assert "5 USDC/7д" in text
    assert "30д" not in text


def test_paywall_channels_reads_subs_and_lang_once_whatever_the_channel_count(ledger):
    """/paywall channels asked two things per channel: the subscription row, and a
    settings query for the language. Both are now fixed-cost — the language is read
    once by the dispatcher and passed down, the subscriptions in one bulk read."""

    def channel_statements():
        bot = ChannelBot(members={BOB: "creator", 123456: "administrator"})
        return statements_used(
            ledger,
            lambda: cmd_paywall(
                Message("/paywall channels", bot=bot),
                CommandObject(command="paywall", args="channels"),
            ),
        )[1]

    ledger.credit(ALICE, 20_000_000, "deposit")
    assert ledger.set_paywall_channel(-1001, BOB, 500_000)
    assert ledger.set_paywall_channel(-1002, BOB, 500_000)
    assert ledger.subscribe_channel(-1001, ALICE) == "ok"
    first = channel_statements()
    # BOB stops at the per-owner cap, so the sixth channel belongs to someone else.
    for chat in (-1003, -1004, -1005):
        assert ledger.set_paywall_channel(chat, BOB, 500_000)
    assert ledger.set_paywall_channel(-1006, 2003, 500_000)
    assert ledger.subscribe_channel(-1005, ALICE) == "ok"
    second = channel_statements()
    assert len(second) == len(first), (
        f"/paywall channels cost {len(second) - len(first)} more statements for 4 extra channels"
    )
    assert any("IN (" in q for q in second), "the list reads subscriptions channel by channel"
    assert sum("paywall_subscriptions" in q for q in second) == 1
    assert sum("FROM user_settings" in q for q in second) == 1

    bot = ChannelBot()
    m = Message("/paywall channels", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="channels")))
    lines = [l for l in m.answers[0][0].split("\n") if l.startswith("•")]
    assert len(lines) == 6
    assert sum("🔑" in l for l in lines) == 2  # -1001 and -1005, nobody else's


def test_paywall_channels_pages_and_trims_to_fit(ledger):
    """/paywall channels had the same unbounded render as the post list, and worse:
    every row it builds asks Telegram for that channel's title, so an oversized page
    was a page of round trips ending in a send the server refuses."""
    from bot import config as cfg
    from bot.handlers.paywall import TG_MESSAGE_MAX

    page = cfg.PAYWALL_LIST_MAX
    for i in range(page + 3):
        # one owner each: the per-owner channel cap is far below the page size
        assert ledger.set_paywall_channel(-2000 - i, 4000 + i, 500_000)
    bot = ChannelBot()
    m = Message("/paywall channels", bot=bot)
    run(cmd_paywall(m, CommandObject(command="paywall", args="channels")))
    assert len(m.answers) == 1
    text = m.answers[0][0]
    assert len([l for l in text.split("\n") if l.startswith("•")]) == page
    assert str(page) in text  # the page says how many it shows
    # one get_chat per rendered row, no more
    assert len(bot.chat_calls) == page

    wide = ChannelBot(title="&" * 120)
    m2 = Message("/paywall channels", bot=wide)
    run(cmd_paywall(m2, CommandObject(command="paywall", args="channels")))
    text2 = m2.answers[0][0]
    assert len(text2) <= TG_MESSAGE_MAX
    rows = [l for l in text2.split("\n") if l.startswith("•")]
    assert 1 <= len(rows) < page, "the page was not trimmed to fit"


# ---------- per-user wallets ----------


def test_wallet_creates_and_shows_address(ledger):
    m = Message("/wallet", from_id=ALICE)
    run(cmd_wallet(m))
    assert "0x" in m.answers[0][0]
    addr = ledger.wallet_address(ALICE)
    assert addr and addr.startswith("0x") and len(addr) == 42


def test_wallet_export_returns_key_and_seed(ledger):
    m = Message("/wallet export", from_id=ALICE)
    run(cmd_wallet(m))
    text = m.answers[0][0]
    assert "0x" in text
    assert "🔒" in text  # security notice


def test_wallet_encrypted_at_rest(ledger):
    run(cmd_wallet(Message("/wallet", from_id=ALICE)))
    row = ledger.get_wallet(ALICE)
    from bot import wallets as wmod

    assert wmod.decrypt(row["key_enc"]).startswith("0x")
    assert len(wmod.decrypt(row["seed_enc"]).split()) == 12
    assert row["key_enc"] != wmod.decrypt(row["key_enc"])  # not plaintext


def test_wallet_export_is_stable(ledger):
    run(cmd_wallet(Message("/wallet", from_id=ALICE)))
    first = ledger.get_wallet(ALICE)["address"]
    run(cmd_wallet(Message("/wallet", from_id=ALICE)))
    assert ledger.wallet_address(ALICE) == first  # no regeneration


def test_import_seed_attaches_existing_wallet(ledger):
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    acct, mnemonic = Account.create_with_mnemonic()
    m = Message(f"/import {mnemonic}", from_id=ALICE)
    run(cmd_import(m))
    assert ledger.wallet_address(ALICE).lower() == acct.address.lower()
    row = ledger.get_wallet(ALICE)
    from bot import wallets as wmod

    assert wmod.decrypt(row["key_enc"]).lower() == ("0x" + acct.key.hex()).lower()


def test_import_rejects_bad_seed(ledger):
    m = Message("/import one two three", from_id=ALICE)
    run(cmd_import(m))
    assert "12 или 24" in m.answers[0][0]
    assert ledger.wallet_address(ALICE) is None


def test_import_rejects_taken_wallet(ledger):
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    acct, mnemonic = Account.create_with_mnemonic()
    run(cmd_import(Message(f"/import {mnemonic}", from_id=ALICE)))
    m2 = Message(f"/import {mnemonic}", from_id=BOB)
    run(cmd_import(m2))
    assert "другому пользователю" in m2.answers[0][0]
    assert ledger.wallet_address(BOB) is None


def test_import_rejects_switching_existing_wallet(ledger):
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    run(cmd_wallet(Message("/wallet", from_id=ALICE)))
    acct, mnemonic = Account.create_with_mnemonic()
    m = Message(f"/import {mnemonic}", from_id=ALICE)
    run(cmd_import(m))
    assert "уже есть кошелёк" in m.answers[0][0]
    assert ledger.wallet_address(ALICE) != acct.address


def test_export_owner_only(ledger, monkeypatch):
    from bot import config as cfg

    monkeypatch.setattr(cfg, "ADMIN_TG_ID", BOB)
    m = Message("/export", from_id=ALICE)
    run(cmd_export(m))
    assert "Только владелец" in m.answers[0][0]
    m2 = Message("/export", from_id=BOB)
    run(cmd_export(m2))
    assert "Hot wallet" in m2.answers[0][0]


def test_deposit_has_disclaimer(ledger):
    m = Message("/deposit", from_id=ALICE)
    run(cmd_deposit(m))
    assert m.photos  # QR is sent
    caption = m.photos[0][1]
    assert "Дисклеймер" in caption
    assert "кастодиальный" in caption
