cd /d/tippy_on_base
cp AGENTS.md .qa/wave39_agents_clobbered_16lines.md
cp .qa/wave39_agents_pre_clobber.md AGENTS.md
python - <<'PY'
import hashlib, pathlib
a = pathlib.Path("AGENTS.md").read_bytes()
b = pathlib.Path(".qa/wave39_agents_pre_clobber.md").read_bytes()
print("AGENTS.md восстановлен побайтово:", hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest())
t = a.decode("utf-8")
print("строк:", t.count("\n") + 1, "байт:", len(a))
for k in ["# Agent Guidelines", "## Ledger facts", "## Interface", "1544 tests", "1,204 s", "Every text file is stored with LF"]:
    print(f"  {k!r}: {t.count(k)}")
print("конец файла:", repr(t[-60:]))
PY