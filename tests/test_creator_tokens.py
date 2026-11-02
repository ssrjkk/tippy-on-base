"""Creator tokens: Postgres persistence, dividend math, legacy JSON import."""

import json
import os
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
)

CREATOR = 123456
BUYER_A = 789012
BUYER_B = 445566


async def _make_token(registry, name="Test Token", symbol="TST"):
    return await registry.create_token(
        creator_tg_id=CREATOR,
        name=name,
        symbol=symbol,
        total_supply=1_000_000,
        initial_price_micro=100_000,  # $0.10
    )


async def test_create_and_token_info(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)

    assert token.token_id.startswith(f"ct_{CREATOR}_")
    info = await registry.get_token_info(token.token_id)
    assert info["name"] == "Test Token"
    assert info["symbol"] == "TST"
    assert info["total_supply"] == 1_000_000
    assert info["price_micro"] == 100_000
    assert info["holder_count"] == 0
    assert info["total_held"] == 0
    assert info["total_revenue_micro"] == 0
    assert await registry.get_token_info("missing") is None

    tokens = await registry.list_creator_tokens(CREATOR)
    assert [t.token_id for t in tokens] == [token.token_id]
    assert tokens[0].symbol == "TST"
    assert await registry.list_creator_tokens(999) == []


async def test_buy_updates_holder_and_cost(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(BUYER_A, 200_000_000, "deposit")
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)

    success, cost = await registry.buy_tokens(token.token_id, BUYER_A, 400)
    assert success is True
    # price_micro is the price of ONE token: 400 × $0.10 = $40.
    assert cost == 400 * 100_000
    assert ledger.balance(BUYER_A) == Decimal("160.000000")
    # The price goes to the token's creator, so the trade conserves money.
    assert ledger.balance(CREATOR) == Decimal("40.000000")

    info = await registry.get_holder_info(token.token_id, BUYER_A)
    assert info["balance"] == 400
    assert info["pending_dividends_micro"] == 0

    token_info = await registry.get_token_info(token.token_id)
    assert token_info["holder_count"] == 1
    assert token_info["total_held"] == 400

    # Top-up buy adds to the existing balance.
    await registry.buy_tokens(token.token_id, BUYER_A, 60)
    info = await registry.get_holder_info(token.token_id, BUYER_A)
    assert info["balance"] == 460

    assert await registry.buy_tokens("missing", BUYER_A, 10) == (False, 0)


async def test_buy_without_funds_grants_no_tokens(tmp_path, ledger):
    """Regression: `/buytoken` granted the shares first and only then tried to
    charge, so the tokens existed before the (crashed) debit. A buyer who
    cannot pay must end up with nothing, and the creator with nothing."""
    from bot.creator_tokens import CreatorTokenRegistry

    unlinked = 555001
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)

    assert await registry.buy_tokens(token.token_id, unlinked, 400) == (False, 0)

    assert await registry.get_holder_info(token.token_id, unlinked) is None
    assert (await registry.get_token_info(token.token_id))["total_held"] == 0
    # No journal rows either: a rejected trade is not a transaction.
    rows = ledger._conn.execute(
        "SELECT COUNT(*) AS c FROM tx_log WHERE kind IN ('token_buy', 'token_sale')"
    ).fetchone()["c"]
    assert int(rows) == 0


async def test_buy_cannot_exceed_total_supply(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(BUYER_A, 200_000_000_000, "deposit")  # $200k
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)  # supply 1_000_000 at $0.10

    assert (await registry.buy_tokens(token.token_id, BUYER_A, 1_000_000))[0] is True
    assert await registry.buy_tokens(token.token_id, BUYER_A, 1) == (False, 0)
    assert (await registry.get_token_info(token.token_id))["total_held"] == 1_000_000


async def test_creator_cannot_buy_own_token(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(CREATOR, 200_000_000, "deposit")
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)

    assert await registry.buy_tokens(token.token_id, CREATOR, 100) == (False, 0)
    assert await registry.get_holder_info(token.token_id, CREATOR) is None
    assert ledger.balance(CREATOR) == Decimal("200.000000")


