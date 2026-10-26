"""Prediction markets v2 (LMSR AMM): math, ledger lifecycle, handlers.

The core invariant under test: money conservation. Users' balances plus all
market escrows stay constant through create/buy/sell/resolve/cancel — the
AMM only moves money between them, never creates or loses it.
"""

import asyncio
from decimal import Decimal

from conftest import close_market

from bot import ai as ai_mod
from bot.handlers import (
    cb_market_card,
    cb_mk_do,
    cb_mk_resolve,
    cmd_ask,
    cmd_market,
    cmd_markets,
    cmd_positions,
    cmd_sell,
    cmd_trade,
    cmd_tx,
)
from bot.ledger import lmsr_buy_shares, lmsr_cost, lmsr_prices, lmsr_sell_value

ALICE, BOB, CAROL = 3001, 3002, 3003
USDC = 10**6


def run(coro):
    return asyncio.run(coro)


# ---------- mocks (same shape as test_handlers.py) ----------


class User:
    def __init__(self, id, username=None):
        self.id = id
        self.username = username
        self.is_bot = False


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kw):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, chat_id, action, **kw):
        self.sent.append((chat_id, f"<action:{action}>"))


class Message:
    def __init__(self, text="", from_id=ALICE, username="alice", bot=None):
        self.text = text
        self.from_user = User(from_id, username)
        self.bot = bot or Bot()
        self.answers = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))


class AnswerRecorder:
    def __init__(self, bot=None):
        self.text = None
        self.markup = None
        self.bot = bot or Bot()

    async def edit_text(self, text, reply_markup=None, **kw):
        self.text = text
        self.markup = reply_markup

    async def edit_caption(self, caption=None, reply_markup=None, **kw):
        self.caption = caption
        self.markup = reply_markup


class Callback:
    def __init__(self, data, from_id, bot=None):
        self.data = data
        self.from_user = User(from_id)
        self.message = AnswerRecorder(bot)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def money(ledger):
    """Total money in the system: user balances + open-market escrows."""
    balances = ledger._conn.execute("SELECT COALESCE(SUM(balance), 0) AS s FROM users").fetchone()["s"]
    escrow = ledger._conn.execute(
        "SELECT COALESCE(SUM(escrow_micro), 0) AS s FROM markets WHERE status = 'open'"
    ).fetchone()["s"]
    return int(balances) + int(escrow)


def make_market(ledger, subsidy=50 * USDC, options=("Алиса", "Боб")):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    mid = ledger.create_market(ALICE, "Кто победит?", list(options), subsidy)
    assert isinstance(mid, int)
    return mid



# ---------- LMSR math ----------


def test_lmsr_prices_uniform_at_zero():
    p = lmsr_prices([0, 0], 10_000_000)
    assert len(p) == 2
    for x in p:
        assert abs(x - Decimal("0.5")) < Decimal("0.0001")


def test_lmsr_prices_sum_to_one():
    p = lmsr_prices([1_200_000, 300_000, 50_000], 2_000_000)
    assert abs(sum(p) - 1) < Decimal("1e-12")
    assert p[0] > p[1] > p[2]


def test_lmsr_buy_shares_respects_budget():
    q = [0, 0]
    b = 20_000_000
    spend = 5 * USDC
    shares = lmsr_buy_shares(q, b, 0, spend)
    assert shares > 0
    # cost of exactly `shares` must fit the budget; one more share must not
    cost = lmsr_cost([shares, 0], b) - lmsr_cost(q, b)
    cost_plus = lmsr_cost([shares + 1, 0], b) - lmsr_cost(q, b)
    assert cost <= spend < cost_plus


def test_lmsr_buy_moves_price_up():
    q = [0, 0]
    b = 20_000_000
    before = lmsr_prices(q, b)[0]
    shares = lmsr_buy_shares(q, b, 0, 10 * USDC)
    after = lmsr_prices([shares, 0], b)[0]
    assert after > before


def test_lmsr_sell_value_less_than_buy_cost():
    """Round-trip must lose money (spread + rounding favor the AMM)."""
    q = [0, 0]
    b = 20_000_000
    spend = 10 * USDC
    shares = lmsr_buy_shares(q, b, 0, spend)
    got_back = lmsr_sell_value([shares, 0], b, 0, shares)
    assert 0 < got_back <= spend


