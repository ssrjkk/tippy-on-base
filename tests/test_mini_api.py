"""Tests for the Mini App API endpoints (web/mini.py)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from web import server
from web.auth import COOKIE_NAME, make_session


def _auth(client, tg_id):
    """Attach a valid session cookie for tg_id."""
    client.cookies.set(COOKIE_NAME, make_session(tg_id))
    return client


_IDEM = {"Idempotency-Key": "test-key"}


@pytest.fixture()
def client(ledger, monkeypatch):
    from bot.ledger import AsyncLedger

    monkeypatch.setattr(server, "ledger", AsyncLedger(ledger))
    return TestClient(server.app)


# ── helpers ──────────────────────────────────────────────────────────

TG_USER = 1001
TG_OTHER = 1002


# ── tests ────────────────────────────────────────────────────────────

def test_mini_state_returns_balance(client, ledger):
    ledger.ensure_user(TG_USER, None)
    ledger.credit(TG_USER, 5_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.get("/api/mini/state")
    assert r.status_code == 200
    data = r.json()
    assert "balance_usdc" in data
    assert data["balance_usdc"] == 5.0
    assert data["tg_id"] == TG_USER


def test_mini_state_no_auth(client, ledger):
    ledger.ensure_user(TG_USER, None)

    r = client.get("/api/mini/state")
    assert r.status_code == 401


def test_mini_state_bets_carry_pools_and_chances(client, ledger):
    """The app renders the bet list from this payload, so the batched read must
    produce exactly what the per-row query did: same market, same option order,
    same pot and percentages."""
    ledger.ensure_user(TG_USER, "alice")
    ledger.ensure_user(TG_OTHER, "bob")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    ledger.credit(TG_OTHER, 10_000_000, "deposit")
    bid = ledger.create_bet(TG_USER, "Падение BTC?", ["Да", "Нет"])
    ledger.place_bet(bid, TG_USER, 0, 6_000_000)
    ledger.place_bet(bid, TG_OTHER, 1, 4_000_000)
    _auth(client, TG_USER)

    bets = client.get("/api/mini/state").json()["bets"]
    assert [b["id"] for b in bets] == [bid]
    bet = bets[0]
    assert bet["question"] == "Падение BTC?"
    assert bet["creator"] == TG_USER
    assert bet["pot_usdc"] == 10.0
    assert [(o["index"], o["label"]) for o in bet["options"]] == [(0, "Да"), (1, "Нет")]
    assert [o["pool_usdc"] for o in bet["options"]] == [6.0, 4.0]
    assert [o["chance_pct"] for o in bet["options"]] == [60.0, 40.0]


def test_mini_state_bet_read_does_not_grow_with_the_market_count(client, ledger):
    """/state once ran bet_totals() per market — one query per row on the hottest
    endpoint in the Mini App. bulk_market_views() is a fixed three."""

    def statements_for_call():
        seen = []
        real_execute = ledger._conn.execute

        def spy(query, *args, **kwargs):
            seen.append(query)
            return real_execute(query, *args, **kwargs)

        # Direct attribute juggling, not monkeypatch.undo(): the latter would also
        # tear down the `ledger` fixture's rebinding and the request would then
        # read the developer's live database.
        ledger._conn.execute = spy
        try:
            assert client.get("/api/mini/state").status_code == 200
        finally:
            del ledger._conn.execute
        return seen

    ledger.ensure_user(TG_USER, "alice")
    _auth(client, TG_USER)
    ledger.credit(TG_USER, 50_000_000, "deposit")
    ledger.create_bet(TG_USER, "Первый?", ["Да", "Нет"])
    first = statements_for_call()
    for i in range(4):
        ledger.create_bet(TG_USER, f"Рынок {i}?", ["Да", "Нет"])
    second = statements_for_call()
    assert len(second) == len(first), (
        f"/api/mini/state cost {len(second) - len(first)} more statements for 4 extra markets"
    )


def test_mini_tip_self_tip_rejected(client, ledger):
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/tip", json={"to": str(TG_USER), "amount": 1.0})
    assert r.status_code == 400
    assert "cannot tip yourself" in r.json()["detail"]


def test_mini_tip_unknown_user(client, ledger):
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/tip", json={"to": "ghost_user_xyz", "amount": 1.0})
    assert r.status_code == 404


def test_mini_tip_success(client, ledger):
    ledger.ensure_user(TG_USER, "alice")
    ledger.ensure_user(TG_OTHER, "bob")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/tip", json={"to": str(TG_OTHER), "amount": 2.5}, headers=_IDEM)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["new_balance"] == pytest.approx(7.5)


def test_mini_trade_invalid_market(client, ledger):
    ledger.ensure_user(TG_USER, None)
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/trade", json={
        "market_id": 99999, "option": 0, "amount": 1.0
    }, headers=_IDEM)
    assert r.status_code == 400


def test_mini_create_validation(client, ledger):
    ledger.ensure_user(TG_USER, None)
    ledger.credit(TG_USER, 100_000_000, "deposit")
    _auth(client, TG_USER)

    r = client.post("/api/mini/create", json={
        "kind": "market",
        "question": "Hi",
        "options": ["Yes"],
        "hours": 24,
    })
    assert r.status_code == 400


def test_mini_lang_valid(client, ledger):
    ledger.ensure_user(TG_USER, None)
    _auth(client, TG_USER)

    r = client.post("/api/mini/lang", json={"lang": "en"})
    assert r.status_code == 200
    assert r.json()["lang"] == "en"


def test_mini_lang_invalid(client, ledger):
    ledger.ensure_user(TG_USER, None)
    _auth(client, TG_USER)

    r = client.post("/api/mini/lang", json={"lang": "xx"})
    assert r.status_code == 400
    assert "unsupported language" in r.json()["detail"]


def test_mini_tip_enforces_max_cap(client, ledger, monkeypatch):
    """The Mini App must enforce MAX_TIP_USDC like the Telegram handlers do —
    before it used to route straight to the ledger with no cap."""
    from bot import config

    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000_000, "deposit")
    _auth(client, TG_USER)

    over = float(config.MAX_TIP_USDC) + 1
    r = client.post("/api/mini/tip", json={"to": str(TG_OTHER), "amount": over})
    assert r.status_code == 400
    assert "cap" in r.json()["detail"]


def test_mini_trade_enforces_max_cap(client, ledger, monkeypatch):
    from bot import config

    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 10_000_000_000, "deposit")
    _auth(client, TG_USER)

    over = float(config.MARKET_MAX_TRADE_USDC) + 1
    r = client.post("/api/mini/trade", json={
        "market_id": 99999, "option": 0, "amount": over,
    })
    assert r.status_code == 400
    assert "cap" in r.json()["detail"]


def test_mini_create_enforces_subsidy_caps(client, ledger, monkeypatch):
    """Markets must respect MARKET_MIN/MAX_SUBSIDY_USDC (previously any
    micro-positive subsidy passed, bypassing the Telegram handler's range)."""
    import web.mini
    from bot import config

    monkeypatch.setattr(web.mini.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, 100_000_000_000, "deposit")
    _auth(client, TG_USER)

    low = float(config.MARKET_MIN_SUBSIDY_USDC) / 100
    r = client.post("/api/mini/create", json={
        "kind": "market",
        "question": "Will it rain tomorrow?",
        "options": ["Yes", "No"],
        "hours": 24,
        "subsidy_usdc": low,
    })
    assert r.status_code == 400
    assert "minimum" in r.json()["detail"]

    high = float(config.MARKET_MAX_SUBSIDY_USDC) + 1
    r = client.post("/api/mini/create", json={
        "kind": "market",
        "question": "Will it rain tomorrow?",
        "options": ["Yes", "No"],
        "hours": 24,
        "subsidy_usdc": high,
    })
    assert r.status_code == 400
    assert "maximum" in r.json()["detail"]


def test_mini_money_throttle_blocks_rapid_repeat(client, ledger, monkeypatch):
    """Per-user money cooldown must apply to the Mini App (it previously only
    had the shared per-IP limiter). A second money action within the cooldown
    window returns 429, even though the first was a benign 404."""
    import web.mini

    monkeypatch.setattr(web.mini.config, "MONEY_CMD_COOLDOWN_SECONDS", 60)
    ledger.ensure_user(TG_USER, "alice")
    _auth(client, TG_USER)

    r1 = client.post("/api/mini/tip", json={"to": "ghost_user_xyz", "amount": 0.5})
    assert r1.status_code == 404  # passed the throttle, failed target resolution

    r2 = client.post("/api/mini/tip", json={"to": str(TG_OTHER), "amount": 0.5})
    assert r2.status_code == 429  # throttled by the per-user cooldown


# ── Mini App withdrawals: stage first, debit only on confirm ─────────

# A plain wallet: neither the zero address nor one of the bot's own.
EOA = "0x" + "ab" * 20
X402_ADDR = "0x" + "0" * 39 + "1"  # conftest pins this as the x402 pool


def _stage(client, address, amount):
    return client.post("/api/mini/withdraw",
                       json={"address": address, "amount": str(amount)})


def _confirm(client, token, yes=True):
    return client.post("/api/mini/withdraw/confirm",
                       json={"token": token, "confirm": yes})


def _queued_rows(ledger, tg_id):
    return ledger._conn.execute(
        "SELECT status, amount FROM tx_log WHERE tg_id = %s AND kind = 'withdraw'",
        (tg_id,),
    ).fetchall()


def _queue_withdrawals(ledger, tg_id, n):
    """Put n payouts in the batch queue without going through the API."""
    for _ in range(n):
        ledger._conn.execute(
            "INSERT INTO tx_log (kind, tg_id, counterparty, amount, note, status) "
            "VALUES ('withdraw', %s, %s, %s, 'fee=0', 'queued')",
            (tg_id, EOA, 1_000_000),
        )
    ledger._conn.commit()


async def _never_a_contract(addr):
    return False


async def _always_a_contract(addr):
    return True


@pytest.fixture(autouse=True)
def _withdraw_defaults(monkeypatch):
    """Cooldown off (several cases stage twice in one test) and no live RPC:
    the contract probe would otherwise reach for Base and trip the breaker."""
    import web.mini
    from bot.chain import network

    monkeypatch.setattr(web.mini.config, "MONEY_CMD_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(network, "is_contract", _never_a_contract)


def _wallet(client, ledger, micro):
    ledger.ensure_user(TG_USER, "alice")
    ledger.credit(TG_USER, micro, "deposit")
    _auth(client, TG_USER)


def test_mini_withdraw_requires_auth(client):
    assert _stage(client, EOA, 1).status_code == 401
    assert _confirm(client, "deadbeef").status_code == 401


def test_mini_withdraw_stages_without_debit(client, ledger):
    """The whole point of step 1: a real summary, and not one micro-unit moved."""
    _wallet(client, ledger, 10_000_000)

    r = _stage(client, EOA, 5)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["staged"] is True
    assert d["address"] == EOA
    assert (d["amount"], d["fee"], d["total"]) == ("5", "0.05", "5.05")
    assert d["balance_after"] == "4.95"
    assert d["token"]
    assert d["expires_in"] == 600

    assert ledger.balance(TG_USER) == Decimal("10.000000")
    assert _queued_rows(ledger, TG_USER) == []


def test_mini_withdraw_confirm_debits_once(client, ledger):
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]

    r = _confirm(client, token)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["queued"] is True
    assert (d["amount"], d["fee"]) == ("5", "0.05")
    assert d["new_balance"] == pytest.approx(4.95)
    assert d["withdraw_id"]

    assert ledger.balance(TG_USER) == Decimal("4.950000")
    rows = _queued_rows(ledger, TG_USER)
    assert len(rows) == 1 and rows[0]["status"] == "queued"
    fees = ledger._conn.execute(
        "SELECT amount FROM tx_log WHERE tg_id = %s AND kind = 'fee'", (TG_USER,)
    ).fetchall()
    assert [int(f["amount"]) for f in fees] == [50_000]


def test_mini_withdraw_confirm_is_single_use(client, ledger):
    """A replayed confirm must never reserve a second payout."""
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]

    assert _confirm(client, token).status_code == 200
    r = _confirm(client, token)
    assert r.status_code == 410
    assert "expired or was already used" in r.json()["detail"]

    assert ledger.balance(TG_USER) == Decimal("4.950000")
    assert len(_queued_rows(ledger, TG_USER)) == 1


