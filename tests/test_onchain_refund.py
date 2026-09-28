"""Cancelled-market refunds and bulk settlement for on-chain markets.

An abandoned market used to trap its escrow USDC forever: the contract's
``claimCancelled`` was reachable but nothing in the bot ever called it, and
``/oc_redeem`` only knew how to take winnings out of a *resolved* pool. This
file pins the whole pull path — the signer wrappers, the ``/oc_redeem`` routing
decisions, the bulk sweep, the sell pre-checks and the gas top-up that makes
the exits reachable in the first place.

Mocks stop at the RPC boundary: ABI decoding, address derivation, LMSR math,
gas bookkeeping and every SQL statement run for real.
"""

import json
import time
import types
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from eth_abi import encode
from eth_account import Account
from hexbytes import HexBytes
from web3 import Web3

from bot import config, i18n
from bot import onchain_market as om
from bot.chain import network
from bot.handlers import _common as common
from bot.handlers import onchain

KEY = "0x" + "5a" * 32
ACC = Account.from_key(KEY)
CREATOR, TRADER = 7301, 7302
MARKET_ADDR = Web3.to_checksum_address("0x" + "99" * 20)
OTHER_TOKEN = Web3.to_checksum_address("0x" + "3c" * 20)
TX_HASH = HexBytes("0x" + "77" * 32)
DRIP_ETH = Decimal("0.0002")
DRIP_WEI = int(DRIP_ETH * Decimal(10**18))
DRIP_HASH = "0x" + "ab" * 32


def usdc_transfer(to: str, value: int, token: str | None = None) -> dict:
    """A real USDC Transfer log, shaped like an RPC receipt entry.

    Decoded by the production ABI in `_usdc_received`, so a mistake in the
    topic/data layout fails here instead of silently paying zero.
    """
    pad = lambda a: HexBytes("0x" + "0" * 24 + Web3.to_checksum_address(a)[2:])  # noqa: E731
    return {
        "address": token or config.USDC_ADDRESS,
        "topics": [
            Web3.keccak(text="Transfer(address,address,uint256)"),
            pad("0x" + "11" * 20),
            pad(to),
        ],
        "data": HexBytes(encode(["uint256"], [value])),
        "blockNumber": 1,
        "blockHash": HexBytes("0x" + "44" * 32),
        "transactionHash": TX_HASH,
        "transactionIndex": 0,
        "logIndex": 0,
    }


class _Receipt(dict):
    """web3 hands back an AttributeDict: dict payload plus attribute access."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


class _ContractStub:
    """The slice of a web3 contract handle that the on-chain signer uses."""

    def __init__(self):
        self.reads: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, tuple, dict]] = []
        self.readings: dict[tuple[str, tuple], object] = {}
        self.state = {"balance": om._NEEDED_GAS_WEI, "status": 1, "logs": [],
                      "sent": [], "waits": [], "receipt_error": None}

    def _call(self, name, args):
        outer = self

        class Call:
            def call(self):
                outer.reads.append((name, args))
                key = (name, args)
                if key not in outer.readings:
                    raise AssertionError(f"no reading configured for {key}")
                return outer.readings[key]

            def build_transaction(self, kw):
                outer.writes.append((name, args, kw))
                return dict(kw, to=MARKET_ADDR, value=0, data=b"", chainId=8453)

        return Call()

    @property
    def functions(self):
        outer = self

        class NS:
            def __getattr__(self, name):
                return lambda *args: outer._call(name, args)

        return NS()


@pytest.fixture(autouse=True)
def _no_shared_state():
    """The read caches and the drip cooldown are process-wide; a stale entry
    would let one test answer another test's RPC call."""
    om._market_info_cache.clear()
    om._prices_cache.clear()
    om._cancel_claim_cache.clear()
    om._last_drip.clear()
    yield


