"""Reconstruct AGENTS.md: take the SessionStart snapshot from the transcript, replay every
Edit tool_use that targeted the file in order, and report what each step changed."""
import json
import pathlib
import sys

TX = pathlib.Path(r"C:\Users\ssrjk\.qoder\projects\D--tippy-on-base\802a3ec4-7e64-4606-a9f7-a2b69f46fd9e.jsonl")

snapshot = None
edits = []
for raw in TX.read_text(encoding="utf-8", errors="replace").splitlines():
    if not raw.strip():
        continue
    try:
        node = json.loads(raw)
    except Exception:
        continue
    blob = json.dumps(node, ensure_ascii=False)
    # the SessionStart context block carries the file from '# Agent Guidelines' to its end
    if snapshot is None and "# Agent Guidelines" in blob and "tracked files carry a UTF-8 BOM" in blob:
        for chunk in [node]:
            pass
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str):
            if "# Agent Guidelines" in cur and "tracked files carry a UTF-8 BOM" in cur and len(cur) > 40000:
                if snapshot is None or len(cur) > len(snapshot):
                    snapshot = cur
            if '"file_path"' in cur and "AGENTS.md" in cur:
                edits.append(cur)

if snapshot is None:
    print("СНИМОК НЕ НАЙДЕН")
    sys.exit(1)

start = snapshot.index("# Agent Guidelines")
text = snapshot[start:]
# the reminder block may continue past the file; cut at the last known bullet tail
print("длина снятка после '# Agent Guidelines':", len(text))
pathlib.Path(".qa/wave39_agents_snapshot.md").write_bytes(text.encode("utf-8"))
print("найдено строк с file_path/AGENTS.md:", len(edits))
for e in edits[:6]:
    print("---", e[:300].replace("\n", " "))