async def test_buytoken_command_charges_and_formats_cost(tmp_path, ledger, monkeypatch):
    """Regression: `/buytoken` used to grant the shares itself in the handler
    and then call a 3-argument `ledger.debit()` that does not exist (TypeError
    AFTER the grant), and its success text formatted the Decimal cost with a
    `{:.2f}` spec that crashed on the string it was given. The command must
    charge through the registry, debit exactly once and answer with the cost.
    """
    from test_handlers import Message

    from bot.creator_tokens import CreatorTokenRegistry
    from bot.handlers import _common as common
    from bot.handlers import cmd_buy_token

    ledger.credit(BUYER_A, 200_000_000, "deposit")  # $200
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)  # $0.10 per token
    monkeypatch.setattr(common, "creator_tokens", registry)

    msg = Message(text=f"/buytoken {token.token_id} 400", from_id=BUYER_A)
    await cmd_buy_token(msg)

    assert len(msg.answers) == 1, msg.answers
    text = msg.answers[0][0]
    assert "400" in text and "$40.00" in text, text
    assert ledger.balance(BUYER_A) == Decimal("160.000000")
    info = await registry.get_holder_info(token.token_id, BUYER_A)
    assert info["balance"] == 400


async def test_buytoken_command_reports_failure_without_granting(
    tmp_path, ledger, monkeypatch
):
    """The handler must surface the registry's refusal and leave the balance
    and the supply untouched — the old code minted the shares first."""
    from test_handlers import Message

    from bot.creator_tokens import CreatorTokenRegistry
    from bot.handlers import _common as common
    from bot.handlers import cmd_buy_token
    from bot.i18n import t

    broke = 555003
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    monkeypatch.setattr(common, "creator_tokens", registry)

    msg = Message(text=f"/buytoken {token.token_id} 400", from_id=broke)
    await cmd_buy_token(msg)

    assert [a[0] for a in msg.answers] == [t("ru", "token_buy_failed")]
    assert await registry.get_holder_info(token.token_id, broke) is None
    assert (await registry.get_token_info(token.token_id))["total_held"] == 0


