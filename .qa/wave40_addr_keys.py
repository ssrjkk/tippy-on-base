import ast, pathlib, re
root = pathlib.Path(".").resolve()
KEYS = {"addr", "to_addr", "from_addr", "active_addr", "address", "wallet"}
ESCAPERS = ("_h(", "_esc(", "esc(", "escape(", "quote_plus(")
rows = []
for pkg in ("bot", "web"):
    for path in sorted((root / pkg).rglob("*.py")):
        src = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "t"):
                continue
            for kw in node.keywords:
                if kw.arg not in KEYS:
                    continue
                expr = ast.get_source_segment(src, kw.value) or ""
                ok = any(e in expr for e in ESCAPERS) or isinstance(kw.value, ast.Constant)
                rows.append((kw.arg, ok, path.relative_to(root).as_posix(), kw.lineno, expr[:55]))
print("address-ish kwarg sites:", len(rows))
for k in sorted({r[0] for r in rows}):
    raw = [r for r in rows if r[0] == k and not r[1]]
    print(f"  {k}: total={sum(1 for r in rows if r[0]==k)} raw={len(raw)}")
    for r in raw: print("      RAW", f"{r[2]}:{r[3]} {r[4]}")
