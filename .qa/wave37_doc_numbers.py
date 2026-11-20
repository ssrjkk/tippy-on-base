"""Wave 37: restate every documented number the six new design laws moved.

Suite size measured by `pytest -m "not production" --collect-only` on this run:
1537 ordinary, 199 production. Lines of code measured by
`tests/test_docs.py::_lines_of_code_by_package()`: 81,980 total, tests 39,338, web 11,523.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(rel: str, pairs) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        assert text.count(old) == 1, f"{rel}: {text.count(old)} copies of {old[:60]!r}"
        text = text.replace(old, new)
    path.write_bytes(text.encode("utf-8"))


patch("docs/WHITEPAPER.md", [
    ("is **81,666** lines: tests 39,050 · bot 20,752 · web 11,497 · scripts 2,906 ·",
     "is **81,980** lines: tests 39,338 · bot 20,752 · web 11,523 · scripts 2,906 ·"),
    ("10 модулей агента, 1531 обычных тестов + 199 боевых.",
     "10 модулей агента, 1537 обычных тестов + 199 боевых."),
    ("| Tests | 1531 ordinary + 199 production",
     "| Tests | 1537 ordinary + 199 production"),
    ("and 1531 + 199 tests including structural gates",
     "and 1537 + 199 tests including structural gates"),
])

patch("README.md", [
    ('python -m pytest -q -m "not production"   # 1531 passed',
     'python -m pytest -q -m "not production"   # 1537 passed'),
])

patch("AGENTS.md", [
    ("  1531 tests (199 more, marked `production`, run",
     "  1537 tests (199 more, marked `production`, run"),
])

print("patched")
