"""Measure the frame/cell inventories of both stylesheets and every shipped surface (wave 37).

Run: python -X utf8 .qa/wave37_measure_frames.py
"""

import importlib.util
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tds)

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


def rules(text: str) -> list[tuple[str, dict[str, str]]]:
    return [(sel, tds.rule_declarations(body)) for sel, body in tds.rule_selector_and_body(text)]


def frame_classes(css: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for sel, decls in rules(css):
        if decls.get("gap") == "1px" and decls.get("background", "").startswith("var(--border"):
            for cls in CLASS_IN_SELECTOR.findall(sel):
                out[cls] = decls["background"]
    return out


def cell_classes(css: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for sel, decls in rules(css):
        bg = decls.get("background", "")
        if not bg.startswith("var(--") or bg.startswith("var(--border"):
            continue
        if any(k.startswith("border") for k in decls) or "position" in decls or "padding" not in decls:
            continue
        if not sel.strip().startswith("."):
            continue
        for cls in CLASS_IN_SELECTOR.findall(sel):
            out.setdefault(cls, bg)
    return out


def markup_outside_scripts(markup: str) -> str:
    return COMMENT.sub(" ", SCRIPT_BLOCK.sub(" ", STYLE_BLOCK.sub(" ", markup)))


def loose_cells_in_markup(markup: str, frames, cells):
    loose = []
    stack: list[tuple[set[str], str]] = []
    for match in TAG.finditer(markup_outside_scripts(markup)):
        closing, name, rest = match.group(1), match.group(2).lower(), match.group(3)
        classes = set(CLASS_ATTR.search(rest).group(1).split()) if CLASS_ATTR.search(rest) else set()
        if closing:
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][1] == name:
                    del stack[index:]
                    break
            continue
        if classes & cells and not {c for seen, _ in stack for c in (seen & frames)}:
            loose.append((sorted(classes & cells)[0], ">".join(n for _, n in stack) or "top"))
        if name in VOID_TAGS or rest.rstrip().endswith("/"):
            continue
        stack.append((classes, name))
    return loose


APP = tds.APP_HTML
APP_STYLE = STYLE_BLOCK.search(APP).group(0)
SITE_CSS = tds.read("style.css")

for label, css in (("app.html <style>", APP_STYLE), ("style.css", SITE_CSS)):
    frames, cells = frame_classes(css), cell_classes(css)
    print(f"== {label}: frames={sorted(frames)} cells={sorted(cells)}")
    print("   cell grounds:", sorted(set(cells.values())))
    print("   frame grounds:", sorted(set(frames.values())))

print("\n== static markup, loose cells")
frames_all = set(frame_classes(APP_STYLE)) | set(frame_classes(SITE_CSS))
for page in tds.PAGES:
    text = tds.read(page)
    cells = set(cell_classes(STYLE_BLOCK.search(text).group(0))) if page == "app.html" else set(cell_classes(SITE_CSS))
    found = loose_cells_in_markup(text, frames_all, cells)
    print(f"   {page:16} cells={len(cells)} loose={found}")

print("\n== js emitters")


def calls(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w$.])(?<!function\s){re.escape(name)}\b")


def reaches(name, functions, frames, seen=None):
    seen = {name} if seen is None else seen
    body = functions[name]
    if any(f'class="{frame}"' in body for frame in frames):
        return True
    return any(other not in seen and calls(other).search(body) and reaches(other, functions, frames, seen | {other})
               for other in functions)


sources = [("app.html", APP)] + [(s, tds.read(s)) for s in tds.SCRIPTS]
sources += [(p, tds.read(p)) for p in tds.PAGES if p != "app.html"]
for name, text in sources:
    cells = (set(cell_classes(STYLE_BLOCK.search(text).group(0))) if name == "app.html"
             else set(cell_classes(SITE_CSS)))
    if not cells:
        continue
    functions = tds.js_functions(text)
    for fname, body in sorted(functions.items()):
        written = sorted(c for c in cells if re.search(rf'class="[^"]*\b{re.escape(c)}\b', body))
        if not written:
            continue
        callers = sorted(o for o, ob in functions.items() if o != fname and calls(fname).search(ob))
        framed = reaches(fname, functions, frames_all)
        callers_framed = all(reaches(o, functions, frames_all) for o in callers) if callers else False
        print(f"   {name:14} {fname:22} {written} framed={framed} callers={callers} framed_via_callers={callers_framed}")
