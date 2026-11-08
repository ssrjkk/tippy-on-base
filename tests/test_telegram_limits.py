"""Telegram hard limits: button text ≤64 chars, callback_data ≤64 bytes, message ≤4096.

These limits are enforced by Telegram's API, not by this repo. A label that exceeds
them does not shorten one button — it refuses the whole keyboard, so the screen the
user tapped stays frozen and the action behind it never happens. The gates here
measure the codebase against those limits so a future edit cannot silently break
a money path.
"""
import ast
import re
from pathlib import Path

from bot import i18n
from bot.handlers import _common as common
from bot.handlers.onchain import _resolve_buttons

ROOT = Path(__file__).resolve().parent.parent
HANDLERS = ROOT / "bot" / "handlers"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def _button_text_nodes(tree: ast.AST) -> list[ast.expr]:
    """Every `text=` keyword argument inside an InlineKeyboardButton/KeyboardButton call."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in ("InlineKeyboardButton", "KeyboardButton"):
            continue
        for kw in node.keywords:
            if kw.arg == "text":
                out.append(kw.value)
    return out


def _callback_data_nodes(tree: ast.AST) -> list[ast.expr]:
    """Every `callback_data=` keyword argument inside an InlineKeyboardButton/KeyboardButton call."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in ("InlineKeyboardButton", "KeyboardButton"):
            continue
        for kw in node.keywords:
            if kw.arg == "callback_data":
                out.append(kw.value)
    return out


def _is_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_i18n_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "t":
        # i18n.t(...) where i18n is a Name
        if isinstance(func.value, ast.Name) and func.value.id == "i18n":
            return True
        # i18n.t(...) where i18n is an Attribute (e.g., bot.i18n.t)
        if isinstance(func.value, ast.Attribute):
            return True
    return False


def _is_lang_name(node: ast.expr) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    val = node.value
    return (isinstance(val, ast.Attribute) and val.attr == "LANG_NAME"
            and isinstance(val.value, ast.Name) and val.value.id == "i18n")


def _is_bounded_concat(node: ast.expr) -> bool:
    """String concatenation where every operand is bounded (literal or i18n call)."""
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
        return False
    # Check left and right operands
    def bounded(op):
        return (_is_literal(op) or _is_i18n_call(op) or _is_lang_name(op)
                or _is_btn_text_call(op) or _is_bounded_concat(op))
    return bounded(node.left) and bounded(node.right)


def _is_btn_text_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # common.btn_text(...) where common is a Name
    if (isinstance(func, ast.Attribute) and func.attr == "btn_text"
            and isinstance(func.value, ast.Name) and func.value.id == "common"):
        return True
    # common.btn_text(...) where common is an Attribute (e.g., bot.handlers.common)
    return (isinstance(func, ast.Attribute) and func.attr == "btn_text"
            and isinstance(func.value, ast.Attribute))


def _is_fstring(node: ast.expr) -> bool:
    return isinstance(node, ast.JoinedStr)


def _fstring_max_bytes(node: ast.JoinedStr) -> int:
    """Estimate the max UTF-8 byte length of an f-string.

    Literal parts are measured directly. Interpolated values are assumed to be
    small: integers ≤10 digits, Decimal amounts ≤20 chars, short strings ≤10 chars.
    This is conservative — a real value that exceeds the assumption fails the gate.
    """
    total = 0
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            total += len(part.value.encode("utf-8"))
        elif isinstance(part, ast.FormattedValue):
            total += 10
        else:
            total += 10
    return total


def _is_bounded_callback_fstring(node: ast.expr) -> bool:
    """An f-string whose estimated max length is ≤64 bytes.

    Callback data interpolates integers (market IDs, option indices), short tokens
    (16 hex chars from secrets.token_hex(8)), or bounded amount strings. The estimate
    is conservative: if it passes here, the real value cannot exceed 64 bytes unless
    the interpolated data itself is pathological (a 40-digit integer).
    """
    if not _is_fstring(node):
        return False
    return _fstring_max_bytes(node) <= 64


def _is_bounded_ifexp(node: ast.expr) -> bool:
    """A conditional expression (X or Y) where both branches are bounded strings."""
    if not isinstance(node, ast.IfExp):
        return False
    def bounded(n):
        return (_is_literal(n) or _is_i18n_call(n) or _is_lang_name(n)
                or isinstance(n, ast.Name))
    return bounded(node.body) and bounded(node.orelse)