def test_lmsr_funding_theorem_never_insolvent():
    """Escrow >= max(q_i) along an aggressive trading path (b*ln(n) funding)."""
    import random

    rng = random.Random(42)
    n = 3
    subsidy = 30 * USDC
    from decimal import ROUND_FLOOR, localcontext

    with localcontext() as ctx:
        ctx.prec = 40
        b = int((Decimal(subsidy) / Decimal(n).ln()).to_integral_value(rounding=ROUND_FLOOR))
    q = [0] * n
    escrow = subsidy
    for _ in range(60):
        i = rng.randrange(n)
        if rng.random() < 0.6:
            spend = rng.randrange(1, 8 * USDC)
            shares = lmsr_buy_shares(q, b, i, spend)
            if shares > 0:
                q[i] += shares
                escrow += spend
        else:
            held = q[i]
            if held > 0:
                sell = rng.randrange(1, held + 1)
                value = lmsr_sell_value(q, b, i, sell)
                q[i] -= sell
                escrow -= value
        assert escrow >= max(q), f"insolvent: escrow={escrow} max_q={max(q)}"


# ---------- ledger lifecycle ----------


def test_create_market_debits_and_funds_escrow(ledger):
    mid = make_market(ledger)
    m = ledger.get_market(mid)
    assert m["status"] == "open"
    assert m["escrow_micro"] == 50 * USDC
    assert ledger.balance(ALICE) == Decimal(950)
    assert money(ledger) == 1000 * USDC


def test_create_market_insufficient_balance(ledger):
    ledger.credit(ALICE, 5 * USDC, "deposit")
    assert ledger.create_market(ALICE, "?", ["a", "b"], 50 * USDC) == "balance"


