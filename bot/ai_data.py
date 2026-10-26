"""Answering /ask with no model at all.

The assistant has always been one urllib call away from a hard 503: an empty
key, a revoked token, a free tier returning 429. But most questions people ask
a tipbot are not open-ended — they are "what's on the market right now", "what
are the odds on #7", "what can you do", and every one of those has an exact,
current, already-computed answer in the ledger.

So when no provider answers, /ask serves that instead of an error. This module
uses no network beyond Postgres: numbers come from the same ledger views the
market screens use, and the wording comes from the shared i18n table. It never
invents a fact — an answer is either derived from data or it says what it can
derive.
"""

import html
import json
import re
from decimal import Decimal

from . import i18n
from .commands_catalog import BOT_COMMAND_SPECS
from .ledger import MICRO
from .ledger import async_ledger as ledger

_MARKET_RE = re.compile(
    r"\bрын\w*|\bмаркет(?!инг)\w*|котировк|оддс|шанс|вероятн|price|odds|market|chance|probab|percent"
    r"|市场|行情|价格|概率",
    re.IGNORECASE,
)
# \b keeps "здравствуй" out of the pool intent while still matching the
# inflected forms the bot's own screens use (ставка, ставок, ставку).
_BET_RE = re.compile(
    r"\bстав(?:к|о|у|е|а)\w*|\bпул\w*|призов|пари-|mutuel|\bbets?\b|pool|payout|奖池|投注",
    re.IGNORECASE,
)
_HELP_RE = re.compile(
    r"\bкоманд|что\s+(?:ты\s+)?(?:умеешь|можешь|умеет)|возможност|помощ|подскажи как"
    r"|help|command|what can|how do i|feature|функци"
    r"|命令|帮助|功能",
    re.IGNORECASE,
)
# An explicit id wins over a list, and "ставка 12" must not resolve to market 12.
_MARKET_ID_RE = re.compile(
    r"(?:\bрын\w*|\bмаркет(?!инг)\w*|market|#)\D{0,8}(\d{1,7})",
    re.IGNORECASE,
)
_BET_ID_RE = re.compile(
    r"(?:\bстав(?:к|о|у|е|а)\w*|\bбет\w*|\bbet\b|pool|奖池|投注)\D{0,8}(\d{1,7})",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(r"/([a-z_]{3,15})", re.IGNORECASE)
_TAG_RE = re.compile(r'</?(?:b|i|code|a(?: href="[^"]*")?)>', re.IGNORECASE)


def _plain(text: str) -> str:
    """Telegram-HTML strings into plain text — see :func:`_help_card`."""
    return html.unescape(_TAG_RE.sub("", text))

_MAX_LISTED = 5
_WORD_RE = re.compile(r"[\wа-яё]{4,}")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _usd(micro: int) -> str:
    return f"{Decimal(int(micro)) / Decimal(MICRO):.2f}"


def _pct(price: float) -> str:
    return f"{price:.1f}%"


def _options_text(view: dict) -> str:
    return " · ".join(f"{o['label'][:24]} {_pct(o['price_pct'])}" for o in view["options"][:4])


def _market_text(view: dict, lang: str) -> str:
    lines = [f"#{view['id']} {view['question']}", f"   {_options_text(view)}"]
    lines.append(
        i18n.t(
            lang,
            "ai_data_market_meta",
            liquidity=_usd(view["liquidity_micro"]),
            traders=view["traders"],
        )
    )
    return "\n".join(lines)


async def _markets_answer(ids: list[int], lang: str) -> str:
    blocks = []
    for mid in ids:
        view = await ledger.amm_market_view(mid)
        if view:
            blocks.append(_market_text(view, lang))
    if not blocks:
        return i18n.t(lang, "ai_data_not_found")
    return "\n\n".join(blocks) + "\n\n" + i18n.t(lang, "ai_data_market_hint")


async def _markets_list(lang: str) -> str:
    rows = await ledger.open_markets(_MAX_LISTED)
    if not rows:
        return _plain(i18n.t(lang, "market_open"))
    views = await ledger.bulk_amm_market_views([int(r["id"]) for r in rows])
    blocks = [_market_text(views[int(r["id"])], lang) for r in rows if int(r["id"]) in views]
    if not blocks:
        return _plain(i18n.t(lang, "market_open"))
    return (
        i18n.t(lang, "ai_data_markets_head", n=len(blocks))
        + "\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + i18n.t(lang, "ai_data_market_hint")
    )


async def _bet_text(bet: dict, lang: str) -> str:
    bet_id = int(bet["id"])
    totals = await ledger.bet_totals(bet_id)
    labels = [str(o) for o in json.loads(bet["options"])]
    per_option = " · ".join(
        f"{label[:24]} {_usd(totals.get(i, 0))}" for i, label in enumerate(labels[:4])
    )
    return (
        f"#{bet_id} {bet['question']}\n"
        f"   {per_option}\n"
        + i18n.t(lang, "ai_data_bet_meta", amount=_usd(sum(totals.values())))
    )


async def _bets_list(lang: str) -> str:
    bets = await ledger.open_bets(_MAX_LISTED)
    if not bets:
        return i18n.t(lang, "ai_data_no_bets")
    blocks = [await _bet_text(b, lang) for b in bets]
    return i18n.t(lang, "ai_data_bets_head", n=len(blocks)) + "\n\n" + "\n\n".join(blocks)


def _help_card(question: str, lang: str) -> str:
    """The canonical /help text, rendered plain.

    One copy of the fact: this is the same `help_full` string the /help command
    sends, only undone — it is written for Telegram's HTML parser, so its tags
    mark emphasis and its entities mark the angle brackets around arguments.
    Stripping the tags without unescaping the entities is what makes a user see
    "&lt;банк&gt;" instead of "<банк>".
    """
    card = _plain(i18n.t(lang, "help_full"))
    named = {c.lower() for c, _d, _g in BOT_COMMAND_SPECS} & {
        m.lower() for m in _COMMAND_RE.findall(question)
    }
    if not named:
        return card
    return (
        i18n.t(lang, "ai_data_cmd_head")
        + "\n"
        + "\n".join(f"/{c}" for c in sorted(named))
        + "\n\n"
        + card
    )


async def _match_market_by_words(question: str) -> list[int]:
    """No id in the question, but it may name a market by its title."""
    words = _words(question)
    if not words:
        return []
    rows = await ledger.open_markets(20)
    hits = [int(r["id"]) for r in rows if words & _words(str(r["question"]))]
    return hits[:2]


async def answer(question: str, lang: str = i18n.DEFAULT_LANG) -> str:
    """Plain-text reply derived from ledger data. Never raises: this is the
    path that runs precisely because nothing else is available."""
    body = await _answer(question, lang)
    return body + "\n\n" + i18n.t(lang, "ai_data_footer")


async def _answer(question: str, lang: str) -> str:
    lang = i18n.norm(lang)
    text = (question or "").strip()
    if not text:
        return i18n.t(lang, "ai_data_none")
    market_ids = [int(x) for x in _MARKET_ID_RE.findall(text)]
    bet_ids = [int(x) for x in _BET_ID_RE.findall(text)]
    try:
        if bet_ids and not market_ids:
            blocks = []
            for bid in bet_ids[:2]:
                bet = await ledger.get_bet(bid)
                if bet:
                    blocks.append(await _bet_text(bet, lang))
            return "\n\n".join(blocks) if blocks else i18n.t(lang, "ai_data_not_found")
        if market_ids:
            return await _markets_answer(market_ids, lang)
        # Data questions first: "how do I buy shares on a market" is about a
        # market, and only a question with no data in it is a request for help.
        if _BET_RE.search(text) and not _MARKET_RE.search(text):
            return await _bets_list(lang)
        if _MARKET_RE.search(text):
            return await _markets_list(lang)
        if _HELP_RE.search(text) or _COMMAND_RE.search(text):
            return _help_card(text, lang)
        by_words = await _match_market_by_words(text)
        if by_words:
            return await _markets_answer(by_words, lang)
        return i18n.t(lang, "ai_data_none")
    except Exception:
        # A ledger we cannot read is the one failure this path has left, and it
        # has to be reported as text rather than as the 500 the caller would
        # otherwise turn this into.
        return i18n.t(lang, "ai_data_unavailable")