def test_mini_withdraw_cancel_costs_nothing(client, ledger):
    """The cancel tap consumes the token just like confirm does, so an old
    prompt cannot be turned into a payout afterwards."""
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]

    r = _confirm(client, token, yes=False)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["cancelled"] is True
    assert d["new_balance"] == pytest.approx(10.0)

    assert _confirm(client, token).status_code == 410
    assert ledger.balance(TG_USER) == Decimal("10.000000")
    assert _queued_rows(ledger, TG_USER) == []


def test_mini_withdraw_confirm_after_expiry(client, ledger):
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]
    ledger._conn.execute(
        "UPDATE withdraw_confirmations SET created_at = created_at - 601 WHERE token = %s",
        (token,),
    )
    ledger._conn.commit()

    r = _confirm(client, token)
    assert r.status_code == 410
    assert ledger.balance(TG_USER) == Decimal("10.000000")
    assert _queued_rows(ledger, TG_USER) == []


def test_mini_withdraw_confirm_unknown_token(client, ledger):
    _wallet(client, ledger, 10_000_000)
    assert _confirm(client, "0" * 16).status_code == 410


def test_mini_withdraw_confirm_is_bound_to_its_owner(client, ledger):
    """Stage as one user, confirm as another: the token is worthless."""
    ledger.ensure_user(TG_USER, "alice")
    ledger.ensure_user(TG_OTHER, "bob")
    ledger.credit(TG_USER, 10_000_000, "deposit")
    _auth(client, TG_USER)
    token = _stage(client, EOA, 5).json()["token"]

    _auth(client, TG_OTHER)
    assert _confirm(client, token).status_code == 410
    assert ledger.balance(TG_USER) == Decimal("10.000000")


