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


def test_css_has_new_ui_styles(client):
    css = client.get("/style.css").text
    for needle in (
        ".chart-bar",
        ".chip-open",
        ".option-winner",
        ".bar-fill-win",
        ".empty",
    ):
        assert needle in css


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


def test_landing_deadline_hint_quotes_the_configured_default(client, monkeypatch):
    """The form said "Пусто — без дедлайна", but an empty field has meant
    `now + MARKET_DEFAULT_HOURS` since deadlines became mandatory (015)."""
    from bot import config as cfg

    monkeypatch.setattr(cfg, "MARKET_DEFAULT_HOURS", 42)
    html = client.get('/').text
    assert "42 ч по умолчанию" in html
    assert "без дедлайна" not in html
    assert "__DEFAULT_DEADLINE_HOURS__" not in html


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
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.html").read_text(
        encoding="utf-8"
    )
    dicts = {}
    for lang in ("ru", "en", "zh"):
        block = re.search(rf"\n  {lang}: \{{(.*?)\}},?\n", text, re.S)
        assert block, f"I18N.{lang} not found in app.html"
        dicts[lang] = set(re.findall(r'(?:^|[,{\s])([a-z0-9_]+):\s*"', block.group(1), re.M))
    assert dicts["ru"] == dicts["en"], dicts["ru"] ^ dicts["en"]
    assert dicts["ru"] == dicts["zh"], dicts["ru"] ^ dicts["zh"]

    used = set(re.findall(r"(?<![A-Za-z0-9_$.])t\(\s*'([a-z0-9_]+)'\s*\)", text))
    assert not used - dicts["ru"], sorted(used - dicts["ru"])


def test_every_class_in_the_page_templates_is_defined_in_style_css():
    """AGENTS.md requires class parity in the direction a rename breaks: markup
    carrying a class no rule defines renders unstyled and looks intentional until
    someone compares two screens. The unused direction is not machine-checkable
    without a long exception list (the Mini App keeps its own stylesheet, and
    several classes arrive through f-strings), so this pins the one that can be
    checked without guessing — and it covers the JS renderers, because that is
    where the templates actually live: `cmd-group` was emitted from app.js and no
    HTML file mentioned it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "web" / "static"
    css = (root / "style.css").read_text(encoding="utf-8-sig")
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    for name in ("index.html", "market.html", "oc.html", "user.html", "agent.html",
                 "app.js", "site.js"):
        text = (root / name).read_text(encoding="utf-8-sig")
        used = {c for m in re.finditer(r'class="([^"]+)"', text) for c in m.group(1).split()
                if re.fullmatch(r"[a-zA-Z][\w-]*", c)}
        used |= set(re.findall(r"classList\.(?:add|toggle|remove)\(\s*['\"]([\w-]+)['\"]", text))
        assert used <= defined, f"{name}: {sorted(used - defined)}"


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


def test_every_mini_app_translation_key_exists_in_all_three_languages():
    """`web/static/app.html` keeps its own dictionary inline (ru/en/zh), apart
    from bot/i18n.py, so `scripts/check_i18n.py` cannot see it: a key used in
    the UI and added to two of the three languages silently renders English or
    the raw key for the third one."""
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[1] / "web" / "static" / "app.html"
    ).read_text(encoding="utf-8-sig")

    used = set(re.findall(r"""\bt\(\s*['"]([a-z0-9_]+)['"]""", page))
    used |= set(re.findall(r'data-i18n(?:-ph|-title)?="([a-z0-9_]+)"', page))
    assert used, "the Mini App stopped using t() — this test needs re-reading"

    for lang in ("ru", "en", "zh"):
        line = next(
            (l for l in page.splitlines()
             if re.match(rf"\s*{lang}: \{{.*\}},?\s*$", l, re.S)),
            None,
        )
        assert line, f"the {lang} dictionary is no longer a single block"
        defined = set(re.findall(r"""['"]?([a-z0-9_]+)['"]?\s*:""", line))
        assert used <= defined, f"{lang} is missing {sorted(used - defined)}"


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
    market could read 12,34% in one place and 12,35% in the other."""
    from bot import config
    from bot import onchain_market as om

    async def fake_prices(market_id, count):
        # Chosen because the two rounders disagree here: the builtin sees
        # 0.10499999999999999 * 100 and gives 0.1, ROUND_HALF_UP on the decimal
        # text gives 0.11.
        return [0.00105, 0.99895]

    async def fake_info(market_id):
        return {"b": 5_000_000_000, "escrow_micro": 250_000_000, "resolved": False,
                "disputed": False, "cancelled": False, "winning_outcome": None}

    monkeypatch.setattr(om, "market_prices", fake_prices)
    monkeypatch.setattr(om, "get_market_info", fake_info)
    monkeypatch.setattr(config, "OUTCOME_MARKET_ADDRESS", "0x" + "ab" * 20)
    ledger.save_onchain_market(7, 777, "Один и тот же рынок?", ["да", "нет"], 4_000_000_000)

    page = client.get("/api/onchain/market/7").json()["options"]
    listed = next(m for m in client.get("/api/onchain/markets").json() if m["id"] == 7)
    expected = [0.11, 99.9]
    assert [o["price_pct"] for o in page] == expected, page
    assert [o["price_pct"] for o in listed["options"]] == expected, listed


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
    for plumbing in ("/_headers", "/_redirects", "/tos.md"):
        assert client.get(plumbing).status_code == 404, plumbing
    for asset in ("/style.css", "/theme.js", "/app.js", "/site.js", "/miniapp-hero.png", "/icon.png"):
        assert client.get(asset).status_code == 200, asset


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
    where it cannot coincide with the pending line."""
    ledger.credit(1, 50_000_000, "deposit")
    bid = ledger.create_bet(1, "Пул для сверки?", ["Да", "Нет"])
    ledger.place_bet(bid, 1, 0, 10_000_000)
    data = client.get("/api/solvency").json()
    owed = data["liabilities_usdc"]
    assert owed > 0, "nothing on the ledger — the gate would pass on an empty payload"
    assert data["pending_deposits_usdc"] != owed
    duplicated = [key for key, value in data.items()
                  if key != "liabilities_usdc" and isinstance(value, (int, float)) and value == owed]
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
    assert "moneyHeaders" in app, "app.html lost its key helper"

    def windows(text: str, endpoint: str) -> list[str]:
        """Every place the bundle names this endpoint, with the call around it.
        The Mini App picks trade/betplace with a ternary, so the path is not
        always the first argument of the fetch()."""
        quoted = re.escape(f"'{endpoint}'")
        return [text[max(0, m.start() - 120):m.end() + 300]
                for m in re.finditer(quoted, text)]

    for path, actions in sorted(required.items()):
        for name, text, needs in (("site.js", site, r",\s*(?:'[a-z_]+'|action)\s*\)"),
                                  ("app.html", app, r"moneyHeaders\(")):
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

    for fn in ast.walk(ast.parse(sources["server.py"], filename="server.py")):
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
