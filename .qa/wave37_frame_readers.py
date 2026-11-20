"""Scratch prototype for the generalized frame/cell containment readers (wave 37).

Run: python -X utf8 .qa/wave37_frame_readers.py
"""

import importlib.util
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tds)

HAIRLINE_GROUND = "var(--border)"
CANVAS_GROUND = "var(--bg)"

CLASS_IN_SELECTOR = re.compile(r"\.([a-z][a-z0-9-]*)")

SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
COMMENT = re.compile(r"<!--.*?-->", re.S)
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
})
TAG = re.compile(r"<(/?)([a-zA-Z][-\w]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>")
CLASS_ATTR = re.compile(r'class="([^"]*)"')


def frame_classes(css: str) -> set[str]:
    out: set[str] = set()
    for sel, body in tds.rule_selector_and_body(css):
        decls = tds.rule_declarations(body)
        if decls.get("gap") == "1px" and decls.get("background") == HAIRLINE_GROUND:
            out.update(CLASS_IN_SELECTOR.findall(sel))
    return out


def cell_classes(css: str) -> set[str]:
    out: set[str] = set()
    for sel, body in tds.rule_selector_and_body(css):
        decls = tds.rule_declarations(body)
        if decls.get("background") != CANVAS_GROUND:
            continue
        if any(k.startswith("border") for k in decls) or "position" in decls or "padding" not in decls:
            continue
        out.update(CLASS_IN_SELECTOR.findall(sel))
    return out


def markup_outside_scripts(markup: str) -> str:
    return COMMENT.sub(" ", SCRIPT_BLOCK.sub(" ", STYLE_BLOCK.sub(" ", markup)))


def _classes(rest: str) -> set[str]:
    match = CLASS_ATTR.search(rest)
    return set(match.group(1).split()) if match else set()


def loose_cells_in_markup(markup: str, frames, cells):
    loose = []
    stack: list[tuple[set[str], str]] = []
    for match in TAG.finditer(markup_outside_scripts(markup)):
        closing, name, rest = match.group(1), match.group(2).lower(), match.group(3)
        classes = _classes(rest)
        if closing:
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][1] == name:
                    del stack[index:]
                    break
            continue
        if classes & cells:
            ancestors = {c for seen, _ in stack for c in (seen & frames)}
            if not ancestors:
                loose.append((sorted(classes & cells)[0],
                              ">".join(n for _, n in stack) or "top of the document"))
        if name in VOID_TAGS or rest.rstrip().endswith("/"):
            continue
        stack.append((classes, name))
    return loose


def opens_a_frame(body: str, frames) -> bool:
    return any(f'class="{frame}"' in body for frame in frames)


def calls(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w$.]){re.escape(name)}\s*\(")


def reaches_a_frame(name, functions, frames, seen=None):
    seen = {name} if seen is None else seen
    body = functions[name]
    if opens_a_frame(body, frames):
        return True
    return any(
        other not in seen
        and calls(other).search(body)
        and reaches_a_frame(other, functions, frames, seen | {other})
        for other in functions
    )


def writes_a_cell(body: str, cell: str) -> bool:
    return re.search(rf'class="[^"]*\b{re.escape(cell)}\b', body) is not None


APP = tds.APP_HTML
FRAMES = frame_classes(APP)
CELLS = cell_classes(APP)
print("frames:", sorted(FRAMES))
print("cells:", sorted(CELLS))
print("loose in static markup:", loose_cells_in_markup(APP, FRAMES, CELLS))

functions = tds.js_functions(APP)
rows = []
for name, body in sorted(functions.items()):
    cells = sorted(c for c in CELLS if writes_a_cell(body, c))
    if cells:
        framed = reaches_a_frame(name, functions, FRAMES)
        callers = sorted(o for o, ob in functions.items() if o != name and calls(name).search(ob))
        framed_callers = all(reaches_a_frame(o, functions, FRAMES) for o in callers) if callers else False
        rows.append((name, cells, framed, len(callers), framed_callers))
for row in rows:
    print("  ", row)

print("\n--- mutations")
loose_markup = APP.replace('<div class="list-group lang-list">', '<div class="lang-list">', 1)
print("lang frame dropped ->", loose_cells_in_markup(loose_markup, FRAMES, CELLS)[:2])
no_gap = APP.replace("gap:1px;background: var(--border)", "gap:8px;background: var(--border)")
print("gap 8px -> frames:", sorted(frame_classes(no_gap)))
cell_gains_border = APP.replace(".tx-item{display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--bg)}",
                                ".tx-item{display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--bg);border:1px solid var(--border)}")
print("cell gains a border -> cells:", sorted(CELLS - cell_classes(cell_gains_border)))
loose_js = APP.replace("return `<div class=\"wd-row\"><span>${label}</span>",
                       "return `<div class=\"tx-item loose\"><span>${label}</span>")
fns2 = tds.js_functions(loose_js)
print("infoRow rewritten ->", [n for n, b in fns2.items() if writes_a_cell(b, "tx-item")])