def test_mini_withdraw_staging_replaces_previous_offer(client, ledger):
    """One live confirmation per user: superseding an offer must not leave the
    older prompt confirmable, or two taps would pay out twice."""
    _wallet(client, ledger, 20_000_000)
    first = _stage(client, EOA, 5).json()["token"]
    second = _stage(client, EOA, 7).json()["token"]

    assert _confirm(client, first).status_code == 410
    assert _confirm(client, second).status_code == 200
    assert ledger.balance(TG_USER) == Decimal("12.930000")


def test_mini_withdraw_amount_is_truncated_not_rounded(client, ledger):
    """USDC has 6 decimals, so 1.0000009 stages as exactly 1 — the same way the
    Telegram path converts it."""
    _wallet(client, ledger, 10_000_000)
    d = _stage(client, EOA, "1.0000009").json()
    assert (d["amount"], d["fee"], d["total"]) == ("1", "0.01", "1.01")


def test_mini_withdraw_accepts_checksummed_address(client, ledger):
    from web3 import Web3

    _wallet(client, ledger, 10_000_000)
    mixed = Web3.to_checksum_address(EOA)
    assert mixed != EOA  # sanity: the EIP-55 form really is mixed-case
    r = _stage(client, mixed, 5)
    assert r.status_code == 200
    assert r.json()["address"] == mixed