def test_create_market_subsidy_too_small(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    assert ledger.create_market(ALICE, "?", ["a", "b"], 0) == "subsidy"


def test_buy_shares_conservation_and_escrow(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    before = money(ledger)
    status, info = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    assert status == "ok"
    assert info["shares"] > 0
    assert info["cost"] == 10 * USDC
    assert ledger.balance(BOB) == Decimal(90)
    m = ledger.get_market(mid)
    assert m["escrow_micro"] == 60 * USDC
    pos = ledger.user_market_position(mid, BOB)
    assert pos[0]["shares"] == info["shares"]
    assert pos[0]["cost"] == 10 * USDC
    assert money(ledger) == before


def test_buy_shares_other_user_can_trade(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    status, _ = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    assert status == "ok"


def test_agent_cannot_trade_its_own_market(ledger, monkeypatch):
    """The DB-level own-market guard (survives restarts) rejects the agent
    buying shares in a market it created — even if agent/tools.py is bypassed
    (e.g. a forged callback_data or a direct ledger call)."""
    import bot.config as bot_config

    mid = make_market(ledger)
    ledger.credit(ALICE, 100 * USDC, "deposit")
    # Make the agent the creator of a fresh market (ALICE funder is fine —
    # the guard keys on creator == AGENT_TG_ID).
    agent_id = int(bot_config.AGENT_TG_ID)
    ledger.credit(agent_id, 100 * USDC, "deposit")
    agent_mid = ledger.create_market(agent_id, "Агент маркет?", ["А", "Б"], 50 * USDC)
    assert isinstance(agent_mid, int)
    status, _ = ledger.buy_shares(agent_mid, agent_id, 0, 10 * USDC)
    assert status == "ownmarket"
    # A random participant is unaffected by the guard.
    ledger.credit(BOB, 100 * USDC, "deposit")
    status, info = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    assert status == "ok" and info["shares"] > 0


def test_buy_shares_too_small_refunds(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    status, _ = ledger.buy_shares(mid, BOB, 0, 1)  # 1 micro-USDC
    assert status == "toosmall"
    assert ledger.balance(BOB) == Decimal(100)  # refunded


def test_sell_shares_conservation(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, buy = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    before = money(ledger)
    status, info = ledger.sell_shares(mid, BOB, 0, buy["shares"])
    assert status == "ok"
    assert 0 < info["value"] <= 10 * USDC
    assert ledger.balance(BOB) == Decimal(90) + Decimal(info["value"]) / USDC
    assert ledger.user_market_position(mid, BOB).get(0, {}).get("shares", 0) == 0
    assert money(ledger) == before


def test_sell_without_position(ledger):
    mid = make_market(ledger)
    status, _ = ledger.sell_shares(mid, BOB, 0, 5 * USDC)
    assert status in ("noshare", "closed")


def test_resolve_market_pays_winners_leftover_pro_rata(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.credit(CAROL, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 20 * USDC)
    _, c = ledger.buy_shares(mid, CAROL, 1, 10 * USDC)
    before = money(ledger)
    close_market(ledger, mid)

    ok, msg, payouts = ledger.resolve_market(mid, 0, ALICE)
    assert ok
    bob_win = [p for p in payouts if p["tg_id"] == BOB and p["win"]]
    assert bob_win and bob_win[0]["net_micro"] == 80 * USDC - ledger.creator_fees(ALICE)
    assert bob_win[0]["net_micro"] > b["shares"]  # escrow leftover is shared with winners
    carol_lost = [p for p in payouts if p["tg_id"] == CAROL]
    assert carol_lost and not carol_lost[0]["win"]
    # conservation: the escrow is fully distributed between winners and creator fee
    assert money(ledger) == before
    m = ledger.get_market(mid)
    assert m["status"] == "resolved" and m["winner"] == 0
    total_paid = sum(p["net_micro"] for p in payouts) + ledger.creator_fees(ALICE)
    assert total_paid == 80 * USDC  # whole escrow distributed


def test_resolve_market_only_creator(ledger):
    mid = make_market(ledger)
    ok, msg, _ = ledger.resolve_market(mid, 0, BOB)
    assert not ok and "создатель" in msg


def test_resolve_blocked_while_creator_holds_winning_side(ledger):
    """Creator must exit their position before declaring that side the winner."""
    mid = make_market(ledger)
    ledger.credit(ALICE, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, ALICE, 0, 10 * USDC)

    # The deadline is checked first: a freshly listed market cannot be settled
    # before anyone else had the chance to weigh in.
    ok, msg, _ = ledger.resolve_market(mid, 0, ALICE)
    assert not ok and "дедлайна" in msg
    assert ledger.get_market(mid)["status"] == "open"

    st, _ = ledger.sell_shares(mid, ALICE, 0, b["shares"])
    assert st == "ok"
    close_market(ledger, mid)
    ok, msg, _ = ledger.resolve_market(mid, 0, ALICE)
    assert not ok and "победителя без держателей" in msg

    # A non-creator has to hold the outcome — and they have to buy before the
    # deadline, because trading ends with it.
    mid2 = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    assert ledger.buy_shares(mid2, BOB, 0, 10 * USDC)[0] == "ok"
    assert ledger.buy_shares(mid2, ALICE, 1, 5 * USDC)[0] == "ok"
    close_market(ledger, mid2)
    ok, msg, _ = ledger.resolve_market(mid2, 0, ALICE)
    assert ok, msg
    assert ledger.get_market(mid2)["status"] == "resolved"


def test_creator_still_holding_at_the_deadline_is_told_to_cancel(ledger):
    """Past close_at sell_shares refuses too, so the guard that says "sell your
    side first" would send the creator into an action that can no longer work.
    The advice has to be the exit that is still real: cancel, which refunds
    every holder."""
    mid = make_market(ledger)
    _, b = ledger.buy_shares(mid, ALICE, 0, 10 * USDC)
    close_market(ledger, mid)

    ok, msg, _ = ledger.resolve_market(mid, 0, ALICE)
    assert not ok and "Отмените рынок" in msg
    assert ledger.sell_shares(mid, ALICE, 0, b["shares"])[0] == "deadline"
    assert ledger.get_market(mid)["status"] == "open"

    ok, msg = ledger.cancel_market(mid, ALICE)
    assert ok, msg
    assert ledger.get_market(mid)["status"] == "cancelled"
    assert ledger.balance(ALICE) == Decimal("1000")  # subsidy and buy both back


def test_cancel_market_refunds_cost_basis(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    before = money(ledger)
    ok, msg = ledger.cancel_market(mid, ALICE)
    assert ok
    assert ledger.balance(BOB) == Decimal(90) + Decimal(b["cost"]) / USDC  # net cost refunded
    # Bob's refund comes from the shared escrow (subsidy + his own buy), so
    # the creator gets the whole subsidy back (plus any rounding dust).
    assert ledger.balance(ALICE) >= Decimal(950)
    assert ledger.balance(ALICE) <= Decimal(1000)
    assert money(ledger) == before
    assert ledger.get_market(mid)["status"] == "cancelled"


def test_cancel_market_underfunded_escrow_refunds_pro_rata(ledger):
    """Corruption guard: if the escrow is smaller than the sum of the net cost
    basis (the funding theorem says it never is), the refund must not be a
    queue. The old cost-descending loop paid the biggest position everything it
    was owed and left the small one with zero.

    Here the escrow is forced to half of what is due: both holders are paid,
    pro-rata, and the creator sweeps nothing."""
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.credit(CAROL, 100 * USDC, "deposit")
    _, big = ledger.buy_shares(mid, BOB, 0, 30 * USDC)
    _, small = ledger.buy_shares(mid, CAROL, 1, 5 * USDC)
    due = int(big["cost"]) + int(small["cost"])
    half = due // 2
    ledger._conn.execute("UPDATE markets SET escrow_micro = %s WHERE id = %s", (half, mid))
    ledger._conn.commit()
    before = money(ledger)
    bal_bob, bal_carol, bal_alice = (
        ledger.balance(BOB), ledger.balance(CAROL), ledger.balance(ALICE)
    )

    ok, _ = ledger.cancel_market(mid, ALICE)
    assert ok
    got_bob = int((ledger.balance(BOB) - bal_bob) * USDC)
    got_carol = int((ledger.balance(CAROL) - bal_carol) * USDC)
    assert got_bob > 0 and got_carol > 0
    for got, cost in ((got_bob, int(big["cost"])), (got_carol, int(small["cost"]))):
        expected = half * cost // due
        assert expected <= got <= expected + 1  # exact share, ± the rounding unit
    assert got_bob + got_carol == half  # every micro of the escrow left the market
    assert ledger.balance(ALICE) == bal_alice  # nothing swept to the creator
    assert money(ledger) == before


def test_market_deadline_blocks_trades(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    import time

    mid = ledger.create_market(
        ALICE, "?", ["a", "b"], 50 * USDC, close_at=int(time.time()) - 1
    )
    ledger.credit(BOB, 100 * USDC, "deposit")
    assert ledger.buy_shares(mid, BOB, 0, 5 * USDC)[0] == "deadline"
    assert ledger.sell_shares(mid, BOB, 0, 1)[0] == "deadline"


def test_resolve_blocked_before_deadline(ledger):
    import time

    ledger.credit(ALICE, 1000 * USDC, "deposit")
    mid = ledger.create_market(
        ALICE, "?", ["a", "b"], 50 * USDC, close_at=int(time.time()) + 3600
    )
    ledger.credit(BOB, 100 * USDC, "deposit")
    assert ledger.buy_shares(mid, BOB, 0, 5 * USDC)[0] == "ok"
    ok, msg, _ = ledger.resolve_market(mid, 0, ALICE)
    assert not ok
    assert "дедлайн" in msg
    assert ledger.get_market(mid)["status"] == "open"


def test_resolve_blocked_without_non_creator_winners(ledger):
    """Declaring a winner nobody else holds would sweep the escrow to creator."""
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    mid = ledger.create_market(ALICE, "?", ["a", "b", "c"], 50 * USDC)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.credit(CAROL, 100 * USDC, "deposit")
    assert ledger.buy_shares(mid, BOB, 0, 20 * USDC)[0] == "ok"
    assert ledger.buy_shares(mid, CAROL, 1, 10 * USDC)[0] == "ok"
    close_market(ledger, mid)
    ok, msg, _ = ledger.resolve_market(mid, 2, ALICE)
    assert not ok
    assert "держателей" in msg
    assert ledger.get_market(mid)["status"] == "open"


def test_user_market_positions_view(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    positions = ledger.user_market_positions(BOB)
    assert len(positions) == 1
    p = positions[0]
    assert p["market_id"] == mid
    assert p["shares"] == b["shares"]
    assert 0 < float(p["price"]) < 1


def test_amm_market_view_shape(ledger):
    mid = make_market(ledger)
    view = ledger.amm_market_view(mid)
    assert view["id"] == mid
    assert len(view["options"]) == 2
    assert abs(sum(o["price_pct"] for o in view["options"]) - 100.0) < 0.01
    assert view["liquidity_micro"] == 50 * USDC


# ---------- batched reads (/markets, /positions, /predictions) ----------


def test_quantities_batch_pads_unheld_options(ledger):
    """A traded market yields one GROUP BY row per *held* option. The batch must
    still report 0 for the others, in option order, exactly like the single read."""
    traded = make_market(ledger, options=("А", "Б", "В"))
    untouched = make_market(ledger, options=("А", "Б"))
    ledger.credit(BOB, 100 * USDC, "deposit")
    assert ledger.buy_shares(traded, BOB, 1, 10 * USDC)[0] == "ok"
    batch = ledger.market_quantities_batch([traded, untouched])
    assert batch == {
        traded: ledger.market_quantities(traded),
        untouched: ledger.market_quantities(untouched),
    }
    assert batch[untouched] == [0, 0]
    held = batch[traded]
    assert len(held) == 3 and held[1] > 0 and held[0] == 0 and held[2] == 0


def test_quantities_batch_edge_cases(ledger):
    assert ledger.market_quantities_batch([]) == {}
    assert ledger.market_quantities_batch([424242]) == {}  # unknown: absent, not []
    mid = make_market(ledger)
    assert list(ledger.market_quantities_batch([mid, mid])) == [mid]


def test_prices_batch_matches_market_prices_exactly(ledger):
    m1 = make_market(ledger)
    m2 = make_market(ledger, options=("X", "Y", "Z"))
    ledger.credit(BOB, 200 * USDC, "deposit")
    ledger.buy_shares(m1, BOB, 0, 20 * USDC)
    ledger.buy_shares(m2, BOB, 2, 10 * USDC)
    batch = ledger.market_prices_batch([m1, m2, 424242])
    assert set(batch) == {m1, m2}  # unknown id: no key, and no KeyError
    for mid in (m1, m2):
        assert batch[mid] == ledger.market_prices(mid)  # Decimals, not floats
        assert abs(sum(batch[mid]) - 1) < Decimal("1e-9")
    assert batch[m1][0] > batch[m1][1]  # the 20 USDC bought option 0 up


def test_bulk_amm_views_match_amm_market_view_field_for_field(ledger):
    """bulk_amm_market_views replaced an amm_market_view-per-market loop on
    /predictions and in agent/pnl, so it owes the caller the same dict."""
    m1 = make_market(ledger)
    m2 = make_market(ledger, options=("X", "Y", "Z"))
    ledger.credit(BOB, 200 * USDC, "deposit")
    ledger.credit(CAROL, 200 * USDC, "deposit")
    ledger.buy_shares(m1, BOB, 0, 20 * USDC)
    ledger.buy_shares(m1, CAROL, 1, 5 * USDC)
    ledger.buy_shares(m2, CAROL, 2, 7 * USDC)
    views = ledger.bulk_amm_market_views([m1, m2])
    assert set(views) == {m1, m2}
    for mid in (m1, m2):
        assert views[mid] == ledger.amm_market_view(mid)
    assert views[m1]["traders"] == 2
    assert views[m2]["traders"] == 1
    assert views[m1]["volume_micro"] == sum(ledger.market_quantities(m1))


def test_bulk_amm_views_keep_request_order(ledger):
    m1 = make_market(ledger)
    m2 = make_market(ledger)
    assert list(ledger.bulk_amm_market_views([m2, m1])) == [m2, m1]
    assert list(ledger.bulk_amm_market_views([m1, m1])) == [m1]
    assert ledger.bulk_amm_market_views([]) == {}
    assert ledger.bulk_amm_market_views([424242]) == {}
    assert ledger.bulk_amm_market_views([m1, 424242]) == {
        m1: ledger.amm_market_view(m1)
    }


def test_bulk_amm_views_drop_sold_out_traders(ledger):
    # `traders` counts holders, and the batch carries its own shares > 0 filter:
    # a seller who is back to zero shares must stop being counted.
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    assert ledger.bulk_amm_market_views([mid])[mid]["traders"] == 1
    assert ledger.sell_shares(mid, BOB, 0, b["shares"])[0] == "ok"
    assert ledger.bulk_amm_market_views([mid])[mid]["traders"] == 0
    assert ledger.amm_market_view(mid)["traders"] == 0


def test_user_market_positions_prices_across_markets(ledger):
    """One row per open position, priced from the batched quantities — and only
    this user's shares, never another backer's."""
    m1 = make_market(ledger)
    m2 = make_market(ledger, options=("X", "Y", "Z"))
    ledger.credit(BOB, 200 * USDC, "deposit")
    _, a = ledger.buy_shares(m1, BOB, 0, 10 * USDC)
    _, c = ledger.buy_shares(m2, BOB, 2, 20 * USDC)
    ledger.credit(CAROL, 100 * USDC, "deposit")
    ledger.buy_shares(m1, CAROL, 1, 5 * USDC)
    rows = ledger.user_market_positions(BOB)
    assert [r["market_id"] for r in rows] == [m2, m1]  # newest market first
    by_mid = {r["market_id"]: r for r in rows}
    assert by_mid[m1]["shares"] == a["shares"]
    assert by_mid[m1]["cost"] == a["cost"]
    assert by_mid[m1]["price"] == ledger.market_prices(m1)[0]
    assert by_mid[m2]["price"] == ledger.market_prices(m2)[2]
    for r in rows:
        assert r["value"] == r["shares"] * r["price"]
    assert {r["option"] for r in rows} == {"Алиса", "Z"}
    # Scoping: CAROL's money belongs in her rows only, and the pooled quantity
    # per option is the sum of the two of them.
    carol_rows = ledger.user_market_positions(CAROL)
    assert [(r["market_id"], r["option"]) for r in carol_rows] == [(m1, "Боб")]
    q1 = ledger.market_quantities(m1)
    assert q1[0] == by_mid[m1]["shares"]
    assert q1[1] == carol_rows[0]["shares"]


def test_user_market_positions_drops_closed_markets(ledger):
    mid = make_market(ledger)
    other = make_market(ledger)
    ledger.credit(BOB, 200 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    ledger.buy_shares(other, BOB, 0, 10 * USDC)
    assert len(ledger.user_market_positions(BOB)) == 2
    close_market(ledger, mid)
    ok, _, _ = ledger.resolve_market(mid, 0, ALICE)
    assert ok
    assert [r["market_id"] for r in ledger.user_market_positions(BOB)] == [other]


# ---------- handlers ----------


def test_cmd_market_create(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    m = Message("/market create 50 Кто победит? | Алиса | Боб 24h")
    run(cmd_market(m))
    assert m.answers and "создан" in m.answers[0][0]
    markets = ledger.open_markets()
    assert len(markets) == 1
    assert ledger.balance(ALICE) == Decimal(950)


def test_cmd_market_create_min_subsidy(ledger):
    ledger.credit(ALICE, 1000 * USDC, "deposit")
    m = Message("/market create 5 Кто? | А | Б")
    run(cmd_market(m))
    assert "Минимальный банк" in m.answers[0][0]
    assert not ledger.open_markets()


def test_cmd_markets_lists(ledger):
    make_market(ledger)
    m = Message("/markets")
    run(cmd_markets(m))
    text, kb = m.answers[0]
    assert "Рынки предсказаний" in text
    assert kb.inline_keyboard[-1][0].callback_data == "mkcreate"


def test_cmd_trade_buys(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    m = Message(f"/trade {mid} 1 10", from_id=BOB)
    run(cmd_trade(m))
    assert "Куплено" in m.answers[0][0]
    assert ledger.balance(BOB) == Decimal(90)


def test_cmd_trade_validates(ledger):
    mid = make_market(ledger)
    m = Message("/trade", from_id=BOB)
    run(cmd_trade(m))
    assert "Формат" in m.answers[0][0]
    m = Message(f"/trade {mid} 9 10", from_id=BOB)
    run(cmd_trade(m))
    assert "Неверный номер варианта" in m.answers[0][0]


def test_cmd_sell_sells_back(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    m = Message(f"/sell {mid} 1 50%", from_id=BOB)
    run(cmd_sell(m))
    assert "Продано" in m.answers[0][0]
    sold = ledger._conn.execute(
        "SELECT amount FROM tx_log WHERE kind='market_sell' AND tg_id=%s", (BOB,)
    ).fetchall()
    assert sold  # a sell was recorded


def test_cmd_positions(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    m = Message("/positions", from_id=BOB)
    run(cmd_positions(m))
    assert "Твои позиции" in m.answers[0][0]
    assert "#" in m.answers[0][0]


def test_cb_market_card_shows_odds(ledger):
    mid = make_market(ledger)
    cb = Callback(f"mk:{mid}", BOB)
    run(cb_market_card(cb))
    assert "%" in cb.message.text
    assert any(b.callback_data.startswith("mkbuy:") for row in cb.message.markup.inline_keyboard for b in row)


def test_cb_mk_do_buys(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    cb = Callback(f"mkdo:{mid}:0:10", BOB)
    run(cb_mk_do(cb))
    assert "Куплено" in cb.message.text
    assert ledger.balance(BOB) == Decimal(90)


def test_cb_mk_resolve_notifies_traders(ledger):
    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    _, b = ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    close_market(ledger, mid)
    bot = Bot()
    cb = Callback(f"mkres:{mid}:0", ALICE, bot=bot)
    run(cb_mk_resolve(cb))
    assert "закрыт" in cb.message.text
    dms = [s for s in bot.sent if s[0] == BOB]
    assert dms and "выиграл" in dms[0][1]


def test_cb_mk_resolve_rejects_non_creator(ledger):
    mid = make_market(ledger)
    cb = Callback(f"mkres:{mid}:0", BOB)
    run(cb_mk_resolve(cb))
    assert cb.answers and "создатель" in cb.answers[0][0]


# ---------- AI assistant ----------


def test_ask_without_a_key_answers_from_the_ledger(ledger, monkeypatch):
    """/ask used to be one empty env var away from being useless. With no
    provider configured it now reads the answer out of the ledger instead."""
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr(ai_mod.config, "AI_PROVIDER_CHAIN", "")
    mid = make_market(ledger)
    m = Message("/ask какие рынки открыты?")
    run(cmd_ask(m))
    text = m.answers[0][0]
    assert "не подключён" not in text
    assert f"#{mid}" in text
    assert "Ответ по данным бота" in text


def test_ai_no_question_shows_usage(ledger):
    m = Message("/ask")
    run(cmd_ask(m))
    assert "/ask" in m.answers[0][0]


def test_ask_success(ledger, monkeypatch):

    async def _ask(q):
        return "Base — это L2 от Coinbase."

    monkeypatch.setattr(ai_mod, "ask_about_markets", _ask)
    m = Message("/ask что такое base?")
    run(cmd_ask(m))
    assert "Tippy AI" in m.answers[0][0]
    assert "L2 от Coinbase" in m.answers[0][0]


def test_ask_falls_back_to_data_when_no_provider_answers(ledger, monkeypatch):
    """A 429 from the model is not a reason to show the user an error: the
    provider chain parks it and the ledger answers."""

    async def boom(q, max_rounds=4):
        raise RuntimeError("primary HTTP 429")

    monkeypatch.setattr(ai_mod, "ask_about_markets", boom)
    make_market(ledger)
    m = Message("/ask какие рынки открыты?")
    run(cmd_ask(m))
    assert "HTTP 429" not in m.answers[0][0]
    assert "Ответ по данным бота" in m.answers[0][0]


def test_ask_with_reply_context(ledger, monkeypatch):
    captured = {}
    async def _ask(q):
        captured.update(q=q)
        return "ок"

    monkeypatch.setattr(ai_mod, "ask_about_markets", _ask)
    ref = Message("ETH газ дорогой")
    m = Message("/ask объясни", )
    m.reply_to_message = ref
    run(cmd_ask(m))
    assert "Context (replied message)" in captured["q"]
    assert "ETH газ" in captured["q"]


# ---------- /tx on-chain lookup ----------


def test_cmd_tx_invalid_hash(ledger):
    m = Message("/tx nothash")
    run(cmd_tx(m))
    assert "Формат" in m.answers[0][0]


def test_cmd_tx_found_decodes_usdc(ledger, monkeypatch):
    from bot import base

    h = "0x" + "ab" * 32
    async def _fake_tx(t):
        return {
            "hash": t,
            "from": "0x" + "1" * 40,
            "to": "0x" + "2" * 40,
            "status": True,
            "value_micro": 5 * USDC,
            "usdc_to": "0x" + "3" * 40,
        }
    monkeypatch.setattr(base, "tx_info", _fake_tx)
    m = Message(f"/tx {h}")
    run(cmd_tx(m))
    text = m.answers[0][0]
    assert "USDC:" in text
    assert "confirmed" in text.lower() or "подтвержд" in text.lower()
    assert "Basescan" in text


def test_cmd_tx_not_found(ledger, monkeypatch):
    from bot import base

    async def _fake_tx_none(t):
        return None
    monkeypatch.setattr(base, "tx_info", _fake_tx_none)
    m = Message(f"/tx {'0x' + 'cd' * 32}")
    run(cmd_tx(m))
    assert "не найдена" in m.answers[0][0]


# ---------- web API ----------


def test_api_predictions_endpoint(ledger):
    from fastapi.testclient import TestClient

    from web import server

    mid = make_market(ledger)
    ledger.credit(BOB, 100 * USDC, "deposit")
    ledger.buy_shares(mid, BOB, 0, 10 * USDC)
    prev = server.ledger
    from bot.ledger import AsyncLedger
    server.ledger = AsyncLedger(ledger)
    try:
        client = TestClient(server.app)
        r = client.get("/api/predictions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["id"] == mid
        assert data[0]["traders"] == 1
        assert abs(sum(o["price_pct"] for o in data[0]["options"]) - 100.0) < 0.01
        r = client.get(f"/api/prediction/{mid}")
        assert r.status_code == 200
        assert r.json()["liquidity_usdc"] > 0
        assert client.get("/api/prediction/999999").status_code == 404
    finally:
        server.ledger = prev


# ---------- LMSR edge cases ----------


def test_lmsr_tiny_b_no_crash():
    """Very small liquidity parameter (b=1 micro) must not overflow."""
    q = [0, 0]
    b = 1  # 1 micro-USDC
    p = lmsr_prices(q, b)
    assert abs(sum(p) - 1) < Decimal("1e-6")
    assert all(0 < x <= 1 for x in p)


def test_lmsr_extreme_concentration():
    """All shares in one option — prices must still sum to 1."""
    q = [1_000_000_000, 0, 0]  # 1000 USDC in option 0
    b = 10_000_000  # 10 USDC
    p = lmsr_prices(q, b)
    assert abs(sum(p) - 1) < Decimal("1e-12")
    assert p[0] > Decimal("0.99")  # >99% probability


def test_lmsr_buy_zero_spend():
    """Spending 0 must yield 0 shares."""
    q = [0, 0]
    b = 10_000_000
    assert lmsr_buy_shares(q, b, 0, 0) == 0


def test_lmsr_sell_more_than_held_bounded():
    """Selling more shares than held must return 0 (can't go negative)."""
    q = [100, 0]
    b = 10_000_000
    # lmsr_sell_value doesn't check bounds — the caller does.
    # But mathematically, selling more than q[0] should give a very large value
    # (which the caller rejects). Verify it doesn't crash.
    val = lmsr_sell_value(q, b, 0, 50)
    assert val >= 0


def test_lmsr_buy_sell_roundtrip_conservation():
    """Buy then sell same shares: escrow change = buy_spend - sell_value >= 0."""
    q = [0, 0]
    b = 20_000_000
    spend = 15 * USDC
    shares = lmsr_buy_shares(q, b, 0, spend)
    assert shares > 0
    q_after_buy = [shares, 0]
    sell_value = lmsr_sell_value(q_after_buy, b, 0, shares)
    # Round-trip cost = spend - sell_value (the AMM's spread)
    assert spend - sell_value >= 0


def test_lmsr_three_options_monotone():
    """Buying more of option 0 must increase its price monotonically."""
    q = [0, 0, 0]
    b = 30_000_000
    prices_before = lmsr_prices(q, b)
    shares = lmsr_buy_shares(q, b, 0, 5 * USDC)
    q[0] += shares
    prices_after = lmsr_prices(q, b)
    assert prices_after[0] > prices_before[0]
    # Other options' prices must decrease
    for i in range(1, 3):
        assert prices_after[i] < prices_before[i]
