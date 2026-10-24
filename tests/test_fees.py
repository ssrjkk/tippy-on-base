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
from bot.fees import win_fee_micro, win_fee_pct_label

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
            f"{(config.WITHDRAW_FEE_PCT * 100).normalize():f}",
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
    assert "{pct}%" in i18n.STRINGS["bet_fee_note"]["ru"]