def test_mini_withdraw_shares_the_telegram_address_grammar():
    """Both entry points must accept exactly the same strings: a drift here
    would mean an address /withdraw refuses is still payable from the app (or
    the other way round)."""
    import web.mini
    from bot.handlers import _common

    assert web.mini._ADDR_RE.pattern == _common.USDC_ADDR_RE.pattern


def test_mini_withdraw_rejects_malformed_address(client, ledger):
    _wallet(client, ledger, 10_000_000)
    for bad in ["not-an-address", "0x123", "0x" + "zz" * 20, ""]:
        r = _stage(client, bad, 1)
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "invalid address"
    assert ledger.balance(TG_USER) == Decimal("10.000000")


def test_mini_withdraw_defers_to_the_stricter_validator(client, ledger, monkeypatch):
    """The app runs both checks the bot handler runs, so it can never be the
    more permissive door: whatever `is_address` rejects is refused even when it
    matches the shared regex. (eth_utils 6.0 only checks shape here; EIP-55
    checksum validation lives in `is_checksum_address`, so a future release
    that tightens `is_address` must not open a second payout path.)"""
    import eth_utils

    monkeypatch.setattr(eth_utils, "is_address", lambda a: False)
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, EOA, 5)
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid address"


def test_mini_withdraw_refuses_zero_address(client, ledger):
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, "0x" + "0" * 40, 5)
    assert r.status_code == 400
    assert "burns funds" in r.json()["detail"]


def test_mini_withdraw_refuses_hot_wallet(client, ledger):
    from bot.base import hot_wallet

    _wallet(client, ledger, 10_000_000)
    r = _stage(client, str(hot_wallet()), 5)
    assert r.status_code == 400
    assert "own wallet" in r.json()["detail"]


def test_mini_withdraw_refuses_x402_pool(client, ledger):
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, X402_ADDR, 5)
    assert r.status_code == 400
    assert "x402 receive pool" in r.json()["detail"]


def test_mini_withdraw_refuses_vault(client, ledger, monkeypatch):
    import web.mini

    vault = "0x" + "cd" * 20
    monkeypatch.setattr(web.mini.config, "VAULT_ADDRESS", vault)
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, vault, 5)
    assert r.status_code == 400
    assert "vault" in r.json()["detail"]


