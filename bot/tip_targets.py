"""Tip/x402 recipient resolution: Telegram usernames AND Basenames.

A target is resolved in this order:
  1. `@username` / `username`          -> ledger.find_by_username
  2. `name.base.eth` (a Basename)      -> on-chain resolution to an address,
                                          then the tg_id that owns the
                                          address (external linked wallet OR
                                          a custodial in-bot wallet).

Basename resolutions are cached for 5 minutes (names rarely change owners,
and every miss would otherwise cost an RPC round trip).
"""

import asyncio
import time

from bot.chain.basenames import is_basename, resolve_basename_sync, reverse_basename_sync
from bot.ledger import async_ledger as ledger

_CACHE_TTL_SECONDS = 300
_CACHE_MAX = 5000
_cache: dict[str, tuple[int | None, float]] = {}


async def resolve_tip_target(target: str) -> tuple[int | None, str | None]:
    """Resolve a user-supplied target to a tg_id.

    Returns (tg_id, error_key). Exactly one is meaningful:
      - (tg_id, None)              — resolved;
      - (None, None)               — not a basename; caller falls back to the
                                     Telegram-username path;
      - (None, 'basename_unknown') — a well-formed basename with no Tippy
                                     user behind it.
    """
    name = (target or "").strip().lstrip("@").lower()
    if not is_basename(name):
        return None, None

    hit = _cache.get(name)
    if hit is not None:
        tg_id, ts = hit
        if time.time() - ts <= _CACHE_TTL_SECONDS:
            if tg_id is None:
                return None, "basename_unknown"
            return tg_id, None
        _cache.pop(name, None)

    address = await asyncio.to_thread(resolve_basename_sync, name)
    if not address:
        _cache[name] = (None, time.time())
        return None, "basename_unknown"

    tg_id = await ledger.tg_id_of_address(address)  # external linked wallet
    if tg_id is None:
        tg_id = await ledger.tg_id_of_wallet_address(address)  # custodial wallet
    _make_room(_cache)
    _cache[name] = (tg_id, time.time())
    if tg_id is None:
        return None, "basename_unknown"
    return tg_id, None

_reverse_cache: dict[str, tuple[str | None, float]] = {}
_REVERSE_TTL_SECONDS = 600
# In-flight reverse lookups keyed by address, plus how many a batch may run at
# once. `reverse_basename_sync` blocks on an RPC, so a batch of four keeps a top
# list down to a few round trips without opening one per row.
_inflight: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}
_BATCH_CONCURRENCY = 4


def _make_room(cache: dict) -> None:
    """Evict the oldest quarter of a full cache.

    This used to be `cache.clear()`, which fired exactly when the cache was most
    useful: one name over the cap threw away every answer the process had paid
    for, and the next burst of lookups re-resolved all of it over RPC.
    """
    if len(cache) < _CACHE_MAX:
        return
    for key, _entry in sorted(cache.items(), key=lambda kv: kv[1][1])[: _CACHE_MAX // 4]:
        cache.pop(key, None)


def _reverse_hit(address: str) -> tuple[bool, str | None]:
    """(fresh, name) for a cached reverse lookup; fresh=False when absent or expired."""
    entry = _reverse_cache.get(address.lower())
    if entry is None:
        return False, None
    name, ts = entry
    if time.time() - ts > _REVERSE_TTL_SECONDS:
        _reverse_cache.pop(address.lower(), None)
        return False, None
    return True, name


async def _resolve_reverse(address: str) -> str | None:
    name = await asyncio.to_thread(reverse_basename_sync, address)
    _make_room(_reverse_cache)
    _reverse_cache[address.lower()] = (name, time.time())
    return name


async def reverse_name_for(address: str) -> str | None:
    """Primary Basename of one address: cached, and shared with whoever else is
    already asking for the same address on this loop."""
    fresh, name = _reverse_hit(address)
    if fresh:
        return name
    loop = asyncio.get_running_loop()
    running = _inflight.get(address.lower())
    if running is not None and running[0] is loop:
        return await asyncio.shield(running[1])
    task = loop.create_task(_resolve_reverse(address))
    _inflight[address.lower()] = (loop, task)
    task.add_done_callback(lambda _t: _inflight.pop(address.lower(), None))
    return await asyncio.shield(task)


async def display_names_for(tg_ids: list[int]) -> dict[int, str | None]:
    """tg_id -> displayable Basename (None where the user has none).

    One batched address lookup, then only the cache misses resolved a few at a
    time and deduplicated by address: ten leaders sharing two wallets cost two ENS
    calls four-apart, not ten sequential ones behind twenty single-row queries.
    """
    ids = list(dict.fromkeys(int(i) for i in tg_ids))
    if not ids:
        return {}
    addresses = await ledger.addresses_for(ids)
    names: dict[str, str | None] = {}
    pending: list[str] = []
    for addr in dict.fromkeys(a for a in (addresses[i] for i in ids) if a):
        fresh, cached = _reverse_hit(addr)
        if fresh:
            names[addr] = cached
        else:
            pending.append(addr)
    for start in range(0, len(pending), _BATCH_CONCURRENCY):
        chunk = pending[start : start + _BATCH_CONCURRENCY]
        found = await asyncio.gather(*(reverse_name_for(a) for a in chunk))
        names.update(dict(zip(chunk, found, strict=True)))
    return {i: names.get(addresses[i]) if addresses.get(i) else None for i in ids}


async def display_name_for(tg_id: int) -> str | None:
    """A displayable Basename for a user with no Telegram username.

    Resolves the user's address (external linked wallet first, then the
    custodial in-bot wallet) to its PRIMARY basename via ENSIP-19 reverse
    resolution. Cached 10 minutes (negative results too). None = no basename.
    """
    return (await display_names_for([tg_id]))[tg_id]
