"""Static scan: blocking primitives reached from inside a coroutine.

Two shapes are looked for:
  1. a call to a known-blocking primitive (`urllib.request.urlopen`, `requests.<verb>`,
     `time.sleep`, `psycopg.connect`) whose innermost enclosing function is `async def`;
  2. a call to a `*_sync` helper whose innermost enclosing function is `async def` and whose
     enclosing chain contains no `asyncio.to_thread` / `run_in_executor`.

A nested plain `def` inside a coroutine is the sanctioned offload shape (`_bals()` in
`web/mini.py`, handed to `asyncio.to_thread`), so a primitive inside a nested sync function
is only a violation when that nested function is never passed to a thread.
"""
import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DIRS = ("bot", "web", "agent", "deploy")
BLOCKING = {
    ("urllib.request.urlopen",),
    ("urllib.request", "urlopen"),
}
OFFLOADS = ("to_thread", "run_in_executor", "submit")


def attr_chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def blocking_name(chain):
    text = ".".join(chain)
    if text.endswith("urlopen") or text == "urlopen":
        return "urllib.request.urlopen"
    if text.startswith("requests.") or text == "requests":
        return text
    if text.endswith("time.sleep") or text == "sleep":
        return "time.sleep" if text != "sleep" else "sleep"
    if text.endswith("ConnectionPool") or text.endswith("connect"):
        return text
    return None


def scan():
    hits = []
    sync_calls = []
    for pkg in DIRS:
        for path in sorted((REPO / pkg).rglob("*.py")):
            src = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(src, filename=str(path))
            # parent map + nested-def offload table
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            threaded = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fname = attr_chain(node.func)
                    if fname[-1:] in OFFLOADS:
                        for arg in node.args[1:] if fname[-1:] == "submit" else node.args:
                            if isinstance(arg, ast.Name):
                                threaded.add(arg.id)
                            for sub in ast.walk(arg):
                                if isinstance(sub, ast.FunctionDef):
                                    threaded.add(sub.name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                chain = attr_chain(node.func)
                name = blocking_name(chain)
                # precise enclosing: walk parents
                anc = parents.get(node)
                funcs = []
                while anc is not None:
                    if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        funcs.append(anc)
                    anc = parents.get(anc)
                inside_async = any(isinstance(f, ast.AsyncFunctionDef) for f in funcs)
                offloaded = any(isinstance(f, ast.FunctionDef) and f.name in threaded for f in funcs)
                rel = path.relative_to(REPO).as_posix()
                expr = ast.get_source_segment(src, node) or ""
                if name and inside_async and not offloaded:
                    hits.append(f"{rel}:{node.lineno}: {name} -> {expr[:70]}")
                if isinstance(node.func, ast.Name) and node.func.id.endswith("_sync"):
                    if inside_async and not offloaded:
                        sync_calls.append(f"{rel}:{node.lineno}: {node.func.id}() -> {expr[:70]}")
                if isinstance(node.func, ast.Attribute) and node.func.attr.endswith("_sync"):
                    if inside_async and not offloaded:
                        sync_calls.append(f"{rel}:{node.lineno}: .{node.func.attr}() -> {expr[:70]}")
    return hits, sync_calls


def main():
    hits, sync_calls = scan()
    print(f"blocking primitives inside a coroutine without an offload: {len(hits)}")
    for h in hits:
        print("  ", h)
    print(f"`*_sync` called from a coroutine outside to_thread: {len(sync_calls)}")
    for s in sync_calls:
        print("  ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
