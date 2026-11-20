"""Restate the suite size and the line inventory after tests/test_event_loop.py joined the tree."""

from pathlib import Path

EDITS = {
    "README.md": [
        ('python -m pytest -q -m "not production"   # 1559 passed',
         'python -m pytest -q -m "not production"   # 1562 passed'),
    ],
    "AGENTS.md": [
        ("  1559 tests (199 more, marked", "  1562 tests (199 more, marked"),
    ],
    "docs/WHITEPAPER.md": [
        ("1559 обычных тестов + 199 боевых", "1562 обычных тестов + 199 боевых"),
        ("| Tests | 1559 ordinary + 199 production", "| Tests | 1562 ordinary + 199 production"),
        ("and 1559 + 199 tests", "and 1562 + 199 tests"),
        ("is **84,135** lines: tests 40,850 · bot 20,825 · web 12,030 · scripts 2,906 ·",
         "is **84,433** lines: tests 41,130 · bot 20,825 · web 12,048 · scripts 2,906 ·"),
    ],
}

for name, pairs in EDITS.items():
    p = Path(name)
    raw = p.read_bytes()
    assert b"\r\n" not in raw, f"CRLF in {name}"
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    for old, new in pairs:
        assert text.count(old) == 1, (name, text.count(old), old[:50])
        text = text.replace(old, new)
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    assert b"\r\n" not in data
    p.write_bytes(data)
    print("patched", name)
