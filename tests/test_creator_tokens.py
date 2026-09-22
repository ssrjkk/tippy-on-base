"""Creator tokens: Postgres persistence, dividend math, legacy JSON import."""

import json

import pytest

pytestmark = pytest.mark.asyncio

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

    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)

    success, cost = await registry.buy_tokens(token.token_id, BUYER_A, 400)
    assert success is True
    assert cost == (400 * 100_000) // 1_000_000

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


async def test_sell_flow(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    await registry.buy_tokens(token.token_id, BUYER_A, 500)

    success, proceeds = await registry.sell_tokens(token.token_id, BUYER_A, 200)
    assert success is True
    assert proceeds == (200 * 100_000) // 1_000_000
    assert (await registry.get_holder_info(token.token_id, BUYER_A))["balance"] == 300

    # Selling more than held is refused and leaves the balance intact.
    assert await registry.sell_tokens(token.token_id, BUYER_A, 999_999) == (False, 0)
    assert (await registry.get_holder_info(token.token_id, BUYER_A))["balance"] == 300
    assert await registry.sell_tokens(token.token_id, BUYER_B, 1) == (False, 0)
    assert await registry.sell_tokens("missing", BUYER_A, 1) == (False, 0)


async def test_dividend_distribution_is_proportional(tmp_path, ledger):
    from bot.creator_tokens import CreatorTokenRegistry

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

    registry = CreatorTokenRegistry(tmp_path)
    token = await _make_token(registry)
    await registry.buy_tokens(token.token_id, BUYER_A, 1)
    await registry.buy_tokens(token.token_id, BUYER_B, 1)
    third = BUYER_A + 1
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
