"""List every transcript event that touched AGENTS.md, so the pre-clobber file can be rebuilt."""
import json
import pathlib

TX = pathlib.Path(r"C:\Users\ssrjk\.qoder\projects\D--tippy-on-base\802a3ec4-7e64-4606-a9f7-a2b69f46fd9e.jsonl")
out = []
for no, raw in enumerate(TX.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
    if "AGENTS.md" not in raw and "agents.md" not in raw:
        continue
    try:
        node = json.loads(raw)
    except Exception:
        continue
    stack = [(node, ())]
    while stack:
        cur, path = stack.pop()
        if isinstance(cur, dict):
            name = cur.get("name") or cur.get("tool_name")
            inp = cur.get("input") or cur.get("arguments")
            if name in ("Edit", "Write", "MultiEdit") and isinstance(inp, dict) and "AGENTS" in str(inp.get("file_path", "")).upper():
                out.append((no, name, {k: (len(str(v)) if isinstance(v, str) else v) for k, v in inp.items() if k in ("old_string", "new_string", "content")}))
            for k, v in cur.items():
                if isinstance(v, (dict, list)):
                    stack.append((v, path + (k,)))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                if isinstance(v, (dict, list)):
                    stack.append((v, path + (i,)))

for i, (no, name, fields) in enumerate(out):
    print(f"[{i}] jsonl:{no} {name} " + " ".join(f"{k}={v}" for k, v in fields.items()))
print("всего событий по AGENTS.md:", len(out))
