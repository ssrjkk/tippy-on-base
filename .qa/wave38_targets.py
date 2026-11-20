import importlib.util
spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec); spec.loader.exec_module(tds)
for name in tds.SCRIPTS + tds.PAGES:
    text = tds.read(name)
    css = text if name == "app.html" else tds.CSS
    frames, cells = tds.frame_classes(css), tds.cell_classes(css)
    code = text if name in tds.SCRIPTS else tds.code_only(text)
    functions = tds.js_functions(code)
    rows = []
    for fn, body in sorted(functions.items()):
        painted = tds.writes_a_cell(body, cells)
        if not painted or not tds.paints_into_a_node(body):
            continue
        targets = tds.write_targets(body)
        docs = [name] if name in tds.PAGES else [
            p for p in tds.PAGES if name in {s.rsplit("/", 1)[-1] for s in tds.page_scripts(p)}]
        around = {d: tds.frames_around_ids(tds.read(d), frames) for d in docs}
        rows.append((fn, painted, targets,
                     {t: sorted(f for d in docs for f in around[d].get(t, ["-"])) for t in targets},
                     tds.opens_a_frame(body, frames)))
    if rows:
        print(f"\n{name}  frames={sorted(frames)}")
        for row in rows:
            print("   ", row)
