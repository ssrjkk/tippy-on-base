"""Test fixtures: a fresh Postgres test database, patched into bot modules.

Requires a running PostgreSQL server (docker compose up -d db — the default
credentials below match the compose service). A dedicated test database is
dropped and recreated once per session; every test gets a truncated ledger.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Config reads these at import time.
os.environ.setdefault("BOT_TOKEN", "0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("HOT_WALLET_KEY", "0x" + "11" * 32)
os.environ.setdefault("WALLET_ENC_KEY", "a" * 32)  # Test-only: 32-char key for wallet encryption
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci-only-32ch!")
os.environ.setdefault("X402_ENABLED", "1")
os.environ.setdefault("X402_RECEIVE_ADDRESS", "0x0000000000000000000000000000000000000001")
os.environ.setdefault("ADMIN_TG_ID", "111")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret-for-ci")
# Tests fake Base MAINNET (chain 8453); the repo .env points to Sepolia.
os.environ.setdefault("EXPECTED_CHAIN_ID", "8453")
# No test may reach a live node. CI ships no .env, so BASE_RPC_URL would fall
# back to Base mainnet — where this suite's ACC address carries EIP-7702
# delegation code, which made /withdraw refuse it as "smart contract" upstream
# while the very same test passed against a dev Sepolia .env. One refused port
# puts every unmocked chain call on the same offline path in both environments;
# tests that need chain answers stub bot.chain.core.w3 themselves.
os.environ["BASE_RPC_URL"] = "http://127.0.0.1:1"
# Empty string, NOT pop(): bot/config.py load_dotenv()s the dev .env at import
# time and would restore the popped variable, silently wiring live Sepolia
# fallback RPCs into core._w3_providers (they were reached through the system
# proxy and hung the suite when the proxy stalled). An empty env var wins over
# .env, so no test can reach a live node even with a .env present.
os.environ["BASE_RPC_FALLBACK_URLS"] = ""
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
)
# Where to connect to CREATE/DROP the test database.
TEST_ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://tipbot:tipbot@localhost:5432/postgres"
)

TABLES = [
    "users", "tx_log", "pending_deposits", "link_nonces", "wallet_links",
    "bets", "bet_positions", "last_block", "message_authors", "reaction_tips",
    "user_settings", "user_wallets", "x402_payments", "paywall_items", "paywall_purchases",
    "paywall_channels", "paywall_subscriptions", "markets", "market_shares",
    "suspicious_activity", "community_treasuries", "treasury_transactions",
    "treasury_proposals", "treasury_votes", "onchain_markets", "onchain_trades", "gas_drips",
    "notification_outbox", "create2_proxies", "x402_invoices",
    "market_subsidies", "login_nonces",
    "creator_tokens", "creator_token_holders", "creator_dividends",
    "withdraw_confirmations",
    "recurring_payments",
    "paymaster_usage",
]


def _reset_db(ledger) -> None:
    # TRUNCATE can be blocked by open transactions, so roll back any
    # in-progress transaction first, then truncate and commit.
    ledger._conn.rollback()
    ledger._conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
    ledger._conn.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db(request):
    # Skip database setup for production tests — they hit the live site
    if request.session.config.getoption("-m") == "production":
        yield
        return

    import time

    import psycopg

    # DROP ... WITH (FORCE) kills backends left over from a previous run;
    # right after a heavy suite those backends can outlive the client for a
    # moment (and a different-role owner cannot be terminated), so retry.
    last = None
    for attempt in range(5):
        try:
            with psycopg.connect(TEST_ADMIN_URL, connect_timeout=3, autocommit=True) as admin:
                admin.execute("DROP DATABASE IF EXISTS tipbot_test WITH (FORCE)")
                admin.execute("CREATE DATABASE tipbot_test")
            last = None
            break
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    if last is not None:
        pytest.exit(f"PostgreSQL not available: {last}", returncode=1)
    yield


@pytest.fixture(autouse=True)
def _clean_shared_ledger():
    """Reset the shared bot.ledger.ledger singleton before each test.

    Tests that don't request the `ledger` fixture call the import-time
    singleton directly. `_pg_test_db` drops the database at session start,
    which kills that connection; on reconnect to the brand-new empty DB a
    statement can fail ("relation does not exist") and leave the connection
    in [INERROR], poisoning every later fixture-less test (e.g.
    test_bet_card_unknown -> InFailedSqlTransaction). Rolling it back here
    drops that stale transaction so each test starts from a clean state.
    """
    try:
        from bot import ledger as ledger_mod
        led = getattr(ledger_mod, "ledger", None)
        if led is not None:
            led.rollback()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_rpc_breaker():
    """Close the bot.chain.core circuit breaker before every test.

    The breaker is module-level state; a test that touches the chain layer
    (whose endpoint this file pins to a refused port) opens it, and every later
    mocked test inherits the RuntimeError. Reset both knobs so tests are
    isolated from each other's RPC failures.
    """
    from bot.chain import core

    core._cb_fail_times.clear()
    core._cb_open_until = 0.0
    yield


@pytest.fixture(autouse=True)
def _isolate_playwright_event_loop(request):
    """Playwright's sync API spins up its own event loop in a thread. When a
    test uses the `page` fixture, that loop can leak into pytest-asyncio's
    management, causing "cannot be called from a running event loop" for every
    subsequent async test. Force-close any stray loops after Playwright tests.
    """
    yield
    if "page" in request.fixturenames:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except RuntimeError:
            pass


@pytest.fixture(autouse=True)
def _no_create2(monkeypatch):
    """Disable the CREATE2 deposit flow for tests by default.

    The real .env wires a live factory + forwarder; handler tests (e.g.
    cmd_deposit, e2e user journey) must not deploy proxies to Mainnet/Base.
    test_create2.py re-enables it explicitly via its own monkeypatches.
    """
    import bot.create2

    monkeypatch.setattr(bot.create2, "FACTORY_ADDRESS", None)
    monkeypatch.setattr(bot.create2, "CREATE2_SAFE_DEPOSITS", False)


@pytest.fixture()
def ledger(monkeypatch):
    from bot import handlers
    from bot import ledger as ledger_mod

    fresh = ledger_mod.Ledger(TEST_DB_URL)
    _reset_db(fresh)
    monkeypatch.setattr(ledger_mod, "ledger", fresh)
    # Handlers now call the async proxy; wrap the fresh instance so awaited
    # ledger calls hit the hermetic test database.
    async_fresh = ledger_mod.AsyncLedger(fresh)
    monkeypatch.setattr(ledger_mod, "async_ledger", async_fresh)
    monkeypatch.setattr(handlers._common, "ledger", async_fresh)
    # Web modules bind the singleton at import time; rebind them so web
    # tests are hermetic (test database, not whatever DATABASE_URL points to).
    # Wrap in AsyncLedger: the routes now `await ledger.x()`, so the injected
    # ledger must be awaitable too.
    import web.auth
    import web.frame
    import web.mini
    import web.server
    import web.x402

    monkeypatch.setattr(web.server, "ledger", async_fresh)
    monkeypatch.setattr(web.auth, "ledger", async_fresh)
    for _mod in (web.mini, web.frame, web.x402):
        if hasattr(_mod, "ledger"):
            monkeypatch.setattr(_mod, "ledger", async_fresh)
    # tip_targets holds its own module reference to the ledger proxy —
    # rebind it too, or basename resolution would hit the DEV database.
    import bot.tip_targets
    monkeypatch.setattr(bot.tip_targets, "ledger", async_fresh)
    # Same story for the agent, which binds the proxy at import time.
    import agent.pnl
    import agent.tools

    monkeypatch.setattr(agent.tools, "ledger", async_fresh)
    monkeypatch.setattr(agent.pnl, "ledger", async_fresh)
    handlers._common._money_cmd_last.clear()
    web.server._rl_state.clear()
    web.server._auth_rl_state.clear()
    if hasattr(web.auth, "_consumed_login_states"):
        web.auth._consumed_login_states.clear()
    if hasattr(web.mini, "_money_last"):
        web.mini._money_last.clear()
    yield fresh
    fresh.close()  # release the open transaction, or TRUNCATE in the next test hangs
