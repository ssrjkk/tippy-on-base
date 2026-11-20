import ast, pathlib
root = pathlib.Path(".").resolve()
BLOCKING = {"sleep": "time.sleep", "requests": None}
hits = []
orphan_tasks = []
for pkg in ("bot", "web", "agent"):
    for path in sorted((root / pkg).rglob("*.py")):
        src = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(src, filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                seg = ast.get_source_segment(src, node) or ""
                f = node.func
                attr = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                val = getattr(f, "value", None)
                mod = getattr(val, "id", "") if isinstance(val, ast.Name) else ""
                if attr == "sleep" and mod == "time":
                    hits.append((path.relative_to(root).as_posix(), node.lineno, f"{fn.name}: {seg[:60]}"))
                if attr in ("get", "post", "put") and mod in ("requests",):
                    hits.append((path.relative_to(root).as_posix(), node.lineno, f"{fn.name}: {seg[:60]}"))
                if attr == "create_task":
                    parent = None
                    # detect bare statement: create_task not assigned and not awaited
                    for n2 in ast.walk(fn):
                        if isinstance(n2, ast.Expr) and n2.value is node:
                            parent = "statement"
                    if parent:
                        orphan_tasks.append((path.relative_to(root).as_posix(), node.lineno, f"{fn.name}: {seg[:80]}"))
print("blocking calls inside async def:", len(hits))
for h in hits: print("  ", ":".join(str(x) for x in h))
print("fire-and-forget create_task (no assignment/await):", len(orphan_tasks))
for h in orphan_tasks: print("  ", ":".join(str(x) for x in h))
