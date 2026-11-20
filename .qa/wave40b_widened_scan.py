import ast, pathlib, re

FIELDS = {"username", "to_username", "name", "full_name", "first_name",
          "question", "label", "memo", "title", "symbol",
          "addr", "to_addr", "from_addr", "active_addr"}
ESCAPERS = ("_h(", "_esc(", "esc(", "escape(", "quote_plus(")


def hops_of(tree, source):
    hops = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = ast.get_source_segment(source, node.value) or ""
            for t in targets:
                if isinstance(t, ast.Name):
                    hops.setdefault(t.id, []).append(value)
    return hops


def escaped_text(expr, hops, seen=frozenset()):
    if any(e in expr for e in ESCAPERS):
        return True
    name = expr.strip()
    if not re.fullmatch(r"[A-Za-z_]\w*", name) or name in seen:
        return False
    writes = hops.get(name)
    if not writes:
        return False
    return all(escaped_text(w, hops, seen | {name}) for w in writes)


root = pathlib.Path(".").resolve()
offenders = []
stats = {"calls": 0, "kwargs": 0}
for pkg in ("bot", "web"):
    for path in sorted((root / pkg).rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        hops = hops_of(tree, source)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "t"):
                continue
            stats["calls"] += 1
            for kw in node.keywords:
                if kw.arg not in FIELDS:
                    continue
                stats["kwargs"] += 1
                expr = ast.get_source_segment(source, kw.value) or ""
                if escaped_text(expr, hops):
                    continue
                if isinstance(kw.value, ast.Constant):
                    continue
                if re.match(r"[A-Za-z_]\w*\.[A-Z_]+\[", expr):
                    continue
                rel = path.relative_to(root).as_posix()
                offenders.append(f"{rel}:{kw.lineno}: {kw.arg}={expr[:60]}")
print(stats)
print("offenders:", len(offenders))
for o in offenders:
    print("  ", o)
