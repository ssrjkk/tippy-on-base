"""Prove the three metrics-surface laws fire, restoring every file byte-for-byte."""
import pathlib
import shutil
import subprocess
import sys

TARGETS = ["web/metrics.py", "docs/OPERATIONS.md"]
BACKUP = {t: pathlib.Path(t).read_bytes() for t in TARGETS}
for t in TARGETS:
    shutil.copyfile(t, ".qa/wave40_" + pathlib.Path(t).name + ".pristine")

MUTATIONS = [
    (
        "M1 коллектор снова считает и выбрасывает",
        "web/metrics.py",
        '        m["volume_usdc"] = float(Decimal(s.get("volume_micro", 0)) / Decimal(_MICRO))\n',
        '        m["volume_usdc"] = float(Decimal(s.get("volume_micro", 0)) / Decimal(_MICRO))\n        m["tips_usdc"] = 0.0\n',
        "tests/test_metrics.py::test_the_collector_writes_exactly_what_the_formatter_reads",
    ),
    (
        "M3 докстринг обещает несуществующую метрику",
        "web/metrics.py",
        "`tipbot_solvent`, `tipbot_users_total`,\n`tipbot_markets_open`, `tipbot_volume_usdc`",
        "`tipbot_solvent`, `tipbot_users_total`,\n`tipbot_markets_open`, `tipbot_volume_usdc`, `tipbot_rpc_latency_seconds`",
        "tests/test_metrics.py::test_the_module_docstring_promises_the_names_the_payload_carries",
    ),
    (
        "M4 ранбук теряет опубликованную метрику",
        "docs/OPERATIONS.md",
        "- `tipbot_volume_usdc` — Total volume (counter)\n",
        "",
        "tests/test_metrics.py::test_the_runbook_offers_the_metrics_the_endpoint_answers",
    ),
    (
        "M5 форматтер печатает пустой пэйлоад (читатель ослеплён)",
        "web/metrics.py",
        "def _format_metrics(m: dict) -> str:\n    lines = [",
        "def _format_metrics(m: dict) -> str:\n    if m == {}:\n        return \"\"\n    lines = [",
        "tests/test_metrics.py::test_the_collector_writes_exactly_what_the_formatter_reads",
    ),
    (
        "M6 публикуемая строка удалена из экспозиции",
        "web/metrics.py",
        '        f"tipbot_markets_open {m.get(\'markets_open\', 0)}",\n',
        "",
        "tests/test_metrics.py::test_the_module_docstring_promises_the_names_the_payload_carries",
    ),
]

lines = []
fired = 0
try:
    for name, target, old, new, node in MUTATIONS:
        text = BACKUP[target].decode("utf-8")
        assert text.count(old) == 1, f"{name}: якорь в {target} найден {text.count(old)} раз"
        pathlib.Path(target).write_bytes(text.replace(old, new, 1).encode("utf-8"))
        out = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", node, "-q", "-x",
             "--timeout=600", "--timeout-method=thread", "-p", "no:cacheprovider"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
        ok = out.returncode != 0
        fired += ok
        lines.append(("СРАБОТАЛ  " if ok else "МОЛЧИТ    ") + name + "\n            " + node)
        if not ok:
            lines.append("            " + (out.stdout or out.stderr).strip().replace("\n", "\n            ")[-500:])
        for t, raw in BACKUP.items():
            pathlib.Path(t).write_bytes(raw)
        for t in TARGETS:
            assert pathlib.Path(t).read_bytes() == BACKUP[t], f"{t} НЕ восстановлен"
finally:
    for t, raw in BACKUP.items():
        pathlib.Path(t).write_bytes(raw)

restored = all(pathlib.Path(t).read_bytes() == BACKUP[t] for t in TARGETS)
lines.insert(0, f"мутаций проверено: {len(MUTATIONS)}, сработали: {fired}, файлы восстановлены: {restored}")
text = "\n".join(lines) + "\n"
pathlib.Path(".qa/wave40_metrics_mutations.txt").write_text(text, encoding="utf-8")
print(text)