@pytest.fixture()
def chain(monkeypatch):
    """RPC-boundary stub for bot/onchain_market.py. Returns the contract.

    Gas economics are pinned here too: a developer .env must not decide
    whether a test's wallet can afford its own exit.
    """
    monkeypatch.setattr(config, "GAS_DRIP_ETH", DRIP_ETH)
    monkeypatch.setattr(om, "_NEEDED_GAS_WEI", int(Decimal("0.00005") * Decimal(10**18)))
    contract = _ContractStub()
    monkeypatch.setattr(network, "assert_base_chain_sync", lambda: 8453)
    monkeypatch.setattr(network, "eip1559_fees_sync", lambda **kw: {
        "base_fee_gwei": 0.05, "priority_gwei": 0.01, "max_fee_gwei": 0.11,
    })
    signer = lambda tx, private_key=None: types.SimpleNamespace(  # noqa: E731
        raw_transaction=b"\x09" * 32)
    def wait_for_receipt(tx_hash, timeout=60):
        state = contract.state
        state["waits"].append(tx_hash)
        if state["receipt_error"] is not None:
            raise state["receipt_error"]
        return _Receipt(status=state["status"], logs=state["logs"],
                        transactionHash=TX_HASH)

    fake = types.SimpleNamespace(eth=types.SimpleNamespace(
        account=types.SimpleNamespace(
            from_key=lambda k: types.SimpleNamespace(address=ACC.address),
            sign_transaction=signer,
        ),
        get_transaction_count=lambda a, p="latest": 4,
        get_balance=lambda a: contract.state["balance"],
        send_raw_transaction=lambda raw: (contract.state["sent"].append(raw), TX_HASH)[1],
        wait_for_transaction_receipt=wait_for_receipt,
    ))
    monkeypatch.setattr(om, "_w3", lambda: fake)
    monkeypatch.setattr(om, "_market_contract", lambda w3=None: contract)
    return contract


# ------------------------------------------------ signer wrappers (chain layer)

@pytest.mark.asyncio
async def test_claim_cancelled_sends_refund_and_reports_payout(chain):
    chain.state["logs"] = [
        usdc_transfer(ACC.address, 400_000),
        usdc_transfer(ACC.address, 100_000),
        usdc_transfer("0x" + "12" * 20, 999_999),  # someone else's Transfer
        usdc_transfer(ACC.address, 7, token=OTHER_TOKEN),  # not USDC
    ]
    payout = await om.claim_cancelled(12, KEY)

    assert payout == 500_000, "only USDC paid to this wallet counts"
    fn, args, kw = chain.writes[-1]
    assert (fn, args) == ("claimCancelled", (12,))
    assert kw["from"] == ACC.address and kw["nonce"] == 4
    assert kw["gas"] == 300_000
    assert {"maxFeePerGas", "maxPriorityFeePerGas"} <= set(kw)
    assert chain.state["sent"] == [b"\x09" * 32]


@pytest.mark.asyncio
async def test_claim_cancelled_reverted_tx_raises(chain):
    chain.state["status"] = 0
    with pytest.raises(RuntimeError, match="claimCancelled reverted"):
        await om.claim_cancelled(12, KEY)


@pytest.mark.asyncio
async def test_claim_cancelled_many_batches_ids_and_scales_gas(chain):
    chain.state["logs"] = [usdc_transfer(ACC.address, 800_000)]
    payout = await om.claim_cancelled_many([3, 4, 5], KEY)

    fn, args, kw = chain.writes[-1]
    assert (fn, args) == ("claimCancelledMany", ([3, 4, 5],))
    assert kw["gas"] == 300_000 * 3
    assert payout == 800_000


@pytest.mark.asyncio
async def test_claim_cancelled_many_skips_chain_when_nothing_to_claim(chain):
    assert await om.claim_cancelled_many([], KEY) == 0
    assert chain.writes == [] and chain.state["sent"] == []


@pytest.mark.asyncio
async def test_cancel_claim_state_reads_reserve_and_caches(chain):
    chain.readings = {
        ("claimRatePerShare", (7,)): 1234,
        ("unclaimedEscrowMicro", (7,)): 5_000_000,
    }
    assert await om.cancel_claim_state(7) == (1234, 5_000_000)
    assert await om.cancel_claim_state(7) == (1234, 5_000_000)
    assert len(chain.reads) == 2, "second read must come from the cache"


