"""Web dashboard API tests (FastAPI TestClient)."""

import json
import re
import types
from pathlib import Path

import pytest
from conftest import close_bet
from fastapi.testclient import TestClient

from web.auth import COOKIE_NAME, make_session


def _auth(client, tg_id):
    """Attach a valid owner session cookie for tg_id to the TestClient."""
    client.cookies.set(COOKIE_NAME, make_session(tg_id))
    return client


@pytest.fixture()
def client(ledger, monkeypatch):
    from bot.ledger import AsyncLedger
    from web import server

    # Routes now `await ledger.x()`; wrap the injected sync ledger so the
    # await resolves against the hermetic test database.
    monkeypatch.setattr(server, "ledger", AsyncLedger(ledger))
    return TestClient(server.app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """The web rate limiter keeps a module-global hit counter (_rl_state).
    Without a reset it accumulates across the whole suite and 429s later
    tests once the per-IP window fills. Clear it around every test."""
    from web import server

    server._rl_state.clear()
    # /api/ask keeps its own per-IP cooldown; without this the second AI test
    # in a row is refused by the first one's timestamp.
    server._ask_cooldown.clear()
    yield
    server._rl_state.clear()
    server._ask_cooldown.clear()


@pytest.fixture(autouse=True)
def _reset_stats_cache():
    """api_stats caches the aggregate totals (TTL in seconds); tests write to
    the ledger then immediately read /api/stats, so the cache must not carry
    a stale value across tests. Same reset pattern as web/metrics.py tests."""
    from web import server

    server._stats_cache = None
    server._stats_ts = 0.0
    yield
    server._stats_cache = None
    server._stats_ts = 0.0


def test_info(client):
    r = client.get("/api/info")
    assert r.status_code == 200
    assert "bot_username" in r.json()


def test_info_publishes_the_x402_terms_the_sandbox_prints(client, monkeypatch):
    """/api/info's x402 block is what the handshake sandbox puts into the amount
    field's `min`/`max` and into the sentence about the range it accepts. If the
    page advertised a wider range than `_parse_amount` enforces, an agent would
    follow the documentation, pay an amount the endpoint then refuses, and the
    money would sit in an invoice nobody can redeem."""
    from bot import config as cfg
    from web import x402

    body = client.get("/api/info").json()["x402"]
    assert set(body) == {"enabled", "min_usdc", "max_usdc", "invoice_ttl_seconds"}
    assert body["min_usdc"] == float(cfg.X402_MIN_USDC)
    assert body["max_usdc"] == float(cfg.MAX_TIP_USDC)
    assert body["invoice_ttl_seconds"] == x402.PAYMENT_TTL_SECONDS

    # Anything inside the advertised range is an amount the endpoint will quote.
    assert x402._parse_amount(str(body["min_usdc"]))[0] is not None
    assert x402._parse_amount(str(body["max_usdc"]))[0] is not None
    assert x402._parse_amount(str(body["max_usdc"] + 1))[0] is None
    assert x402._parse_amount(str(body["min_usdc"] / 100))[0] is None

    # A refusal an agent can act on: the sentence carries the number to fix.
    # "recipient and amount are required" for an over-cap amount told the client
    # to add a parameter it had already sent, so it resent the same request.
    for query, needle in [
        ("amount=99999", str(cfg.MAX_TIP_USDC)),
        ("amount=0.0001", str(cfg.X402_MIN_USDC)),
        ("amount=abc", "not a number"),
        ("amount=0", "greater than 0"),
    ]:
        r = client.post(f"/api/x402/tip?recipient=777&{query}")
        assert r.status_code == 400, query
        assert needle in r.json()["detail"], (query, r.json())
    assert "recipient" in client.post("/api/x402/tip?amount=1").json()["detail"]

    # A parameter the client never sent is not a parameter it sent badly:
    # `Decimal('')` raised inside the parser, so an omitted `amount` was answered
    # «amount is not a number» — a fix nobody could apply to a request that had
    # no number in it. Both routes that parse an amount share the answer, and a
    # blank one arrives at the parser the same way because each route strips.
    for url in ("/api/x402/tip?recipient=777", "/api/x402/paywall?item=1"):
        missing = client.post(url)
        assert missing.status_code == 400, url
        detail = missing.json()["detail"]
        assert "required" in detail, (url, detail)
        assert "not a number" not in detail, (url, detail)
    blank = client.post("/api/x402/tip?recipient=777&amount=%20%20")
    assert "required" in blank.json()["detail"], blank.json()
    assert x402._parse_amount("")[1] == "amount is required"
    assert x402._parse_amount("abc")[1] == "amount is not a number"

    # The switch the page reports is the switch the endpoint reads.
    monkeypatch.setattr(cfg, "X402_ENABLED", False)
    assert client.get("/api/info").json()["x402"]["enabled"] is False


def test_info_network_labels_follow_the_signing_config(client, monkeypatch):
    """The site reads its chain name and explorer from here, so both have to come
    from the config the bot signs with rather than constants baked into the pages.
    Patched rather than asserted against defaults: a developer's `.env` may point
    at Sepolia while CI runs mainnet values."""
    from bot import config as cfg

    monkeypatch.setattr(cfg, "EXPECTED_CHAIN_ID", 8453)
    monkeypatch.setattr(cfg, "BASESCAN_URL", "https://basescan.org")
    body = client.get("/api/info").json()
    assert body["chain_name"] == "Base"
    assert body["explorer"] == "https://basescan.org"

    monkeypatch.setattr(cfg, "EXPECTED_CHAIN_ID", 84532)
    body = client.get("/api/info").json()
    assert body["chain_name"] == "Base Sepolia"

    # An id we do not name gets the raw number, never a guessed "Base".
    monkeypatch.setattr(cfg, "EXPECTED_CHAIN_ID", 31337)
    assert client.get("/api/info").json()["chain_name"] == "chain 31337"


def test_peer_trusted_matches_ip_cidr_and_hostname(monkeypatch):
    from web import server

    assert server._peer_trusted({"127.0.0.1"}, "127.0.0.1") is True
    assert server._peer_trusted({"172.16.0.0/12"}, "172.31.5.4") is True
    assert server._peer_trusted({"172.16.0.0/12", "127.0.0.1"}, "8.8.8.8") is False
    assert server._peer_trusted({"cloudflared"}, "cloudflared") is True
    assert server._peer_trusted(frozenset(), "unknown") is False


def _fake_request(peer_ip, xff):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", str(xff).encode())] if xff else []
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/health",
        "headers": headers,
        "client": (peer_ip, 4242),
    })


def test_client_ip_never_trusts_xff_from_untrusted_peer(monkeypatch):
    """A client forging X-Forwarded-For straight at the app socket (peer IP
    not in TRUSTED_PROXY_PEERS) must be bucketed by its real peer IP — even
    when TRUST_PROXY_XFF=1 — otherwise the rate limiter is fully spoofable."""
    from bot import config as cfg
    from web import server

    monkeypatch.setattr(cfg, "TRUST_PROXY_XFF", True)
    monkeypatch.setattr(cfg, "TRUSTED_PROXY_PEERS", frozenset({"127.0.0.1"}))

    req = _fake_request("8.8.8.8", "1.2.3.4")
    assert server._client_ip(req) == "8.8.8.8"


def test_client_ip_trusts_xff_from_trusted_peer_rightmost(monkeypatch):
    """When the direct peer IS a trusted proxy, the rightmost XFF entry (the
    real client appended by the proxy) is used; the leftmost forged entries
    are ignored."""
    from bot import config as cfg
    from web import server

    monkeypatch.setattr(cfg, "TRUST_PROXY_XFF", True)
    monkeypatch.setattr(cfg, "TRUSTED_PROXY_PEERS", frozenset({"172.16.0.0/12"}))

    req = _fake_request("172.20.3.9", "7.7.7.7, 203.0.113.5")
    assert server._client_ip(req) == "203.0.113.5"


def test_client_ip_ignores_xff_when_flag_off(monkeypatch):
    from bot import config as cfg
    from web import server

    monkeypatch.setattr(cfg, "TRUST_PROXY_XFF", False)
    monkeypatch.setattr(cfg, "TRUSTED_PROXY_PEERS", frozenset({"127.0.0.1"}))

    req = _fake_request("127.0.0.1", "1.2.3.4")
    assert server._client_ip(req) == "127.0.0.1"


def test_stats_zeros(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["users"] == 0
    assert data["volume_usdc"] == 0.0
    assert data["open_markets"] == 0
    assert data["fees_usdc"] == 0.0


def test_stats_tracks_fees(client, ledger):
    ledger.credit(1, 1_000_000, "deposit")
    ledger.record_withdraw_fee(1, "0x" + "a" * 40, 10_000, "0x" + "1" * 64)
    data = client.get("/api/stats").json()
    assert data["fees_usdc"] == 0.01


def test_wallet_shape(client, monkeypatch):
    from web import server

    async def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(server, "hot_balance", _raise)
    r = client.get("/api/wallet")
    assert r.status_code == 200
    data = r.json()
    assert data["address"].startswith("0x")
    assert data["balance_usdc"] is None  # RPC unavailable in tests


def test_unknown_user_returns_empty_public_shape(client):
    # No 404 oracle: an unknown tg_id yields an all-zero public shape, so the
    # endpoint cannot enumerate which ids exist.
    r = client.get("/api/user/42424242")
    assert r.status_code == 200
    data = r.json()
    assert data["is_owner"] is False
    assert data["username"] is None
    assert data["tips_sent_usdc"] == 0.0
    assert data["tips_received_usdc"] == 0.0
    assert data.get("balance_usdc") is None  # public shape has no live balance


def test_user_endpoint_with_data(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    ledger.transfer(777, 778, 2_000_000)
    _auth(client, 777)
    r = client.get("/api/user/777")
    assert r.status_code == 200
    data = r.json()
    assert data["is_owner"] is True
    assert data["balance_usdc"] == 3.0
    assert data["tips_sent_usdc"] == 2.0
    assert data["tips_received_usdc"] == 0.0


def test_user_endpoint_public_view_for_stranger(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    ledger.transfer(777, 778, 2_000_000)
    r = client.get("/api/user/777")
    assert r.status_code == 200
    data = r.json()
    assert data["is_owner"] is False
    # No live balance, positions, history, or full financial profile for a
    # stranger: only public aggregates.
    assert "balance_usdc" not in data
    assert "history" not in data
    assert "positions" not in data


def test_market_and_leaderboard(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Сыграем?", ["Да", "Нет"])
    ledger.place_bet(bid, 2, 0, 10_000_000)
    ledger.transfer(2, 1, 5_000_000)  # tip: user 2 is a tipper

    r = client.get("/api/markets")
    assert r.status_code == 200
    markets = r.json()
    assert len(markets) == 1
    assert markets[0]["pot_usdc"] == 10.0

    r = client.get(f"/api/market/{bid}")
    assert r.status_code == 200
    assert r.json()["options"][0]["pool_usdc"] == 10.0

    assert client.get("/api/market/99999").status_code == 404

    lb = client.get("/api/leaderboard").json()
    assert lb[0]["total_usdc"] == 5.0  # user 2 tipped 5 USDC


def test_leaderboard_names_a_nameless_leader_by_basename(client, ledger, monkeypatch):
    """`/api/leaderboard` read a `tg_id` the leaderboard row did not carry, so the
    reverse-Basename fallback raised KeyError straight into a blanket
    `except Exception` — and no leader without a Telegram username ever got a
    name. The row carries the id, the fallback runs once for the whole page."""
    from bot import tip_targets

    ledger.credit(1, 100_000_000, "deposit")
    ledger.ensure_user(2, None)
    ledger.credit(2, 100_000_000, "deposit")
    ledger.transfer(2, 1, 40_000_000)  # user 2 is the top tipper, and nameless
    ledger._conn.execute(
        "INSERT INTO wallet_links (tg_id, address) VALUES (%s, %s)", (2, "0x" + "b2" * 20)
    )
    ledger._conn.commit()
    tip_targets._reverse_cache.clear()
    monkeypatch.setattr(tip_targets, "reverse_basename_sync", lambda addr: "anon.base.eth")

    lb = client.get("/api/leaderboard").json()
    assert lb[0]["username"] == "anon.base.eth", "the name a page shows lives here"
    assert "tg_id" not in lb[0], "the internal id is not part of the public row"
    assert lb[0]["total_usdc"] == 40.0


def test_unknown_market_404(client):
    assert client.get("/api/market/99999").status_code == 404


def test_health(client, monkeypatch):
    from web import server

    class FakeEth:
        def __init__(self):
            self.block_number = 1000

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=FakeEth()))
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["hot_wallet"].startswith("0x")


def test_health_includes_scanner_status(client, monkeypatch):
    from web import server

    class FakeEth:
        def __init__(self):
            self.block_number = 1000

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=FakeEth()))
    data = client.get("/api/health").json()
    assert "chain_head" in data
    assert "last_scanned_block" in data
    assert "deposit_lag" in data


def test_health_deposit_lag_none_when_rpc_down(client, monkeypatch):
    from web import server

    class Boom:
        @property
        def block_number(self):
            raise ConnectionError("no rpc")

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=Boom()))
    data = client.get("/api/health").json()
    assert data["chain_head"] is None
    assert data["deposit_lag"] is None
    assert data["status"] == "ok"


def test_solvency_zeros(client):
    r = client.get("/api/solvency")
    assert r.status_code == 200
    data = r.json()
    assert data["liabilities_usdc"] == 0.0
    assert data["pending_deposits_usdc"] == 0.0
    assert data["liabilities_usdc"] == 0.0
    assert data["vault_address"] is None
    assert data["vault_balance_usdc"] is None
    assert data["reserves_source"] == "hot_wallet"


def test_solvency_tracks_liabilities(client, ledger):
    ledger.credit(777, 12_000_000, "deposit")
    ledger.credit(778, 3_000_000, "deposit")
    ledger.record_pending("0x" + "5" * 64, "0xowner", 7_000_000)
    r = client.get("/api/solvency").json()
    assert r["liabilities_usdc"] == 22.0
    assert r["pending_deposits_usdc"] == 7.0
    assert r["liabilities_usdc"] == 22.0


def test_solvency_insolvent_when_rpc_down(client, monkeypatch):
    from web import server

    async def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(server, "hot_balance", _raise)
    monkeypatch.setattr(server, "vault_balance", _raise)
    r = client.get("/api/solvency").json()
    assert r["solvent"] is None
    assert r["reserve_usdc"] is None


def test_solvency_uses_vault_as_primary_reserve(client, ledger, monkeypatch):
    from web import server

    vault_addr = "0x" + "ab" * 20
    monkeypatch.setattr(server.config, "VAULT_ADDRESS", vault_addr)
    async def _vb():
        return 25.5
    monkeypatch.setattr(server, "vault_balance", _vb)
    ledger.credit(777, 12_000_000, "deposit")
    r = client.get("/api/solvency").json()
    assert r["vault_address"] == vault_addr
    assert r["vault_balance_usdc"] == 25.5
    assert r["reserves_source"] == "vault"
    assert r["reserve_usdc"] == 13.5
    assert r["solvent"] is True


def test_solvency_vault_rpc_down_keeps_hot_wallet_source(client, monkeypatch):
    from web import server

    monkeypatch.setattr(server.config, "VAULT_ADDRESS", "0x" + "cd" * 20)

    async def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(server, "vault_balance", _raise)
    r = client.get("/api/solvency").json()
    assert r["vault_balance_usdc"] is None
    assert r["reserve_usdc"] is None
    assert r["solvent"] is None


# ---------- x402 (agent tips over HTTP) ----------


def test_x402_first_call_returns_invoice(client, ledger, monkeypatch):

    ledger.credit(777, 1_000_000, "deposit")
    r = client.post("/api/x402/tip?recipient=777&amount=5")
    assert r.status_code == 402
    body = r.json()
    assert body["detail"] == "payment required"
    assert body["amount_usdc"] == 5.0
    assert body["pay_to"].startswith("0x")
    for header in ("x-402-recipient", "x-402-amount", "x-402-expires-at", "x-402-idempotency-key"):
        assert header in r.headers
    assert r.headers["x-402-amount"] == "5000000"


def test_x402_rejects_bad_recipient_and_amount(client, ledger):
    assert client.post("/api/x402/tip?recipient=999999&amount=5").status_code == 404
    ledger.credit(777, 1_000_000, "deposit")
    assert client.post("/api/x402/tip?recipient=777&amount=0").status_code == 400
    assert client.post("/api/x402/tip?recipient=777&amount=-1").status_code == 400
    assert client.post("/api/x402/tip?recipient=777&amount=99999").status_code == 400
    assert client.post("/api/x402/tip").status_code == 400


def test_x402_payment_credited_once(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    tx = "0x" + "ab" * 32
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt, addr: {"sender": "0x" + "11" * 20, "amount_micro": amt}
    )
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": tx}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tip"]["amount_usdc"] == 5.0
    assert body["tip"]["tx_hash"] == tx
    assert ledger.user_view(777)["balance_micro"] == 6_000_000

    # replay of the same tx is refused before any RPC call
    r2 = client.post("/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": tx})
    assert r2.status_code == 409
    assert ledger.user_view(777)["balance_micro"] == 6_000_000
    assert ledger.x402_paid(tx)


def test_x402_payment_by_username(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    ledger.ensure_user(777, "alice")
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt, addr: {"sender": "0x" + "11" * 20, "amount_micro": amt}
    )
    r = client.post(
        "/api/x402/tip?recipient=alice&amount=2", headers={"x-402-payment": "0x" + "cd" * 32}
    )
    assert r.status_code == 200
    assert ledger.user_view(777)["balance_micro"] == 3_000_000


def test_x402_unverified_payment_gets_invoice_again(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    monkeypatch.setattr(x402, "_verify_payment", lambda h, amt, addr: None)
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "ef" * 32}
    )
    assert r.status_code == 402
    assert r.headers["x-402-amount"] == "5000000"
    assert ledger.user_view(777)["balance_micro"] == 1_000_000


def test_x402_rpc_down_returns_402(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")

    def _raise():
        raise ConnectionError("no rpc")

    monkeypatch.setattr(x402.base.w3.eth, "get_transaction_receipt", _raise)
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "aa" * 32}
    )
    assert r.status_code == 402
    assert ledger.user_view(777)["balance_micro"] == 1_000_000


def _transfer_log(to, value_micro, from_addr=None):
    from web import x402

    def pad(a):
        return "0x" + "0" * 24 + a[2:].lower()

    return {
        "address": x402.config.USDC_ADDRESS,
        "topics": [
            x402.base.w3.keccak(text="Transfer(address,address,uint256)").hex(),
            pad("0x" + (from_addr or "11" * 20)),
            pad(to),
        ],
        "data": "0x" + f"{int(value_micro):064x}",
        "transactionHash": b"\x00" * 32,
        "blockHash": b"\x00" * 32,
        "blockNumber": 1,
        "logIndex": 0,
        "transactionIndex": 0,
    }


def test_x402_verifies_real_transfer_logs(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")

    # 1) Get the invoice to learn the per-invoice pay address
    r = client.post("/api/x402/tip?recipient=777&amount=5")
    assert r.status_code == 402
    pay_addr = r.json()["pay_to"]

    # 2) Build transfer logs that credit THIS invoice's address
    logs = [
        _transfer_log("0x" + "99" * 20, 9_999_999),  # unrelated transfer — ignored
        _transfer_log(pay_addr, 5_000_000, from_addr="33" * 20),  # the payment
    ]
    monkeypatch.setattr(
        x402.base.w3.eth, "get_transaction_receipt", lambda h: {"status": 1, "logs": logs}
    )
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "aa" * 32}
    )
    assert r.status_code == 200
    assert r.json()["tip"]["sender"] == "0x" + "33" * 20
    assert ledger.user_view(777)["balance_micro"] == 6_000_000


def test_x402_reverted_tx_not_credited(client, ledger, monkeypatch):
    from web import x402

    ledger.credit(777, 1_000_000, "deposit")
    monkeypatch.setattr(
        x402.base.w3.eth,
        "get_transaction_receipt",
        lambda h: {"status": 0, "logs": []},
    )
    r = client.post(
        "/api/x402/tip?recipient=777&amount=5", headers={"x-402-payment": "0x" + "bb" * 32}
    )
    assert r.status_code == 402
    assert ledger.user_view(777)["balance_micro"] == 1_000_000


