"""The history rendering contract: one kind table, four readers.

`bot/tx_view.py` decides the label, the sign and the counterparty reference of
every tx_log row. The bot's /history, the landing workspace, the Mini App and
the share profile all render from it, so a kind that is missing a translation or
gets the wrong sign shows up as wrong money in four places at once.
"""
import pytest

from bot import ledger as ledger_mod
from bot.i18n import LANGS, STRINGS
from bot.ledger import is_credit
from bot.tx_view import KIND_REF, label_for, row_view, rows_view

# Every kind the ledger writes, exercised below. `rain` is not a kind of its
# own — it is a `tip` row with note='rain' — and `transaction` is not a kind
# either: it is the fallback label a kind with no translation gets.
PSEUDO_LABELS = {'rain', 'transaction'}
ALL_LABELLED_KINDS = set(KIND_REF) | PSEUDO_LABELS


def test_every_kind_renders_a_label_in_every_language():
    for kind in sorted(ALL_LABELLED_KINDS):
        for lang in LANGS:
            assert label_for(kind, lang).strip(), (kind, lang)


def test_no_label_exists_for_a_kind_without_a_reference_rule():
    """A `kind_*` string with no KIND_REF entry means a kind was added to the
    ledger and never told what its counterparty column holds.

    The two exceptions are labels that are not kinds: `rain` is what a `tip` row
    with note='rain' is shown under, `transaction` is the unknown-kind fallback.
    """
    labelled = {k[len('kind_'):] for k in STRINGS if k.startswith('kind_')}
    assert labelled - set(KIND_REF) == PSEUDO_LABELS


def test_credit_table_matches_the_signs_the_renderers_print():
    for kind in ('deposit', 'rain_in', 'creator_income', 'bet_win', 'market_win',
                 'market_cancel', 'paywall_earn', 'channel_earn', 'x402'):
        assert is_credit(kind), kind
    for kind in ('tip', 'withdraw', 'fee', 'bet', 'paywall', 'channel_pay',
                 'market_create', 'market_buy'):
        assert not is_credit(kind), kind


def test_rain_debits_the_sender_once_and_hides_the_recipient_list():
    row = {'kind': 'tip', 'amount': 3_000_000, 'counterparty': '11,12', 'note': 'rain'}
    view = row_view(row, 'ru')
    assert view['label'] == label_for('rain', 'ru')
    assert view['incoming'] is False
    # "11,12" is not one account: naming it would print a comma list as a user.
    assert view['ref'] is None


def test_rain_lands_as_income_named_by_the_sender():
    row = {'kind': 'rain_in', 'amount': 1_000_000, 'counterparty': '7', 'note': 'rain'}
    view = row_view(row, 'ru', {7: 'alice'})
    assert view['incoming'] is True
    assert view['ref'] == '@alice'


def test_unknown_tip_counterparty_falls_back_to_the_id():
    row = {'kind': 'tip', 'amount': 1_000_000, 'counterparty': '4242', 'note': None}
    assert row_view(row, 'ru')['ref'] == 'id4242'


def test_withdrawal_is_a_debit_even_though_the_amount_is_positive():
    row = {'kind': 'withdraw', 'amount': 5_000_000,
           'counterparty': '0x' + 'ab' * 20, 'note': 'on-chain'}
    view = row_view(row, 'ru')
    assert view['incoming'] is False
    assert view['ref'].startswith('0x')


# ---------- rows_view against the real ledger ----------


@pytest.mark.parametrize('count', [1, 2])
async def test_rain_history_reads_the_same_for_both_sides(ledger, count):
    """The recipient's row must name the sender, and the sender's row must not
    resolve the recipient list into a display name."""
    chat, sender = -1001, 1
    ledger.ensure_user(sender, 'sender')
    for i in range(count):
        recipient = 100 + i
        ledger.record_message(chat, 500 + i, recipient)
        ledger.ensure_user(recipient, f'rec{i}')
    ledger.credit(sender, 10_000_000, 'deposit')
    ok, _, chosen = ledger.rain(chat, sender, 3_000_000, count)
    assert ok and len(chosen) == count

    sender_rows = await rows_view(ledger.history(sender, 20), 'ru', ledger_mod.async_ledger)
    rain_out = next(r for r in sender_rows if r['kind'] == 'tip' and r['note'] == 'rain')
    assert rain_out['incoming'] is False
    assert rain_out['ref'] is None
    assert rain_out['label'] == label_for('rain', 'ru')
    # The deposit and the rain are two separate rows; the rain is one debit.
    assert rain_out['amount_usdc'] == pytest.approx(3_000_000 / 10 ** 6)

    recipient = chosen[0]
    rows = await rows_view(ledger.history(recipient, 20), 'ru', ledger_mod.async_ledger)
    rain_in = next(r for r in rows if r['kind'] == 'rain_in')
    assert rain_in['incoming'] is True
    assert rain_in['ref'] == '@sender'
    assert rain_in['label'] == label_for('rain_in', 'ru')


async def test_history_amounts_leave_in_usdc_and_timestamps_in_epoch_seconds(ledger):
    ledger.ensure_user(1, 'sender')
    ledger.ensure_user(2, 'receiver')
    ledger.credit(1, 2_500_000, 'deposit')
    ledger.transfer(1, 2, 1_250_000)
    rows = await rows_view(ledger.history(1, 10), 'en', ledger_mod.async_ledger)
    tip = next(r for r in rows if r['kind'] == 'tip')
    assert tip['amount_usdc'] == 1.25
    assert tip['ref'] == '@receiver'
    assert tip['label'] == label_for('tip', 'en')
    # created_at is BIGINT epoch seconds; a renderer that treats it as
    # milliseconds prints 1970.
    assert isinstance(tip['created_at'], int) and tip['created_at'] > 1_700_000_000
