"""Where does the AGENTS.md text live in the transcript, and in how many pieces?"""
import json
import pathlib

TX = pathlib.Path(r"C:\Users\ssrjk\.qoder\projects\D--tippy-on-base\802a3ec4-7e64-4606-a9f7-a2b69f46fd9e.jsonl")
NEEDLE = "tracked files carry a UTF-8 BOM"
lines = TX.read_text(encoding="utf-8", errors="replace").splitlines()
for no, raw in enumerate(lines, 1):
    if NEEDLE not in raw:
        continue
    try:
        node = json.loads(raw)
    except Exception as exc:
        print(no, "raw-only, парсер:", type(exc).__name__, len(raw))
        continue
    found = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, str) and NEEDLE in o:
            found.append((path, len(o)))

    walk(node, "$")
    print(no, found[:6], f"type={node.get('type')}")