@pytest.mark.asyncio
async def test_outcome_shares_uses_erc1155_token_id(chain):
    chain.readings = {("balanceOf", (ACC.address, 7 * 256 + 2)): 42}
    assert await om.outcome_shares(7, 2, ACC.address) == 42
    assert chain.reads == [("balanceOf", (ACC.address, 7 * 256 + 2))]


@pytest.mark.asyncio
async def test_holder_shares_sums_every_outcome(chain):
    chain.readings = {
        ("balanceOf", (ACC.address, 7 * 256 + 0)): 10,
        ("balanceOf", (ACC.address, 7 * 256 + 1)): 20,
        ("balanceOf", (ACC.address, 7 * 256 + 2)): 30,
    }
    assert await om.holder_shares(7, 3, ACC.address) == 60
    assert len(chain.reads) == 3


@pytest.mark.asyncio
async def test_wrong_chain_refuses_to_build_a_claim_tx(monkeypatch, chain):
    def boom():
        raise RuntimeError("chain guard: refusing to sign")

    monkeypatch.setattr(network, "assert_base_chain_sync", boom)
    with pytest.raises(RuntimeError, match="refusing to sign"):
        await om.claim_cancelled(12, KEY)
    assert chain.state["sent"] == []


# ------------------------------------------- exits must not need user ETH (E2)

@pytest.mark.asyncio
async def test_exit_paths_drip_gas_before_signing(ledger, chain, monkeypatch):
    """A wallet created by /oc_buy holds no ETH: the first exit funds it, and
    the funding tx is confirmed before that exit is signed."""
    def drip(addr, wei):
        chain.state["balance"] = wei  # a mined drip leaves the wallet able to pay
        return DRIP_HASH

    drips = AsyncMock(side_effect=drip)
    monkeypatch.setattr("bot.chain.transfers.send_eth", drips)
    chain.state["balance"] = 0
    chain.state["logs"] = [usdc_transfer(ACC.address, 250_000)]

    assert await om.sell(7, 0, 10, 1, KEY)
    await om.redeem(7, KEY)
    await om.claim_cancelled(7, KEY)

    assert drips.await_count == 1, "one funded wallet serves all three exits"
    assert drips.await_args.args == (ACC.address, DRIP_WEI)
    assert chain.state["waits"] == [DRIP_HASH] + [TX_HASH] * 3, \
        "the drip clears before the first exit is signed"
    assert [w[0] for w in chain.writes] == ["sell", "redeem", "claimCancelled"]


@pytest.mark.asyncio
async def test_failed_drip_returns_the_budget_slot(ledger, chain, monkeypatch):
    monkeypatch.setattr("bot.chain.transfers.send_eth",
                        AsyncMock(side_effect=RuntimeError("RPC down")))
    chain.state["balance"] = 0
    with pytest.raises(RuntimeError, match="RPC down"):
        await om.claim_cancelled(7, KEY)
    assert _drips_booked(ledger) == 0


@pytest.mark.asyncio
async def test_unconfirmed_drip_spends_nothing_more(ledger, chain, monkeypatch):
    """The ETH is already on its way when the receipt goes missing: releasing
    the booking or re-dripping would pay the same wallet twice."""
    drips = AsyncMock(return_value=DRIP_HASH)
    monkeypatch.setattr("bot.chain.transfers.send_eth", drips)
    chain.state["balance"] = 0
    chain.state["receipt_error"] = TimeoutError("no receipt")

    with pytest.raises(RuntimeError, match="still confirming"):
        await om.claim_cancelled(7, KEY)
    assert _drips_booked(ledger) == 1, "a sent drip keeps its booked slot"
    assert chain.state["waits"] == [DRIP_HASH]

    with pytest.raises(RuntimeError, match="cooldown"):
        await om.claim_cancelled(7, KEY)
    assert drips.await_count == 1


@pytest.mark.asyncio
async def test_wallet_that_can_pay_is_never_dripped(chain, monkeypatch):
    drips = AsyncMock(return_value="0x" + "ab" * 32)
    monkeypatch.setattr("bot.chain.transfers.send_eth", drips)
    chain.state["logs"] = [usdc_transfer(ACC.address, 1)]
    await om.claim_cancelled(7, KEY)
    assert drips.await_count == 0


