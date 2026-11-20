"""Prove the five new solvency laws fire, then restore the file byte-for-byte.

Each mutation is applied to a backup-protected copy in this working tree, one test
node is run, and the original is put back before the next mutation, so a crash
mid-loop still leaves the tree at the measured state (the restore runs in `finally`).
"""
import pathlib
import shutil
import subprocess
import sys

SRC = pathlib.Path("bot/solvency.py")
BAK = pathlib.Path(".qa/wave40_solvency_pristine.py")
shutil.copyfile(SRC, BAK)
pristine = SRC.read_bytes()

MUTATIONS = [
    (
        "M1 повтор разрешён сразу (проверка состояния убрана)",
        '    if state == _last_state and now - _last_alert_ts < _ALERT_REPEAT:\n        return\n',
        "",
        "tests/test_solvency.py::test_a_verdict_that_persists_speaks_once_then_on_the_slow_clock",
    ),
    (
        "M2 окно повтора сгорает до отправки",
        "    if not await _send_alert(bot, text):\n        return\n    _last_state = state\n    _last_alert_ts = now",
        "    _last_state = state\n    _last_alert_ts = now\n    await _send_alert(bot, text)",
        "tests/test_solvency.py::test_a_send_that_fails_does_not_consume_the_repeat_window",
    ),
    (
        "M3 восстановление молчит",
        "        if _last_state is None:\n            return\n        if not await _send_alert(bot, text):",
        "        if True:\n            return\n        if not await _send_alert(bot, text):",
        "tests/test_solvency.py::test_a_recovered_ledger_says_so_once",
    ),
    (
        "M4 слепой канареечный лог удалён",
        '        log.warning("solvency: blind, reserves unreadable, owed=%.2f", owed / _MICRO)\n',
        "",
        "tests/test_solvency.py::test_a_blind_canary_logs_that_it_is_alive_and_blind",
    ),
    (
        "M5 окно повтора равно полу",
        "_ALERT_REPEAT = 6 * 3600",
        "_ALERT_REPEAT = _ALERT_COOLDOWN",
        "tests/test_solvency.py::test_the_repeat_window_is_longer_than_the_floor",
    ),
    (
        "M6 часовой пояс вердикта: состояние не запоминается",
        "    _last_state = state\n    _last_alert_ts = now",
        "    _last_alert_ts = now",
        "tests/test_solvency.py::test_a_verdict_that_persists_speaks_once_then_on_the_slow_clock",
    ),
]

report = []
try:
    for name, old, new, node in MUTATIONS:
        text = pristine.decode("utf-8")
        assert text.count(old) == 1, f"{name}: якорь не найден ({text.count(old)})"
        SRC.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        out = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", node, "-q", "-x",
             "--timeout=300", "--timeout-method=thread", "-p", "no:cacheprovider"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
        fired = out.returncode != 0
        report.append(f"{'СРАБОТАЛ' if fired else 'МОЛЧИТ  '}  {name}\n           {node}")
        if not fired:
            report.append("           " + (out.stdout or out.stderr).strip().replace("\n", "\n           ")[-400:])
finally:
    SRC.write_bytes(pristine)
    assert SRC.read_bytes() == pristine, "ФАЙЛ НЕ ВОССТАНОВЛЕН"

lines = ["мутаций проверено: %d, сработали: %d" % (len(report), sum(1 for r in report if r.startswith("СРАБОТАЛ")))]
lines += report
lines.append("bot/solvency.py восстановлен байт в байт: " + str(SRC.read_bytes() == pristine))
pathlib.Path(".qa/wave40_solvency_mutations.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