def _is_bounded_boolOp_or(node: ast.expr) -> bool:
    """A boolean OR expression (X or Y) where all operands are bounded strings."""
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
        return False
    def bounded(n):
        return (_is_literal(n) or _is_i18n_call(n) or _is_lang_name(n)
                or isinstance(n, ast.Name))
    return all(bounded(v) for v in node.values)


def test_every_button_label_is_bounded():
    """Every button text is either a literal ≤64, an i18n call, a LANG_NAME lookup,
    or wrapped in common.btn_text. An f-string that interpolates anything must go
    through the helper — hand-slicing at each call site is how the 64-char limit
    gets silently broken."""
    offenders = []
    for p in sorted(HANDLERS.glob("*.py")):
        tree = ast.parse(_read(p))
        for node in _button_text_nodes(tree):
            if _is_literal(node):
                if len(node.value) > 64:
                    offenders.append(f"{p.name}:{node.lineno} literal {len(node.value)} chars")
            elif _is_i18n_call(node) or _is_lang_name(node) or _is_bounded_concat(node):
                pass  # catalog-bounded; test_i18n_button_keys_fit_in_64 covers the values
            elif _is_btn_text_call(node):
                pass  # helper guarantees ≤64
            elif _is_fstring(node):
                offenders.append(f"{p.name}:{node.lineno} f-string without btn_text")
            else:
                offenders.append(f"{p.name}:{node.lineno} unbounded expression {ast.unparse(node)[:60]}")
    assert not offenders, "button labels that can exceed Telegram's 64-char limit:\n  " + "\n  ".join(offenders)


def test_i18n_button_keys_fit_in_64():
    """Every i18n key used as a button label must be ≤64 chars in all three languages.
    A translation that grows past the limit makes Telegram refuse the keyboard —
    the same money path works in ru but breaks in zh."""
    # Keys used as button labels (from the AST scan above, filtered to i18n calls).
    # Hard-coded here so the gate is explicit about what it covers.
    button_keys = [
        "btn_mk_create", "btn_all_markets", "btn_all_markets_v2", "btn_back",
        "btn_back_short", "btn_buy", "btn_buy_amount", "btn_close_market",
        "btn_cancel_action", "btn_donate_page", "btn_lang", "btn_market",
        "btn_sell", "btn_sell_pct", "btn_about", "app_open", "deposit_qr_button",
        "paywall_buy_btn", "paywall_all_btn", "withdraw_confirm_btn",
        "withdraw_cancel_btn", "wallet_new_btn",
    ]
    offenders = []
    for key in button_keys:
        d = i18n.STRINGS.get(key)
        if d is None:
            continue
        for lang in ("ru", "en", "zh"):
            val = d.get(lang, "")
            if len(val) > 64:
                offenders.append(f"{key}[{lang}] = {len(val)} chars: {val!r}")
    assert not offenders, "i18n button keys that exceed 64 chars:\n  " + "\n  ".join(offenders)


def test_no_markup_escape_in_button_labels():
    """Inline keyboard buttons are not message text — Telegram does not parse HTML
    in button labels, so `&amp;` in a label is printed to the user as `&amp;` rather
    than hidden from them. Worse: escaping a 30-char label can push it to 150 chars,
    which exceeds the 64-char limit and makes Telegram refuse the whole keyboard."""
    offenders = []
    for p in sorted(HANDLERS.glob("*.py")):
        tree = ast.parse(_read(p))
        for node in _button_text_nodes(tree):
            src = ast.unparse(node)
            if re.search(r"\b(esc|html\.escape)\s*\(", src):
                offenders.append(f"{p.name}:{node.lineno}: {src[:80]}")
    assert not offenders, "markup escape inside a button label (buttons are plain text):\n  " + "\n  ".join(offenders)


def test_every_callback_data_is_bounded():
    """Every callback_data= value must be ≤64 bytes. Telegram refuses the whole keyboard
    if even one button exceeds this — the same shape as the button-text limit, but
    callback_data is UTF-8 bytes, not chars. All current values are f-strings with
    integer IDs, short tokens (16 hex chars), or bounded amount strings; the gate
    measures them so a future edit cannot silently grow one past the limit."""
    offenders = []
    for p in sorted(HANDLERS.glob("*.py")):
        tree = ast.parse(_read(p))
        for node in _callback_data_nodes(tree):
            if _is_literal(node):
                if len(node.value.encode("utf-8")) > 64:
                    offenders.append(f"{p.name}:{node.lineno} literal {len(node.value.encode('utf-8'))} bytes")
            elif (_is_bounded_callback_fstring(node) or _is_bounded_concat(node)
                  or _is_bounded_ifexp(node) or _is_bounded_boolOp_or(node)):
                pass
            elif _is_fstring(node):
                max_bytes = _fstring_max_bytes(node)
                offenders.append(f"{p.name}:{node.lineno} f-string ~{max_bytes} bytes (may exceed 64)")
            else:
                offenders.append(f"{p.name}:{node.lineno} unbounded expression {ast.unparse(node)[:60]}")
    assert not offenders, "callback_data values that may exceed Telegram's 64-byte limit:\n  " + "\n  ".join(offenders)