# ------------------------------------------------------------ handler doubles


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))


class _Answered:
    """What `message.answer()` hands back, so `edit_text` is checkable."""

    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **kw):
        self.texts.append(text)


class Msg:
    def __init__(self, text="", from_id=TRADER):
        self.text = text
        self.from_user = types.SimpleNamespace(id=from_id)
        self.answers = []
        self.edited = []

    async def answer(self, text=None, **kw):
        a = _Answered()
        self.edited.append(a)
        self.answers.append(text)
        return a

    @property
    def last_edit(self):
        return self.edited[-1].texts[-1] if self.edited and self.edited[-1].texts else None


def _market_info(*, resolved=False, cancelled=False, winner=0):
    return {"resolved": resolved, "cancelled": cancelled, "winning_outcome": winner}


@pytest.fixture()
def handlers(monkeypatch):
    """Isolate the redeem/sell handlers from chain, wallet and throttling.

    Every on-chain read/write is an AsyncMock; the registry underneath stays
    real Postgres, so routing bugs can't hide behind a stubbed market row.
    """
    monkeypatch.setattr(common.config, "OUTCOME_MARKET_ADDRESS", MARKET_ADDR)
    monkeypatch.setattr(common, "_throttle", AsyncMock(return_value=None))
    monkeypatch.setattr(common, "user_lang", AsyncMock(return_value="ru"))
    monkeypatch.setattr(onchain, "_wallet_key",
                        AsyncMock(return_value=(ACC.address, KEY)))
    env = {
        "info": _market_info(),
        "held": 100,
        "claim_state": (1000, 5_000_000),
        "get_market_info": AsyncMock(),
        "outcome_shares": AsyncMock(),
        "holder_shares": AsyncMock(),
        "cancel_claim_state": AsyncMock(),
        "redeem": AsyncMock(return_value=900_000),
        "claim_cancelled": AsyncMock(return_value=400_000),
        "redeem_many": AsyncMock(return_value=10),
        "claim_cancelled_many": AsyncMock(return_value=10),
        "sell": AsyncMock(return_value="0x" + "11" * 32),
    }
    env["get_market_info"].side_effect = lambda mid: env["info"]
    env["outcome_shares"].side_effect = lambda mid, out, addr: env["held"]
    env["holder_shares"].side_effect = lambda mid, n, addr: env["held"]
    env["cancel_claim_state"].side_effect = lambda mid: env["claim_state"]
    for name, mock in env.items():
        if isinstance(mock, AsyncMock):
            monkeypatch.setattr(onchain.om, name, mock)
    return env


def save_market(ledger, mid, options=("Yes", "No"), close_at=None):
    ledger.save_onchain_market(
        mid, CREATOR, f"m{mid}", list(options),
        int(time.time()) + 3600 if close_at is None else close_at,
    )


def _drips_booked(ledger) -> int:
    """Rows the bot booked against today's UTC gas-drip budget."""
    row = ledger._conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS n FROM gas_drips WHERE day = %s",
        (int(time.time()) // 86400,),
    ).fetchone()
    return int(row["n"])


# ------------------------------------------------------- /oc_redeem routing

@pytest.mark.asyncio
async def test_redeem_unknown_market(ledger, handlers):
    m = Msg("/oc_redeem 99")
    await onchain.cmd_oc_redeem(m)
    assert m.answers == [i18n.t("ru", "oc_unknown")]
    handlers["redeem"].assert_not_awaited()


@pytest.mark.asyncio
async def test_redeem_live_market_is_refused_before_signing(ledger, handlers):
    """A live pool has nothing to pull; without the pre-check the user gets a
    raw smart-contract revert string instead of an explanation."""
    save_market(ledger, 7)
    m = Msg("/oc_redeem 7")
    await onchain.cmd_oc_redeem(m)
    assert m.answers[-1] == i18n.t("ru", "oc_redeem_not_ready", id=7)
    handlers["redeem"].assert_not_awaited()
    handlers["claim_cancelled"].assert_not_awaited()


