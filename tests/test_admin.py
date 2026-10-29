"""The owner's panel behind /admin.

Two rules hold it together, and both are asserted here by driving real Update
objects through the real router: nobody but the owner reads it (and a refusal
carries no numbers), and it never moves money — every screen is a batch of
SELECTs, which the source gate below enforces instead of trusting a review.
"""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_dispatch import RecorderSession, _callback_update, _message_update, _mk_bot, _mk_dp

from bot import config, i18n

OWNER = 397602448
ALICE, BOB, STRANGER = 4001, 4002, 5001
USDC = 10**6

# Everything the panel is allowed to ask the ledger. A new name here means a new
# read; a money-moving method (credit, debit, place_bet, cancel_market, …) cannot
# slip in without failing the gate and a person looking at why.
READ_ONLY_LEDGER_CALLS = {
    "global_stats",
    "total_liabilities",
    "pending_deposit_total",
    "ai_usage_today",
    "last_block",
    "top_balances",
    "pending_withdraws",
    "open_amm_markets",
    "bets_by_status",
    "amm_market_view",
    "market_view",
    "open_markets_past_deadline",
    "rollback",
}


class Session(RecorderSession):
    """The dispatch tests' transport, plus the calls the panel makes that the
    Telegram API answers with a bare bool."""

    async def make_request(self, bot, method, timeout=None):
        if method.__api_method__ in ("deleteMessage", "answerCallbackQuery"):
            self.calls.append(
                (method.__api_method__, method.model_dump(mode="python", exclude_none=True))
            )
            return True
        return await super().make_request(bot, method, timeout)


def _feed(update) -> Session:
    s = Session()
    asyncio.run(_mk_dp().feed_update(_mk_bot(s), update))
    return s


def _sent(s: Session, method: str = "sendMessage") -> str:
    return next(p["text"] for n, p in s.calls if n == method)


def _edited(s: Session) -> str:
    return _sent(s, "editMessageText")


def _answer(s: Session) -> dict:
    return next(p for n, p in s.calls if n == "answerCallbackQuery")


def _group_message(text: str, user_id: int) -> dict:
    return {
        "update_id": 11,
        "message": {
            "message_id": 12,
            "date": 0,
            "chat": {"id": -100123, "type": "group", "title": "ops"},
            "from": {"id": user_id, "is_bot": False, "first_name": "O", "username": "ops"},
            "text": text,
        },
    }


def _group_callback(data: str, user_id: int) -> dict:
    return {
        "update_id": 13,
        "callback_query": {
            "id": "cq-group",
            "from": {"id": user_id, "is_bot": False, "first_name": "O", "username": "ops"},
            "chat_instance": "-2000",
            "data": data,
            "message": {
                "message_id": 14,
                "date": 0,
                "chat": {"id": -100123, "type": "group", "title": "ops"},
                "from": {"id": 0, "is_bot": True, "first_name": "b"},
                "text": "panel",
            },
        },
    }


