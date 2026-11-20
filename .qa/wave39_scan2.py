"""Find every string in the transcript that carries the AGENTS.md body, with its length."""
import json
import pathlib

TX = pathlib.Path(r"C:\Users\ssrjk\.qoder\projects\D--tippy-on-base\802a3ec4-7e64-4606-a9f7-a2b69f46fd9e.jsonl")


def walk(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str):
            yield cur


hits = []
for no, raw in enumerate(TX.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
    if "Agent Guidelines" not in raw:
        continue
    try:
        node = json.loads(raw)
    except Exception as exc:
        print(no, "не распарсилось:", exc)
        continue
    for s in walk(node):
        if "Agent Guidelines" in s:
            hits.append((no, len(s), s.count("tracked files carry a UTF-8 BOM"), s[:60].replace("\n", " ")))
for h in sorted(hits, key=lambda x: -x[1])[:14]:
    print(f"строка {h[0]:>5} длина {h[1]:>7} BOM-упоминаний {h[2]}  начало: {h[3]!r}")
print("всего:", len(hits))