@pytest.mark.asyncio
async def test_redeem_cancelled_market_claims_the_refund(ledger, handlers):
    save_market(ledger, 7)
    handlers["info"] = _market_info(cancelled=True)
    m = Msg("/oc_redeem 7")
    await onchain.cmd_oc_redeem(m)

    handlers["claim_cancelled"].assert_awaited_once_with(7, KEY)
    handlers["redeem"].assert_not_awaited()
    assert m.last_edit == i18n.t(
        "ru", "oc_refunded", amount=common._fmt(400_000), addr=ACC.address)


@pytest.mark.asyncio
async def test_redeem_resolved_market_pays_winnings(ledger, handlers):
    save_market(ledger, 7)
    handlers["info"] = _market_info(resolved=True, winner=1)
    m = Msg("/oc_redeem 7")
    await onchain.cmd_oc_redeem(m)

    handlers["outcome_shares"].assert_awaited_once_with(7, 1, ACC.address)
    handlers["redeem"].assert_awaited_once_with(7, KEY)
    handlers["claim_cancelled"].assert_not_awaited()
    assert m.last_edit == i18n.t(
        "ru", "oc_redeemed", amount=common._fmt(900_000), addr=ACC.address)


@pytest.mark.asyncio
async def test_redeem_checks_every_outcome_on_a_cancelled_market(ledger, handlers):
    """A refund burns all outcome tokens, so holdings must be summed — reading
    one outcome would hide a holder who bought the losing side."""
    save_market(ledger, 7)
    handlers["info"] = _market_info(cancelled=True)
    handlers["held"] = 0
    m = Msg("/oc_redeem 7")
    await onchain.cmd_oc_redeem(m)
    handlers["holder_shares"].assert_awaited_once_with(7, 2, ACC.address)
    assert m.answers[-1] == i18n.t("ru", "oc_no_shares")


@pytest.mark.asyncio
async def test_redeem_with_zero_holdings_does_not_send(ledger, handlers):
    """redeem/claim on an empty balance reverts with NothingToClaim — the
    pre-check saves the gas and answers something readable instead."""
    save_market(ledger, 7)
    handlers["info"] = _market_info(resolved=True)
    handlers["held"] = 0
    m = Msg("/oc_redeem 7")
    await onchain.cmd_oc_redeem(m)
    assert m.answers[-1] == i18n.t("ru", "oc_no_shares")
    handlers["redeem"].assert_not_awaited()


@pytest.mark.asyncio
async def test_redeem_reports_chain_failure(ledger, handlers):
    save_market(ledger, 7)
    handlers["info"] = _market_info(cancelled=True)
    handlers["claim_cancelled"].side_effect = RuntimeError(
        "replacement transaction underpriced")
    m = Msg("/oc_redeem 7")
    await onchain.cmd_oc_redeem(m)
    assert "replacement transaction underpriced" in m.last_edit


@pytest.mark.asyncio
async def test_redeem_without_id_sweeps_everything(ledger, handlers, monkeypatch):
    settle = AsyncMock()
    monkeypatch.setattr(onchain, "_settle_all", settle)
    await onchain.cmd_oc_redeem(Msg("/oc_redeem"))
    settle.assert_awaited_once()


@pytest.mark.asyncio
async def test_redeem_rejects_a_garbage_id(ledger, handlers):
    m = Msg("/oc_redeem banana")
    await onchain.cmd_oc_redeem(m)
    assert m.answers == [i18n.t("ru", "oc_format_redeem")]


# ------------------------------------------------------- bulk settlement