@pytest.fixture(autouse=True)
def _owner_and_quiet_ai(monkeypatch):
    """The owner id under test, and an AI chain that answers from config only —
    no loopback probe, no public endpoint, no key on this machine."""
    monkeypatch.setattr(config, "OWNER_ID", OWNER)
    monkeypatch.setattr(config, "AI_PROVIDER_CHAIN", "")
    monkeypatch.setattr(config, "AI_AUTODISCOVER_KEYS", False)
    monkeypatch.setattr(config, "AI_LOCAL_SERVERS", "")
    monkeypatch.setattr(config, "AI_FREE_ENDPOINT", 0)
    monkeypatch.setattr("bot.ai._LOCAL", {"at": 0.0, "providers": []})
    for key in ("AI_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _no_chain(monkeypatch):
    from bot import base

    monkeypatch.setattr(base, "w3", SimpleNamespace(eth=SimpleNamespace()), raising=False)


def test_a_stranger_gets_one_sentence_and_no_numbers(ledger):
    s = _feed(_message_update("/admin", user_id=STRANGER))
    payload = next(p for n, p in s.calls if n == "sendMessage")
    assert payload["text"] == i18n.t("ru", "admin_denied")
    assert "reply_markup" not in payload
    assert "USDC" not in payload["text"]


def test_the_owner_opens_the_summary(ledger):
    ledger.ensure_user(ALICE, "alice")
    s = _feed(_message_update("/admin", user_id=OWNER))
    text = _sent(s)
    assert text.startswith(f"<b>{i18n.t('ru', 'admin_title')}</b>")
    assert i18n.t("ru", "admin_users", n=1) in text
    buttons = [b["callback_data"] for row in _markup(s) for b in row]
    assert buttons == ["adm:main", "adm:ai", "adm:treasury", "adm:markets", "adm:agent", "menu"]


def _markup(s: Session) -> list[list[dict]]:
    payload = next(p for n, p in s.calls if n == "sendMessage")
    return payload["reply_markup"]["inline_keyboard"]


def test_every_screen_renders_its_own_title(ledger, monkeypatch, tmp_path):
    from agent import config as agent_config

    monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
    _no_chain(monkeypatch)
    for screen, title in (
        ("main", i18n.t("ru", "admin_title")),
        ("ai", i18n.t("ru", "admin_btn_ai")),
        ("treasury", i18n.t("ru", "admin_btn_treasury")),
        ("markets", i18n.t("ru", "admin_btn_markets")),
        ("agent", i18n.t("ru", "admin_btn_agent")),
    ):
        s = _feed(_callback_update(f"adm:{screen}", user_id=OWNER))
        text = _edited(s)
        assert text.startswith(f"<b>{title}</b>"), (screen, text)
        assert "Не удалось собрать" not in text


def test_the_summary_reports_the_scanner_lag(ledger, monkeypatch):
    from bot import base

    monkeypatch.setattr(base, "w3", SimpleNamespace(eth=SimpleNamespace(block_number=500)), raising=False)
    ledger.set_last_block(490)
    s = _feed(_message_update("/admin", user_id=OWNER))
    assert i18n.t("ru", "admin_lag", n=10) in _sent(s)


def test_the_treasury_shows_the_balances_it_says_it_shows(ledger, monkeypatch):
    from bot import base

    async def hot():
        return 25.0

    async def vault():
        return 100.0

    monkeypatch.setattr(base, "hot_balance", hot, raising=False)
    monkeypatch.setattr(base, "vault_balance", vault, raising=False)
    ledger.ensure_user(ALICE, "alice")
    ledger.credit(ALICE, 12_340_000, "deposit")
    ledger.ensure_user(BOB, "bob")
    ledger.credit(BOB, 1_000_000, "deposit")
    s = _feed(_callback_update("adm:treasury", user_id=OWNER))
    text = _edited(s)
    assert i18n.t("ru", "admin_top_balances") in text
    # The heading promises balances; 12.34 is the stored balance, and the label
    # is the user's name, not a bare id.
    assert "alice — 12.34" in text
    assert "bob — 1" in text
    assert "0.00" not in text.split(i18n.t("ru", "admin_top_balances"))[1]
    assert i18n.t("ru", "admin_hot_wallet", v="25.00") in text
    assert i18n.t("ru", "admin_solvent_yes", v="86.66") in text


def test_the_markets_screen_covers_both_engines(ledger):
    ledger.credit(ALICE, 100 * USDC, "deposit")
    mid = ledger.create_market(ALICE, "Кому достанется кубок?", ["Да", "Нет"], 10 * USDC)
    bet = ledger.create_bet(ALICE, "Кто забьёт первым?", ["Аня", "Боря"])
    ledger.credit(BOB, 10 * USDC, "deposit")
    assert ledger.place_bet(bet, BOB, 0, 5 * USDC) == "ok"
    s = _feed(_callback_update("adm:markets", user_id=OWNER))
    text = _edited(s)
    assert i18n.t("ru", "admin_amm_head") in text and f"#{mid}" in text
    assert "Кому достанется кубок?" in text
    assert i18n.t("ru", "admin_pool_head") in text and f"#{bet}" in text
    assert "Кто забьёт первым?" in text
    assert i18n.t("ru", "admin_pot", v="5") in text


def test_a_long_question_is_clipped_with_an_ellipsis_and_escaped(ledger):
    long_question = "Очень длинный вопрос про <b>градус</b> и всё остальное до конца месяца"
    ledger.credit(ALICE, 100 * USDC, "deposit")
    ledger.create_bet(ALICE, long_question, ["Да", "Нет"])
    s = _feed(_callback_update("adm:markets", user_id=OWNER))
    text = _edited(s)
    assert long_question not in text
    assert "<b>градус</b>" not in text  # user text is not markup in a chat message
    assert "&lt;b&gt;градус&lt;/b&gt;" in text
    assert "…" in text


def test_the_agent_screen_shows_caps_and_the_trail(ledger, monkeypatch, tmp_path):
    from agent import config as agent_config

    monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
    (tmp_path / "agent_state.json").write_text(
        json.dumps({"spent_today": 3, "daily_max": 20, "actions_today": 7, "breaker": True}),
        encoding="utf-8",
    )
    (tmp_path / "agent_audit.jsonl").write_text(
        json.dumps({"action": "rebalance", "detail": "moved 5 USDC"}) + "\n", encoding="utf-8"
    )
    s = _feed(_callback_update("adm:agent", user_id=OWNER))
    text = _edited(s)
    assert i18n.t("ru", "admin_agent_spend", v=3, limit=20) in text
    assert i18n.t("ru", "admin_agent_actions", n=7) in text
    assert i18n.t("ru", "admin_agent_breaker") in text
    assert "rebalance" in text and "moved 5 USDC" in text


def test_the_agent_screen_says_so_when_it_never_ran(ledger, monkeypatch, tmp_path):
    from agent import config as agent_config

    monkeypatch.setattr(agent_config, "STATE_DIR", str(tmp_path))
    s = _feed(_callback_update("adm:agent", user_id=OWNER))
    assert i18n.t("ru", "admin_agent_off") in _edited(s)


def test_the_panel_speaks_the_owners_language(ledger):
    for lang in ("en", "zh"):
        ledger.set_setting(OWNER, "lang", lang)
        s = _feed(_message_update("/admin", user_id=OWNER))
        assert i18n.t(lang, "admin_title") in _sent(s)


def test_an_unknown_screen_is_answered_without_an_edit(ledger):
    s = _feed(_callback_update("adm:nuke", user_id=OWNER))
    assert "editMessageText" not in [n for n, _ in s.calls]
    assert _answer(s).get("show_alert") is not True


def test_a_stranger_cannot_walk_the_screens(ledger):
    for screen in ("main", "treasury", "markets"):
        s = _feed(_callback_update(f"adm:{screen}", user_id=STRANGER))
        assert "editMessageText" not in [n for n, _ in s.calls]
        answer = _answer(s)
        assert answer["text"] == i18n.t("ru", "admin_denied")
        assert answer.get("show_alert") is True


def test_the_panel_prints_no_numbers_into_a_group(ledger):
    from aiogram.types import Update

    s = _feed(Update.model_validate(_group_message("/admin", OWNER)))
    texts = [p.get("text", "") for n, p in s.calls if n == "sendMessage"]
    assert i18n.t("ru", "private_chat_only") in texts
    assert all(i18n.t("ru", "admin_title") not in t for t in texts)
    assert "deleteMessage" in [n for n, _ in s.calls]

    s = _feed(Update.model_validate(_group_callback("adm:treasury", OWNER)))
    assert "editMessageText" not in [n for n, _ in s.calls]
    assert _answer(s)["text"] == i18n.t("ru", "private_chat_only")
    assert _answer(s).get("show_alert") is True


def test_a_failing_screen_reports_the_error_and_rolls_back(ledger, monkeypatch):
    import psycopg

    from bot.handlers import admin

    rolled = []
    real_rollback = ledger.rollback

    async def spy():
        rolled.append(True)
        return await real_rollback()

    def boom():
        raise psycopg.errors.InFailedSqlTransaction("aborted")

    monkeypatch.setattr(admin.ledger, "rollback", spy, raising=False)
    monkeypatch.setattr(admin.ledger, "global_stats", boom, raising=False)
    s = _feed(_callback_update("adm:main", user_id=OWNER))
    assert i18n.t("ru", "admin_error", err="InFailedSqlTransaction") in _edited(s)
    # The batch of SELECTs that aborted must not leave the shared connection in
    # a failed transaction, where every later query until a restart fails.
    assert rolled


def test_nobody_is_owner_without_an_owner_id(monkeypatch):
    from bot.handlers import admin

    monkeypatch.setattr(config, "OWNER_ID", None)
    assert admin._owner(OWNER) is False
    assert admin._owner(None) is False


def test_the_panel_calls_nothing_that_moves_money():
    source = Path(__file__).resolve().parents[1] / "bot" / "handlers" / "admin.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ledger"
    }
    assert called <= READ_ONLY_LEDGER_CALLS, called - READ_ONLY_LEDGER_CALLS
    # And the scan has something to look at: a panel whose reads all moved behind
    # a helper would pass the subset check vacuously.
    assert {"global_stats", "total_liabilities", "top_balances"} <= called
