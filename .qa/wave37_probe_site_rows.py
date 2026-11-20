"""Probe: does every bundle `.row` emitter write into a container the markup frames? (wave 37)

Run: python -X utf8 .qa/wave37_probe_site_rows.py
"""

import importlib.util
import re

spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tds)

CELL = "row"
FRAME = {"rows", "markets"}

TARGET_RE = re.compile(r"""(?:\$\(|getElementById\(|querySelector\(['"]#)(['"]?)([\w-]+)\1\)\s*(?:\.\s*(?:innerHTML|outerHTML|textContent|innerText|insertAdjacentHTML)\s*(?:=|\+=)|\.)""")
ID_ASSIGN = re.compile(r"""(?:\$|getElementById|querySelector)\(\s*['"]#?([\w-]+)['"]\s*\)\s*(?:\.\s*(?:innerHTML|outerHTML|textContent|innerText)\s*=|\.\s*insertAdjacentHTML)""")
APPEND = re.compile(r"""(?:\$|getElementById|querySelector)\(\s*['"]#?([\w-]+)['"]\s*\)\s*(?:\.\s*append(?:Child)?\b|\.\s*insertAdjacentHTML)""")
CLASS_TAG = re.compile(r"<[a-zA-Z][^>]*\bid=[\"']([\w-]+)[\"'][^>]*\bclass=[\"']([^\"']*)[\"'][^>]*>|<[a-zA-Z][^>]*\bclass=[\"']([^\"']*)[\"'][^>]*\bid=[\"']([\w-]+)[\"'][^>]*>", re.S)


def js_functions_of(text):
    return tds.js_functions(text)


def calls(name):
    return re.compile(rf"(?<![\w$.])(?<!function\s){re.escape(name)}\b")


sources = {name: tds.read(name) for name in tds.SCRIPTS}
sources["app.html"] = tds.APP_HTML
pages = {name: tds.read(name) for name in tds.PAGES}

# Which documents load which script.
loaded_by = {name: [] for name in sources}
for page, ptext in pages.items():
    for src in tds.page_scripts(page):
        key = src.split("/")[-1]
        if key in loaded_by:
            loaded_by[key].append(page)

print("page_scripts reader:", hasattr(tds, "page_scripts"))
print("loaded_by:", {k: v for k, v in loaded_by.items() if v})

for src_name, src_text in sources.items():
    if src_name == "app.html":
        continue
    functions = js_functions_of(src_text)
    emitters = {n: b for n, b in functions.items() if re.search(rf'class="[^"]*\b{CELL}\b', b) or re.search(rf"class='[^']*\b{CELL}\b", b)}
    docs = loaded_by[src_name] or [src_name]
    framed_ids = set()
    for doc in docs:
        text = pages.get(doc, "")
        for m in CLASS_TAG.finditer(text):
            a, b, c, d = m.groups()
            if b and a and any(f in b.split() for f in FRAME):
                framed_ids.add(a)
            if d and c and any(f in c.split() for f in FRAME):
                framed_ids.add(d)
    print(f"\n== {src_name} loaded by {docs} framed ids={sorted(framed_ids)}")
    for name, body in sorted(emitters.items()):
        ids = set(ID_ASSIGN.findall(body)) | set(APPEND.findall(body))
        reached = ids
        # one hop up: callers' targets
        callers = [o for o, ob in functions.items() if o != name and calls(name).search(ob)]
        for c in callers:
            reached |= set(ID_ASSIGN.findall(functions[c])) | set(APPEND.findall(functions[c]))
        print(f"   {name:20} targets={sorted(reached)} framed={sorted(r & framed_ids for r in [reached])} callers={callers}")