@pytest.mark.asyncio
async def test_settle_targets_only_lists_payoutable_markets(ledger, handlers):
    now = int(time.time())
    for mid in (1, 2, 3, 4, 5):
        save_market(ledger, mid, close_at=now - 10)
    # 1 cancelled and refundable, 2 cancelled with a drained reserve,
    # 3 resolved with shares, 4 resolved but already sold, 5 still live.
    states = {
        1: _market_info(cancelled=True),
        2: _market_info(cancelled=True),
        3: _market_info(resolved=True),
        4: _market_info(resolved=True),
        5: _market_info(),
    }
    holdings = {1: 50, 2: 50, 3: 50, 4: 0, 5: 50}

    async def info(mid):
        return states[int(mid)]

    async def held(mid, *args):
        return holdings[int(mid)]

    async def claim_state(mid):
        return (0, 0) if int(mid) == 2 else (1000, 5_000_000)

    handlers["get_market_info"].side_effect = info
    handlers["holder_shares"].side_effect = held
    handlers["outcome_shares"].side_effect = held
    handlers["cancel_claim_state"].side_effect = claim_state

    redeem_ids, claim_ids = await onchain._settle_targets(TRADER, ACC.address)
    # A single empty entry reverts the whole batch, so #2 and #4 must stay out.
    assert redeem_ids == [3] and claim_ids == [1]


@pytest.mark.asyncio
async def test_settle_targets_survives_one_unreadable_market(ledger, handlers):
    save_market(ledger, 1)
    save_market(ledger, 2)

    async def info(mid):
        if int(mid) == 2:
            raise RuntimeError("rpc down")
        return _market_info(resolved=True)

    handlers["get_market_info"].side_effect = info
    redeem_ids, claim_ids = await onchain._settle_targets(TRADER, ACC.address)
    assert redeem_ids == [1], "one bad read must not abort the scan"
    assert claim_ids == []


@pytest.mark.asyncio
async def test_settle_all_chunks_batches_and_totals(ledger, handlers):
    now = int(time.time())
    for mid in range(1, 8):  # 7 targets -> a full chunk of 5 plus a short one
        save_market(ledger, mid, close_at=now - 10)
    handlers["info"] = _market_info(resolved=True)
    handlers["held"] = 100
    # First chunk blows up, the second pays: a failure must not lose the rest.
    handlers["redeem_many"].side_effect = [RuntimeError("out of gas"), 700_000]

    m = Msg("/oc_redeem")
    await onchain._settle_all(m, "ru")

    assert handlers["redeem_many"].await_count == 2
    assert len(handlers["redeem_many"].await_args_list[0].args[0]) == onchain._SETTLE_CHUNK
    text = m.last_edit
    assert "❌" in text and "✅" in text
    assert common._fmt(700_000) in text
    assert i18n.t("ru", "oc_settle_winnings") in text


@pytest.mark.asyncio
async def test_settle_all_refunds_cancelled_markets(ledger, handlers):
    save_market(ledger, 4)
    handlers["info"] = _market_info(cancelled=True)
    handlers["claim_cancelled_many"].return_value = 600_000

    m = Msg("/oc_redeem")
    await onchain._settle_all(m, "ru")

    handlers["claim_cancelled_many"].assert_awaited_once_with([4], KEY)
    handlers["redeem_many"].assert_not_awaited()
    assert i18n.t("ru", "oc_settle_refunds") in m.last_edit


@pytest.mark.asyncio
async def test_settle_all_with_nothing_to_claim(ledger, handlers):
    save_market(ledger, 1)
    handlers["held"] = 0
    m = Msg("/oc_redeem")
    await onchain._settle_all(m, "ru")
    assert m.last_edit == i18n.t("ru", "oc_settle_empty")
    handlers["redeem_many"].assert_not_awaited()
    handlers["claim_cancelled_many"].assert_not_awaited()


@pytest.mark.asyncio
async def test_settle_all_respects_throttle(ledger, handlers, monkeypatch):
    monkeypatch.setattr(common, "_throttle", AsyncMock(return_value="slow down"))
    m = Msg("/oc_redeem")
    await onchain._settle_all(m, "ru")
    assert m.answers == ["slow down"], "cooldown text only — no scan, no sweep"
    handlers["redeem_many"].assert_not_awaited()
    handlers["claim_cancelled_many"].assert_not_awaited()


# ------------------------------------------------------- sell pre-checks (E3)

@pytest.mark.asyncio
async def test_sell_refuses_after_deadline(ledger, handlers):
    save_market(ledger, 7, close_at=int(time.time()) - 1)
    ok, text = await onchain._sell_core(TRADER, 7, 0, 50, "ru")
    assert ok is False
    assert text == i18n.t("ru", "market_trade_deadline")
    handlers["sell"].assert_not_awaited()


