cd /d/tippy_on_base
python -X utf8 - > .qa/wave40_agents_edits.txt 2>&1 <<'PY'
import json, pathlib
TX = pathlib.Path(r"C:\Users\ssrjk\.qoder\projects\D--tippy-on-base\802a3ec4-7e64-4606-a9f7-a2b69f46fd9e.jsonl")
outdir = pathlib.Path(".qa/wave40_agents_edits"); outdir.mkdir(exist_ok=True)
rows = []
for no, raw in enumerate(TX.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
    if "AGENTS.md" not in raw.upper():
        continue
    try:
        node = json.loads(raw)
    except Exception:
        continue
    msg = node.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        continue
    for i, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") != "tool_use":
            continue
        inp = part.get("input") or {}
        fp = str(inp.get("file_path", ""))
        if "AGENTS.MD" not in fp.upper():
            continue
        name = part.get("name")
        old = inp.get("old_string") or ""
        new = inp.get("new_string") or ""
        body = inp.get("content") or ""
        (outdir / f"{no:04d}_{i}_{name}_old.txt").write_text(old, encoding="utf-8")
        (outdir / f"{no:04d}_{i}_{name}_new.txt").write_text(new or body, encoding="utf-8")
        rows.append((no, node.get("timestamp"), name, len(old), len(new or body), (new or body)[:60].replace("\n", " ")))
for r in rows:
    print(f"jsonl:{r[0]} {r[1]} {r[2]} old={r[3]} new={r[4]} :: {r[5]!r}")
print("всего:", len(rows))
PY
cat .qa/wave40_agents_edits.txt