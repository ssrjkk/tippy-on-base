"""How a tx_log row is shown to a human.

Amounts in tx_log are always positive and `tg_id` names the account the row
belongs to, so the sign, the label and the counterparty reference all come from
the kind — and every reader (bot /history, the workspace panel, the Mini App,
the share profile) must agree on it. Deriving the sign from the amount instead
renders a withdrawal as income.
"""

from decimal import Decimal

from bot.i18n import t
from bot.ledger import MICRO, is_credit

__all__ = ['label_for', 'row_view', 'rows_view']

# What the `counterparty` column holds for each kind: another Telegram account,
# an on-chain address, a market id, or nothing.
KIND_REF = {
    'tip': 'user', 'rain_in': 'user',
    'deposit': 'address', 'withdraw': 'address', 'fee': 'address', 'x402': 'address',
    'bet': 'market', 'bet_win': 'market', 'bet_cancel': 'market', 'creator_income': 'market',
    'paywall': 'market', 'paywall_earn': 'market', 'channel_pay': 'market',
    'channel_earn': 'market',
    'market_create': 'market', 'market_buy': 'market', 'market_sell': 'market',
    'market_win': 'market', 'market_cancel': 'market',
}


def label_for(kind: str, lang: str) -> str:
    return t(lang, f'kind_{kind}')


def row_view(row: dict, lang: str, names: dict[int, str] | None = None) -> dict:
    """One tx_log row → the fields a renderer needs. `names` maps the user ids
    of tip rows to their display names; without it a user reference is shown as
    `id<n>`, the same fallback the bot uses."""
    kind = row['kind']
    note = row.get('note') or ''
    # A rain debits the sender with one `tip` row whose counterparty is the list
    # of everyone it landed on; the recipients get their own `rain_in` rows.
    is_rain_out = kind == 'tip' and note == 'rain'
    ref_kind = None if is_rain_out else KIND_REF.get(kind)
    counterparty = str(row.get('counterparty') or '')
    ref = None
    if ref_kind == 'user':
        uid = int(counterparty) if counterparty.isdigit() else None
        if uid is not None:
            name = (names or {}).get(uid)
            ref = f'@{name}' if name else f'id{uid}'
    elif counterparty and not is_rain_out:
        ref = counterparty
    return {
        'kind': kind,
        'label': label_for('rain' if is_rain_out else kind, lang),
        'incoming': is_credit(kind),
        'ref': ref,
        'note': note,
    }


USER_REF_KINDS = ('tip', 'rain_in')


def _names_a_user(row: dict) -> bool:
    """True when this row's counterparty is a single Telegram account to resolve.

    The sender's own rain row is the exception: its counterparty is the comma
    list of everyone the rain landed on, not one id.
    """
    if row.get('kind') not in USER_REF_KINDS:
        return False
    if row['kind'] == 'tip' and row.get('note') == 'rain':
        return False
    return str(row.get('counterparty') or '').isdigit()


async def rows_view(rows: list[dict], lang: str, ledger) -> list[dict]:
    """tx_log rows → API history rows, in one call: the display name of every
    tip counterparty is resolved in a single query rather than one per row.

    Amounts leave here in USDC, never in micro-units, so no renderer has to
    know the scale.
    """
    ids = sorted({int(r['counterparty']) for r in rows if _names_a_user(r)})
    names = await ledger.usernames_bulk(ids) if ids else {}
    out = []
    for r in rows:
        view = row_view(r, lang, names)
        out.append({
            'id': r.get('id'),
            'kind': view['kind'],
            'label': view['label'],
            'incoming': view['incoming'],
            'ref': view['ref'],
            'note': view['note'],
            'amount_usdc': float(Decimal(int(r['amount'])) / Decimal(MICRO)),
            'tx_hash': r.get('tx_hash'),
            'created_at': r.get('created_at'),
        })
    return out