def test_x402_paywall_invoice(client, ledger):
    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    r = client.post(f"/api/x402/paywall?item={item_id}&amount=5")
    assert r.status_code == 402
    body = r.json()
    assert body["detail"] == "payment required"
    assert body["amount_usdc"] == 5.0
    assert body["item"] == str(item_id)
    assert r.headers["x-402-amount"] == "5000000"
    # unknown item -> 404, missing params -> 400
    assert client.post("/api/x402/paywall?item=999&amount=5").status_code == 404
    assert client.post("/api/x402/paywall?item=1").status_code == 400


def test_x402_paywall_purchase_returns_content(client, ledger, monkeypatch):
    from web import x402

    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    tx = "0x" + "cc" * 32
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt, addr: {"sender": "0x" + "22" * 20, "amount_micro": amt}
    )
    r = client.post(
        f"/api/x402/paywall?item={item_id}&amount=5", headers={"x-402-payment": tx}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["item"]["title"] == "Мой отчёт"
    assert body["content"] == "секретный контент"
    # owner is credited on-chain
    assert ledger.user_view(777)["balance_micro"] == 5_000_000
    assert ledger.x402_paid(tx)


def test_x402_paywall_lowball_reinvoiced_at_price(client, ledger):
    """An agent cannot buy the content cheaper than the listed price: a lowball
    amount gets a fresh invoice at the real price, and paying the real price
    is what actually unlocks the content."""
    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    lowball = client.post(f"/api/x402/paywall?item={item_id}&amount=0.01")
    assert lowball.status_code == 402
    assert lowball.headers["x-402-amount"] == "5000000"
    assert lowball.json()["amount_usdc"] == 5.0
    # paying only the lowball amount never unlocks anything
    assert client.post(
        f"/api/x402/paywall?item={item_id}&amount=0.01", headers={"x-402-payment": "0x" + "ee" * 32}
    ).status_code == 402
    # no purchases were recorded
    assert ledger.paywall_purchased(item_id, 777) is False


def test_x402_paywall_replay_409(client, ledger, monkeypatch):
    from web import x402

    item_id = ledger.create_paywall(777, "Мой отчёт", 5_000_000, "секретный контент")
    tx = "0x" + "dd" * 32
    monkeypatch.setattr(
        x402, "_verify_payment", lambda h, amt, addr: {"sender": "0x" + "22" * 20, "amount_micro": amt}
    )
    first = client.post(
        f"/api/x402/paywall?item={item_id}&amount=5", headers={"x-402-payment": tx}
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/x402/paywall?item={item_id}&amount=5", headers={"x-402-payment": tx}
    )
    assert second.status_code == 409
    # owner was credited exactly once
    assert ledger.user_view(777)["balance_micro"] == 5_000_000


def test_qr_endpoint(client):
    r = client.get("/qr", params={"data": "base:0x" + "ab" * 20, "size": 220})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_qr_endpoint_rejects_long_data(client):
    assert client.get("/qr", params={"data": "x" * 2000}).status_code == 400


def test_markets_status_filter(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Сыграем?", ["Да", "Нет"])
    ledger.place_bet(bid, 2, 0, 10_000_000)
    close_bet(ledger, bid)
    ledger.resolve_bet(bid, 0, 1)

    open_ = client.get("/api/markets").json()
    assert open_ == []  # resolved market is not "open"

    resolved = client.get("/api/markets?status=resolved").json()
    assert len(resolved) == 1
    assert resolved[0]["id"] == bid
    assert resolved[0]["status"] == "resolved"


def test_user_page_served(client):
    r = client.get("/u/777")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_user_history_in_api(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    _auth(client, 777)
    r = client.get("/api/user/777")
    data = r.json()
    assert data["history"]
    assert data["history"][0]["kind"] == "deposit"
    assert data["history"][0]["amount_usdc"] == 5.0


def test_user_creator_fees_in_api(client, ledger):
    ledger.credit(1, 2_000_000, "deposit")
    ledger.credit(2, 2_000_000, "deposit")
    bid = ledger.create_bet(1, "Q", ["А", "Б"])
    ledger.place_bet(bid, 2, 0, 1_000_000)
    ledger.place_bet(bid, 2, 1, 1_000_000)
    close_bet(ledger, bid)
    ledger.resolve_bet(bid, 0, 1)
    _auth(client, 1)
    data = client.get("/api/user/1").json()
    assert data["creator_fees_usdc"] == 0.02


def test_market_page_served(client, ledger):
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    r = client.get(f"/m/{bid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_owner_history_carries_the_rendered_fields(client, ledger):
    """The page must not guess: label, sign and counterparty come from the API.

    Amounts in tx_log are always positive, so a renderer that derives the sign
    from the amount prints a withdrawal as income.
    """
    ledger.ensure_user(778, "receiver")
    ledger.credit(777, 5_000_000, "deposit")
    ledger.transfer(777, 778, 2_000_000)
    _auth(client, 777)
    history = client.get("/api/user/777").json()["history"]
    by_kind = {row["kind"]: row for row in history}
    assert by_kind["deposit"]["incoming"] is True
    assert by_kind["tip"]["incoming"] is False
    assert by_kind["tip"]["ref"] == "@receiver"
    assert by_kind["tip"]["amount_usdc"] == 2.0
    assert all(row["label"] for row in history)
    assert all(isinstance(row["created_at"], int) for row in history)


def test_history_localized_to_the_owner_language(client, ledger):
    ledger.credit(777, 5_000_000, "deposit")
    ledger.set_setting(777, "lang", "en")
    _auth(client, 777)
    row = client.get("/api/user/777").json()["history"][0]
    assert row["label"] == "Deposit"


def test_onchain_market_page_api_names_the_contract_and_creator(client, ledger, monkeypatch):
    """/m/oc/{id} renders from this payload: without the contract address the
    explorer link cannot be built, and without the username the creator is a
    bare number."""
    from bot import config
    from bot import onchain_market as om

    async def fake_prices(market_id, count):
        return [0.62, 0.38]

    async def fake_info(market_id):
        return {"b": 5_000_000_000, "escrow_micro": 250_000_000, "resolved": False,
                "disputed": False, "cancelled": False, "winning_outcome": None}

    monkeypatch.setattr(om, "market_prices", fake_prices)
    monkeypatch.setattr(om, "get_market_info", fake_info)
    monkeypatch.setattr(config, "OUTCOME_MARKET_ADDRESS", "0x" + "ab" * 20)
    ledger.ensure_user(777, "creator")
    ledger.save_onchain_market(3, 777, "Кто победит?", ["да", "нет"], 4_000_000_000)

    data = client.get("/api/onchain/market/3").json()
    assert data["creator"] == {"id": 777, "username": "creator"}
    assert data["market_address"] == "0x" + "ab" * 20
    assert data["escrow_micro"] == 250_000_000
    assert [o["price_pct"] for o in data["options"]] == [62.0, 38.0]
    assert client.get("/m/oc/3").status_code == 200
    assert client.get("/api/onchain/market/999").status_code == 404


def test_api_rate_limited(client, monkeypatch):
    from web import server

    class FakeEth:
        def __init__(self):
            self.block_number = 1000

    monkeypatch.setattr(server.base, "w3", types.SimpleNamespace(eth=FakeEth()))
    server._rl_state.clear()
    monkeypatch.setattr(server, "WEB_RATE_LIMIT", 2)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 429
    server._rl_state.clear()


def test_static_not_rate_limited(client, monkeypatch):
    from web import server

    server._rl_state.clear()
    monkeypatch.setattr(server, "WEB_RATE_LIMIT", 1)
    # Static pages are outside the limiter (they never touch RPC/CPU-heavy work).
    assert client.get("/").status_code == 200
    assert client.get("/style.css").status_code == 200
    server._rl_state.clear()


# ---------- volume analytics ----------


def test_stats_volume_30d(client, ledger):
    ledger.credit(1, 5_000_000, "deposit")
    ledger.transfer(1, 2, 2_000_000)
    data = client.get("/api/stats").json()
    assert data["volume_30d_usdc"] == 7.0


def test_volume_history(client, ledger):
    from datetime import date, timedelta

    ledger.credit(1, 5_000_000, "deposit")
    r = client.get("/api/volume_history")
    assert r.status_code == 200
    data = r.json()
    # One row per day of the window, zeros included: the chart plots whatever
    # it is given, so gaps used to make "14 days" look like two bars.
    assert len(data) == 14
    assert data[-1]["volume_usdc"] == 5.0  # today
    assert [row["volume_usdc"] for row in data[:-1]] == [0.0] * 13
    days = [date.fromisoformat(row["day"]) for row in data]
    assert days == [days[0] + timedelta(days=i) for i in range(14)]


def test_volume_history_days_clamped(client, ledger):
    ledger.credit(1, 5_000_000, "deposit")
    r = client.get("/api/volume_history?days=400")
    assert r.status_code == 200  # clamped to 30, not rejected
    assert len(r.json()) == 30
    r = client.get("/api/volume_history?days=0")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["volume_usdc"] == 5.0


# ---------- backers ----------


def test_markets_include_backers(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, 1, 0, 10_000_000)
    ledger.place_bet(bid, 2, 0, 5_000_000)
    markets = client.get("/api/markets").json()
    assert len(markets) == 1
    assert markets[0]["total_backers"] == 2
    assert [o["backers"] for o in markets[0]["options"]] == [2, 0]


def test_market_detail_include_backers(client, ledger):
    ledger.credit(1, 100_000_000, "deposit")
    ledger.credit(2, 100_000_000, "deposit")
    ledger.credit(3, 100_000_000, "deposit")
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    ledger.place_bet(bid, 1, 0, 10_000_000)
    ledger.place_bet(bid, 2, 0, 5_000_000)
    ledger.place_bet(bid, 3, 1, 3_000_000)
    market = client.get(f"/api/market/{bid}").json()
    assert market["total_backers"] == 3
    assert [o["backers"] for o in market["options"]] == [2, 1]


# ---------- static pages: HTML ↔ JS wiring ----------


def test_index_page_has_js_wiring(client):
    html = client.get("/").text
    for needle in (
        "stat-vol30",
        "volume-chart",
        "markets-list",
        "closed-markets-list",
        "leaderboard",
        "wallet-solvent",
        "wallet-vault-row",
        "wallet-vault-addr",
        "data-tg-link",
        "/app.js",
    ):
        assert needle in html
    app_js = client.get("/app.js").text
    assert "wallet-vault-row" in app_js
    assert "vault_address" in app_js
    assert "vault_balance_usdc" in app_js


def test_market_page_has_js_wiring(client, ledger):
    bid = ledger.create_bet(1, "Вопрос?", ["А", "Б"])
    html = client.get(f"/m/{bid}").text
    for needle in (
        "m-question",
        "m-options",
        "m-pot",
        "m-meta",
        "m-share",
        "data-close-at",
        "option-winner",
    ):
        assert needle in html


def test_user_page_has_js_wiring(client):
    html = client.get("/u/777").text
    for needle in (
        "u-balance",
        "u-received",
        "u-won",
        "history-list",
        "u-copy",
        "u-qr",
    ):
        assert needle in html


def test_agent_page_served_at_the_url_readme_documents(client):
    # The mounted StaticFiles resolves only paths that name the file, so
    # without this route /agent is a 404 while /agent.html works.
    r = client.get("/agent")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/agent.js" in r.text
    assert client.get("/api/agent/status").status_code == 403


def test_tos_renders_as_a_document_not_raw_markdown(client):
    """As text/plain the browser invents a 980px layout for its own <pre>, so a
    phone got a pinched dump of ``##`` and ``**`` markers for its legal terms."""
    r = client.get("/tos")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert 'name="viewport"' in body
    assert "<h1>Terms of Service" in body
    assert "<li>USDC tipping, rain and a custodial balance between Telegram users</li>" in body
    assert "<b>Last updated:</b>" in body
    assert "<code>/api/solvency</code>" in body
    assert "**" not in body
    # The terms are the only place a user can read the custody warning, so the
    # footer of every money page has to reach it.
    for path in ("/", "/m/1", "/u/777", "/m/oc/1"):
        assert 'href="/tos"' in client.get(path).text


def test_the_served_terms_document_is_a_document(client):
    """`/tos` is assembled at request time, so the shell gate in
    `test_design_system.py` measures the file but not the answer: the body comes
    out of `tos.md` through `_legal_html()`, and a marker the server forgets, a
    second `<h1>` from a second level-1 heading, or a Russian document announcing
    itself as `lang="en"` all exist only in the bytes a user actually reads.

    `lang` is the one with no visual symptom. A screen reader reads the page in
    the language of that attribute, so the English terms wrapped in `lang="ru"`
    were spelled out in the wrong alphabet by the very software a low-vision user
    depends on — while the footer link that opens them is Russian either way."""
    from itertools import pairwise
    from pathlib import Path

    from test_design_system import PLACEHOLDER_RE

    body = client.get("/tos").text
    text = re.sub(r"<script.*?</script>", "", body, flags=re.S)

    levels = [int(level) for level in re.findall(r"<h([1-6])\b", text)]
    assert levels.count(1) == 1, f"{levels.count(1)} h1 in the served terms"
    assert levels[0] == 1, f"the served terms open at h{levels[0]}"
    jumps = [(a, b) for a, b in pairwise(levels) if b - a > 1]
    assert not jumps, f"the served terms skip heading levels {jumps} ({levels})"

    # Every marker in the shell must be consumed, including the ones only the
    # legal route passes: an unsubstituted `__LEGAL_BODY__` is the whole document
    # missing, and it renders as a sentence a lawyer would have to explain.
    leaked = set(PLACEHOLDER_RE.findall(body))
    assert not leaked, f"unsubstituted markers in the served terms: {sorted(leaked)}"

    md = (Path(__file__).resolve().parents[1] / "web" / "static" / "tos.md").read_text(
        encoding="utf-8")
    assert len(re.findall(r"^#\s+\S", md, re.MULTILINE)) == 1, (
        "tos.md carries a second level-1 heading — the document gets two h1 or a heading "
        "the outline does not count"
    )
    assert not re.findall(r"^```", md, re.MULTILINE), (
        "tos.md uses a fenced block, which _legal_html() does not render"
    )

    declared = re.search(r'<html lang="([a-z]+)"', body).group(1)
    want = "ru" if re.search(r"[а-яё]", md) else "en"
    assert declared == want, f"the served terms say lang={declared}, the text is {want}"


@pytest.mark.parametrize(
    "path,static_tg_link",
    [("/", True), ("/u/777", True), ("/m/oc/1", True), ("/m/1", False)],
)
def test_share_pages_take_the_network_from_config(
    client, monkeypatch, path, static_tg_link
):
    """Chain name, USDC address and explorer are filled in from /api/info by
    chain.js, and the bot username is rendered server-side, so a Sepolia
    deploy cannot advertise a mainnet it is not connected to and no page can
    ship a t.me link that 404s before the scripts run."""
    from bot import config as cfg

    monkeypatch.setattr(cfg, "BOT_USERNAME", "base_tipbot")
    html = client.get(path).text

    assert "__BOT_USERNAME__" not in html
    assert 'data-net="chain"' in html
    assert "/chain.js" in html
    # No explorer URL and no USDC address baked in, for either network: a page
    # that ships one of these can tell a testnet story on a mainnet deploy or
    # the other way round. chain.js fills both from /api/info.
    assert "https://basescan.org" not in html
    assert "https://sepolia.basescan.org" not in html
    assert "0x8335c4" not in html
    assert "0x036CbD" not in html
    # /m/ builds its deep link from the API instead of a static href.
    assert ("https://t.me/base_tipbot" in html) is static_tg_link


def test_chain_js_is_served_as_javascript(client):
    """/chain.js is fetched with the same CSP budget as the other page scripts,
    and it is the only place the network labels are mapped. The type is pinned in
    server.py — the OS mime table answers differently on Windows and Linux."""
    r = client.get("/chain.js")
    assert r.status_code == 200
    assert r.headers["content-type"].split(";")[0] == "text/javascript"
    assert "function applyNetInfo" in r.text


def test_the_served_stylesheet_paints_the_option_rows_the_market_page_builds(client):
    """The classes the market page emits are classes the served stylesheet defines.

    This used to be a hand-written list of five names, which is why it kept
    asserting a bar modifier long after the row stopped carrying one. The subject
    set now comes from the page's own markup, so a row that gains a class gains a
    check.
    """
    css = client.get("/style.css").text
    markup = (Path(__file__).resolve().parents[1] / "web" / "static" / "market.html").read_text(
        encoding="utf-8-sig"
    )
    built = {
        token
        for value in re.findall(r'class="([^"]*)"', markup)
        for token in re.findall(r"[A-Za-z0-9_-]+", value)
        if "option" in token or token.startswith("bar")
    }
    missing = sorted(f".{token}" for token in built if f".{token}" not in css)
    assert not missing, missing
    assert len(built) >= 4, f"the scan read {sorted(built)}, which is not the market page"


def test_miniapp_meta_tags_render_public_url(client, monkeypatch):
    """/app must embed fc:miniapp meta with the REAL public URL — the Base
    App crawls these tags to render the launch card.

    The origin is pinned through config, not by replacing
    ``public_base_url``, because the config value is what that function reads:
    stubbing the function would pass even if the page stopped calling it."""
    from bot import config as cfg

    monkeypatch.setattr(cfg, "MINI_APP_URL", "https://tippy.example.com")
    monkeypatch.setattr(cfg, "WEBHOOK_URL", "")
    r = client.get('/app')
    assert r.status_code == 200
    assert 'fc:miniapp' in r.text
    assert 'https://tippy.example.com/app' in r.text
    assert 'https://tippy.example.com/miniapp-hero.png' in r.text
    assert '__PUBLIC_URL__' not in r.text, "placeholder must never leak to users"


def test_miniapp_clickables_are_real_buttons(client):
    """A ``<div data-act>`` never fires click from Enter and is not focusable, so
    the keyboard could not open a market card, pick a chip or choose an option.
    The templates are inline in the document, so this reads what /app serves."""
    html = client.get("/app").text
    offenders = [
        tag
        for tag in re.findall(r"<[a-z]+[^>]*\bdata-act\b[^>]*>", html)
        if not tag.startswith("<button") and 'id="modalOverlay"' not in tag
    ]
    assert offenders == [], "only #modalOverlay (a dismissal scrim) may stay a div"
    assert 'role="button"' not in html


@pytest.mark.parametrize("chain_id,label", [(8453, "Base"), (84532, "Base Sepolia")])
def test_miniapp_deposit_hint_names_the_chain_being_used(client, monkeypatch, chain_id, label):
    """/app tells the user which network to send USDC on; that line has to come
    from EXPECTED_CHAIN_ID, or a testnet build would point deposits at mainnet."""
    from bot import config as cfg

    monkeypatch.setattr(cfg, "EXPECTED_CHAIN_ID", chain_id)
    html = client.get('/app').text
    assert '__CHAIN_NAME__' not in html, "placeholder must never leak to users"
    assert f"сеть {label}" in html
    assert f"({label} network)" in html


def test_miniapp_withdraw_hint_quotes_the_configured_terms(client, monkeypatch):
    """The sheet used to spell out "1%, min 1 USDC, 5 per day" in three
    languages. An operator who changes a limit in .env must see the screen
    change: a hardcoded hint quotes terms the server no longer enforces."""
    from decimal import Decimal

    from bot import config as cfg

    monkeypatch.setattr(cfg, "WITHDRAW_FEE_PCT", Decimal("0.025"))
    monkeypatch.setattr(cfg, "MIN_WITHDRAW_USDC", Decimal("3"))
    monkeypatch.setattr(cfg, "MAX_WITHDRAWS_PER_DAY", 2)
    html = client.get('/app').text
    assert "2.5% комиссия, минимум 3 USDC, не более 2 в день" in html
    assert "2.5% fee, min 3 USDC, up to 2 per day" in html
    assert "手续费 2.5%，最低 3 USDC，每天最多 2 次" in html
    assert "1% fee, min 1 USDC" not in html


def _landing_hint_slots(markup: str) -> set[str]:
    """Ids of `.hint` elements the landing ships with no text of its own.

    The attribute order is the file's business, not the reader's: `searchFoot` is
    `class="hint palette-foot" id="…" aria-live="polite"` and a reader that
    demanded `id` right after `class` reported it as a filled slot."""
    out: set[str] = set()
    for match in re.finditer(r"<(p|div|span)\b([^>]*)>\s*</\1>", markup):
        attrs = match.group(2)
        hit = re.search(r'id="([\w-]+)"', attrs)
        if hit and re.search(r'class="[^"]*\bhint\b[^"]*"', attrs):
            out.add(hit.group(1))
    assert out, "the landing ships no empty hint slot — this reader needs re-reading"
    return out


def _landing_written_ids(sources: str) -> set[str]:
    """Every id a script writes content, text or visibility into.

    Both quote styles (site.js uses `$('x')`, app.js `$("x")`) and the variable hop
    (`const foot = $("searchFoot")` then `foot.textContent = …`), because a write
    through a name is the same write."""
    read = r"""(?:\$\(|getElementById\(|querySelector\()['"]#?([\w-]+)['"]\)"""
    write = r"\s*\.\s*(?:textContent|innerHTML|value|hidden|disabled)\s*="
    direct = set(re.findall(read + write, sources))
    bound = re.findall(r"(?:var|let|const)\s+([\w$]+)\s*=\s*" + read, sources)
    via_var = {
        ident for var, ident in bound
        if re.search(rf"\b{re.escape(var)}\s*\.\s*(?:textContent|innerHTML|value|hidden|disabled|classList)",
                     sources)
    }
    assert direct | via_var, "no script writes into an element by id — this reader needs re-reading"
    return direct | via_var


def test_the_landing_hint_readers_read_every_way_a_script_writes():
    """A slot reported as filled while nothing fills it is the defect this pair of
    readers exists for, so each shape the shipped files take is held here: the two
    quote styles, the variable hop, an attribute order that puts other attributes
    between `class` and `id`, and the shapes that must stay invisible — a filled
    hint, an element written by neither text nor visibility, and a non-hint slot."""
    markup = (
        '<div class="hint" id="alpha"></div>'
        '<p class="hint palette-foot" id="beta" aria-live="polite"></p>'
        '<span class="hint" id="gamma">already states its bound</span>'
        '<div class="row" id="delta"></div>'
    )
    assert _landing_hint_slots(markup) == {"alpha", "beta"}
    sources = (
        "$('alpha').textContent = 'x';\n"
        '$("beta").textContent = "y";\n'
        "const g = $('gamma'); g.className = 'z';\n"
        "const foot = $(\"beta\"); foot.textContent = 'y';\n"
    )
    assert _landing_written_ids(sources) == {"alpha", "beta"}
    written = _landing_written_ids(sources + "$('delta').addEventListener('click', f);\n")
    assert {"alpha", "beta"} == written, "a listener is not a write into a slot"


def test_every_hint_the_landing_ships_empty_is_filled_by_a_script_the_landing_loads():
    """A limit the page leaves blank is a bound the visitor decides without. The
    landing moved its bounds out of markup and into painted sentences, and a
    `<div class="hint" id="…"></div>` whose writer was renamed or deleted reads as
    an empty line under the field forever: the markup is valid, the script is happy
    on its own, and only the pair of them is wrong. Both sets come from the files —
    the slots from `index.html`, the writers from every script that same document
    loads — so a new slot joins the law by existing."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web" / "static"
    markup = (root / "index.html").read_text(encoding="utf-8-sig")
    scripts = re.findall(r'<script src="/([\w.-]+\.js)"', markup)
    assert scripts, "the landing loads no script of its own — the subject set is empty"
    sources = "\n".join((root / name).read_text(encoding="utf-8-sig") for name in scripts)
    unwritten = sorted(_landing_hint_slots(markup) - _landing_written_ids(sources))
    assert not unwritten, f"{unwritten} ship empty and no script in {scripts} writes into them"


def _server_substitutions(server_src: str) -> list[tuple[str, set[str], set[str]]]:
    """`(function, tokens it replaces, documents it can substitute into)`.

    Scoped by the enclosing function because the same module serves the site pages
    through `_static_page(page, …)` and the Mini App through its own route, and a
    token one of them needs says nothing about the other. A function that takes the
    page as a parameter gets the pages its own call sites pass."""
    import ast

    tree = ast.parse(server_src)
    out: list[tuple[str, set[str], set[str]]] = []
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        tokens = {
            node.args[0].value
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace" and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str) and node.args[0].value.startswith("__")
        }
        if not tokens:
            continue
        files = {
            node.value for node in ast.walk(fn)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and node.value.endswith(".html")
        }
        if any(arg.arg == "page" for arg in fn.args.args):
            for call in ast.walk(tree):
                if (isinstance(call, ast.Call) and getattr(call.func, "id", None) == fn.name
                        and call.args and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)):
                    files.add(call.args[0].value)
        assert files, f"{fn.name} replaces a placeholder into documents this reader cannot name"
        out.append((fn.name, tokens, files))
    assert out, "web/server.py substitutes no placeholder anywhere — this reader needs re-reading"
    return out


def test_every_placeholder_the_server_replaces_is_one_a_document_asks_for():
    """`__DEFAULT_DEADLINE_HOURS__` stayed in `_static_page` after the landing
    stopped carrying it, which reads to the next maintainer as a live contract: the
    page appears to take its default from the server while the sentence is painted
    from `/api/info`. A substitution no served document asks for is code doing
    nothing, and the honest answer is to delete it. The other direction — a token a
    document ships that no writer replaces — is what the design gate refuses in the
    painted bytes of every page."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    server_src = (root / "web" / "server.py").read_text(encoding="utf-8-sig")
    scopes = _server_substitutions(server_src)
    carried: dict[str, set[str]] = {}
    for _fn, _tokens, files in scopes:
        for name in files:
            if name not in carried:
                path = root / "web" / "static" / name
                assert path.is_file(), f"{name} is served by web/server.py and is not in web/static"
                carried[name] = set(re.findall(
                    r"__\w+__", path.read_text(encoding="utf-8-sig")))
    dead = sorted(
        f"{fn}: {token} (serves {sorted(files)})"
        for fn, tokens, files in scopes
        for token in tokens
        if not any(token in carried[name] for name in files)
    )
    assert not dead, "replaced by web/server.py and asked for by no document it serves: " + "; ".join(dead)


def test_landing_deadline_hint_quotes_the_configured_default(client, monkeypatch):
    """The form said "Пусто — без дедлайна", but an empty field has meant
    `now + MARKET_DEFAULT_HOURS` since deadlines became mandatory (015).

    The sentence is now painted from `/api/info` rather than typed into markup, so
    the gate reads the chain instead of the bytes: the config value reaches the
    published key, the published key reaches the sentence, and no figure of the
    author's sits between them. What it cannot see is the painted result — that is
    `test_the_open_layer_states_the_bounds_this_origin_publishes`, which needs a
    browser and so runs in the `production` set."""
    from pathlib import Path

    from bot import config as cfg

    monkeypatch.setattr(cfg, "MARKET_DEFAULT_HOURS", 42)
    monkeypatch.setattr(cfg, "MARKET_MAX_DEADLINE_HOURS", 4095)
    limits = client.get("/api/info").json()["limits"]
    assert limits["default_deadline_hours"] == 42
    assert limits["max_deadline_hours"] == 4095

    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "site.js").read_text(
        encoding="utf-8-sig")
    sentence = re.search(r"\$\(['\"]hoursLimits['\"]\)\.textContent = ([^;]+);", js)
    assert sentence, "the landing no longer paints the deadline hint this law exists for"
    painted = sentence.group(1)
    assert "L.default_deadline_hours" in painted and "L.max_deadline_hours" in painted, painted
    assert not re.search(r"\d", painted), f"a typed figure is back in the painted sentence: {painted}"

    html = client.get('/').text
    assert "без дедлайна" not in html
    assert "__DEFAULT_DEADLINE_HOURS__" not in html
    assert 'id="hoursLimits"' in html


# Each VOLUME_KINDS entry and the words the landing uses for it. A kind the copy
# does not name is a number the site under-reports to the visitor.
VOLUME_KIND_WORDS = {
    "deposit": "депозит",
    "tip": "чаев",
    "bet": "ставк",
    "market_create": "субсиди",
    "market_buy": "долей",
    "token_buy": "токен",
    "paywall": "пост",
    "channel_pay": "подписк",
    "x402": "x402",
}


def test_landing_volume_hint_names_every_volume_kind(client):
    """The chart's headline claim is a list of what counts as volume; the SQL
    behind it sums `VOLUME_KINDS`. If the two sets diverge, the number on the
    page is bigger than the sentence that explains it."""
    from bot.ledger._views import VOLUME_KINDS

    assert set(VOLUME_KIND_WORDS) == set(VOLUME_KINDS), (
        f"VOLUME_KINDS = {sorted(VOLUME_KINDS)}, the hint's map = {sorted(VOLUME_KIND_WORDS)}"
    )
    hint = re.search(r'<p class="hint">(Дневной объём[^<]*)</p>', client.get('/').text)
    assert hint, "the volume chart lost its explanatory hint"
    missing = [kind for kind, word in VOLUME_KIND_WORDS.items() if word not in hint.group(1)]
    assert not missing, f"объём считается по {sorted(VOLUME_KINDS)}, а в тексте нет про: {missing}"


def test_landing_parimutuel_formula_names_the_winner_fee(client):
    """`stake / option pool × total pool` is the gross share; the ledger debits
    WIN_FEE_PCT from the winner. A formula printed without the fee promises more
    than the bot pays."""
    formula = re.search(r"Все ставки складываются.*?обнуляется\.", client.get('/').text, re.S)
    assert formula, "the parimutuel explanation is gone from the landing page"
    assert "комисс" in formula.group(0), "the payout formula does not mention the winner fee"


def test_farcaster_manifest_404_when_not_configured(client, monkeypatch):
    from pathlib import Path

    from web import server as web_server

    monkeypatch.setattr(web_server, "ROOT", Path(__file__).resolve().parent.parent)
    manifest = Path(__file__).resolve().parent.parent / 'deploy' / 'farcaster_manifest.json'
    exists = manifest.exists()
    if exists:
        pytest.skip('operator manifest present on this machine')
    r = client.get('/.well-known/farcaster.json')
    assert r.status_code == 404


def test_farcaster_manifest_served_when_configured(client):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    manifest = root / 'deploy' / 'farcaster_manifest.json'
    sample = '{"accountAssociation": {"header": "h", "payload": "p", "signature": "s"}}'
    manifest.write_text(sample, encoding='utf-8')
    try:
        r = client.get('/.well-known/farcaster.json')
        assert r.status_code == 200
        assert 'accountAssociation' in r.text
    finally:
        manifest.unlink()


def test_miniapp_webhook_always_200(client):
    r = client.post('/api/webhook-miniaction', json={'event': 'notification_clicked'})
    assert r.status_code == 200
    r = client.post('/api/webhook-miniaction', content=b'not json',
                    headers={'content-type': 'application/json'})
    assert r.status_code == 200  # never make the platform retry


def test_miniapp_webhook_oversized_body_returns_200(client):
    # An unbounded body read would let anyone park 100s of MB on the server
    # for free; the cap must still answer 200 (no platform retries).
    r = client.post('/api/webhook-miniaction', content=b'x' * 40_000,
                    headers={'content-type': 'application/json'})
    assert r.status_code == 200


def test_csp_header_strict_no_unsafe_inline(client):
    r = client.get('/')
    csp = r.headers.get('content-security-policy', '')
    assert 'script-src' in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert 'nonce-' in csp
    assert 'frame-ancestors' in csp
    # The Telegram login widget (/login) loads from telegram.org — a strict
    # CSP that does not source-allow it silently breaks the widget.
    assert 'https://telegram.org' in csp  # codeql[py/incomplete-url-substring-sanitization] — test asserts CSP header, not URL sanitization


def test_csp_injects_nonce_into_inline_scripts(client):
    # /app serves the Mini App which has inline <script> blocks → must get nonces.
    r = client.get('/app')
    assert 'nonce="' in r.text
    csp = r.headers.get('content-security-policy', '')
    m = re.search(r"'nonce-([^']+)'", csp)
    assert m, 'nonce missing from CSP'
    assert f'nonce="{m.group(1)}"' in r.text  # header nonce matches the inline tags
    # External module import from the KitSDK CDN must be source-whitelisted.
    assert 'https://esm.sh' in csp  # codeql[py/incomplete-url-substring-sanitization] — test asserts CSP header, not URL sanitization


def test_csp_nonce_varies_per_request(client):
    r1 = client.get('/app')
    r2 = client.get('/app')
    n1 = re.search(r"'nonce-([^']+)'", r1.headers['content-security-policy']).group(1)
    n2 = re.search(r"'nonce-([^']+)'", r2.headers['content-security-policy']).group(1)
    assert n1 != n2  # nonce must be unpredictable across requests


def test_csp_non_html_untouched(client):
    # API JSON responses should not be rewritten with nonces.
    r = client.get('/api/info')
    assert r.status_code == 200
    assert 'nonce="' not in r.text


# --- agent operator endpoints read the audit trail from STATE_DIR, not CWD ---

def _agent_env(ledger, monkeypatch, tmp_path):
    """Point bot.config.AGENT_TG_ID at a funded user and agent state at tmp_path,
    with a separate fake CWD holding a decoy trail. Returns (state_dir, cwd_dir)."""
    from agent import config as agent_config
    from bot import config as bot_config

    state_dir = tmp_path / "state"
    cwd_dir = tmp_path / "cwd"
    state_dir.mkdir()
    cwd_dir.mkdir()
    monkeypatch.setattr(bot_config, "AGENT_TG_ID", 42)
    monkeypatch.setattr(agent_config, "STATE_DIR", str(state_dir))
    ledger.credit(42, 10_000_000, "deposit")
    return state_dir, cwd_dir


def test_agent_status_reads_state_dir_not_cwd(client, ledger, monkeypatch, tmp_path):
    """Regression: agent.main writes agent_audit.jsonl into STATE_DIR while
    /api/agent/status used to open a CWD-relative copy, so the owner dashboard
    reported markets_created=0 and bets_placed=0 for an agent that was working."""
    state_dir, cwd_dir = _agent_env(ledger, monkeypatch, tmp_path)
    (state_dir / "agent_audit.jsonl").write_text(
        '{"market_id": 1, "bet_amount_usdc": 2.0, "bet_placed": true}\n'
        '{"market_id": 2, "bet_amount_usdc": 5.0, "bet_placed": false}\n',
        encoding="utf-8",
    )
    # A decoy in the working directory must be ignored, not counted.
    (cwd_dir / "agent_audit.jsonl").write_text(
        '{"market_id": 99, "bet_amount_usdc": 9.0, "bet_placed": true}\n', encoding="utf-8",
    )
    monkeypatch.chdir(cwd_dir)
    _auth(client, 111)
    data = client.get("/api/agent/status").json()
    assert data["markets_created"] == 2
    assert data["bets_placed"] == 1  # only the bet the ledger actually took


def test_agent_status_without_trail(client, ledger, monkeypatch, tmp_path):
    state_dir, _ = _agent_env(ledger, monkeypatch, tmp_path)
    assert not (state_dir / "agent_audit.jsonl").exists()
    _auth(client, 111)
    data = client.get("/api/agent/status").json()
    assert data["markets_created"] == 0 and data["bets_placed"] == 0
    assert "caps" in data


def test_agent_status_requires_admin(client, ledger, monkeypatch, tmp_path):
    _agent_env(ledger, monkeypatch, tmp_path)
    assert client.get("/api/agent/status").status_code == 403
    assert client.get("/api/agent/audit").status_code == 403


def test_agent_audit_tail_reads_state_dir(client, ledger, monkeypatch, tmp_path):
    """The tail reader seeks from the end of the file, so it must resolve the
    same path the writer used — and return the newest entries in file order."""
    state_dir, _ = _agent_env(ledger, monkeypatch, tmp_path)
    lines = [f'{{"market_id": {i}, "bet_amount_usdc": 1.0, "bet_placed": true}}' for i in range(1, 61)]
    (state_dir / "agent_audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _auth(client, 111)
    entries = client.get("/api/agent/audit").json()
    assert len(entries) == 50
    assert [entries[0]["market_id"], entries[-1]["market_id"]] == [11, 60]


def test_agent_audit_missing_trail_is_empty(client, ledger, monkeypatch, tmp_path):
    state_dir, _ = _agent_env(ledger, monkeypatch, tmp_path)
    _auth(client, 111)
    assert client.get("/api/agent/audit").json() == []


def test_pages_render_the_session_state_instead_of_a_doomed_probe(client):
    """Both scripts used to probe the session endpoints before knowing whether a
    session exists, so every anonymous visitor got a 401 in the browser console.
    The cookie is HttpOnly, so the server says whether it is there and the
    scripts read that off <html data-signed-in>."""
    anon = client.get("/").text
    assert 'data-signed-in="0"' in anon
    assert "__SIGNED_IN__" not in anon
    assert "dataset.signedIn" in client.get("/site.js").text

    app = client.get("/app").text
    assert 'data-signed-in="0"' in app
    assert "dataset.signedIn" in app

    _auth(client, 777)
    assert 'data-signed-in="1"' in client.get("/").text
    assert 'data-signed-in="1"' in client.get("/app").text


def test_mini_app_dictionary_is_complete_across_languages():
    """`web/static/app.html` keeps its own dictionary (I18N.ru/en/zh) outside
    `bot/i18n.py`, and `scripts/check_i18n.py` cannot see it. A key added for the
    Russian UI only renders as the raw identifier for an English or Chinese user,
    which is the exact drift AGENTS.md warns about — so it is enforced here."""
    text = _mini_app_text()
    dicts = _mini_app_dictionaries(text)
    assert dicts["ru"] == dicts["en"], dicts["ru"] ^ dicts["en"]
    assert dicts["ru"] == dicts["zh"], dicts["ru"] ^ dicts["zh"]

    used = set(re.findall(r"(?<![A-Za-z0-9_$.])t\(\s*'([a-z0-9_]+)'\s*\)", text))
    assert not used - dicts["ru"], sorted(used - dicts["ru"])


def _class_attribute_spans(text: str) -> list[str]:
    """The raw value of every `class="…"` in the file, template included.

    A JS renderer's markup is a string literal in this tree, so the attribute is
    read the same way whether it sits in `.html` or in a `.js` template."""
    return re.findall(r'class="([^"]+)"', text)


def _literal_markup(attr_value: str) -> str:
    """A class attribute with every `${…}` interpolation replaced by a sentinel.

    A template decides part of the value in code position —
    `class="chip${v === value ? ' active' : ''}"` — and the words inside that
    expression belong to JS, not to markup. Read them as classes and `value`
    fails this gate as an undefined class while the `active` it selects is real:
    the reader would be judging the grammar of the file, not its design. The
    sentinel stays inside the token it interrupts, so `class="tag-${state}"`
    yields no name at all rather than a half-word `tag-`."""
    out: list[str] = []
    i, depth = 0, 0
    while i < len(attr_value):
        if depth == 0 and attr_value.startswith("${", i):
            out.append("\x00")
            depth, i = 1, i + 2
            continue
        if depth:
            if attr_value[i] == "{":
                depth += 1
            elif attr_value[i] == "}":
                depth -= 1
            i += 1
            continue
        out.append(attr_value[i])
        i += 1
    return "".join(out)


def _painted_classes(text: str) -> set[str]:
    """Class names the document or a renderer writes into markup.

    A JS template closes the attribute to concatenate — `'<div class="bar-fill' +
    cls + '" aria-hidden…'` — so the raw value alternates string and code. Even
    segments (counted from the opening string) are the literal markup; a bare
    word in an odd segment is a variable name, not a class, and `agent.js`
    proved the difference: `cls` failed this gate as an undefined class while
    the real `bar-fill-over` it holds was defined all along."""
    used = set()
    for value in _class_attribute_spans(text):
        for i, segment in enumerate(_literal_markup(value).split("'")):
            if i % 2 == 0:
                used |= {
                    c for c in segment.split()
                    if "\x00" not in c and re.fullmatch(r"[a-zA-Z][\w-]*", c)
                }
    used |= set(re.findall(r"classList\.(?:add|toggle|remove)\(\s*['\"]([\w-]+)['\"]", text))
    return used


def test_the_class_reader_reads_a_class_attribute_the_way_a_template_writes_one():
    """Both readers exist to name classes, and each has already been fooled once by
    a word in code position. Held here to the shapes the shipped files take: a
    ternary inside an interpolation, a state-suffixed name, a closed attribute
    concatenation, a nested template and an interpolation the attribute never
    closes — plus the plain literal, so a reader that returned nothing for
    everything could not pass."""
    assert _painted_classes('<span class="chip active">') == {"chip", "active"}
    assert _painted_classes("""<button class="chip${v === value ? ' active' : ''}">""") == set()
    assert _painted_classes('<span class="tag-${state}">') == set()
    painted_bar = _painted_classes("""'<div class="bar-fill' + cls + '" aria-hidden="true">'""")
    assert painted_bar == {"bar-fill"}, "the literal half of a concatenated attribute is markup"
    assert "cls" not in painted_bar, "the variable holding `bar-fill-over` is not a class"
    assert _painted_classes("<div class=\"row ${a ? `sub-${b}` : ''} x\">") == {"row", "x"}
    assert _painted_classes('<div class="row ${unclosed x">') == {"row"}
    assert "\x00" in _literal_markup("a ${b}") and "\x00" not in _literal_markup("a b")
    assert _class_attribute_spans('class="a" class="b"') == ["a", "b"]


def test_every_class_a_page_paints_is_defined_in_the_stylesheet_that_serves_it():
    """AGENTS.md requires class parity in the direction a rename breaks: markup
    carrying a class no rule defines renders unstyled and looks intentional until
    someone compares two screens. The unused direction is not machine-checkable
    without a long exception list (several classes arrive through f-strings), so
    this pins the one that can be checked without guessing — and it covers the JS
    renderers, because that is where the templates actually live: `cmd-group` was
    emitted from app.js and no HTML file mentioned it. What it cannot see is a
    class the template decides in code position — through a variable (`agent.js`
    builds `bar-fill-over` that way, inside `cls`) or inside `${…}`
    (`class="tag-${state}"` selects a `.tag-open` that this gate never reads) —
    a word in code position is not evidence of markup, and the gate stays honest
    about that instead of keeping a list of exceptions.

    The Mini App is measured against its own `<style>` rather than skipped: it is
    one self-contained document, so `style.css` says nothing about a class its JS
    toggles — which is the same hole that let the 4.27:1 chip through, in the one
    direction a typo in `classList.toggle('warn')` would have landed unstyled."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web" / "static"

    css = (root / "style.css").read_text(encoding="utf-8-sig")
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    # Every file the directory holds, not a list someone has to remember: the
    # legal shell arrived as a new page and would have been outside this gate for
    # the whole time it was on the site, which is the same hole `cmd-group` fell
    # through — a page nobody thought to name is a page nobody measures.
    surfaces = sorted(p.name for p in root.glob("*.html") if p.name != "app.html") \
        + sorted(p.name for p in root.glob("*.js"))
    for name in surfaces:
        text = (root / name).read_text(encoding="utf-8-sig")
        assert _painted_classes(text) <= defined, f"{name}: {sorted(_painted_classes(text) - defined)}"

    app = (root / "app.html").read_text(encoding="utf-8-sig")
    style = re.search(r"<style>(.*?)</style>", app, re.S).group(1)
    own = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
    used = _painted_classes(app.replace(style, ""))
    assert used <= own, f"app.html: {sorted(used - own)}"


def test_command_groups_match_the_website_renderer():
    """bot/commands_catalog.py owns the group, web/static/app.js owns the heading
    and its order. A group that exists in one and not the other means commands
    silently fall into «Прочее» (or a heading renders over nothing)."""
    from pathlib import Path

    from bot.commands_catalog import BOT_COMMAND_SPECS

    js = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.js").read_text(
        encoding="utf-8-sig")
    block = re.search(r"const CMD_GROUPS = \[(.*?)\n\];", js, re.S)
    assert block, "CMD_GROUPS not found in app.js"
    declared = set(re.findall(r'\[\s*"([a-z]+)",', block.group(1)))
    used = {group for _cmd, _desc, group in BOT_COMMAND_SPECS}
    assert used == declared, used ^ declared
    assert all(desc.strip() for _cmd, desc, _group in BOT_COMMAND_SPECS)


def test_docs_do_not_advertise_a_palette_or_entrypoint_that_does_not_exist():
    """base.org was measured, not assumed, and #0052FF appears zero times on it —
    yet the README still described the design system with that hex for weeks after
    the rebuild. Same for `python main.py`, a command for a file the repo never
    had. Both are the kind of drift that only a check can keep out."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("README.md", "docs/ARCHITECTURE.md", "web/static/style.css", "web/static/app.html"):
        text = (root / rel).read_text(encoding="utf-8").upper()
        assert "#0052FF" not in text, rel

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "python main.py" not in readme
    assert "deploy/run.py" in readme


def test_the_documents_agree_on_the_size_of_their_own_run():
    """The route and command inventories this law used to measure are now read in
    `tests/test_docs.py`, where the subject set is `git ls-files web/*.py` and the command
    catalogue rather than the three files someone remembered: `web/auth.py` had been serving
    five routes while this law's hand-written list kept answering 48, and the whitepaper's own
    opening line carried that 48 into prose no gate read.

    What stays here is the half that is not an inventory: five documents publish one suite
    size. `test_the_documented_suite_size_is_the_size_of_this_run` pins that figure to the run
    that is happening — agreement alone is not truth, these statements said "66 production"
    while the run deselected 74, and every gate stayed green because each only compared the
    documents to each other. Here they must still agree."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    paper = (root / "docs" / "WHITEPAPER.md").read_text(encoding="utf-8")

    # The size of the suite is stated in three files, and all three are pinned to
    # the *run itself* by test_the_documented_suite_size_is_the_size_of_this_run:
    # agreement alone is not truth — these statements said "66 production" while
    # the run deselected 74, and every gate stayed green because each only
    # compared the documents to each other. Here they must still agree.
    readme = (root / "README.md").read_text(encoding="utf-8")
    stated = [
        # `тестов|теста` and `боевой|боевых` are Russian numeral agreement, not two
        # statements: 1432 governs «теста», 1408 governed «тестов». The digits are still
        # captured, so a stale count fails exactly as before.
        re.search(r"(\d+) обычных тест(?:ов|а) \+ (\d+) боев(?:ой|ых)", paper),
        re.search(r"(\d+) ordinary \+ (\d+) production", paper),
        re.search(r"(\d+) \+ (\d+) tests", paper),
        re.search(r"(\d+) tests \((\d+) more, marked",
                  (root / "AGENTS.md").read_text(encoding="utf-8-sig")),
    ]
    assert all(stated), "a document stopped stating its test counts"
    pairs = {tuple(t.groups()) for t in stated}
    assert len(pairs) == 1, [t.groups() for t in stated]
    ordinary, production = pairs.pop()

    readme_run = re.search(r'-m "not production"\s+# (\d+) passed', readme)
    readme_prod = re.search(r"The\s+(\d+) `production`-marked tests are excluded", readme)
    assert readme_run and readme_prod, "the README stopped counting the run it documents"
    assert readme_run.group(1) == ordinary, (readme_run.group(1), ordinary)
    assert readme_prod.group(1) == production, (readme_prod.group(1), production)


def test_the_documented_suite_size_is_the_size_of_this_run(request):
    """`conftest.py` drops and recreates `tipbot_test` **at import**, which happens
    during collection — so a nested `pytest --collect-only` would destroy the
    database of the run that spawned it, and no gate could re-measure the suite
    from inside it. (Measured: a sentinel table written before such a subprocess
    was gone when it finished.)

    The number is therefore taken from the run that is already happening. When
    this session is the documented whole-tree ordinary run, its collected count
    *is* the size the README, AGENTS.md and the whitepaper publish, and a stale
    claim fails here instead of agreeing with itself in five places. A targeted
    file, a `-k` expression or another marker says nothing about the suite, so
    those runs skip and print why."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    markexpr = (request.config.option.markexpr or "").strip().strip('"')
    reason = None
    if request.config.getoption("file_or_dir"):
        reason = f"a partial run over {request.config.getoption('file_or_dir')}"
    elif request.config.option.keyword:
        reason = f"a `-k {request.config.option.keyword}` run"
    elif markexpr != "not production":
        reason = f"-m {markexpr!r} is not the documented ordinary run"
    if reason:
        pytest.skip(f"{reason} — it says nothing about the size of the suite")

    measured = len(request.session.items)
    paper = (root / "docs" / "WHITEPAPER.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8-sig")
    readme = (root / "README.md").read_text(encoding="utf-8")
    claims = {
        "whitepaper ru": re.search(r"(\d+) обычных тест(?:ов|а) \+ \d+ боев(?:ой|ых)", paper),
        "whitepaper en": re.search(r"(\d+) ordinary \+ \d+ production", paper),
        "whitepaper short": re.search(r"(\d+) \+ \d+ tests", paper),
        "AGENTS.md": re.search(r"(\d+) tests \((\d+) more, marked", agents),
        "README run line": re.search(r'-m "not production"\s+# (\d+) passed', readme),
    }
    assert all(claims.values()), [k for k, v in claims.items() if not v]
    stated = {key: int(match.group(1)) for key, match in claims.items()}
    assert set(stated.values()) == {measured}, (measured, stated)


def test_prod_health_workflow_fails_instead_of_skipping():
    """`Prod Health Check & Validation` ran on a cron and reported *green* while
    the origin answered 503: two curl steps turned "site is down" into a
    `::warning::` plus `reachable=false`, and the pytest step was gated on both
    outputs, so the only test of the live site never executed. A monitoring gate
    has to be able to fail, so the shape that allowed that stays banned here.
    The single allowed suppression is a human typing `expect_down` by hand."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "prod-health-check.yml"
    ).read_text(encoding="utf-8")

    assert "pytest tests/test_production_health.py tests/test_production_design.py -m production" in text
    assert "::warning::" not in text
    assert "reachable=false" not in text

    # No step may be gated on a probe output: that is the escape hatch.
    gated = re.findall(r"^\s+if: (.+)$", text, re.M)
    assert gated, "the workflow lost every `if:` and this test needs re-reading"
    for condition in gated:
        assert "steps." not in condition, condition
    assert sum("expect_down" in c for c in gated) == 2, gated

    # `expect_down` only exists as a manual, default-off input.
    assert "workflow_dispatch" in text
    assert re.search(r"expect_down:\n\s+type: boolean\n\s+required: false\n\s+default: false", text)
    assert "schedule:" in text


# ---------- /api/ask and the assistant's availability ----------


def _no_provider(monkeypatch):
    """Pretend nothing can serve a model answer: no key anywhere, no model server
    on this host and no public fallback. That is the state the no-model answer
    path exists for, and the only state in which /api/info may say "data" —
    with a fallback enabled the assistant does run on a model, which is the point."""
    from bot import ai
    from web import server

    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr(ai.config, "AI_PROVIDER_CHAIN", "")
    monkeypatch.setattr(ai.config, "AI_AUTODISCOVER_KEYS", False)
    monkeypatch.setattr(ai.config, "AI_FREE_ENDPOINT", False)
    monkeypatch.setattr(ai.config, "AI_LOCAL_SERVERS", "")
    ai._LOCAL.update(at=0.0, providers=[])
    monkeypatch.setattr(server.config, "AI_COOLDOWN_SECONDS", 0)


def _one_market(ledger):
    ledger.credit(4101, 200 * 10**6, "deposit")
    return ledger.create_market(4101, "Перепишет ли Tippy 1000 чаевых?", ["Да", "Нет"], 20 * 10**6)


def test_the_trade_panel_quotes_the_numbers_the_write_path_books(client, ledger):
    """The landing asks the server for a quote instead of recomputing LMSR in
    float64, which only buys anything if the quote *is* the trade: the share
    count must be the one `buy_shares` books, the two odds must be the ones the
    cards print before and after it, and every refusal the panel can draw has to
    be a refusal the endpoint really makes."""
    from bot import config as cfg

    mid = _one_market(ledger)
    ledger.credit(4102, 200 * 10**6, "deposit")

    r = client.get(f"/api/prediction/{mid}/quote", params={"option": 0, "amount": "5"})
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["market_id"] == mid and q["label"] == "Да"
    assert q["amount_usdc"] == 5.0
    assert 0 < q["cost_usdc"] <= q["amount_usdc"]
    assert q["shares"] > 0 and q["payout_usdc"] == q["shares_usdc"]
    assert q["max_trade_usdc"] == float(cfg.MARKET_MAX_TRADE_USDC)

    before = next(m for m in client.get("/api/predictions").json() if m["id"] == mid)
    assert before["options"][0]["price_pct"] == q["price_before_pct"]

    status, info = ledger.buy_shares(mid, 4102, 0, 5 * 10**6)
    assert status == "ok", status
    assert info["shares"] == q["shares"], (info["shares"], q["shares"])
    after = next(m for m in client.get("/api/predictions").json() if m["id"] == mid)
    assert after["options"][0]["price_pct"] == q["price_after_pct"]

    # refusals: the same bounds the panel's own chips are built from
    assert client.get(
        f"/api/prediction/{mid}/quote", params={"option": 7, "amount": "5"}
    ).status_code == 400
    dust = client.get(f"/api/prediction/{mid}/quote", params={"option": 0, "amount": "0.001"})
    assert dust.status_code == 400
    assert client.get(
        f"/api/prediction/{mid}/quote", params={"option": 0, "amount": "abc"}
    ).status_code == 400
    over = client.get(
        f"/api/prediction/{mid}/quote",
        params={"option": 0, "amount": str(float(cfg.MARKET_MAX_TRADE_USDC) + 1)},
    )
    assert over.status_code == 400
    assert any(ch.isdigit() for ch in over.json()["detail"]), over.json()
    assert client.get(
        "/api/prediction/999999/quote", params={"option": 0, "amount": "5"}
    ).status_code == 404

    from time import time

    # A market the ledger has closed is a market the panel must refuse, not a
    # curve it keeps pricing — and `mid` cannot be resolved before its deadline.
    closed = ledger.create_market(
        4101, "Рынок, который уже закрыт?", ["Да", "Нет"], 20 * 10**6,
        close_at=int(time()) - 1,
    )
    ok, msg = ledger.cancel_market(closed, 4101)
    assert ok, msg
    assert client.get(
        f"/api/prediction/{closed}/quote", params={"option": 0, "amount": "5"}
    ).status_code == 409


def test_a_config_name_written_into_a_ledger_docstring_resolves_on_config():
    """`buy_shares` documented that it rejects dust «below MARKET_MIN_TRADE_MICRO»
    while nothing in the repository defined that name: the reader could not find
    the floor, and the number that enforced it was copied into a second file. The
    same class as the ledger call sites that were never written — a promise in a
    docstring that the tree cannot answer."""
    import inspect

    from bot import config as cfg
    from bot.ledger import Ledger

    doc = inspect.getdoc(Ledger.buy_shares) or ""
    named = set(re.findall(r"\b(MARKET_[A-Z0-9_]{3,})\b", doc))
    assert named, "the docstring names no bound — this gate has stopped measuring"
    unresolvable = {name for name in named if not hasattr(cfg, name)}
    assert not unresolvable, f"{sorted(unresolvable)} are documented but not configured"


def test_the_dust_floor_is_one_number_read_from_that_name_everywhere():
    """Three files enforced the same boundary by comparing against a literal, so
    an operator raising the floor would have to find all three — and the panel
    that copied it also copied the wrong word for it."""
    import ast

    from bot import config as cfg

    floor = cfg.MARKET_MIN_TRADE_MICRO
    root = Path(__file__).resolve().parents[1]
    offenders, readers = [], []
    for rel in ("bot/ledger/_markets.py", "web/server.py", "web/mini.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8-sig"))
        uses_name = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            comparators = [c for c in node.comparators if isinstance(c, ast.Constant)]
            for comp in comparators:
                if comp.value == floor and isinstance(comp.value, int):
                    offenders.append(f"{rel}:{node.lineno} compares against the literal {comp.value}")
            if any(
                isinstance(c, ast.Attribute) and c.attr == "MARKET_MIN_TRADE_MICRO"
                for c in node.comparators
            ):
                uses_name = True
        if not uses_name:
            offenders.append(f"{rel} enforces no floor read from config.MARKET_MIN_TRADE_MICRO")
    assert not offenders, offenders


def test_the_dust_floor_answers_the_same_word_below_and_above_it(client, ledger):
    """The reader of a refusal has to learn which bound broke. Below the floor the
    answer is the floor with its number; at the floor the order is not dust any
    more, and «Минимальная сумма — 0.01 USDC» would be a sentence about an amount
    the user just typed."""
    from bot import config as cfg

    mid = _one_market(ledger)
    ledger.credit(4103, 200 * 10**6, "deposit")
    dust_url = f"/api/prediction/{mid}/quote"
    floor = cfg.MARKET_MIN_TRADE_MICRO
    floor_usdc = f"{floor / 10**6:g}"
    below_usdc = f"{(floor - 1) / 10**6}"

    below = client.get(dust_url, params={"option": 0, "amount": below_usdc})
    assert below.status_code == 400
    detail = below.json()["detail"]
    assert f"{floor_usdc} USDC" in detail, detail
    assert "больше нуля" not in detail, detail
    assert "числом" not in detail, detail

    at = client.get(dust_url, params={"option": 0, "amount": floor_usdc})
    assert f"{floor_usdc} USDC" not in at.json().get("detail", ""), (at.status_code, at.json())

    # the write path refuses the amount just below the name's value
    assert ledger.buy_shares(mid, 4103, 0, floor - 1)[0] == "toosmall"

    typed = client.get(dust_url, params={"option": 0, "amount": "abc"})
    assert typed.status_code == 400
    assert "числом" in typed.json()["detail"], typed.json()
    assert "больше нуля" not in typed.json()["detail"], typed.json()


def _route_call_matcher(path):
    """A regex for the way a shipped script spells a route: whole path in one
    string, or the part before the first `{placeholder}` quoted and the id
    appended by concatenation."""
    head, *rest = re.split(r"\{[^}]+\}", path)
    if not rest:
        return r"""["']""" + re.escape(path) + r"""["']"""
    tail = "".join(re.escape(piece) for piece in rest)
    return (
        r"""["']""" + re.escape(head) + r"[^'\"]+" + tail + r"""["']"""
        + "|"
        + r"""["']""" + re.escape(head) + r"""["']"""
    )


def _enclosing_function(text, idx):
    """The top-level `function` block a byte offset falls inside, so an amount
    is read from the code that actually sends it and not from the whole file."""
    starts = [m.start() for m in re.finditer(r"^[ \t]*(?:async[ \t]+)?function[ \t]+\w+", text, re.M)]
    before = [s for s in starts if s <= idx]
    if not before:
        return ""
    after = [s for s in starts if s > idx]
    return text[before[-1]: after[0] if after else len(text)]


def _money_bounds_by_route():
    """`route -> {attr: (config name, Decimal)}` for every refusal that names a
    money bound. The value is resolved through the handler's own assignments:
    `min=_exact(min_micro)` where `min_micro = _cap_micro(config.MIN_WITHDRAW_USDC)`
    is the same number, and a name ending in `_MICRO` is a micro-unit figure
    while every other money knob this repository configures is a USDC figure."""
    import ast
    from decimal import Decimal

    from bot import config as cfg

    root = Path(__file__).resolve().parents[1]
    out = {}
    for rel in ("web/server.py", "web/mini.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            routes = [
                d.args[0].value
                for d in node.decorator_list
                if isinstance(d, ast.Call) and d.args
                and isinstance(d.args[0], ast.Constant)
                and isinstance(d.args[0].value, str)
            ]
            if not routes:
                continue
            assigned = {
                t.id: ast.unparse(stmt.value)
                for stmt in ast.walk(node)
                if isinstance(stmt, ast.Assign)
                for t in stmt.targets
                if isinstance(t, ast.Name)
            }
            for call in ast.walk(node):
                if not (isinstance(call, ast.Call)
                        and getattr(call.func, "id", "") in ("refuse", "refuse_protocol")):
                    continue
                for kw in call.keywords:
                    if kw.arg not in ("min", "cap"):
                        continue
                    text = ast.unparse(kw.value)
                    names = set(re.findall(r"\bconfig\.([A-Z0-9_]+)", text))
                    if not names and re.fullmatch(r"_\w+\((\w+)\)", text):
                        inner = re.fullmatch(r"_\w+\((\w+)\)", text).group(1)
                        names = set(re.findall(r"\bconfig\.([A-Z0-9_]+)", assigned.get(inner, "")))
                    if len(names) != 1:
                        raise AssertionError(f"{rel}: {text} names {sorted(names)} — not one bound")
                    name = names.pop()
                    raw = getattr(cfg, name)
                    value = (Decimal(raw) / Decimal(10**6)) if name.endswith("_MICRO") else Decimal(str(raw))
                    attr = "min" if kw.arg == "min" else "max"
                    for route in routes:
                        out.setdefault(route, {})[attr] = (name, value)
    return out


def _limits_the_origin_publishes(client):
    """Every USDC number `/api/info` answers with, so a page never has to
    remember one: the limits block and the x402 block it publishes beside it."""
    from decimal import Decimal

    info = client.get("/api/info").json()
    published = {}
    for block in ("limits", "x402"):
        for key, value in (info.get(block) or {}).items():
            if isinstance(value, (int, float)) and key.endswith(("_usdc", "_micro")):
                published[f"{block}.{key}"] = Decimal(str(value))
    return published


def _bound_assigned_by_script(sources):
    """`(attr, limits key)` pairs each shipped script or sheet binds to one
    input id: `bindBound('wdAmount', L, [['min', 'min_withdraw_usdc']])` on the
    site and `boundAttr([['min', 'min_withdraw_usdc']])` inside the Mini App's
    own template, whose field id is the input the pair is interpolated into."""
    pairs = {}
    for text in sources.values():
        for m in re.finditer(
            r"""bindBound\(\s*['"](\w+)['"]\s*,\s*\w+\s*,\s*(\[.*?\])\s*\)""", text, re.S
        ):
            pairs.setdefault(m.group(1), set()).update(
                (a, k) for a, k in re.findall(r"""\[\s*['"](\w+)['"]\s*,\s*['"]([\w.]+)['"]""", m.group(2))
            )
        for m in re.finditer(r"""boundAttr\(\s*(\[.*?\])\s*\)""", text, re.S):
            head = text[max(0, m.start() - 400):m.start()]
            # The sheet paints several fields in one template: the id this
            # interpolation decorates is the closest one before it, not the first.
            found = re.findall(r"""id=[\\]?['"](\w+)['"]""", head)
            if not found:
                continue
            field = found[-1]
            for attr, lit, ident in re.findall(
                r"""\[\s*['"](\w+)['"]\s*,\s*(?:['"](\w+)['"]|(\w+))\s*\]""", m.group(1)
            ):
                if lit:
                    keys = [lit]
                else:
                    # The buy panel is one markup for a pool bet and an LMSR
                    # trade, so its ceiling is a name chosen at run time: every
                    # key its arms answer is a key the field states.
                    decl = re.search(r"const\s+" + re.escape(ident) + r"\s*=\s*([^;]+)", head)
                    expr = decl.group(1) if decl else ""
                    keys = re.findall(r"""['"](\w+)['"]""", expr.split("?", 1)[-1])
                pairs.setdefault(field, set()).update((attr, k) for k in keys)
    return pairs


def _hours_caps_by_route():
    """`route -> {parameter: (config name, hours, published key)}` for every
    refusal that caps a clock.

    `_money_bounds_by_route()` reads the `min`/`cap` kwargs whose values are USDC
    figures, so a ceiling stated in hours is invisible to it and to
    `_limits_the_origin_publishes()`, which collects the `_usdc`/`_micro` keys:
    the create route answered `api_deadline_long` over a configured ceiling while
    no law read the number the create fields offered. The parameter comes out of
    the comparison the refusal guards — `if body.hours and body.hours > config.X` —
    because a field is paired with the payload key it types, not with the attribute
    name the markup spells."""
    import ast

    from bot import config as cfg

    root = Path(__file__).resolve().parents[1]
    published = {}
    for rel in ("web/server.py", "web/mini.py"):
        text = (root / rel).read_text(encoding="utf-8-sig")
        published.update(
            (name, key) for key, name in re.findall(r"'(\w+)':\s*config\.([A-Z0-9_]+)", text)
        )
    out = {}
    for rel in ("web/server.py", "web/mini.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            routes = [
                d.args[0].value
                for d in node.decorator_list
                if isinstance(d, ast.Call) and d.args
                and isinstance(d.args[0], ast.Constant)
                and isinstance(d.args[0].value, str)
            ]
            if not routes:
                continue
            for call in ast.walk(node):
                if not (isinstance(call, ast.Call)
                        and getattr(call.func, "id", "") in ("refuse", "refuse_protocol")):
                    continue
                for kw in call.keywords:
                    if kw.arg != "max":
                        continue
                    names = [n for n in re.findall(r"\bconfig\.([A-Z0-9_]+)", ast.unparse(kw.value))
                             if n.endswith("_HOURS")]
                    if not names:
                        continue
                    assert len(names) == 1, f"{rel}: {ast.unparse(kw.value)} names {names}"
                    name = names[0]
                    key = published.get(name)
                    assert key, f"config.{name} caps a route but /api/info answers no name for it"
                    parameter = None
                    for cmp in ast.walk(node):
                        if isinstance(cmp, ast.Compare) and f"config.{name}" in ast.unparse(cmp):
                            found = re.findall(r"\b(?:body|payload)\.(\w+)", ast.unparse(cmp))
                            if found:
                                parameter = found[0]
                                break
                    assert parameter, f"config.{name} is compared against no payload field"
                    for route in routes:
                        out.setdefault(route, {})[parameter] = (name, getattr(cfg, name), key)
    return out


def test_the_clock_a_field_offers_is_the_clock_the_route_honours(client):
    """A deadline is a figure the user types before any refusal says otherwise, and
    both create surfaces offered one the route would not honour: the Mini App's
    hours fields carried no ceiling at all, and the landing typed `max="720"` and
    restated the same cap as «До 30 дней» while the knob it echoes lives in
    `config`. The money law cannot see either — it reads USDC kwargs and USDC keys —
    so the clock is measured here, out of the handler that refuses it."""
    from decimal import Decimal

    caps = _hours_caps_by_route()
    assert caps, "no shipped route caps a clock — the reader needs re-reading"
    limits = client.get("/api/info").json()["limits"]

    static = Path(__file__).resolve().parents[1] / "web" / "static"
    sources = {
        p.name: p.read_text(encoding="utf-8-sig")
        for p in sorted(static.glob("*.html")) + sorted(static.glob("*.js"))
    }
    bindings = _bound_assigned_by_script(sources)

    fields = {}
    for route, parameters in sorted(caps.items()):
        for parameter, (name, value, key) in sorted(parameters.items()):
            assert Decimal(str(limits[key])) == Decimal(str(value)), (
                f"{route} refuses {parameter} above {value} ({name}) while /api/info "
                f"publishes {key}={limits[key]}")
            for text in sources.values():
                for hit in re.finditer(_route_call_matcher(route), text):
                    body = _enclosing_function(text, hit.start())
                    for read in re.finditer(
                        r"""(?:getElementById|\$)\(\s*['"](\w+)['"]\s*\)""", body
                    ):
                        field = read.group(1)
                        if parameter.lower() in field.lower():
                            fields.setdefault(field, []).append((parameter, name, value, key, route))
    assert len(fields) >= 3, f"only {sorted(fields)} fields type a deadline"

    problems = []
    for field, refs in sorted(fields.items()):
        parameter, name, value, key, route = refs[0]
        if ("max", key) not in bindings.get(field, set()):
            problems.append(
                f"#{field} types the {parameter} {route} refuses above {value} ({name}) "
                f"and binds no ceiling: {sorted(bindings.get(field, set()))}")
        markup = next(
            (text for text in sources.values()
             if re.search(r"<input\b[^>]*id=[\"']" + re.escape(field) + r"[\"']", text)),
            "",
        )
        tag = re.search(
            r"<input\b[^>]*id=[\"']" + re.escape(field) + r"[\"'][^>]*>", markup, re.S
        )
        typed = re.search(r"\bmax=[\"']([^\"']*)[\"']", tag.group(0)) if tag else None
        if typed and Decimal(typed.group(1)) != Decimal(str(value)):
            problems.append(
                f"#{field} carries the fallback max={typed.group(1)} while {name} is {value}: "
                f"the number written into markup is a second copy of the knob")
    assert not problems, problems


def test_the_bound_a_route_refuses_with_is_the_bound_the_field_states(client, ledger):
    """A money field is the only place a user learns the bound before the
    refusal, and every bound this repository enforces was already copied into
    markup or not copied at all: `min="0"` on a withdrawal the route rejects
    under `MIN_WITHDRAW_USDC`, a subsidy floor of 0 against a configured 10, an
    absent ceiling on a tip, and the whole Mini App, which never read the
    published numbers at all.

    Both halves are measured from the tree: the bound comes out of the
    handler's own `refuse(..., min=config.X)` through the assignments it
    resolves to, and the field comes out of the HTML attribute or the script
    that binds the published key to it. A bound no endpoint publishes is a
    bound a page can only invent."""
    from decimal import Decimal

    routes = _money_bounds_by_route()
    assert len(routes) >= 6, f"only {len(routes)} routes name a money bound"
    published = _limits_the_origin_publishes(client)
    assert len(published) >= 8, f"only {len(published)} bounds are published"

    # (a) every bound a refusal states is published, so a page can read it.
    unbound = []
    for route, bounds in routes.items():
        for attr, (name, value) in bounds.items():
            if value not in published.values():
                unbound.append(f"{route} refuses on {attr}={value} ({name}), which nothing publishes")
    assert not unbound, unbound

    # The quote endpoint is the second publisher of the same two numbers: the
    # drawer reads its bounds off the quote it just asked for, so the quote and
    # /api/info cannot be allowed to answer different floors for one market.
    mid = _one_market(ledger)
    quote = client.get(
        f"/api/prediction/{mid}/quote", params={"option": 0, "amount": "5"}
    ).json()
    for key, attr in (("min_trade_usdc", "min"), ("max_trade_usdc", "max")):
        assert Decimal(str(quote[key])) == routes["/api/prediction/{market_id}/quote"][attr][1], key

    static = Path(__file__).resolve().parents[1] / "web" / "static"
    sources = {
        p.name: p.read_text(encoding="utf-8-sig")
        for p in sorted(static.glob("*.html")) + sorted(static.glob("*.js"))
    }
    typed_into = {}
    for text in sources.values():
        for route in routes:
            for hit in re.finditer(_route_call_matcher(route), text):
                body = _enclosing_function(text, hit.start())
                for read in re.finditer(
                    r"""(?:num\(\s*['"](\w+)['"]\s*\)"""
                    r"""|(?:getElementById|\$)\(\s*['"](\w+)['"]\s*\)\s*\.value)""",
                    body,
                ):
                    typed_into.setdefault(read.group(1) or read.group(2), set()).add(route)
    assert typed_into, "no shipped field types an amount into a bound-refusing route"

    # A create-market form reads a question, a deadline and a subsidy: only the
    # money field is held to the money bound.
    def is_money_field(input_id):
        for text in sources.values():
            tag = re.search(
                r"<input\b[^>]*id=[\"']" + re.escape(input_id) + r"[\"'][^>]*>", text, re.S
            )
            if tag:
                chunk = tag.group(0)
                return bool(
                    re.search(r"""type=[\"']number[\"']""", chunk)
                    and re.search(r"""inputmode=[\"']decimal[\"']|step=[\"']0\.\d+[\"']""", chunk)
                )
        return False

    amount_fields = {k: v for k, v in typed_into.items() if is_money_field(k)}
    assert len(amount_fields) >= 5, f"only {len(amount_fields)} money fields were read"

    bindings = _bound_assigned_by_script(sources)

    # The Mini App's `LIMITS` is the only reader of the published bounds block,
    # so a mutation that changes where it reads from (`.limits` → `.fees`)
    # leaves every `boundAttr` call pointing at an empty object. The source must
    # state the block name, and that block must carry every key a `boundAttr`
    # call references — a block of percentages cannot answer for USDC bounds.
    app_text = sources.get("app.html", "")
    # The Mini App keeps the whole /api/info answer in one object and slices the
    # bounds out of it, because the fee block and the chain block are read
    # beside the bounds. The slice still has to name a block of that answer and
    # come out of the very object the /api/info response was assigned to: point
    # it at another name, or at `.fees`, and every `boundAttr` call below reads
    # an empty object and this law goes blind on purpose.
    sliced = re.search(r"LIMITS\s*=\s*\(await\s+r\.json\(\)\)\.(\w+)"
                       r"|LIMITS\s*=\s*([A-Za-z_$][\w$]*)\.(\w+)", app_text)
    assert sliced, "the Mini App no longer slices the published bounds out of the /api/info answer"
    direct, holder, sliced_block = sliced.groups()
    if holder is not None:
        loaders = [_enclosing_function(app_text, hit.start())
                   for hit in re.finditer(r"""fetch\(\s*['"]/api/info['"]""", app_text)]
        assert loaders, f"nothing fetches /api/info, yet LIMITS is sliced out of {holder}.…"
        assigned = {m.group(1) for body in loaders
                    for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*=\s*await\s+r\.json\(\)", body)}
        assert holder in assigned, (
            f"LIMITS is sliced out of {holder}.…, which is not the object this origin's "
            f"/api/info answer was assigned to ({sorted(assigned) or 'no response parsed'})")
        block = sliced_block
    else:
        block = direct
    info = client.get("/api/info").json()
    block_keys = set((info.get(block) or {}).keys())
    bound_keys = {key for pairs in bindings.values() for attr, key in pairs}
    missing = bound_keys - block_keys
    assert not missing, (
        f"Mini App reads LIMITS from .{block}, which carries {block_keys}; "
        f"boundAttr references {bound_keys}, missing {missing}"
    )

    def published_value(key):
        """The limits block and the x402 block are one namespace to a page: a
        binding names the key, the origin answers it under one of the two."""
        for cand in (f"limits.{key}", f"x402.{key}", key):
            if cand in published:
                return published[cand]
        return None

    checked = 0
    failures = []
    for input_id, refs in sorted(amount_fields.items()):
        markup = next(
            (text for text in sources.values()
             if re.search(r"<input\b[^>]*id=[\"']" + re.escape(input_id) + r"[\"']", text)),
            "",
        )
        tag = re.search(r"<input\b[^>]*id=[\"']" + re.escape(input_id) + r"[\"'][^>]*>", markup, re.S)
        for route in sorted(refs):
            for attr, (name, value) in sorted(routes[route].items()):
                checked += 1
                literal = re.search(r"\b" + attr + r"=[\"']([^\"']*)[\"']", tag.group(0)) if tag else None
                stated = []
                if literal:
                    stated.append(("markup", Decimal(literal.group(1))))
                for b_attr, key in sorted(bindings.get(input_id, ())):
                    if b_attr != attr:
                        continue
                    bound = published_value(key)
                    if bound is None:
                        failures.append(
                            f"#{input_id} binds {attr} to '{key}', a key no block of "
                            f"/api/info answers ({route} refuses at {value})"
                        )
                    else:
                        stated.append((f"{key} bound at run time", bound))
                if not stated:
                    failures.append(
                        f"#{input_id} types into {route}, which refuses on {attr}={value} "
                        f"({name}); the field states no bound"
                    )
                elif any(d != value for _, d in stated):
                    failures.append(
                        f"#{input_id} states {[(where, str(d)) for where, d in stated]} for "
                        f"{attr}, but {route} refuses at {value} ({name})"
                    )
    assert not failures, failures
    assert checked >= 8, f"only {checked} field/bound pairs were measured"

    # (b) …and a bounds *sentence* is built from the published number too. The
    # drawer used to paint «От 0.01 до 500 USDC» from a typed literal while the
    # ceiling beside it came from the API, so one operator edit moved one figure.
    typed = re.compile(
        r"(?:\b(?:от|до|из|from|to|per|за|минимум|максимум|minimum|maximum|至少|上限)\b\W{0,8})"
        r"(?:\$\s?\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*USDC|USDC\s*\$?\d+(?:[.,]\d+)?)",
        re.I,
    )
    offenders = [f"{name}: {m.group(0)!r}"
                 for name, text in sources.items()
                 for line in text.splitlines()
                 for m in typed.finditer(line)]
    assert not offenders, offenders


def _mini_app_text():
    """`web/static/app.html` ships its markup, its stylesheet and its script in
    one document; the laws below ask what that document paints."""
    return (Path(__file__).resolve().parents[1] / "web" / "static" / "app.html").read_text(
        encoding="utf-8-sig")


def _enclosing_function_name(text, idx):
    body = _enclosing_function(text, idx)
    match = re.match(r"[ \t]*(?:async[ \t]+)?function[ \t]+(\w+)", body)
    return match.group(1) if match else "<top level>"


def test_every_money_panel_the_mini_app_paints_states_what_the_account_holds():
    """A panel that takes a sum is the only place a user learns what they can
    actually move, and not one of them said it: the tip sheet opened on 5 USDC
    against a 3 USDC balance, the buy panel offered the same 5 with nothing on
    the account, and the withdrawal sheet stated the fee but never the balance
    the fee is added on top of.

    The subject set is the shipped markup, not a list of sheets somebody
    remembers: every panel that types a decimal amount owes the line, so a new
    money field joins this law the moment it exists."""
    text = _mini_app_text()
    panels = {}
    for hit in re.finditer(r'inputmode="decimal"', text):
        panels[_enclosing_function_name(text, hit.start())] = _enclosing_function(text, hit.start())
    assert len(panels) >= 3, f"only {len(panels)} Mini App panels type a decimal amount: {sorted(panels)}"
    silent = sorted(name for name, body in panels.items() if "availableLine()" not in body)
    assert not silent, f"these money panels take a sum and never say what the account holds: {silent}"


def test_a_chip_row_offers_the_one_ladder_the_balance_filters():
    """Two chip ladders are two answers to "what can I tap", and only the one
    that reads the balance knows the difference between offering 25 USDC and
    holding 3. The tip sheet typed four buttons by hand and the buy panel mapped
    the same four numbers a second time, so the identical tap worked on one
    screen and came back refused from the ledger on the other."""
    text = _mini_app_text()
    ladders = re.findall(r"\[\s*\d+(?:\s*,\s*\d+){2,}\s*\]", text)
    assert len(ladders) == 1, f"quick amounts are typed in {len(ladders)} places: {ladders}"
    declared = re.search(r"const QUICK_LADDER = (\[[^;]+);", text)
    assert declared, f"the one ladder is declared by no name: {ladders}"
    values = [int(v) for v in re.findall(r"\d+", declared.group(1))]
    standing = re.search(r"const QUICK_DEFAULT = (\d+);", text)
    assert standing and int(standing.group(1)) in values, (
        f"the amount a money field opens on is not one of the amounts it offers: "
        f"ladder {values}, default {standing.group(1) if standing else None}")
    orphan = set()
    for hit in re.finditer(r'class="chips"', text):
        name = _enclosing_function_name(text, hit.start())
        if "quickAmounts(" not in _enclosing_function(text, hit.start()):
            orphan.add(name)
    assert not orphan, (
        f"these chip rows are painted beside a ladder that never reads the balance: {sorted(orphan)}")


def test_the_confirmation_count_the_deposit_sheet_names_is_the_knob_the_sweep_uses(client):
    """A deposit is not spendable the moment it is seen — the sweep credits it
    `DEPOSIT_CONFIRM_BLOCKS` later — and the sheet that shows a QR is the only
    place a user can learn that, where it said nothing at all. The window is
    published from the knob and read back out of the published block, so an
    operator shortening it moves the screen and nothing else has to be told."""
    from bot import config

    limits = client.get("/api/info").json()["limits"]
    assert "deposit_confirmations" in limits, (
        f"/api/info publishes no confirmation window: {sorted(limits)}")
    assert limits["deposit_confirmations"] == config.DEPOSIT_CONFIRM_BLOCKS, (
        f"/api/info publishes {limits['deposit_confirmations']} while the sweep compares "
        f"against {config.DEPOSIT_CONFIRM_BLOCKS}")
    sweep = (Path(__file__).resolve().parents[1] / "bot" / "base.py").read_text(encoding="utf-8-sig")
    assert re.search(r"cutoff\s*=\s*current\s*-\s*config\.DEPOSIT_CONFIRM_BLOCKS\b", sweep), (
        "the deposit sweep no longer compares against the knob the sheet quotes")
    text = _mini_app_text()
    hit = re.search(r"infoRow\(\s*t\('dep_confirmations'\)\s*,\s*([A-Za-z_.]+)\s*\)", text)
    assert hit, "the deposit sheet states no confirmation count"
    assert hit.group(1) == "LIMITS.deposit_confirmations", (
        f"the confirmation row prints {hit.group(1)}, which is not the published knob")
    assert 'id="depAddr"' in _enclosing_function(text, hit.start()), (
        "the confirmation row is not painted by the panel that shows the deposit address")


def test_every_action_the_mini_app_paints_is_dispatched_to_a_function_that_exists():
    """`data-act` is a promise that a tap does something, and the delegated
    handler is a chain of `if`s that falls off the end in silence: an action no
    dispatcher answers is a control that eats the thumb, and a branch that calls
    a function nobody wrote is the same control with a console error behind it.
    Measured in both directions, because an action that never shipped and one
    that was removed leave the identical dead screen."""
    text = _mini_app_text()
    painted = set(re.findall(r'data-act="(\w+)"', text))
    assert len(painted) >= 15, f"only {len(painted)} delegated actions were read"
    dispatched = set(re.findall(r"act === '(\w+)'", text))
    unhandled = sorted(painted - dispatched)
    assert not unhandled, f"data-act values nothing dispatches: {unhandled}"
    stale = sorted(dispatched - painted)
    assert not stale, f"dispatcher branches no shipped markup paints: {stale}"

    # A branch is accountable for reaching code: the names it calls have to be
    # functions or arrow constants the same document defines.
    keywords = {"if", "for", "while", "switch", "return", "typeof", "catch", "parseInt",
                "parseFloat", "Number", "String", "Boolean", "Math", "JSON", "document",
                "window", "setTimeout", "clearTimeout", "encodeURIComponent", "Array",
                "Object", "fetch", "navigator", "localStorage", "confirm", "alert"}
    defined = set(re.findall(r"function\s+(\w+)\s*\(", text))
    defined |= set(re.findall(
        r"(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", text))
    unresolved = []
    for act in sorted(dispatched):
        branch = re.search(r"act === '" + re.escape(act) + r"'\)\s*\{(.{0,400}?)return; \}", text, re.S)
        if branch is None:
            unresolved.append(f"{act}: the branch never returns, so a tap falls through")
            continue
        called = [n for n in re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", branch.group(1))
                  if n not in keywords]
        if called and not any(n in defined for n in called):
            unresolved.append(f"{act} -> {called}")
    assert not unresolved, f"dispatcher branches that reach no function: {unresolved}"

def test_api_ask_answers_from_the_ledger_with_no_model(client, ledger, monkeypatch):
    """A public endpoint that 503s because an env var is empty is a broken
    feature; it now answers with the numbers the bot itself shows."""
    _no_provider(monkeypatch)
    mid = _one_market(ledger)

    r = client.post("/api/ask", json={"question": "какие рынки открыты?", "lang": "ru"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "data"
    assert f"#{mid}" in body["answer"]


def test_api_ask_says_which_language_to_answer_in(client, ledger, monkeypatch):
    _no_provider(monkeypatch)
    _one_market(ledger)

    zh = client.post("/api/ask", json={"question": "有哪些市场", "lang": "zh"}).json()["answer"]
    en = client.post("/api/ask", json={"question": "what markets are open", "lang": "en"}).json()["answer"]

    assert "未调用模型" in zh and "no model involved" in en


def test_api_ask_marks_a_model_answer_as_such(client, ledger, monkeypatch):
    from bot import ai as ai_mod

    async def answered(q):
        return "Model prose."

    monkeypatch.setattr(ai_mod, "ask_about_markets", answered)
    r = client.post("/api/ask", json={"question": "что такое base?"})
    assert r.json()["mode"] == "llm"
    assert r.json()["answer"] == "Model prose."


def test_api_ask_still_throttles_per_ip(client, ledger, monkeypatch):
    """The cooldown is what keeps the free-data path from being a free DB
    hammer; the no-model answer must not have dissolved it."""
    from web import server

    _no_provider(monkeypatch)
    monkeypatch.setattr(server.config, "AI_COOLDOWN_SECONDS", 600)
    _one_market(ledger)

    first = client.post("/api/ask", json={"question": "какие рынки открыты?", "lang": "ru"})
    second = client.post("/api/ask", json={"question": "какие рынки открыты?", "lang": "ru"})

    assert first.status_code == 200
    assert second.status_code == 429


def test_api_info_advertises_what_the_assistant_runs_on(client, ledger, monkeypatch):
    _no_provider(monkeypatch)
    info = client.get("/api/info").json()

    assert info["ai_mode"] == "data"
    monkeypatch.setenv("AI_API_KEY", "k1")
    assert client.get("/api/info").json()["ai_mode"] == "llm"


def test_api_ai_status_is_owner_only(client, ledger, monkeypatch):
    """The row names the env vars the keys live in and quotes provider error
    bodies, so it cannot be public."""
    anon = client.get("/api/ai/status")
    assert anon.status_code == 403

    _auth(client, 111)
    body = client.get("/api/ai/status").json()
    assert body["mode"] in ("llm", "data")
    assert {"name", "model", "key_env", "key_present", "parked", "last_error"} <= set(body["providers"][0])
    assert "key" not in body["providers"][0]


def test_metrics_loopback_opt_in_is_not_reachable_through_the_tunnel(monkeypatch):
    """METRICS_ALLOW_LOOPBACK exists for a local Prometheus scrape. cloudflared
    also dials the app from 127.0.0.1, so a loopback peer alone used to publish
    liabilities, reserves and solvency to anyone fetching /metrics through the
    Pages Function. A request carrying proxy headers must fall through to the
    token check; a direct local one still gets the metrics."""
    from web import metrics as metrics_mod
    from web import server

    async def _fake_metrics() -> str:
        return "tipbot_solvent 1"

    monkeypatch.setattr(metrics_mod, "collect_metrics", _fake_metrics)
    monkeypatch.setenv("METRICS_ALLOW_LOOPBACK", "1")
    monkeypatch.setattr(server.config, "METRICS_TOKEN", "")

    local = TestClient(server.app, client=("127.0.0.1", 12345))
    for header in ("Cf-Connecting-IP", "X-Forwarded-For", "X-Real-IP", "Forwarded"):
        refused = local.get("/metrics", headers={header: "203.0.113.7"})
        assert refused.status_code == 401, header
    assert local.get("/metrics").status_code == 200


def _mini_app_dictionaries(page: str) -> dict[str, set[str]]:
    """`{lang: {keys}}` for the three dictionaries `web/static/app.html` keeps
    inline, apart from `bot/i18n.py` and therefore outside `check_i18n.py`.

    One reader for every law that asks what the Mini App can say. The block is
    found by its own braces rather than by a line shape, so a dictionary written
    on one line and one spread over twenty answer the same way, and the number of
    words parsed is checked against the number of `key: "` pairs visible in the
    block — a rename that leaves one word written twice shadows the first, and a
    reader returning a set cannot show that it lost a declaration.
    """
    out: dict[str, set[str]] = {}
    for lang in ("ru", "en", "zh"):
        hit = re.search(rf"(?:^|\n)[ \t]*{lang}: \{{", page)
        assert hit, f"app.html declares no inline I18N.{lang} dictionary"
        depth, end = 0, hit.end()
        for idx in range(hit.end() - 1, len(page)):
            if page[idx] == "{":
                depth += 1
            elif page[idx] == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        body = page[hit.end():end]
        keys = set(re.findall(r'(?:^|[,{\s])([a-z0-9_]+):\s*["\']', body, re.M))
        written = len(re.findall(r'(?:^|[,{\s])[a-z0-9_]+:\s*["\']', body, re.M))
        assert keys, f"I18N.{lang} parsed to no word at all — this reader needs re-reading"
        assert len(keys) == written, f"I18N.{lang} shows {len(keys)} of {written} words to the gate"
        out[lang] = keys
    return out


def _mini_app_repainted_attributes(page: str) -> set[str]:
    """Every `data-i18n*` attribute `applyI18n()` walks, read out of its own
    `querySelectorAll('[data-i18n…]')` calls.

    The law below used to type the suffixes as an alternation and typed them
    wrong: it named `-title`, which no element in the file carries and no line
    walks, and missed `-label`, whose four aria-labels were therefore checked
    against no dictionary by anything.
    """
    attrs = set(re.findall(r"querySelectorAll\(['\"]\[(data-i18n[\w-]*)\]['\"]\)", page))
    assert attrs, "applyI18n() walks no data-i18n attribute — this reader needs re-reading"
    return attrs


def _mini_app_runtime_key_families(page: str) -> dict[str, int]:
    """`{prefix: rows}` for every localization key the Mini App builds at runtime.

    `addCreateOpt` names the row it appends `opt_<n>` and caps `n` with a literal
    in its own body, so the cap — not a hand-written list — is what says how many
    words the dictionaries owe. A body that caps nothing states no count and
    answers no family.
    """
    out: dict[str, int] = {}
    for hit in re.finditer(r'`([a-z][a-z0-9_]*?)_\$\{[^{}]*\}`', page):
        cap = re.search(
            r"\.length\s*(?:>=|>)\s*(\d+)", _enclosing_function(page, hit.start())
        )
        if cap:
            prefix = hit.group(1) + "_"
            out[prefix] = max(out.get(prefix, 0), int(cap.group(1)))
    return out


def test_every_mini_app_translation_key_exists_in_all_three_languages():
    """`web/static/app.html` keeps its own dictionary inline (ru/en/zh), apart
    from bot/i18n.py, so `scripts/check_i18n.py` cannot see it: a key used in
    the UI and added to two of the three languages silently renders English or
    the raw key for the third one.

    The subject set is what the app repaints, not what a law remembers: the
    attributes come out of `applyI18n()`'s own `querySelectorAll` calls, and the
    markup is refused an attribute those calls do not walk, because a hook nothing
    walks is a word that survives a language switch — which is the shape that put
    `Option 1` in a Russian placeholder."""
    page = _mini_app_text()
    dicts = _mini_app_dictionaries(page)
    attrs = _mini_app_repainted_attributes(page)

    used = set(re.findall(r"""\bt\(\s*['"]([a-z0-9_]+)['"]""", page))
    for attr in sorted(attrs):
        painted = set(re.findall(re.escape(attr) + r'="([a-z0-9_]+)"', page))
        assert painted, f"applyI18n() walks [{attr}] while no element in the file carries it"
        used |= painted
    assert len(used) >= 60, f"only {len(used)} keys reached the reader — a blind scan passes this"

    typed = set(re.findall(r'(data-i18n[\w-]*)="', page))
    assert not typed - attrs, (
        f"markup carries {sorted(typed - attrs)} and applyI18n() repaints none of it: "
        "those words stay in the language the screen was drawn in")

    for lang, defined in sorted(dicts.items()):
        assert used <= defined, f"{lang} is missing {sorted(used - defined)}"


def test_the_localization_key_the_mini_app_builds_covers_every_row_it_offers():
    """`addCreateOpt` appends a row and names it `opt_<n>`, so the word the
    placeholder shows is assembled at runtime and appears in no literal `t('…')`
    span. The cap on `n` is a literal in the same body; the dictionaries owe one
    word per row that cap allows. Raising the cap without the words paints the raw
    identifier `opt_5` as UI text in every language, and nothing read either side
    of the pair until now."""
    page = _mini_app_text()
    families = _mini_app_runtime_key_families(page)
    assert families, "the Mini App assembles no localization key — this law needs re-reading"
    dicts = _mini_app_dictionaries(page)

    problems = []
    for prefix, rows in sorted(families.items()):
        for lang, defined in sorted(dicts.items()):
            missing = sorted(
                f"{prefix}{n}" for n in range(1, rows + 1) if f"{prefix}{n}" not in defined
            )
            if missing:
                problems.append(f"{prefix} offers {rows} rows and {lang} has no word for {missing}")
    assert not problems, problems


def test_the_mini_app_localization_readers_read_the_shapes_a_rename_leaves_behind():
    """Each reader above is one regex away from reporting a clean tree: the
    dictionary block moved onto its own lines, the quotes around an attribute
    changed, a cap written `>` instead of `>=`, one word left written twice by a
    rename. A reader that quietly returned nothing would make every law using it
    pass."""
    page = (
        "  ru: { one: 'a', two: 'b' },\n"
        "  en: { one: 'a', two: 'b' },\n"
        "  zh: {\n    one: 'a',\n    two: 'b',\n  },\n"
        "function paint() {\n"
        "  document.querySelectorAll('[data-i18n]').forEach(el => el.textContent = t(el.dataset.i18n));\n"
        "  document.querySelectorAll(\"[data-i18n-label]\").forEach(el => el.setAttribute('aria-label', t(el.dataset.i18nLabel)));\n"
        "}\n"
        "function add(container) {\n"
        "  if (container.children.length > 3) return;\n"
        "  const key = `row_${container.children.length + 1}`;\n"
        "  return `<input data-i18n-label=\"${key}\">`;\n"
        "}\n"
    )
    dicts = _mini_app_dictionaries(page)
    assert dicts["ru"] == dicts["en"] == dicts["zh"] == {"one", "two"}, dicts
    assert _mini_app_repainted_attributes(page) == {"data-i18n", "data-i18n-label"}, page
    assert _mini_app_runtime_key_families(page) == {"row_": 3}, page

    spread = page.replace("  ru: { one: 'a', two: 'b' },", "  ru: {\n    one: 'a',\n    two: 'b'\n  },")
    assert _mini_app_dictionaries(spread)["ru"] == {"one", "two"}, "a spread dictionary read short"

    dupe = page.replace("  en: { one: 'a', two: 'b' },", "  en: { one: 'a', two: 'b', two: 'c' },")
    with pytest.raises(AssertionError, match="shows 2 of 3"):
        _mini_app_dictionaries(dupe)

    no_cap = page.replace("container.children.length > 3", "container.children.length > n")
    assert _mini_app_runtime_key_families(no_cap) == {}, "a body that caps nothing became a family"

    unwalked = page.replace("[data-i18n]", "aria-hidden").replace("[data-i18n-label]", "aria-hidden")
    with pytest.raises(AssertionError, match="no data-i18n attribute"):
        _mini_app_repainted_attributes(unwalked)

def test_no_substitution_marker_survives_into_any_served_page(client):
    """Individual `assert "__X__" not in html` checks only remember the
    placeholders someone thought of. This one scans for the shape: a new
    `__SOMETHING__` that `_static_page()` does not replace reaches the visitor as
    literal text on the page."""
    import re

    marker = re.compile(r"__[A-Z][A-Z0-9_]*__")
    leaked = []
    for url in ("/", "/app", "/market/1", "/oc", "/u/someone", "/agent", "/tos"):
        response = client.get(url)
        if response.status_code >= 400:
            continue
        found = sorted(set(marker.findall(response.text)))
        if found:
            leaked.append((url, found))
    assert not leaked, leaked
def test_onchain_list_and_market_page_print_the_same_price(client, ledger, monkeypatch):
    """`price_pct` is one field served two ways. The list rounded with the builtin
    (banker's, over a float) and the page with Decimal ROUND_HALF_UP, so the same
    market could read 12,3% in one place and 12,4% on its own page."""
    from bot import config
    from bot import onchain_market as om

    async def fake_prices(market_id, count):
        # Chosen because the two rounders disagree on BOTH options at the one
        # decimal the interface paints: the builtin sees 12.3499999999999996 and
        # 87.64999999999999 and gives 12.3 / 87.6, while ROUND_HALF_UP on the
        # decimal text gives 12.4 / 87.7. Two decimals would have hidden the
        # whole question behind a digit no face prints.
        return [0.1235, 0.8765]

    async def fake_info(market_id):
        return {"b": 5_000_000_000, "escrow_micro": 250_000_000, "resolved": False,
                "disputed": False, "cancelled": False, "winning_outcome": None}

    monkeypatch.setattr(om, "market_prices", fake_prices)
    monkeypatch.setattr(om, "get_market_info", fake_info)
    monkeypatch.setattr(config, "OUTCOME_MARKET_ADDRESS", "0x" + "ab" * 20)
    ledger.save_onchain_market(7, 777, "Один и тот же рынок?", ["да", "нет"], 4_000_000_000)

    page = client.get("/api/onchain/market/7").json()["options"]
    listed = next(m for m in client.get("/api/onchain/markets").json() if m["id"] == 7)
    expected = [12.4, 87.7]
    assert [o["price_pct"] for o in page] == expected, page
    assert [o["price_pct"] for o in listed["options"]] == expected, listed
    # The published figure is already at the face the page paints: a second
    # decimal would be rounded a second time, by ICU, with a different rule.
    for row in (*page, *listed["options"]):
        assert round(row["price_pct"], 1) == row["price_pct"], row


# ---------------------------------------------------------------------------
# SEO: the document's identity is written by the server, once
# ---------------------------------------------------------------------------

PUBLIC_ORIGIN = "https://tippy.example"
BUNDLE = Path(__file__).resolve().parents[1] / "web" / "static"


@pytest.fixture()
def seo_origin(monkeypatch):
    """Pin the origin canonicals, share cards and the sitemap are built from.

    Left to a maintainer's .env, these gates would assert whatever host that
    file happens to name — green on the laptop, red in CI, or the reverse.
    """
    from bot import config as cfg

    monkeypatch.setattr(cfg, "MINI_APP_URL", PUBLIC_ORIGIN)
    monkeypatch.setattr(cfg, "WEBHOOK_URL", "")
    return PUBLIC_ORIGIN


def _head(html, pattern):
    return re.findall(pattern, html)


def test_robots_txt_is_rules_and_not_a_landing_page(client, seo_origin):
    """The origin had no robots.txt, so the Pages Function answered it from the
    asset bundle, Pages found no such file and returned the landing page: 200
    and text/html where a crawler expected rules."""
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain"), r.headers["content-type"]
    body = r.text
    assert body.startswith("User-agent: *")
    for blocked in ("/api/", "/app", "/me", "/metrics", "/qr"):
        assert f"Disallow: {blocked}" in body, blocked
    assert f"Sitemap: {seo_origin}/sitemap.xml" in body


def test_sitemap_names_only_pages_that_answer_200(client, seo_origin, ledger):
    import xml.etree.ElementTree as ET

    bid = ledger.create_bet(111, "Сыграем в эту игру?", ["Да", "Нет"])
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = [node.text for node in ET.fromstring(r.text).iter(f"{ns}loc")]
    assert locs, "an empty sitemap tells a crawler the site has no pages"
    assert len(locs) == len(set(locs)), "duplicate <loc>"
    for loc in locs:
        assert loc.startswith(seo_origin + "/"), loc
        path = loc[len(seo_origin):]
        page = client.get(path)
        assert page.status_code == 200, f"{loc} is announced and answers {page.status_code}"
    assert f"{seo_origin}/m/{bid}" in locs


def test_the_share_card_names_the_market_it_is_about(client, seo_origin, ledger):
    """Every market used to carry the same static og:title, so forwarding any
    two pools produced one identical preview and the card said nothing about
    the thing it linked to."""
    a = ledger.create_bet(111, "Пойдёт дождь в субботу?", ["Да", "Нет"])
    b = ledger.create_bet(111, "Закроет ли BTC год выше 200k?", ["Да", "Нет"])
    page_a = client.get(f"/m/{a}").text
    page_b = client.get(f"/m/{b}").text
    title_a = _head(page_a, r"<title>(.*?)</title>")[0]
    title_b = _head(page_b, r"<title>(.*?)</title>")[0]
    assert title_a != title_b, "two markets, one title"
    assert "Пойдёт дождь в субботу?" in title_a
    assert "Закроет ли BTC год выше 200k?" in title_b
    assert _head(page_a, r'<meta property="og:title" content="([^"]*)"')[0] == title_a


def test_the_card_agrees_with_the_tab_on_every_indexable_page(client, seo_origin, ledger):
    bid = ledger.create_bet(111, "Один заголовок?", ["Да", "Нет"])
    pages = {"/": 200, "/tos": 200, "/agent": 200, f"/m/{bid}": 200, "/m/987654": 404}
    for path, status in pages.items():
        r = client.get(path)
        assert r.status_code == status, path
        html = r.text
        titles = _head(html, r"<title>(.*?)</title>")
        canonicals = _head(html, r'<link rel="canonical" href="([^"]*)"')
        og_titles = _head(html, r'<meta property="og:title" content="([^"]*)"')
        og_urls = _head(html, r'<meta property="og:url" content="([^"]*)"')
        tw_titles = _head(html, r'<meta name="twitter:title" content="([^"]*)"')
        assert len(titles) == 1, f"{path}: {len(titles)} titles"
        assert len(canonicals) == 1, f"{path}: {len(canonicals)} canonicals"
        assert len(og_titles) == 1, f"{path}: {len(og_titles)} og:title"
        assert canonicals[0] == seo_origin + path, (path, canonicals[0])
        assert og_urls == [canonicals[0]], (path, og_urls)
        assert og_titles == tw_titles == [titles[0]], (path, titles[0], og_titles, tw_titles)
        assert len(_head(html, r'<meta name="robots"')) <= 1, f"{path}: competing directives"
        # the card speaks the language of the document, not of the product
        lang = _head(html, r'<html lang="([a-z]+)"')
        locale = _head(html, r'<meta property="og:locale" content="([a-zA-Z_]+)"')
        assert locale and locale[0].startswith(lang[0] + "_"), (path, lang, locale)


def test_a_page_for_a_row_that_never_existed_is_a_404_and_not_indexed(client, ledger):
    """A 200 shell for any integer is a soft-404: the crawler cannot tell it
    from a page, and the sitemap can no longer be trusted to name real rows."""
    for path in ("/m/987654", "/m/oc/987654"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert 'content="noindex"' in r.text, path


def test_an_unknown_profile_stays_200_because_the_api_promised_it_would(client):
    """The deliberate asymmetry with /m/{id}. /api/user/{tg_id} answers the same
    empty shape for a stranger and for a newcomer precisely so Telegram ids
    cannot be enumerated; the page keeps that rule instead of leaking it in a
    status code, and a profile with no activity is not in the sitemap anyway."""
    assert client.get("/u/987654").status_code == 200
    assert client.get("/api/user/987654").status_code == 200


def test_private_surfaces_are_not_indexable(client):
    for path in ("/app", "/agent"):
        assert 'name="robots" content="noindex"' in client.get(path).text, path
    assert 'name="robots"' not in client.get("/").text


def test_the_landing_is_the_only_document_with_structured_data(client, seo_origin):
    html = client.get("/").text
    # the CSP middleware stamps a nonce onto every inline <script>, ld+json
    # included, so the attribute order in the tag is not the one written
    blocks = _head(html, r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>')
    assert len(blocks) == 1, f"{len(blocks)} ld+json blocks on /"
    data = json.loads(blocks[0])
    assert data["@context"] == "https://schema.org"
    canonical = _head(html, r'<link rel="canonical" href="([^"]*)"')[0]
    assert {node["url"] for node in data["@graph"]} == {canonical}
    org = next(node for node in data["@graph"] if node["@type"] == "Organization")
    assert (BUNDLE / org["logo"][len(seo_origin) + 1:]).is_file(), org["logo"]
    assert not _head(client.get("/tos").text, "application/ld+json")


def test_no_share_image_points_at_a_path_the_bundle_does_not_have(client, seo_origin):
    """pages_build_output_dir is web/static, so the URL a crawler fetches has no
    /static/ in it. Both share images were published with it —
    /static/miniapp-hero.png and /static/frame.png — and neither the origin nor
    the Pages bundle ever served them, so every card was a card without a
    picture."""
    seen = set()
    for path in ("/", "/tos", "/agent", "/app", "/m/987654", "/u/987654"):
        html = client.get(path).text
        seen |= set(_head(html, r'<meta (?:property="og|name="twitter):image" content="([^"]*)"'))
        for blob in _head(html, r'<meta name="fc:miniapp" content=\'([^\']*)\''):
            seen.add(json.loads(blob)["imageUrl"])
    assert seen, "no share image anywhere: the gate would pass on an empty site"
    for url in seen:
        assert url.startswith(seo_origin + "/"), url
        assert (BUNDLE / url[len(seo_origin) + 1:]).is_file(), url


def test_no_substitution_marker_reaches_the_visitor(client, ledger):
    """The general form of the __PUBLIC_URL__ check: a marker left in the HTML is
    text the visitor reads."""
    bid = ledger.create_bet(111, "Вопрос без ответа?", ["Да", "Нет"])
    for path in ("/", "/tos", "/agent", "/app", f"/m/{bid}", "/u/987654"):
        leaked = _head(client.get(path).text, r"__[A-Z][A-Z0-9_]*__")
        assert not leaked, f"{path}: {leaked}"


def test_no_double_encoded_text_reaches_a_public_page(client, ledger):
    """web/frame.py shipped an em dash as its UTF-8 bytes read back through
    cp1251, and the Farcaster card rendered it as «вЂ”». A page can be correct
    and still print mojibake, so the signature is what gets measured."""
    bid = ledger.create_bet(111, "Вопрос?", ["Да", "Нет"])
    for path in ("/", "/tos", "/agent", "/app", f"/m/{bid}", "/u/987654", "/robots.txt",
                 "/sitemap.xml"):
        text = client.get(path).text
        for signature in ("вЂ", "Ã¢", "â€"):
            assert signature not in text, f"{path}: mojibake {signature!r}"


def test_frame_page_exposes_paywall_card(client, ledger, monkeypatch):
    from bot import config as cfg

    monkeypatch.setattr(cfg, "MINI_APP_URL", "https://tipbot.example.com")
    monkeypatch.setattr(cfg, "WEBHOOK_URL", "")
    monkeypatch.setattr(cfg, "BOT_USERNAME", "base_tipbot")
    item_id = ledger.create_paywall(777, "Альфа-сигнал", 1_000_000, "секрет")
    r = client.get(f"/frame/{item_id}")
    assert r.status_code == 200
    html = r.text
    assert "fc:frame" in html
    assert "https://tipbot.example.com/miniapp-hero.png" in html
    assert "https://t.me/base_tipbot?start=paywall_" + str(item_id) in html
    assert "x402" in html
    assert "https://tipbot.example.com/api/x402/paywall?item=" + str(item_id) in html
    assert "вЂ" not in html, "mojibake on a card Warpcast renders"


def test_frame_page_for_a_post_that_does_not_exist_is_a_404(client):
    """It used to be a 200 that said "Post not found" — a card for no post,
    indexable and indistinguishable from a real one by anything that reads the
    status instead of the text."""
    r = client.get("/frame/987654")
    assert r.status_code == 404
    assert 'content="noindex"' in r.text


def test_the_legal_page_declares_the_language_its_body_is_written_in(client):
    """``/tos`` wrapped the English legal file in ``lang="ru"``, so a screen
    reader read English prose with Russian phonemes and Google processed the
    page as Russian. The chrome around it is Russian either way, which is why
    the rule is measured on ``<main>`` and on the description, not on the whole
    document."""
    html = client.get("/tos").text
    lang = re.search(r'<html lang="([^"]+)"', html).group(1)
    main = html[html.index("<main>"):html.index("</main>")]
    description = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
    cyrillic = lambda text: bool(re.search(r"[а-яё]", text, re.I))  # noqa: E731
    assert (lang == "ru") == cyrillic(main), (lang, cyrillic(main), main[:120])
    assert (lang == "ru") == cyrillic(description), (lang, description[:80])
    locale = re.search(r'<meta property="og:locale" content="([^"]*)"', html).group(1)
    assert locale == {"ru": "ru_RU", "en": "en_US"}[lang], (lang, locale)


def test_every_api_call_the_site_makes_is_a_route_the_server_serves():
    """The bundle fetches 25 endpoints by string, and a renamed or dropped one
    fails only when a visitor presses the button. The OpenAPI table is what the
    app really routes, so the two are compared here: every path a script fetches
    has to be served, and a path built by concatenation is matched on the static
    part the literal gives."""
    import re as _re
    from pathlib import Path

    from web.server import app

    static = Path(__file__).resolve().parents[1] / "web" / "static"
    served = set(app.openapi()["paths"])
    fetched = set()
    for file in list(static.glob("*.js")) + list(static.glob("*.html")):
        text = file.read_text(encoding="utf-8")
        for match in _re.finditer(r"""fetch\(\s*[`'"](/api/[^`'"]*)""", text):
            fetched.add((file.name, match.group(1)))
        for match in _re.finditer(r"""[`'"](/api/[^`'"]*)[`'"]\s*\+""", text):
            fetched.add((file.name, match.group(1)))
    assert fetched, "no /api/ call found in the bundle — the gate would prove nothing"

    def served_by(prefix: str) -> bool:
        clean = prefix.split("?")[0].rstrip("/")
        return any(path.rstrip("/") == clean or path.startswith(clean + "/") or clean == path
                   for path in served)

    dead = sorted((name, path) for name, path in fetched if not served_by(path))
    if dead:
        name, path = dead[0]
        pytest.fail(f"{name} fetches {path}, which the server does not route ({len(dead)} in total)")


def test_the_bundle_directory_does_not_publish_its_own_plumbing(client):
    """``web/static`` is both the site's source and the Pages build output, so a
    file added there is a URL whether or not it was meant for a browser. Before
    this rule the origin answered ``/_headers`` with 200 and
    ``application/octet-stream``, and ``/tos.md`` served the terms as a second
    copy of ``/tos`` with no canonical to point back at the page. The real
    assets keep serving — the guard is about plumbing, not about files."""
    for plumbing in ("/_headers", "/_redirects"):
        assert client.get(plumbing).status_code == 404, plumbing

    # The two maps in web/server.py are compared with the directory instead of
    # trusted: a file added to `web/static` is a URL the day it is committed,
    # and a map nobody checks drifts behind it.
    from web.server import MARKDOWN_PAGES, TEMPLATE_PAGES

    root = Path(__file__).resolve().parents[1]
    bundle = {f"/{p.name}" for p in (root / "web" / "static").glob("*.md")}
    assert bundle == set(MARKDOWN_PAGES), f"{bundle ^ set(MARKDOWN_PAGES)} drifted from the map"
    for name, page in MARKDOWN_PAGES.items():
        moved = client.get(name, follow_redirects=False)
        assert moved.status_code == 301, (name, moved.status_code)
        assert moved.headers["location"] == page, (name, moved.headers["location"])
    for name in sorted({f"/{p.name}" for p in (root / "web" / "static").glob("*.html")}
                       - set(TEMPLATE_PAGES)):
        assert client.get(name, follow_redirects=False).status_code == 404, name
    missing_md = client.get("/nothingelse.md", follow_redirects=False)
    assert missing_md.status_code == 404 and "text/html" in missing_md.headers["content-type"]
    for asset in ("/style.css", "/theme.js", "/app.js", "/site.js", "/miniapp-hero.png", "/icon.png"):
        assert client.get(asset).status_code == 200, asset

    # The templates are the same class of file, and there are six of them.
    # Served raw, `/index.html` is a second copy of the landing with no
    # canonical to choose between it and `/`, `/app.html` carries a literal
    # `__PUBLIC_URL__` inside its fc:miniapp block, and `/user.html` is a shell
    # whose id comes from a path it does not have. None of them is a page.
    templates = sorted(p.name for p in (Path(__file__).resolve().parents[1]
                                        / "web" / "static").glob("*.html"))
    assert len(templates) >= 6, f"only {templates} — the scan is blind"
    for name in templates:
        r = client.get(f"/{name}", follow_redirects=False)
        assert r.status_code != 200, f"/{name} is published as a URL"
        assert b"__" not in r.content, f"/{name} serves the template's own tokens"

    # Three of them name a page that needs no id, so the visitor is moved to it.
    for template, page in (("/index.html", "/"), ("/app.html", "/app"), ("/agent.html", "/agent")):
        r = client.get(template, follow_redirects=False)
        assert r.status_code == 301, (template, r.status_code)
        assert r.headers["location"] == page, (template, r.headers["location"])


def test_the_window_a_response_carries_is_the_window_its_class_declares(client):
    """The middleware stamps `no-store` on everything it sees, and until this wave
    that included the 61 KB stylesheet, the seven scripts and the eleven faces the
    same process serves from disk — so a visitor at the VPS refetched the entire
    bundle on every page view, and a visitor at Pages got Cloudflare's own default
    (`max-age=0, must-revalidate`) because the bundle said nothing either.

    The subject set is not a list of URLs. The assets are the files the bundle
    directory carries (the same walk the MIME gate above uses), and the documents
    are the parameterless `GET` routes the app itself registers — so a new page, a
    new endpoint and a new file type are each measured the day they land, and a new
    one gets `no-store` until someone decides otherwise on purpose.

    `/qr` is the exception the walk cannot express, and the reason it is allowed:
    its whole input is the query string, so a cache key cannot go stale relative to
    the bytes it holds — a browser asking `/qr?data=0xOLD` is asking for the QR of
    0xOLD, and the document that chose that address is itself `no-store`. That
    precondition is measured, not asserted: the same URL answered cold and by a
    signed-in session must be the same bytes with the same window."""
    from web.server import ASSET_CACHE

    root = Path(__file__).resolve().parents[1] / "web" / "static"
    assets = sorted(str(p.relative_to(root)).replace("\\", "/")
                    for p in root.rglob("*")
                    if p.is_file() and p.suffix and not p.name.endswith((".html", ".md")))
    kinds = {Path(name).suffix for name in assets}
    assert len(kinds) >= 4, f"the walk saw {sorted(kinds)} — no gate"
    assert kinds <= set(ASSET_CACHE), f"{sorted(kinds - set(ASSET_CACHE))} reach the client with no declared window"

    offenders = []
    for name in assets:
        served = client.get(f"/{name}")
        got = served.headers.get("cache-control", "<none>")
        want = ASSET_CACHE[Path(name).suffix]
        if served.status_code != 200 or got != want:
            offenders.append({"path": f"/{name}", "status": served.status_code, "served": got, "declared": want})
    assert not offenders, "an asset answered something other than its class's window:\n" + json.dumps(offenders)

    routes = sorted({r.path for r in client.app.routes
                     if "GET" in (getattr(r, "methods", None) or set()) and "{" not in r.path})
    assert len(routes) >= 20, f"the router answered {routes} — no gate"
    # /metrics needs no session, /api/agent/status needs the admin token, and an
    # answer that is a refusal is still an answer about a document.
    cacheable = {"/qr"}
    stale = []
    for path in routes:
        if path in cacheable:
            continue
        got = client.get(path, follow_redirects=False).headers.get("cache-control", "<none>")
        if "no-store" not in got:
            stale.append((path, got))
    assert not stale, f"a page or an endpoint is cacheable, and a page is the session: {stale}"

    public = client.get("/qr", params={"data": "0x" + "ab" * 20, "size": 120})
    other = TestClient(client.app)
    other.cookies.set(COOKIE_NAME, make_session(1))
    signed_in = other.get("/qr", params={"data": "0x" + "ab" * 20, "size": 120})
    assert public.status_code == signed_in.status_code == 200, (public.status_code, signed_in.status_code)
    assert public.content == signed_in.content, "the QR is a function of something besides its URL"
    assert public.headers["cache-control"] == signed_in.headers["cache-control"]
    assert public.headers["cache-control"].startswith("public, max-age="), public.headers["cache-control"]


def test_no_asset_the_bundle_serves_arrives_as_undifferentiated_bytes(client):
    """`web/static` is the build output, so every file in it is a URL and the
    `Content-Type` the origin stamps is the only thing a browser learns about it.
    The self-hosted faces shipped a whole wave as `application/octet-stream` — the
    type the guard above already calls a refused file — because no Windows registry
    entry names `.woff2`, `mimetypes.guess_type` answers None, and on Linux the
    answer depends on whether that box happens to ship a system `mime.types` that
    knows the extension. One file, two machines, two types: the same class of defect
    the `.js` pin was written for, on the other side of the table.

    The walk is the directory itself, not a list of asset names, so the next file
    anyone drops into `fonts/` is measured the day it lands. A source the mount
    refuses is not an asset and is not counted: the plumbing gate above owns it."""
    root = Path(__file__).resolve().parents[1] / "web" / "static"
    assets = sorted(str(p.relative_to(root)).replace("\\", "/")
                    for p in root.rglob("*")
                    if p.is_file() and p.suffix and not p.name.endswith((".html", ".md")))
    kinds = {Path(name).suffix for name in assets}
    assert ".woff2" in kinds, f"the walk saw {sorted(kinds)} — no font to measure, so no gate"
    offenders = []
    for name in assets:
        served = client.get(f"/{name}")
        declared = served.headers.get("content-type", "")
        if served.status_code != 200 or not declared or declared.startswith("application/octet-stream"):
            offenders.append((f"/{name}", served.status_code, declared or "<no content-type>"))
    assert not offenders, json.dumps(offenders, ensure_ascii=False)


def test_a_disabled_stack_is_a_state_the_page_reads_not_an_error_it_catches(client):
    """A GET that reports whether a feature exists has no business answering 5xx
    because the deployment chose not to turn it on. `GET /api/mini/smartwallet`
    used to `raise refuse(request, 503, 'api_smart_disabled')`, so every signed-in
    load of the Mini App carried a server error, and the client decided the shape
    of the wallet card from `r.status === 503` — a transport code read as a product
    flag. A stack this origin has not configured is now `200 {'enabled': False}`.

    The other half is the point: `POST /api/mini/smartbuy` still answers 503. There
    the visitor asked for something to happen and nothing can happen, which is what
    a refusal is for."""
    from web import mini

    assert mini._smart_wallet_enabled() is False, (
        "this deployment has the gasless stack on, so the disabled answer is not measurable here"
    )
    _auth(client, 777)
    state = client.get("/api/mini/smartwallet")
    assert state.status_code == 200, state.status_code
    assert state.json() == {"enabled": False}

    refused = client.post("/api/mini/smartbuy",
                          json={"market_id": 1, "outcome": 0, "shares": 10,
                                "max_cost_usdc": "1.00"},
                          headers={"Idempotency-Key": "gate-disabled-stack"})
    assert refused.status_code == 503, refused.status_code
    assert refused.json()["detail"], "a refusal has to carry the text the user sees"

    # And no client may read the feature out of a status code again.
    root = Path(__file__).resolve().parents[1] / "web" / "static"
    for name in ("app.html", "site.js"):
        assert "503" not in (root / name).read_text(encoding="utf-8-sig"), (
            f"{name} still decides a surface from an HTTP status"
        )


def test_the_commands_the_site_publishes_are_the_ones_the_bot_installs(client):
    """The landing states it in prose: «Тот же список, который бот регистрирует в
    меню Telegram через set_my_commands, — сайт отдаёт его из того же источника,
    поэтому команды здесь не могут разойтись с ботом». A sentence like that is a
    contract, so it is measured: the payload and the installed menu have to be
    the same commands, in the same order, with the same descriptions."""
    from bot.main import BOT_COMMANDS

    payload = client.get("/api/info").json()["commands"]
    assert [(c["command"], c["description"]) for c in payload] == [
        (c.command, c.description) for c in BOT_COMMANDS
    ]


# ---------------------------------------------------------------------------
# proof of reserves: what the landing promises, against what the ledger sums
# ---------------------------------------------------------------------------

LIABILITY_WORDS = {
    "user_bal": "балансами пользователей",
    "market_escrow": "эскроу рынков",
    "bet_pool": "открытыми пулами",
    "treasury_bal": "казначействами сообществ",
    "x402_unswept_credit_total": "незачисленными x402",
    "pending_deposit_total": "депозитами в ожидании",
}


def test_the_landing_names_every_category_total_liabilities_sums(client):
    """«Все обязательства бота» is one number, and the sentence under it lists
    what goes into it. The list is prose and the sum is SQL, so the number can
    grow past the sentence without anyone noticing — which is how a
    proof-of-reserves becomes a proof of something else."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "bot" / "ledger" / "_withdraw.py").read_text(
        encoding="utf-8-sig")
    body = re.search(r"def total_liabilities\(self\).*?\n        \)\n", source, re.S)
    assert body, "total_liabilities() moved somewhere this gate cannot read"
    parts = [name for pair in re.findall(
        r'int\(row\["(\w+)"\]\)|self\.(\w+_total)\(\)', body.group(0)) for name in pair if name]
    assert set(parts) == set(LIABILITY_WORDS), f"код суммирует {sorted(parts)}, карта гейта — {sorted(LIABILITY_WORDS)}"
    claimed = re.search(r"([A-Z][a-z]+) categories", body.group(0))
    assert claimed and {"Six": 6, "Seven": 7}.get(claimed.group(1), -1) == len(parts), (
        f"docstring says {claimed and claimed.group(1)}, the sum has {len(parts)} terms"
    )

    hint = re.search(r"Все депозиты, чаевые и пулы.*?</p>", client.get("/").text, re.S)
    assert hint, "the reserves explanation is gone from the landing"
    missing = [word for key, word in LIABILITY_WORDS.items() if word not in hint.group(0)]
    assert not missing, f"обязательства считаются по {sorted(parts)}, в тексте нет про: {missing}"


def test_the_solvency_payload_names_the_liabilities_number_once(client, ledger):
    """One figure under two keys is two figures waiting to disagree. The payload
    used to answer both `liabilities_usdc` and `owed_usdc` from the same
    variable; a liabilities number now appears exactly once, measured on a ledger
    where it cannot coincide with the pending line.

    A `_pct` key is a different unit and is left out of the comparison: 100,0 %
    of coverage and 100,00 USDC of obligations are two facts that happen to share
    a numeral, and on the live treasury both are 100 whenever the vault exactly
    matches the book.
    """
    ledger.credit(1, 50_000_000, "deposit")
    bid = ledger.create_bet(1, "Пул для сверки?", ["Да", "Нет"])
    ledger.place_bet(bid, 1, 0, 10_000_000)
    data = client.get("/api/solvency").json()
    owed = data["liabilities_usdc"]
    assert owed > 0, "nothing on the ledger — the gate would pass on an empty payload"
    assert data["pending_deposits_usdc"] != owed
    duplicated = [key for key, value in data.items()
                  if key != "liabilities_usdc" and not key.endswith("_pct")
                  and isinstance(value, (int, float)) and value == owed]
    assert not duplicated, f"the same liabilities figure is also published as {duplicated}"


def test_the_landing_does_not_promise_a_solvency_it_does_not_enforce(client):
    """The page used to state «Обязательство не может превысить то, что реально
    лежит на адресе» — an invariant nothing enforces, contradicted two rows
    above whenever the treasury is short and the chip reads «недостаточно». A
    promise the code cannot keep is not a guarantee, it is a bug that reads like
    marketing."""
    html = client.get("/").text
    assert "не может превысить" not in html
    assert "недостаточно" in html, "the page lost the word it shows when reserves fall short"


def test_both_bundles_send_the_key_every_money_endpoint_requires():
    """`web/mini.py` refuses a money POST with a 400 before the ledger is
    touched when no `Idempotency-Key` arrives, and the Mini App has sent the key
    since the guard was introduced. The landing's own workspace never did — so
    every money button on `/` (tip, trade, bet, create, gasless buy) was
    refused, and the refusal text was what the visitor saw in its place.

    The rule is read from the server's guarded endpoints, so a new money route
    cannot ship unreachable from the web: each guarded path has to be called by
    both bundles with a key, and the action label the client keeps its key under
    has to be one the server actually guards with — `create` and `create_market`
    are two different attempts to the ledger."""
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[1]
    mini = (root / "web" / "mini.py").read_text(encoding="utf-8-sig")
    required: dict[str, set[str]] = {}
    for match in re.finditer(r"@router\.post\('(/api/mini/[a-z/]+)'", mini):
        path = match.group(1)
        upto = mini.find("@router.", match.end())
        body = mini[match.end():upto if upto > 0 else len(mini)]
        actions = set(re.findall(r"_idempotent\(request,\s*'([a-z_]+)'", body))
        if actions:
            required[path] = actions
    assert len(required) >= 5, f"only {len(required)} guarded endpoints — the scan is blind"

    site = (root / "web" / "static" / "site.js").read_text(encoding="utf-8")
    app = (root / "web" / "static" / "app.html").read_text(encoding="utf-8")
    assert "Idempotency-Key" in site, "site.js has no idempotency helper at all"
    assert "function moneyPost" in app, "app.html lost its idempotent request helper"

    def windows(text: str, endpoint: str) -> list[str]:
        """Every place the bundle names this endpoint, with the call around it.
        The Mini App picks trade/betplace with a ternary, so the path is not
        always the first argument of the fetch()."""
        quoted = re.escape(f"'{endpoint}'")
        return [text[max(0, m.start() - 120):m.end() + 300]
                for m in re.finditer(quoted, text)]

    for path, actions in sorted(required.items()):
        for name, text, needs in (("site.js", site, r",\s*(?:'[a-z_]+'|action)\s*\)"),
                                  ("app.html", app, r"moneyPost\(")):
            calls = windows(text, path)
            if not calls:
                # this surface does not offer the action at all — the Mini App
                # sends on-chain trading to the bot chat (onchain_hint), so it
                # never calls smartbuy. Absent is allowed; present-and-keyless
                # is not.
                continue
            assert any(re.search(needs, call) for call in calls), (
                f"{name} calls {path} without an Idempotency-Key — the server "
                f"refuses it with 400 before the ledger is touched"
            )
            # The label is checked in the file, not in the call: the server
            # hashes (tg_id, its own action, key), so a client label that names
            # the action loosely can never make a double spend — at worst it
            # reuses one key where two would be tidier. What must not happen is
            # a guarded call leaving without a key at all.
            for action in sorted(actions):
                assert f"'{action}'" in text, f"{name} never names the guarded action {action}"

    assert windows(site, "/api/mini/tip"), (
        "site.js no longer tips at all — the loop above would pass on an empty scan"
    )


def test_no_developer_string_answers_a_request_the_bundles_make():
    """`insufficient balance` is a line for the log, not an answer to a person:
    the landing paints `detail` straight into the slot under the button that was
    just pressed, so every refusal on `/` ended in developer English — in front
    of the Russian page and a Chinese user alike. Refusals now come out of
    `bot/i18n.py`, and this gate reads the rule from the files:

    * the only bare `HTTPException` left in `web/mini.py` and `web/auth.py` is
      the 401 session probe — the one answer both bundles give in their own
      words, because the server cannot know a language before the login that
      just failed;
    * every route the bundles fetch in `web/server.py` refuses through `refuse()`
      too (`/qr` is an `<img>` src and `/api/admin` a page, so neither is in the
      fetched set and both keep their machine strings);
    * every refusal key answers in all three languages, in three different
      sentences, each written in that language's script, and fills every
      placeholder its own text asks for;
    * and no api_* key sits in the table unused — copy no route answers with is
      copy nobody asked for.
    """
    import ast
    from pathlib import Path as _P

    from bot import i18n

    root = _P(__file__).resolve().parents[1]
    web = root / "web"

    fetched = set()
    for file in list((web / "static").glob("*.js")) + list((web / "static").glob("*.html")):
        text = file.read_text(encoding="utf-8")
        for match in re.finditer(r"""fetch\(\s*[`'"](/api/[^`'"]*)""", text):
            fetched.add(match.group(1).rstrip("/"))
        for match in re.finditer(r"""[`'"](/api/[^`'"]*)[`'"]\s*\+""", text):
            fetched.add(match.group(1).rstrip("/"))
    assert fetched, "no /api/ call in the bundle — the scan is blind"

    def bare_raises(tree):
        """Every HTTPException this AST builds, except the 401 session probe."""
        lines = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HTTPException"):
                continue
            status = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "status_code"), None)
            if isinstance(status, ast.Constant) and status.value == 401:
                continue
            lines.append(node.lineno)
        return lines

    sources = {name: (web / name).read_text(encoding="utf-8-sig")
               for name in ("mini.py", "auth.py", "server.py")}
    offenders = []
    for name in ("mini.py", "auth.py"):
        offenders += [f"web/{name}:{line}" for line in
                      bare_raises(ast.parse(sources[name], filename=name))]

    server_tree = ast.parse(sources["server.py"], filename="server.py")
    for fn in ast.walk(server_tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in fn.decorator_list:
            if not (isinstance(deco, ast.Call) and deco.args
                    and isinstance(deco.args[0], ast.Constant)
                    and isinstance(deco.args[0].value, str)):
                continue
            route = re.sub(r"\{[^}]*\}", "", deco.args[0].value).rstrip("/")
            if route not in fetched:
                continue
            body = ast.parse(ast.unparse(fn), filename="server.py")
            offenders += [f"web/server.py:{line} on fetched route {route}"
                          for line in bare_raises(body)]
    assert not offenders, f"{offenders} answer with a string no user reads"

    # The duplicate-replay answer is not a route, so the walk above cannot see
    # it — and it is the one refusal whose two open states must not blur.
    assert "{'detail': exc.message}" not in sources["server.py"], (
        "the replay answer stopped going through the refusal table")

    # The rate limiters answer JSONResponse from the middleware, outside any
    # route function, so nothing above looks at their wording. A refusal there
    # reaches the same two bundles. Only the plumbing guard's status phrase is
    # allowed as a literal: it advises nobody, and no person is meant to see it.
    middleware = [fn for fn in ast.walk(server_tree)
                  if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and any(isinstance(d, ast.Call)
                          and getattr(d.func, "attr", "") == "middleware"
                          for d in fn.decorator_list)]
    assert middleware, "no http middleware found — the scan is blind"
    literal_refusals = []
    for fn in middleware:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == 'detail'
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str) and value.value != 'Not found'):
                    literal_refusals.append(f"web/server.py:{value.lineno}: {value.value!r}")
    assert not literal_refusals, (
        f"{literal_refusals} answer a request in text no user reads")

    declared = {k for k in i18n.STRINGS if k.startswith("api_")}
    assert declared, "there is no refusal copy in the table at all"
    used: dict[str, set[str]] = {}
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text, filename=name)):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith("api_")):
                if node.value not in declared:
                    offenders.append(
                        f"web/{name}:{node.lineno} names {node.value}, which i18n does not have")
                used.setdefault(node.value, set())
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") in ("refuse", "refuse_protocol")
                    and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant)):
                used.setdefault(node.args[2].value, set()).update(
                    kw.arg for kw in node.keywords if kw.arg)
    assert not offenders, f"{offenders}"

    unused = sorted(declared - set(used))
    assert not unused, f"{unused} are refusal copy no route answers with"

    bad = []
    for key in sorted(used):
        table = i18n.STRINGS[key]
        assert set(table) == {"ru", "en", "zh"}, f"{key} does not answer in all three languages"
        ru, en, zh = table["ru"], table["en"], table["zh"]
        if len({ru, en, zh}) != 3:
            bad.append(f"{key}: one sentence reused across languages")
        if not re.search(r"[а-яё]", ru, re.I):
            bad.append(f"{key}: ru is not Russian")
        if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", ru):
            bad.append(f"{key}: ru carries Chinese text")
        if re.search(r"[а-яё\u3040-\u30ff\u4e00-\u9fff]", en, re.I):
            bad.append(f"{key}: en carries Russian or Chinese text")
        if not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", zh):
            bad.append(f"{key}: zh has no Chinese text")
        want = {name for text in table.values()
                for name in re.findall(r"\{([a-zA-Z_]\w*)", text)}
        if want - used[key]:
            bad.append(f"{key} needs {sorted(want - used[key])}; "
                       f"the call sites pass {sorted(used[key])}")
    assert not bad, "\n".join(bad)


def test_a_dead_session_is_answered_by_each_bundle_in_its_own_words():
    """The 401 carve-out above only holds if both surfaces really do answer a
    dead session themselves: the server cannot know a language before the login
    that failed, so a bundle painting `detail` there would show Russian text to a
    Chinese user. Their own strings, and both paint as text — which is also why a
    refusal is allowed to carry a nickname the visitor typed."""
    root = Path(__file__).resolve().parents[1]
    site = (root / "web" / "static" / "site.js").read_text(encoding="utf-8")
    app = (root / "web" / "static" / "app.html").read_text(encoding="utf-8")

    assert re.search(r"r\.status === 401", site), "site.js paints a dead session's detail"
    assert "сессия истекла" in site
    assert re.search(r"status === 401 \|\| status === 403", app), (
        "app.html paints the server's pre-identity string to a zh user")
    assert "t('auth_stale')" in app

    assert re.search(r"function showError[\s\S]{0,160}\.textContent = text", site), (
        "site.js no longer paints refusals as text, so a nickname in one is markup now")
    assert re.search(r"function toast[\s\S]{0,160}el\.textContent = msg", app), (
        "app.html no longer paints toasts as text")


def test_the_agent_page_has_a_line_for_every_error_the_api_answers():
    """`/agent` answers a failed load with `ERRORS[s.error] || s.error`, so a
    code the table has no row for is painted to the owner as the server's
    English sentence — and a row for a code the server no longer sends is a
    line nobody reads. Both directions are measured here, from the two files.
    """
    root = Path(__file__).resolve().parents[1]
    server_src = (root / "web" / "server.py").read_text(encoding="utf-8-sig")
    agent_js = (root / "web" / "static" / "agent.js").read_text(encoding="utf-8")

    answered = set(re.findall(r"return \{'error': '([^']+)'\}", server_src))
    assert answered, "no agent error code found — the scan is blind"

    table = re.search(r"var ERRORS = \{([\s\S]*?)\};", agent_js)
    assert table, "agent.js has no error table at all"
    rows = dict(re.findall(r"'([^']+)':\s*'([^']+)'", table.group(1)))

    missing = sorted(answered - set(rows))
    assert not missing, f"/agent would paint these codes raw: {missing}"
    stale = sorted(set(rows) - answered)
    assert not stale, f"agent.js still translates codes the server never answers: {stale}"
    for code, line in rows.items():
        assert re.search(r"[а-яё]", line, re.I), f"{code} is answered in a language /agent is not"


def test_the_landing_has_a_line_for_every_login_reason_the_callback_emits():
    """`?auth=<code>` is a contract across three files: `web/auth.py` and the
    login rate limiter in `web/server.py` emit the code, `site.js` holds the
    sentence. A code with no line is silence exactly where a person was told
    something failed, and a line for a code nobody emits is copy that can never
    be shown — so both directions are measured, from every file that can answer
    a login with a redirect, the same way the agent page's error table is.
    """
    root = Path(__file__).resolve().parents[1]
    emit_src = "".join((root / "web" / name).read_text(encoding="utf-8-sig")
                       for name in ("auth.py", "server.py"))
    site = (root / "web" / "static" / "site.js").read_text(encoding="utf-8")

    emitted = set(re.findall(r"login_failed\('([a-z]+)'\)", emit_src))
    assert emitted, "the callback emits no reason code — the scan is blind"
    table = re.search(r"var LOGIN_FAILURES = \{([\s\S]*?)\};", site)
    assert table, "site.js has no login-failure copy at all"
    lines = dict(re.findall(r"([a-z]+):\s*'([^']+)'", table.group(1)))

    missing = sorted(emitted - set(lines))
    assert not missing, f"the landing would stay silent on {['?auth=' + c for c in missing]}"
    stale = sorted(set(lines) - emitted)
    assert not stale, f"site.js translates codes the callback never emits: {stale}"
    for code, line in lines.items():
        assert re.search(r"[а-яё]", line, re.I), f"?auth={code} answers in a language the page is not"
        assert "Telegram" in line or "вход" in line.lower(), (code, line)

    # Spent once it is on screen: left in the address bar, the same line returns
    # on every reload and a shared link looks like a broken page.
    assert re.search(r"q\.delete\('auth'\)[\s\S]{0,220}history\.replaceState", site), (
        "the reason code is never stripped from the URL")


def test_a_dead_link_answers_a_browser_a_page(client):
    """An unknown URL used to reach `{"detail": "Not Found"}` — the only page a
    visitor with a mistyped link, an expired bookmark or a search hit ever got,
    with no title, no theme, no link back and nothing to read. The StaticFiles
    mount raises the 404, so no route ever saw it.

    The rule is by path, not by `Accept`: the Cloudflare function is what talks
    to this origin, and its own headers say nothing about who sent the browser.
    Everything under `/api/` keeps answering JSON, because that is what the two
    bundles parse; everything else is a page.
    """
    r = client.get("/nothing-here")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html"), r.headers["content-type"]
    body = r.text
    # Every path that renders this page has to arrive stamped by the CSP layer:
    # the refusal lives in the mount, not in the middleware's early return, and
    # the only way to tell those two placements apart is the headers. Checking
    # one path proves one path — `/user.html` is the answer the mount gives, so
    # it is measured the same way.
    for dead_end in ("/nothing-here", "/user.html", "/nothingelse.md"):
        page = client.get(dead_end, follow_redirects=False)
        assert page.status_code == 404, dead_end
        csp = page.headers.get("content-security-policy", "")
        assert "script-src 'self' 'nonce-" in csp, f"{dead_end}: {csp[:80]!r}"
        assert page.headers.get("x-content-type-options", "").lower() == "nosniff", dead_end
        assert page.headers.get("referrer-policy", "") == "no-referrer", dead_end
    assert body.count("<h1") == 1, "the 404 is not a document with one title"
    assert 'name="robots" content="noindex"' in body, "a 404 page must not be indexed"
    assert '<link rel="canonical"' not in body, "a 404 has no canonical to name"
    assert "не найдена" in body, body[:200]
    for target in ('href="/"', 'href="/tos"', "t.me/"):
        assert target in body, f"the dead-end page offers no way to {target}"

    # An id that is not a number is the same dead end. FastAPI's 422 answered a
    # validation tree here — a page echoing the visitor's own guess back at them
    # — while the bundles and any integrator still need that body on /api/ paths.
    partial = client.get("/m/abc")
    assert partial.status_code == 404, partial.status_code
    assert partial.headers["content-type"].startswith("text/html"), partial.headers["content-type"]
    assert "abc" not in partial.text, "the dead end repeats what was typed at it"
    bad = client.post("/api/mini/tip", json={"to": "nobody"})
    assert bad.status_code == 422, bad.text[:200]
    assert "detail" in bad.json(), "an integrator lost the field list it fixes the call with"

    # An API client is not a person reading a page, and gets the machine answer.
    api = client.get("/api/nothing-here")
    assert api.status_code == 404
    assert api.headers["content-type"].startswith("application/json"), api.headers["content-type"]

    # The same route the pages fetch: a missing row is still JSON, even to a
    # browser tab that typed it — the bundles parse it and paint their own line.
    row = client.get("/api/market/999999", headers={"accept": "text/html"})
    assert row.status_code == 404
    assert row.headers["content-type"].startswith("application/json")


def test_every_template_is_rendered_by_a_route_and_none_by_its_own_url():
    """The two halves of one rule: a template nobody renders is dead weight in
    the bundle (and therefore a URL), and a route rendering a file that is not
    there is a 500. Measured from the directory and the source, both ways."""
    static = Path(__file__).resolve().parents[1] / "web" / "static"
    files = {p.name for p in static.glob("*.html")}
    source = "".join((Path(__file__).resolve().parents[1] / "web" / name)
                     .read_text(encoding="utf-8-sig") for name in ("server.py", "mini.py"))

    rendered = set(re.findall(r"'([a-z_]+\.html)'", source))
    missing = sorted(rendered - files)
    assert not missing, f"routes render {missing}, which the bundle does not have"
    orphan = sorted(files - rendered)
    assert not orphan, f"{orphan} sit in the bundle directory as URLs that no route renders"


def test_every_error_handler_in_the_source_is_registered():
    """A patch that shifts a decorator off a handler leaves the function
    defined, imported, unit-tested by nothing and never called: the duplicate
    money answer becomes an unhandled exception, and only a test that happens to
    hit the 409 path notices — which is exactly how this one was lost while the
    dead-end page was being added. The registration is therefore checked
    syntactically, so a handler without a decorator cannot survive the commit
    that dropped it.
    """
    import ast

    from web.server import app

    source = (Path(__file__).resolve().parents[1] / "web" / "server.py").read_text(
        encoding="utf-8-sig")
    unregistered, named = [], []
    for fn in ast.parse(source, filename="server.py").body:
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and fn.name.endswith("_handler")):
            continue
        named.append(fn.name)
        if not any("exception_handler" in ast.unparse(d) for d in fn.decorator_list):
            unregistered.append(fn.name)
    assert len(named) >= 3, f"only {named} matched — the scan is blind"
    assert not unregistered, f"{unregistered} are error handlers nobody registered"
    assert len(app.exception_handlers) >= len(named), (
        f"{len(named)} handlers in the source, {len(app.exception_handlers)} registered")
