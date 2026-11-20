import ast, pathlib
root = pathlib.Path(".").resolve()
for rel in ("bot/ledger/_markets.py", "bot/ledger/_paywall.py", "bot/ledger/_users.py", "bot/ledger/_views.py"):
    path = root / rel
    src = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(path))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_src = ast.get_source_segment(src, fn) or ""
        if "placeholders" not in body_src:
            continue
        guarded = any(t in body_src.split("placeholders")[0] for t in ("if not ", "len(", "return []", "return {}"))
        head = "\n".join(body_src.splitlines()[:8])
        print(f"### {rel}:{fn.lineno} {fn.name}  guard_before_placeholders={guarded}")
        print(head)
        print()