def test_mini_withdraw_unknown_reason_still_refuses(client, ledger, monkeypatch):
    """A block reason with no copy must never turn into an accept."""
    import web.mini

    monkeypatch.setattr(web.mini, "blocked_destination", lambda a: "something_new")
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, EOA, 5)
    assert r.status_code == 400
    assert r.json()["detail"] == "destination refused"


def test_mini_withdraw_rejects_non_positive_amount(client, ledger):
    _wallet(client, ledger, 10_000_000)
    for amount in ["0", "-5"]:
        r = _stage(client, EOA, amount)
        assert r.status_code == 400, amount
        assert r.json()["detail"] == "amount must be positive"


def test_mini_withdraw_rejects_below_minimum(client, ledger):
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, EOA, "0.5")
    assert r.status_code == 400
    assert r.json()["detail"] == "below the 1 USDC minimum"


def test_mini_withdraw_rejects_contract_destination(client, ledger, monkeypatch):
    """USDC sent to a contract that cannot pull it back is a plain loss."""
    from bot.chain import network

    monkeypatch.setattr(network, "is_contract", _always_a_contract)
    _wallet(client, ledger, 10_000_000)
    r = _stage(client, EOA, 5)
    assert r.status_code == 400
    assert "contract, not a wallet" in r.json()["detail"]


def test_mini_withdraw_rejects_insufficient_balance(client, ledger):
    _wallet(client, ledger, 1_500_000)
    r = _stage(client, EOA, "1.5")
    assert r.status_code == 400
    assert r.json()["detail"] == "insufficient balance: need 1.515, have 1.5"


def test_mini_withdraw_daily_cap_blocks_staging(client, ledger):
    _wallet(client, ledger, 100_000_000)
    _queue_withdrawals(ledger, TG_USER, 5)
    r = _stage(client, EOA, 1)
    assert r.status_code == 400
    assert r.json()["detail"] == "daily limit of 5 withdrawals reached"


def test_mini_withdraw_confirm_refuses_blocked_destination(client, ledger, monkeypatch):
    """Defense in depth: even if the destination only becomes bot-owned after
    staging, the ledger says no and the refusal names the real reason."""
    import web.mini

    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]
    monkeypatch.setattr(web.mini.config, "X402_RECEIVE_ADDRESS", EOA)

    r = _confirm(client, token)
    assert r.status_code == 400
    assert "refused as a bot-owned address" in r.json()["detail"]
    assert ledger.balance(TG_USER) == Decimal("10.000000")
    assert _queued_rows(ledger, TG_USER) == []


def test_mini_withdraw_confirm_refuses_cap_race(client, ledger):
    """A second device can fill the daily cap between stage and confirm. The
    debit must roll back with the INSERT, not leave the user short."""
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]
    _queue_withdrawals(ledger, TG_USER, 5)

    r = _confirm(client, token)
    assert r.status_code == 400
    assert r.json()["detail"] == "daily withdrawal limit reached"
    assert ledger.balance(TG_USER) == Decimal("10.000000")
    assert len(_queued_rows(ledger, TG_USER)) == 5


def test_mini_withdraw_confirm_refuses_balance_race(client, ledger):
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]
    ledger._conn.execute("UPDATE users SET balance = 1000000 WHERE tg_id = %s", (TG_USER,))
    ledger._conn.commit()

    r = _confirm(client, token)
    assert r.status_code == 400
    assert r.json()["detail"] == "insufficient balance"
    assert ledger.balance(TG_USER) == Decimal("1.000000")
    assert _queued_rows(ledger, TG_USER) == []


def test_mini_withdraw_confirm_flags_large_withdrawal(client, ledger, monkeypatch):
    """AML screening runs where the money actually moves — on confirm."""
    from bot import config

    monkeypatch.setattr(config, "WITHDRAW_LARGE_USDC_THRESHOLD", 1, raising=False)
    _wallet(client, ledger, 10_000_000)
    token = _stage(client, EOA, 5).json()["token"]

    assert _confirm(client, token).status_code == 200
    kinds = [
        r["kind"] for r in ledger._conn.execute(
            "SELECT kind FROM suspicious_activity WHERE tg_id = %s", (TG_USER,)
        ).fetchall()
    ]
    assert "large_withdraw" in kinds


def test_mini_withdraw_all_the_way_to_zero(client, ledger):
    """Spending the last micro-unit is legitimate; balance_after must say 0,
    not 0.00 and never a negative number."""
    _wallet(client, ledger, 1_010_000)
    d = _stage(client, EOA, 1).json()
    assert (d["total"], d["balance_after"]) == ("1.01", "0")
    assert _confirm(client, d["token"]).json()["new_balance"] == 0.0
