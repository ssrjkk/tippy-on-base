import ast, pathlib, re
root = pathlib.Path(".").resolve()
rows = []
for pkg in ("bot", "web"):
    for path in sorted((root / pkg).rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "t"):
                continue
            for kw in node.keywords:
                if kw.arg != "addr":
                    continue
                expr = ast.get_source_segment(source, kw.value) or ""
                key = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "?"
                ok = any(e in expr for e in ("_h(", "_esc(", "esc(", "escape(", "quote_plus("))
                rows.append((ok, path.relative_to(root).as_posix(), kw.lineno, key, expr[:60]))
print("total addr= sites:", len(rows))
for ok, rel, ln, key, expr in rows:
    print(("OK   " if ok else "RAW  "), f"{rel}:{ln} t('{key}', addr={expr})")
