"""Basename recipient resolution for tips and x402.

`name.base.eth` resolves on-chain to an address; the address maps to a
Tippy user through EITHER an external linked wallet (wallet_links) OR a
custodial in-bot wallet (user_wallets). Unlinked names are refused with a
clear message (money must never be sent to a name nobody can claim).

The chain resolver is monkeypatched — these tests verify the mapping and
caching logic, not the RPC.
"""

import pytest

from bot import tip_targets
from bot.ledger import Ledger

TEST_DB_URL = "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
ALICE, BOB = 3101, 3102
ALICE_ADDR = "0x" + "a1" * 20
BOB_CUSTODIAL = "0x" + "b2" * 20
UNLINKED = "0x" + "c3" * 20


@pytest.fixture()
def db():
    ledger = Ledger(TEST_DB_URL)
    ledger._conn.execute("TRUNCATE users, wallet_links, user_wallets, link_nonces RESTART IDENTITY")
    ledger._conn.commit()
    yield ledger
    ledger.close()


def _setup_users(ledger):
    """Alice linked her external wallet; Bob owns a custodial in-bot wallet."""
    ledger.ensure_user(ALICE, "alice")
    ledger.ensure_user(BOB, "bob")
    ledger._conn.execute(
        "INSERT INTO wallet_links (tg_id, address) VALUES (%s, %s)",
        (ALICE, ALICE_ADDR),
    )
    ledger._conn.execute(
        "INSERT INTO user_wallets (tg_id, address, key_enc, seed_enc, slot, active) "
        "VALUES (%s, %s, 'enc', 'enc', 1, true)",
        (BOB, BOB_CUSTODIAL),
    )
    ledger._conn.commit()


@pytest.fixture()
def resolver(db, monkeypatch):
    """Patch the chain resolver + the async ledger proxy tip_targets uses."""
    from bot.ledger import AsyncLedger

    addresses = {}
    monkeypatch.setattr(tip_targets, "resolve_basename_sync",
                        lambda name: addresses.get(name.strip().lower().lstrip("@")))
    monkeypatch.setattr(tip_targets, "ledger", AsyncLedger(db))
    return addresses


def _name(owner_addr):
    return {"alice.base.eth": ALICE_ADDR, "bob.base.eth": BOB_CUSTODIAL,
            "stranger.base.eth": UNLINKED}.get(owner_addr, owner_addr)


@pytest.mark.asyncio
async def test_basename_to_linked_wallet(db, resolver):
    _setup_users(db)
    resolver["alice.base.eth"] = ALICE_ADDR
    tg, err = await tip_targets.resolve_tip_target("alice.base.eth")
    assert (tg, err) == (ALICE, None)


@pytest.mark.asyncio
async def test_basename_to_custodial_wallet(db, resolver):
    _setup_users(db)
    resolver["bob.base.eth"] = BOB_CUSTODIAL
    tg, err = await tip_targets.resolve_tip_target("@bob.base.eth")  # @ stripped
    assert (tg, err) == (BOB, None)


@pytest.mark.asyncio
async def test_basename_unlinked_refused(db, resolver):
    _setup_users(db)
    resolver["stranger.base.eth"] = UNLINKED
    tg, err = await tip_targets.resolve_tip_target("stranger.base.eth")
    assert tg is None and err == "basename_unknown"


@pytest.mark.asyncio
async def test_basename_unregistered_refused(db, resolver):
    _setup_users(db)
    # resolver returns None for names nobody registered (miss = no address)
    tg, err = await tip_targets.resolve_tip_target("ghost.base.eth")
    assert tg is None and err == "basename_unknown"


@pytest.mark.asyncio
async def test_non_basename_falls_back(db, resolver):
    """Regular Telegram-style targets return (None, None): the caller falls
    back to find_by_username — basenames never shadow them."""
    tg, err = await tip_targets.resolve_tip_target("alice")
    assert (tg, err) == (None, None)


@pytest.mark.asyncio
async def test_resolution_is_cached(db, resolver, monkeypatch):
    _setup_users(db)
    tip_targets._cache.clear()  # module-level cache: tests must not leak into it
    calls = []
    import bot.tip_targets as tt

    def counting(name):
        calls.append(name)
        return ALICE_ADDR

    monkeypatch.setattr(tt, "resolve_basename_sync", counting)
    tg, err = await tt.resolve_tip_target("cached.base.eth")
    assert tg == ALICE
    tg, err = await tt.resolve_tip_target("cached.base.eth")
    assert tg == ALICE
    assert len(calls) == 1, "second lookup must hit the cache"


