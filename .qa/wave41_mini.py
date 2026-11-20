"""Wave 41, stage 3b — the v2 component language on the Mini App.

The palette landed in stage 1; this squares the controls and firms the labels, the
same moves `style.css` took, so the phone surface and the site stop being two
design systems wearing one colour scheme.

Sizes stay `var(--t-*)` tokens and touch heights stay where the coarse-pointer
block already grows them (406-418): a base-rule min-height here would be
overridden anyway and would only add a second number to maintain.
"""

from pathlib import Path

P = Path("web/static/app.html")
t = P.read_text(encoding="utf-8")
n = 0


def sub(old, new):
    global t, n
    assert t.count(old) == 1, f"pattern found {t.count(old)} times, expected 1: {old[:90]!r}"
    t = t.replace(old, new, 1)
    n += 1


# .btn — square, bordered, heavier, measured in the UI face.
sub(
    ".btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;"
    "border-radius:var(--pill);font-weight:500;font-size:var(--t-lead);padding:12px 22px;"
    "border:1px solid transparent;",
    ".btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;"
    "border-radius:0;font-weight:600;font-size:var(--t-small);padding:12px 18px;"
    "border:1px solid var(--border-strong);",
)
# .chip / .chip-btn / .balance-chip — a control box, not a pill.
sub(
    ".chip{background:var(--bg);border:1px solid var(--border-strong);border-radius:var(--pill);padding:8px 16px;",
    ".chip{background:var(--bg);border:1px solid var(--border-strong);border-radius:0;padding:8px 14px;",
)
sub(
    ".chip-btn{border:1px solid var(--border-strong);border-radius:var(--pill);",
    ".chip-btn{border:1px solid var(--border-strong);border-radius:0;",
)
sub(
    ".balance-chip{border:1px solid var(--border-strong);border-radius:var(--pill);",
    ".balance-chip{border:1px solid var(--border-strong);border-radius:0;",
)
# .tag — the v2 badge: a hairline box around a bold monospaced word. The state
# colours stay on the text (`.tag-open` etc. below), never on the box, because a
# tinted frame makes the Mini App the only surface painting state on chrome.
sub(
    ".tag{display:inline-flex;align-items:center;gap:6px;flex:none;border-radius:var(--pill);"
    "padding:3px 9px;font-size:var(--t-chip);font-weight:500;",
    ".tag{display:inline-flex;align-items:center;gap:6px;flex:none;border-radius:0;"
    "border:1px solid var(--border);padding:3px 9px;font-size:var(--t-chip);font-weight:700;",
)
# .input — square like the site's field (42px here; the coarse block raises it to 44).
sub(
    ".input{width:100%;background:var(--bg);border:1px solid var(--border-strong);"
    "border-radius:var(--radius-sm);padding:12px 14px;font-size:var(--t-body);",
    ".input{width:100%;background:var(--bg);border:1px solid var(--border-strong);"
    "border-radius:0;padding:11px 14px;font-size:var(--t-small);",
)

P.write_bytes(t.encode("utf-8"))
assert "\r\n" not in t
print(f"mini app component layer squared ({n} rules)")
