import importlib.util, json
spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec); spec.loader.exec_module(tds)
app = tds.APP_HTML
frames, cells = tds.frame_classes(app), tds.cell_classes(app)
print("app frames:", frames)
print("app cells:", cells)
site = tds.CSS
print("site frames:", tds.frame_classes(site))
print("site cells:", tds.cell_classes(site))
fns = tds.js_functions(app)
out = []
for name, body in sorted(fns.items()):
    painted = tds.writes_a_cell(body, cells)
    if not painted:
        continue
    framed = tds.reaches_a_frame(name, fns, frames)
    callers = sorted(o for o, b in fns.items() if o != name and tds.calls(name).search(b))
    out.append({"fn": name, "cells": painted, "own": tds.opens_a_frame(body, frames),
                "framed": framed, "callers": callers})
print(json.dumps(out, indent=1))
# where does each cell ground come from, per theme
for name, body in sorted(fns.items()):
    if tds.opens_a_frame(body, frames):
        print("FRAMES in", name)