@pytest.mark.asyncio
async def test_display_name_reverse_resolution(db, resolver, monkeypatch):
    """A username-less user with a linked address displays their primary
    basename (reverse resolution, cached)."""
    _setup_users(db)
    db._conn.execute("UPDATE users SET username = NULL WHERE tg_id = %s", (ALICE,))
    db._conn.commit()
    import bot.tip_targets as tt
    tip_targets._cache.clear()
    tip_targets._reverse_cache.clear()

    class FakeLedger:
        async def addresses_for(self, tg_ids):
            # matches Ledger.addresses_for: tg_id -> the address a reverse
            # lookup would use (linked wins over the active custodial wallet)
            return {i: (ALICE_ADDR if i == ALICE else None) for i in tg_ids}

    monkeypatch.setattr(tt, "ledger", FakeLedger())
    monkeypatch.setattr(tt, "reverse_basename_sync", lambda addr: "alice.base.eth")

    name = await tt.display_name_for(ALICE)
    assert name == "alice.base.eth"
    monkeypatch.setattr(tt, "reverse_basename_sync",
                        lambda addr: (_ for _ in ()).throw(AssertionError("must be cached")))
    assert await tt.display_name_for(ALICE) == "alice.base.eth"


@pytest.mark.asyncio
async def test_display_name_none_without_addresses(db, monkeypatch):
    db.ensure_user(BOB, "bob")
    db._conn.execute("UPDATE users SET username = NULL WHERE tg_id = %s", (BOB,))
    db._conn.commit()
    import bot.tip_targets as tt
    tip_targets._reverse_cache.clear()

    class NoWalletLedger:
        async def addresses_for(self, tg_ids):
            return dict.fromkeys(tg_ids)

    monkeypatch.setattr(tt, "ledger", NoWalletLedger())
    assert await tt.display_name_for(BOB) is None


@pytest.mark.asyncio
async def test_display_names_for_pays_one_rpc_per_address(db, resolver, monkeypatch):
    """A top list resolves each wallet once, in one address query, and answers
    None for a user with no wallet at all — the old code walked two queries and
    one sequential ENS call per row."""
    _setup_users(db)
    tip_targets._cache.clear()
    tip_targets._reverse_cache.clear()
    carol = 3103
    db.ensure_user(carol, "carol")  # no linked and no custodial wallet
    db._conn.execute("UPDATE users SET username = NULL")
    db._conn.commit()

    calls = []

    def fake_reverse(addr):
        calls.append(addr.lower())
        return f"n{len(calls)}.base.eth"

    monkeypatch.setattr(tip_targets, "reverse_basename_sync", fake_reverse)
    names = await tip_targets.display_names_for([ALICE, ALICE, BOB, carol])
    assert set(names) == {ALICE, BOB, carol}, "every id asked for gets an answer"
    assert names[ALICE] and names[BOB] and names[BOB] != names[ALICE]
    assert names[carol] is None, "no wallet is not a failed lookup"
    assert len(calls) == 2, f"one reverse call per distinct address, got {calls}"


@pytest.mark.asyncio
async def test_concurrent_reverse_lookups_share_one_call(monkeypatch):
    """Two awaits of the same cold address must not open two RPCs."""
    import asyncio
    import threading

    tip_targets._reverse_cache.clear()
    tip_targets._inflight.clear()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_reverse(addr):
        calls.append(addr)
        started.set()
        release.wait(5)
        return "shared.base.eth"

    monkeypatch.setattr(tip_targets, "reverse_basename_sync", blocking_reverse)
    try:
        first = asyncio.create_task(tip_targets.reverse_name_for(ALICE_ADDR))
        await asyncio.to_thread(started.wait, 5)  # the RPC thread is inside
        second = asyncio.create_task(tip_targets.reverse_name_for(ALICE_ADDR))
        release.set()
        assert await first == "shared.base.eth"
        assert await second == "shared.base.eth"
    finally:
        release.set()
    assert len(calls) == 1, calls
    assert tip_targets._inflight == {}, "the in-flight entry is dropped when it finishes"


@pytest.mark.asyncio
async def test_full_cache_drops_the_oldest_quarter_not_everything(monkeypatch):
    """`cache.clear()` at the cap threw away every answer the process had paid
    for; eviction must cost a slice, not the whole cache."""
    import time

    monkeypatch.setattr(tip_targets, "_CACHE_MAX", 8)
    # n6 is the oldest entry (stamped 6 seconds back), n0 the freshest.
    cache = {f"n{i}": (f"tg{i}", time.time() - i) for i in range(7)}
    tip_targets._make_room(cache)
    assert len(cache) == 7, "a cache under the cap is left alone"
    cache["n7"] = ("tg7", time.time())
    tip_targets._make_room(cache)
    assert len(cache) == 6, sorted(cache)
    assert "n6" not in cache and "n5" not in cache, "the oldest entries go first"
    assert "n7" in cache and "n0" in cache, "the fresh answers survive"
