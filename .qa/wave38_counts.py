import sys

sys.path.insert(0, r"D:\tippy_on_base\.qa\scratch38\tests")
import test_design_system as T

print("== frames / cells ==")
for label, css in (("style.css", T.CSS), ("app.html", T.APP_HTML)):
    print(f"{label}: frames={sorted(T.frame_classes(css))} cells={sorted(T.cell_classes(css))}")

print("\n== cells painted in markup ==")
tot = 0
for page in T.PAGES:
    css = T.APP_HTML if page == "app.html" else T.CSS
    rows = T.cells_painted_in_markup(T.read(page), T.frame_classes(css), T.cell_classes(css))
    if rows:
        from collections import Counter
        print(f"{page}: {len(rows)} {dict(Counter(c for c, _f, _p in rows))}")
    tot += len(rows)
print("total markup cells:", tot)

print("\n== node writes / builders per code surface ==")
total = 0
for name, text in T.CODE_SURFACES:
    css = text if name == "app.html" else T.CSS
    frames, cells = T.frame_classes(css), T.cell_classes(css)
    code = text if name in T.SCRIPTS else T.code_only(text)
    functions = T.js_functions(code)
    builders = sorted(fn for fn, b in functions.items()
                      if T.value_reaches_a_cell(b, b, fn, functions, cells)
                      and not T.paints_into_a_node(b))
    rows = []
    for fn, body in sorted(functions.items()):
        for eid, painted, value in T.node_writes(body, cells, functions, fn):
            framed = T.value_opens_a_frame(value, fn, functions, frames)
            rows.append((fn, eid, painted, framed))
    total += len(rows)
    print(f"{name}: writes={len(rows)} builders={builders}")
    for r in rows:
        print("   ", r)
print("total judged writes:", total)

print("\n== frames around ids ==")
for page in T.PAGES:
    found = {k: sorted(v) for k, v in T.frames_around_ids(T.read(page),
              T.frame_classes(T.APP_HTML if page == "app.html" else T.CSS)).items() if v}
    if found:
        print(f"{page}: {found}")
