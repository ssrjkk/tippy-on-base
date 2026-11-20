import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("test_dependencies", pathlib.Path("tests/test_dependencies.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
used = m.third_party_runtime_roots()
avail, walked, unresolved = m.image_closure(m.REPO / "requirements.txt")
print("declared pins:", len(m.pins_of(m.REPO / "requirements.txt")))
print("closure dists:", len(walked), "import names:", len(avail), "unresolved:", sorted(unresolved))
print("third-party roots:", len(used))
for k in sorted(used):
    print(("  MISSING " if k not in avail else "  ok      ") + k, used[k][:2])
