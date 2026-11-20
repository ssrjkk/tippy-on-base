import ast, importlib.metadata as md, pathlib, re, sys, collections

root = pathlib.Path(".").resolve()

def _norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()

def _extra_markers(req_str):
    return "extra ==" in req_str or "extra==" in req_str

def provides(dist_name):
    names = {_norm(dist_name)}
    try:
        d = md.distribution(dist_name)
    except Exception:
        return names
    tl = None
    for fn in ("top_level.txt",):
        try:
            tl = d.read_text(fn)
        except Exception:
            tl = None
    if tl:
        names.update(_norm(l) for l in tl.split() if l.strip())
    return names

def closure(req_lines):
    """BFS over non-extra requires of the declared pins; returns import-name set."""
    out, missing, seen = set(), set(), set()
    queue = []
    for line in req_lines:
        m = re.match(r"^([A-Za-z0-9._-]+)", line)
        if m:
            queue.append(m.group(1))
    while queue:
        name = queue.pop()
        n = _norm(name)
        if n in seen:
            continue
        seen.add(n)
        try:
            d = md.distribution(name)
        except Exception:
            missing.add(n)
            continue
        out |= provides(name)
        for r in (d.requires or []):
            if _extra_markers(r) or "-req" in r.split("#")[0].split(" ")[0]:
                continue
            m = re.match(r"^([A-Za-z0-9._-]+)", r.strip())
            if m:
                queue.append(m.group(1))
    return out, seen, missing

req_lines = [l.strip() for l in (root / "requirements.txt").read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
imported, seen, missing = closure(req_lines)
print(f"declared={len(req_lines)} closure_dist_count={len(seen)} import_names={len(imported)} uninstalled_in_closure={sorted(missing)}")

std = set(sys.stdlib_module_names)
local = {"bot", "web", "agent", "deploy", "scripts", "alembic", "tests", "functions", "contracts", "conftest"}
mods = collections.defaultdict(set)
for d in ("bot", "web", "agent", "deploy", "alembic"):
    for path in sorted((root / d).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods[_norm(a.name.split(".")[0])].add(path.relative_to(root).as_posix())
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    mods[_norm(node.module.split(".")[0])].add(path.relative_to(root).as_posix())
gap = [(m, sorted(mods[m])) for m in sorted(mods) if m not in std and m not in local and m not in imported]
print("\nRUNTIME IMPORTS OUTSIDE THE IMAGE CLOSURE:")
for m, where in gap:
    print(f"  {m}: {where}")