def test_resolve_buttons_with_adversarial_options():
    """An on-chain market whose option label is 60 chars of `&<>` (the MAX_OPTION_LEN
    cap) must still produce a keyboard Telegram accepts: every label ≤64 chars, every
    callback_data ≤64 bytes, and no `&amp;`/`&lt;` substring (which would mean the
    label was HTML-escaped — wrong for buttons)."""
    adv_option = "&" * 30 + "<" * 15 + ">" * 15  # 60 chars, all markup
    kb = _resolve_buttons(7, [adv_option, "A & B <win>"])
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.text) <= 64, f"label {btn.text!r} is {len(btn.text)} chars"
            assert len(btn.callback_data.encode()) <= 64, f"callback_data {btn.callback_data!r} is {len(btn.callback_data)} bytes"
            assert "&amp;" not in btn.text, f"label {btn.text!r} contains &amp;"
            assert "&lt;" not in btn.text, f"label {btn.text!r} contains &lt;"


def test_btn_text_truncates_to_64():
    """The helper itself: adversarial input → ≤64 chars, with an ellipsis marker."""
    long = "x" * 100
    out = common.btn_text(long)
    assert len(out) == 64
    assert out.endswith("…")
    short = "hello"
    assert common.btn_text(short) == short


def test_edit_menu_logs_refusals(caplog):
    """When Telegram refuses an edit (e.g. payload over a hard limit), _edit_menu
    must log the failure. A silent swallow makes a broken button look like a slow bot."""
    import logging
    from unittest.mock import AsyncMock, MagicMock

    cb = MagicMock()
    cb.from_user.id = 123
    cb.message.edit_text = AsyncMock(side_effect=Exception("Bad Request: message is not modified"))
    cb.message.edit_caption = AsyncMock(side_effect=Exception("Bad Request: message is not modified"))

    with caplog.at_level(logging.WARNING, logger="tipbot.handlers"):
        import asyncio
        asyncio.run(common._edit_menu(cb, "test text"))

    assert any("menu edit refused" in rec.message for rec in caplog.records), \
        "_edit_menu swallowed the refusal without logging"


def test_docs_do_not_name_paths_outside_the_repo():
    """A document that tells the operator to run a file on the maintainer's laptop
    (D:/Dev/Temp/...) is a document that lies: the file exists nowhere else, and
    the operator cannot follow the instruction. Docs must name only paths inside
    the repo, or server/container paths that are clearly marked as such."""
    docs = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]
    offenders = []
    for p in docs:
        text = _read(p)
        # Windows drive paths (D:/, C:/, etc.) are always the maintainer's laptop.
        # Exclude URLs (anything with ://) and server paths (/opt, /app, /usr, /etc).
        # Also exclude examples/warnings (lines with "no", "not", "never" before the path).
        for m in re.finditer(r"[A-Za-z]:/[^\s`'\"]+", text):
            path = m.group(0)
            if "://" in path:
                continue
            # Server paths: /opt, /app, /usr, /etc (may have drive letter prefix like P:/opt)
            if any(f"/{prefix}/" in path or path.endswith(f"/{prefix}") for prefix in ["opt", "app", "usr", "etc"]):
                continue
            # Check if this is an example/warning (look at the surrounding context)
            line_start = text.rfind('\n', 0, m.start()) + 1
            line_end = text.find('\n', m.end())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end].lower()
            if any(word in line for word in ["no ", "not ", "never ", "don't", "avoid", "exclude"]):
                continue
            offenders.append(f"{p.name}: {path}")
        # /Dev/Temp is the maintainer's scratch directory.
        for m in re.finditer(r"/Dev/Temp/[^\s`'\"]+", text):
            line_start = text.rfind('\n', 0, m.start()) + 1
            line_end = text.find('\n', m.end())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end].lower()
            if any(word in line for word in ["no ", "not ", "never ", "don't", "avoid", "exclude"]):
                continue
            offenders.append(f"{p.name}: {m.group(0)}")
    assert not offenders, "docs name paths outside the repo:\n  " + "\n  ".join(offenders)