async def test_concurrent_buys_cannot_overshoot_supply(ledger):
    """Two connections buying the last shares: the FOR UPDATE lock on the token
    row plus the supply check must let exactly one through."""
    import threading

    from bot.ledger import Ledger

    ledger.credit(BUYER_A, 200_000_000_000, "deposit")
    ledger.credit(BUYER_B, 200_000_000_000, "deposit")
    token_id = "ct_race_1"
    ledger.creator_token_upsert({
        "token_id": token_id, "creator_tg_id": CREATOR, "name": "Race",
        "symbol": "RCE", "total_supply": 1_000_000, "price_micro": 100_000,
        "created_at": 0, "total_revenue_micro": 0,
        "total_dividends_paid_micro": 0, "dividend_per_token_micro": 0,
    })
    before = ledger._conn.execute("SELECT SUM(balance) AS s FROM users").fetchone()["s"]
    # Drop the read transaction: an open ACCESS SHARE on users would make each
    # worker Ledger's ensure_schema (ALTER TABLE) wait for lock_timeout.
    ledger._conn.rollback()

    results = {}

    def buy(user, label):
        conn = Ledger(TEST_DB_URL)
        try:
            results[label] = conn.creator_token_buy(token_id, user, 600_000, 60_000_000_000)[0]
        finally:
            conn.close()

    threads = [
        threading.Thread(target=buy, args=(BUYER_A, "a")),
        threading.Thread(target=buy, args=(BUYER_B, "b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == ["ok", "supply"], results
    total_held = ledger.creator_holders_summary(token_id)[0]
    assert total_held == 600_000
    after = ledger._conn.execute("SELECT SUM(balance) AS s FROM users").fetchone()["s"]
    assert int(after) == int(before)  # buyer's debit == creator's credit


async def test_sell_flow(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(BUYER_A, 200_000_000, "deposit")
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    await registry.buy_tokens(token.token_id, BUYER_A, 500)

    success, proceeds = await registry.sell_tokens(token.token_id, BUYER_A, 200)
    assert success is True
    assert proceeds == 200 * 100_000
    assert (await registry.get_holder_info(token.token_id, BUYER_A))["balance"] == 300

    # Selling more than held is refused and leaves the balance intact.
    assert await registry.sell_tokens(token.token_id, BUYER_A, 999_999) == (False, 0)
    assert (await registry.get_holder_info(token.token_id, BUYER_A))["balance"] == 300
    assert await registry.sell_tokens(token.token_id, BUYER_B, 1) == (False, 0)
    assert await registry.sell_tokens("missing", BUYER_A, 1) == (False, 0)


async def test_dividend_distribution_is_proportional(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(BUYER_A, 200_000_000, "deposit")
    ledger.credit(BUYER_B, 200_000_000, "deposit")
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    await registry.buy_tokens(token.token_id, BUYER_A, 400)
    await registry.buy_tokens(token.token_id, BUYER_B, 600)

    record = await registry.distribute_dividend(token.token_id, 5_000_000)
    assert record is not None
    assert record.amount_micro == 5_000_000
    assert record.dividend_per_token == pytest.approx(5_000.0)
    assert record.total_holders == 2

    assert (await registry.get_holder_info(token.token_id, BUYER_A))[
        "pending_dividends_micro"
    ] == 2_000_000
    assert (await registry.get_holder_info(token.token_id, BUYER_B))[
        "pending_dividends_micro"
    ] == 3_000_000

    token_info = await registry.get_token_info(token.token_id)
    assert token_info["total_revenue_micro"] == 5_000_000
    assert token_info["total_dividends_paid_micro"] == 5_000_000


async def test_dividend_truncation_keeps_dust(tmp_path, ledger):
    """int()-style truncation: per-holder dust stays undistributed."""
    from bot.creator_tokens import CreatorTokenRegistry

    third = BUYER_A + 1
    for uid in (BUYER_A, BUYER_B, third):
        ledger.credit(uid, 1_000_000, "deposit")
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    await registry.buy_tokens(token.token_id, BUYER_A, 1)
    await registry.buy_tokens(token.token_id, BUYER_B, 1)
    await registry.buy_tokens(token.token_id, third, 1)

    await registry.distribute_dividend(token.token_id, 1_000_001)
    # 1_000_001 / 3 = 333333.66… -> 333333 per holder
    for uid in (BUYER_A, BUYER_B, third):
        assert (await registry.get_holder_info(token.token_id, uid))[
            "pending_dividends_micro"
        ] == 333_333


async def test_dividend_without_holders(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)

    assert await registry.distribute_dividend(token.token_id, 5_000_000) is None
    assert await registry.distribute_dividend("missing", 5_000_000) is None


async def test_claim_flow(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(BUYER_A, 200_000_000, "deposit")
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    await registry.buy_tokens(token.token_id, BUYER_A, 400)

    assert await registry.claim_dividends(token.token_id, BUYER_A) == 0

    await registry.distribute_dividend(token.token_id, 5_000_000)
    claimed = await registry.claim_dividends(token.token_id, BUYER_A)
    assert claimed == 5_000_000  # sole holder: 400/400 tokens

    info = await registry.get_holder_info(token.token_id, BUYER_A)
    assert info["pending_dividends_micro"] == 0
    assert info["last_claim"] > 0

    # Double claim pays nothing.
    assert await registry.claim_dividends(token.token_id, BUYER_A) == 0
    assert await registry.claim_dividends(token.token_id, BUYER_B) == 0
    assert await registry.claim_dividends("missing", BUYER_A) == 0


async def test_state_survives_new_registry_instance(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    ledger.credit(BUYER_A, 200_000_000, "deposit")
    first = CreatorTokenRegistry(tmp_path / "state_a")
    token = await _make_token(first)
    await first.buy_tokens(token.token_id, BUYER_A, 400)
    await first.distribute_dividend(token.token_id, 5_000_000)

    # A fresh instance over an empty state dir reads the same Postgres rows.
    second = CreatorTokenRegistry(tmp_path / "state_b")
    info = await second.get_token_info(token.token_id)
    assert info["total_held"] == 400
    assert info["total_revenue_micro"] == 5_000_000
    assert (await second.get_holder_info(token.token_id, BUYER_A))[
        "pending_dividends_micro"
    ] == 5_000_000  # sole holder: 400/400 tokens


async def test_legacy_json_imported_once(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    state = tmp_path / "legacy"
    state.mkdir()
    token = {
        "token_id": "ct_1_1700000000",
        "creator_tg_id": CREATOR,
        "name": "Old Token",
        "symbol": "OLD",
        "total_supply": 1_000_000,
        "price_micro": 100_000,
        "created_at": 1700000000.0,
        "total_revenue_micro": 700,
        "total_dividends_paid_micro": 700,
        "dividend_per_token_micro": 0.7,
    }
    holder = {
        "token_id": token["token_id"],
        "holder_tg_id": BUYER_A,
        "balance": 250,
        "last_dividend_claim": 0.0,
        "pending_dividends_micro": 175,
    }
    (state / "creator_tokens.json").write_text(json.dumps([token]), encoding="utf-8")
    (state / "token_holders.json").write_text(
        json.dumps({token["token_id"]: {"789012": holder}}), encoding="utf-8"
    )

    registry = CreatorTokenRegistry(state)
    info = await registry.get_token_info(token["token_id"])
    assert info["name"] == "Old Token"
    assert info["total_held"] == 250
    assert (await registry.get_holder_info(token["token_id"], BUYER_A))[
        "pending_dividends_micro"
    ] == 175

    # Legacy files are archived, not deleted.
    assert (state / "creator_tokens.json.migrated").exists()
    assert (state / "token_holders.json.migrated").exists()
    assert not (state / "creator_tokens.json").exists()

    # A second registry over the same dir must not duplicate the import.
    again = CreatorTokenRegistry(state)
    assert (await again.get_token_info(token["token_id"]))["total_held"] == 250


async def test_handlers_bind_store_instances():
    """Regression: handlers must call real stores, not the bare modules.

    bot.handlers._common used to bind bot.creator_tokens / bot.recurring
    (modules), so /createtoken, /buytoken, /claim, /subscribe, /subscriptions
    and /cancelsub crashed with AttributeError in production.
    """
    from bot import creator_tokens as ct_module
    from bot import recurring as recurring_module
    from bot.handlers import _common

    assert _common.creator_tokens is ct_module.registry
    assert hasattr(_common.creator_tokens, "create_token")
    assert _common.recurring is recurring_module.store
    assert all(
        hasattr(_common.recurring, name)
        for name in ("create", "cancel", "list_for_user", "get_due", "mark_executed")
    )
async def test_claim_command_formats_the_dividend_it_paid(tmp_path, ledger, monkeypatch):
    """/claim credited the dividend and then formatted it with `{amount:.2f}` while
    handing that spec `_fmt()`'s text — ValueError after the money moved, so the
    user saw nothing and the balance was already higher. The reply has to name
    what was paid."""
    from test_handlers import Message

    from bot.creator_tokens import CreatorTokenRegistry
    from bot.handlers import _common as common
    from bot.handlers import cmd_claim_dividends

    ledger.credit(BUYER_A, 200_000_000_000, "deposit")  # $200k
    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    monkeypatch.setattr(common, "creator_tokens", registry)

    assert (await registry.buy_tokens(token.token_id, BUYER_A, 1_000_000))[0] is True
    after_buy = ledger.balance(BUYER_A)
    assert await registry.distribute_dividend(token.token_id, 10_000_000) is not None

    msg = Message(text=f"/claim {token.token_id}", from_id=BUYER_A)
    await cmd_claim_dividends(msg)

    text = msg.answers[0][0]
    assert "$10.00" in text, text
    assert ledger.balance(BUYER_A) == after_buy + Decimal("10")