@pytest.mark.asyncio
async def test_sell_refuses_settled_market(ledger, handlers):
    """The contract reverts with AlreadyResolved/AlreadyCancelled; the caller
    is pointed at /oc_redeem instead of paying for a doomed tx."""
    save_market(ledger, 7)
    for state in (_market_info(resolved=True), _market_info(cancelled=True)):
        handlers["info"] = state
        ok, text = await onchain._sell_core(TRADER, 7, 0, 50, "ru")
        assert ok is False
        assert text == i18n.t("ru", "market_closed")
    handlers["sell"].assert_not_awaited()


@pytest.mark.asyncio
async def test_sell_records_a_negative_trade_row(ledger, handlers, monkeypatch):
    """E4: without the exit row the registry keeps reporting a sold position as
    a holding, and winner DMs chase traders who are already out."""
    monkeypatch.setattr(onchain, "_q_sync", lambda mid, n: [100_000_000, 100_000_000])
    monkeypatch.setattr(onchain, "_b_sync", lambda mid: 50_000_000)
    save_market(ledger, 7)
    handlers["held"] = 200

    ledger.record_onchain_trade(7, TRADER, 0, 300, "0x" + "aa" * 32)
    ok, text = await onchain._sell_core(TRADER, 7, 0, 50, "ru")
    assert ok is True

    rows = ledger.onchain_trades_for_outcome(7, 0)
    assert len(rows) == 1
    assert int(rows[0]["shares"]) == 300 - 200 * 50 // 100, "net of the 50% exit"
    assert handlers["sell"].await_args.args[2] == 100, "sold 50% of 200 shares"


# --------------------------------------------- registry invariants for DMs

@pytest.mark.asyncio
async def test_notify_holders_dms_net_holders_only(ledger, monkeypatch):
    """A trader who sold out must not be told to claim a refund they don't have."""
    monkeypatch.setattr(common, "user_lang", AsyncMock(return_value="ru"))
    bot = _Bot()
    ledger.record_onchain_trade(7, TRADER, 0, 100, "0x" + "aa" * 32)
    ledger.record_onchain_trade(7, TRADER, 0, -100, "0x" + "bb" * 32)
    ledger.record_onchain_trade(7, CREATOR, 1, 40, "0x" + "cc" * 32)

    await onchain._notify_holders(7, bot)

    assert [c for c, _ in bot.sent] == [CREATOR]
    assert i18n.t("ru", "oc_cancelled_dm", id=7, shares=common._fmt(40)) in [
        t for _, t in bot.sent]


@pytest.mark.asyncio
async def test_notify_holders_survives_a_blocked_bot(ledger, monkeypatch):
    class Dropping(_Bot):
        async def send_message(self, chat_id, text=None, **kw):
            raise RuntimeError("telegram 403: bot was blocked by the user")

    monkeypatch.setattr(common, "user_lang", AsyncMock(return_value="ru"))
    ledger.record_onchain_trade(7, CREATOR, 0, 10, "0x" + "aa" * 32)
    await onchain._notify_holders(7, Dropping())  # must not raise


@pytest.mark.asyncio
async def test_notify_holders_without_bot_is_a_noop(ledger):
    ledger.record_onchain_trade(7, CREATOR, 0, 10, "0x" + "aa" * 32)
    await onchain._notify_holders(7)


def test_redeem_help_documents_both_forms():
    """The bare /oc_redeem sweep is only discoverable if the help says so:
    the id form and the bulk form must both appear in every language."""
    for lang in i18n.LANGS:
        assert i18n.t(lang, "oc_format_redeem").count("/oc_redeem") == 2


def test_settle_chunk_size_is_smaller_than_the_gas_cap():
    """redeemMany/claimCancelledMany scale gas linearly with the batch, and the
    tx must stay inside a sane block gas limit."""
    assert 0 < onchain._SETTLE_CHUNK <= 5
    assert json.dumps([1, 2]) == "[1, 2]"  # sanity: options are stored as JSON
