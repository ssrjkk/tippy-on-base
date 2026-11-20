import ast, pathlib
root = pathlib.Path(".").resolve()
hits = []
for pkg in ("bot", "web", "agent"):
    for path in sorted((root / pkg).rglob("*.py")):
        src = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in ("execute", "executemany", "exec"):
                continue
            if not node.args:
                continue
            a0 = node.args[0]
            shape = type(a0).__name__
            seg = ast.get_source_segment(src, a0) or ""
            if isinstance(a0, ast.JoinedStr) or (isinstance(a0, ast.BinOp) and isinstance(a0.op, (ast.Add, ast.Mod))) or (isinstance(a0, ast.Call) and seg.startswith("(") and ".format" in seg):
                hits.append((path.relative_to(root).as_posix(), node.lineno, seg[:150].replace("\n", " ")))
print("dynamic-SQL execute() sites:", len(hits))
for rel, ln, seg in hits:
    print(f"{rel}:{ln}: {seg}")
