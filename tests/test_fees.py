"""The win fee has one formula, and every place that shows it tells the truth.

Found by the audit: `resolve_bet` ceiled the fee while `/mybets` truncated it,
the AMM resolve and the web cabinet had their own copies, and the rate itself was
written as "2%" in translated help text that no change to WIN_FEE_PCT would
update. These tests keep those copies from coming back.
"""

import re
from decimal import Decimal
from pathlib import Path

from bot import config, i18n
from bot.fees import win_fee_micro, win_fee_pct_label, withdraw_fee_pct_label

ROOT = Path(__file__).resolve().parents[1]


def test_win_fee_is_ceiled_and_never_negative():
    assert win_fee_micro(100) == 2  # exact: 2.00 micro
    assert win_fee_micro(101) == 3  # 2.02 -> up, the direction the ledger pays
    assert win_fee_micro(0) == 0
    assert win_fee_micro(-201) == 0  # a losing share of the pot owes nothing


def test_win_fee_label_does_not_truncate_the_rate(monkeypatch):
    monkeypatch.setattr(config, "WIN_FEE_PCT", Decimal("0.025"))
    assert win_fee_pct_label() == "2.5"
    monkeypatch.setattr(config, "WIN_FEE_PCT", Decimal("0.1"))
    assert win_fee_pct_label() == "10"
    monkeypatch.setattr(config, "WIN_FEE_PCT", Decimal("0.02"))
    assert win_fee_pct_label() == "2"


def test_the_win_fee_has_exactly_one_formula_in_the_source_tree():
    """`config.WIN_FEE_PCT` may be read in two roles only: the micro-unit formula
    (bot/fees.py) and the rate shown to the UI (web/server.py). The value itself
    is defined once, in bot/config.py, which never spells its own prefix.
    Anything else in the tree is a fifth copy of the money math."""
    readers = set()
    for pkg in ("bot", "web", "agent", "deploy", "scripts"):
        for path in (ROOT / pkg).rglob("*.py"):
            if "config.WIN_FEE_PCT" in path.read_text(encoding="utf-8"):
                readers.add(path.relative_to(ROOT).as_posix())
    assert readers == {"bot/fees.py", "web/server.py"}, readers


def test_help_text_percentages_are_the_configured_rates():
    """Help strings in three languages and the docs spell fees out as "1% fee",
    "комиссии 1%/2%" — a rate the operator changes in .env leaves them lying.
    Any literal percentage next to a fee word has to be a configured rate."""
    rates = {
        Decimal(r).normalize()
        for r in (
            win_fee_pct_label(),
            withdraw_fee_pct_label(),
        )
    }
    stale = []
    for rel in ("bot/i18n.py", "README.md", "docs/USER_GUIDE.md", "docs/DEPLOY.md"):
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            if not re.search(r"fee|комисси|手续费", line, re.I):
                continue
            for pct in re.findall(r"(\d+(?:\.\d+)?)%", line):
                if Decimal(pct).normalize() not in rates:
                    stale.append(f"{rel}: {pct}% in {line[:80]!r}")
    assert not stale, stale


def test_help_keys_interpolate_the_win_rate():
    """The two live help keys now carry `{pct}`: a frozen literal is what made
    them disagree with the ledger after a rate change."""
    from bot.handlers._common import _default_deadline_label

    pct = win_fee_pct_label()
    window = _default_deadline_label()
    for lang in ("ru", "en", "zh"):
        fee_note = i18n.t(lang, "bet_fee_note", pct=pct)
        hint = i18n.t(lang, "betcreate_hint_v2", pct=pct, default=window)
        assert f"{pct}%" in fee_note, (lang, fee_note)
        assert f"{pct}%" in hint and f"{window}</i>" in hint, (lang, hint)
        assert hint.count("(") == hint.count(")"), (lang, hint)
        # The resolve card names the same rate; it used to say "2%" outright.
        resolve = i18n.t(lang, "bet_resolve_title", id=1, question="Q", pot="10", pct=pct)
        assert f"{pct}%" in resolve, (lang, resolve)
    assert "{pct}%" in i18n.STRINGS["bet_fee_note"]["ru"]
    assert "{pct}%" in i18n.STRINGS["bet_resolve_title"]["en"]


def test_help_card_names_the_configured_withdrawal_terms(monkeypatch):
    """`help_full` carried "1%, min 1 USDC" as text in three languages, so an
    operator changing either value kept sending users the old terms."""
    monkeypatch.setattr(config, "WITHDRAW_FEE_PCT", Decimal("0.025"))
    monkeypatch.setattr(config, "MIN_WITHDRAW_USDC", Decimal("3"))
    expectations = {
        "ru": ("2.5%", "мин. 3 USDC"),
        "en": ("2.5%", "min 3 USDC"),
        "zh": ("2.5%", "最低 3 USDC"),
    }
    for lang, needles in expectations.items():
        text = i18n.help_text(lang)
        for needle in needles:
            assert needle in text, (lang, needle)
        assert "1%" not in text, (lang, text)
        assert "{wd_fee}" not in text and "{min_wd}" not in text, (lang, text)
MONEY_SPEC = re.compile(r"\{[a-zA-Z_]\w*:[^{}]*[fdge][^{}]*\}")


def test_the_dictionary_never_formats_money_itself():
    """Precision belongs to `_fmt()`. A `{amount:.2f}` inside a translated string
    creates a second money format and explodes with ValueError when a handler hands
    it the text `_fmt()` already produced — which is exactly how `/subscribe` and
    `/claim` looked broken after the money had moved."""
    offenders = [
        f"{key}/{lang}"
        for key, table in i18n.STRINGS.items()
        for lang, text in table.items()
        if MONEY_SPEC.search(text)
    ]
    assert not offenders, offenders


def test_usdc_is_never_printed_with_a_dollar_sign():
    """The unit is USDC. Nine cards wrote "$5.00" while the rest of the bot wrote
    "5 USDC", so the same money looked like two currencies in one chat. A ticker
    like `($MTK)` is not an amount and stays."""
    offenders = []
    for key, table in i18n.STRINGS.items():
        for lang, text in table.items():
            for match in re.finditer(r"\$(?!\{symbol\})", text):
                start = max(0, match.start() - 24)
                offenders.append(f"{key}/{lang}: ...{text[start:match.start() + 24]}...")
    assert not offenders, offenders


def test_the_onchain_outcome_limits_are_the_contract_ones():
    """`/oc_create` advertises an option band and refuses outside it; the chain
    enforces the same numbers in `createMarket`. Two sources, one truth: the
    Python constants are read out of the Solidity here."""
    import bot.handlers.onchain as oc

    source = (Path(__file__).resolve().parents[1] / "contracts" / "OutcomeMarket.sol").read_text(
        encoding="utf-8"
    )
    declared = int(re.search(r"MAX_OUTCOMES = (\d+);", source).group(1))
    accepted = int(re.search(r"numOutcomes < (\d+)", source).group(1))
    bot_band = (oc.MIN_ONCHAIN_OUTCOMES, oc.MAX_ONCHAIN_OUTCOMES)
    contract_band = (accepted, declared)
    assert bot_band == contract_band, (
        f"contract allows {contract_band[0]}..{contract_band[1]}, the bot claims {bot_band[0]}..{bot_band[1]}"
    )
