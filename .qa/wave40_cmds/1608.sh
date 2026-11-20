cd /d/tippy_on_base
python -X utf8 - > .qa/wave40_bash_src.txt 2>&1 <<'PY'
import json, pathlib
TX = pathlib.Path(r"C:\Users\ssrjk\.qoder\projects\D--tippy-on-base\802a3ec4-7e64-4606-a9f7-a2b69f46fd9e.jsonl")
hits = []
for no, raw in enumerate(TX.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
    if "AGENTS.md" not in raw and "agents.md" not in raw:
        continue
    try:
        node = json.loads(raw)
    except Exception:
        continue
    msg = node.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        continue
    for part in content:
        if isinstance(part, dict) and part.get("type") == "tool_use":
            cmd = str((part.get("input") or {}).get("command", ""))
            if "AGENTS" in cmd.upper() and ("Ledger facts" in cmd or "write_bytes" in cmd or "append" in cmd):
                hits.append((no, node.get("timestamp"), len(cmd), cmd))
print("подходящих команд:", len(hits))
outdir = pathlib.Path(".qa/wave40_cmds"); outdir.mkdir(exist_ok=True)
for i, (no, ts, ln, cmd) in enumerate(hits):
    f = outdir / f"{no:04d}.sh"
    f.write_text(cmd, encoding="utf-8")
    print(f"[{i}] jsonl:{no} {ts} длина {ln} -> {f}")
PY
cat .qa/wave40_bash_src.txt