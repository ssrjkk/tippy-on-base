import importlib.util, re
spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec); spec.loader.exec_module(tds)

def report(label, css, docs):
    frames, cells = tds.frame_classes(css), tds.cell_classes(css)
    print(f"\n== {label}: frames {sorted(frames)} cells {sorted(cells)}")
    painted = []
    for doc in docs:
        found = tds.cells_painted_in_markup(tds.read(doc), frames, cells)
        painted += [(doc, cls, framed) for cls, framed, _p in found]
        loose = [(cls, path) for cls, framed, path in found if not framed]
        if loose:
            print(f"   LOOSE markup in {doc}: {loose}")
    print(f"   cells painted in markup: {len(painted)}")
    if "app.html" in docs:
        functions = tds.js_functions(tds.APP_HTML)
        emitters = {n: b for n, b in functions.items() if tds.writes_a_cell(b, cells)}
        print("   emitters:", sorted(emitters))
        for name, body in sorted(emitters.items()):
            print(f"     {name:<16} own-frame={tds.opens_a_frame(body, frames)!s:<5} "
                  f"reaches={tds.reaches_a_frame(name, functions, frames)!s:<5} "
                  f"into-node={tds.paints_into_a_node(body)} writes={tds.write_targets(body)} "
                  f"cells={tds.writes_a_cell(body, cells)}")

report("style.css", tds.CSS, [p for p in tds.PAGES if p != "app.html"])
report("app.html", tds.APP_HTML, ["app.html"])

print("\n== code surfaces painting cells")
for name in tds.SCRIPTS + tds.PAGES:
    text = tds.read(name)
    css = text if name == "app.html" else tds.CSS
    frames, cells = tds.frame_classes(css), tds.cell_classes(css)
    code = text if name in tds.SCRIPTS else tds.code_only(text)
    painted = sorted({c for b in tds.js_functions(code).values() for c in tds.writes_a_cell(b, cells)}
                     | set(tds.writes_a_cell(code, cells)))
    if not painted:
        continue
    documents = ([name] if name in tds.PAGES else
                 [p for p in tds.PAGES if name in {s.rsplit('/', 1)[-1] for s in tds.page_scripts(p)}])
    opened = sorted({f for d in documents for f in tds.frames_painted_in_markup(tds.read(d), frames)})
    targets = sorted({t for b in tds.js_functions(code).values() if tds.writes_a_cell(b, cells)
                      for t in tds.write_targets(b)})
    print(f"   {name:<12} cells={painted} docs={documents} frames_in_docs={opened} targets={targets}")
