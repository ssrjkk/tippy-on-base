import ast, pathlib, sys, collections
root = pathlib.Path(".").resolve()
std = set(sys.stdlib_module_names)
local = {"bot", "web", "agent", "deploy", "scripts", "alembic", "tests", "functions", "contracts"}
runtime_dirs = ["bot", "web", "agent", "deploy", "alembic"]
mods = collections.defaultdict(set)
for d in runtime_dirs:
    for path in sorted((root / d).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as e:
            print("PARSE FAIL", path, e); continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods[a.name.split(".")[0]].add(path.relative_to(root).as_posix())
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    mods[node.module.split(".")[0]].add(path.relative_to(root).as_posix())
# installed distributions in this venv
import importlib.metadata as md
def provided_by(name):
    try:
        return md.distribution(name)
    except Exception:
        return None
req = [l.split("[")[0].split("==")[0].strip() for l in (root/"requirements.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
req_norm = {r.lower().replace("-", "_") for r in req}
print("requirements.txt:", req)
print()
unknown = []
for m in sorted(mods):
    if m in std or m in local or m == "__future__":
        continue
    in_req = m.lower().replace("-", "_") in req_norm
    if not in_req:
        unknown.append((m, sorted(mods[m])[:4]))
print("runtime imports NOT declared in requirements.txt:")
for m, where in unknown:
    dist = provided_by(m)
    print(f"  {m:25s} installed_here={bool(dist)} used_by={where}")
