"""Mutations C: the frame/cell readers of wave 38, run against a scratch copy.

Each case breaks one premise of the composition laws in
`test_a_row_written_into_a_node_is_framed_by_the_write_or_by_the_page` and its siblings, and
the battery prints the test name plus one line of the assertion the build then raises.
Nothing here is collected as a test.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(r"D:/tippy_on_base")
SCRATCH = REPO / ".qa/scratch38"
SELECT = ["-k", "frame_divides or row_painted_in_markup or row_builder_hands or "
                "written_into_a_node or frame_and_cell_readers"]

LAW = SCRATCH / "tests/test_design_system.py"
APP_HTML = SCRATCH / "web/static/app.html"
INDEX_HTML = SCRATCH / "web/static/index.html"

APP_MARKUP = ('<div class="list-group" id="langList">\n'
              '      <button class="lang-item" data-lang="ru">')
APP_WRITER = "rowFrame(rows)"
APP_EMPTY = 'rows.map(ocCard).join("");'
LANDING_HOLDER = '<div class="rows" id="historyList">'
LANDING_POSITIONS = '<div class="rows" id="positionsList">'
LAW_EMPTY_CUT = 'value = statement_value(body, match.end())'
LAW_FRAME_HOP = (
    "    for other in sorted(functions):\n"
    "        if other != name and calls(other).search(value):\n"
    "            built = writes_a_cell(functions[other], cells)\n"
    "            if built:\n"
    "                return built\n")
LAW_LOCAL_HOP = "    for decl in LOCAL_DECL_RE.finditer(body):"
LAW_SCANNER_CUT = '        elif char == ";" and depth == 0:'
LAW_SCANNER_CLOSER = "            if depth == 0:\n                return text[start:index]"
LAW_SELF_FRAME = "        if tag_id is not None and own:"
LAW_BINDING = """    binding = next((match for match in BOUND_NODE_RE.finditer(body)
                    if match.group("name") == call.group("name")), None)"""

CASES = [
    ("landing: the frame the history list is painted in loses its class, and the rows the "
     "script hands to that id stand loose on the page",
     INDEX_HTML, LANDING_HOLDER, '<div id="historyList">'),
    ("landing: the frame the positions list is painted in loses its class",
     INDEX_HTML, LANDING_POSITIONS, '<div id="positionsList">'),
    ("landing: the language list in the Mini App markup stops opening its own frame, and the "
     "three rows inside it lose the hairline",
     APP_HTML, APP_MARKUP,
     '<div id="langList">\n      <button class="lang-item" data-lang="ru">'),
    ("Mini App writer: `renderHistory` stops wrapping its rows in `rowFrame`, so the history "
     "list is painted into a bare container",
     APP_HTML, APP_WRITER, "rows"),
    ("Mini App writer: `renderMarkets` puts its rows in the container without a frame, beside "
     "the empty-state line that really has none",
     APP_HTML, APP_EMPTY, 'rows.map(ocCard).join("<div class=\\"empty\\">x</div>");'),
    ("reader: a node write's value is cut at the first semicolon it meets, which lands inside "
     "the `.map` callback every row list is built from",
     LAW, LAW_SCANNER_CUT, '        elif char == ";":'),
    ("reader: the value of a write is read to the end of the body, so a later statement's "
     "frame pays for an earlier row list",
     LAW, LAW_SCANNER_CLOSER, "            if depth < 0:\n                return text[start:index]"),
    ("reader: a `box.innerHTML` write is no longer resolved through the `var box = $('id')` "
     "the same function holds, so the writer's container goes unnamed",
     LAW, LAW_BINDING, "    binding = None"),
    ("reader: the cells a callee paints are no longer credited to the write that calls it",
     LAW, LAW_FRAME_HOP, ""),
    ("reader: the cells a local variable holds are no longer credited to the write that "
     "interpolates it, which is how the top-5 list is built",
     LAW, LAW_LOCAL_HOP, "    for decl in []:"),
    ("reader: an element that carries a frame itself is no longer read as framed, so the "
     "Mini App's framed containers count as bare",
     LAW, LAW_SELF_FRAME, "        if tag_id is not None and False:"),
]


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def run_laws() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "tests/test_design_system.py", "-q",
         "-p", "no:cacheprovider", "--no-header", "-o", "addopts=", *SELECT],
        cwd=SCRATCH, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def failing_laws(report: str) -> list[str]:
    return re.findall(r"^FAILED tests\\test_design_system\.py::(\S+)", report, re.M)


def first_assertion(report: str) -> str:
    lines = [line.strip() for line in report.splitlines() if line.startswith("E   ")]
    return " | ".join(lines[:3])[:300]


def reset() -> None:
    write(LAW, read(REPO / "tests/test_design_system.py"))
    write(APP_HTML, read(REPO / "web/static/app.html"))
    write(INDEX_HTML, read(REPO / "web/static/index.html"))


def main() -> int:
    reset()
    code, report = run_laws()
    if code != 0:
        print(f"BASELINE IS NOT GREEN (exit {code}):\n{report[-2500:]}")
        return 1
    print(f"baseline: {len(failing_laws(report))} failed — {report.splitlines()[-1]}")

    silent: list[str] = []
    for index, (description, path, anchor, replacement) in enumerate(CASES, start=1):
        reset()
        text = read(path)
        if anchor not in text:
            print(f"[{index:>2}] MISSING ANCHOR: {description}")
            silent.append(f"anchor not found: {description}")
            continue
        if text.count(anchor) != 1:
            print(f"[{index:>2}] ANCHOR NOT UNIQUE ({text.count(anchor)}x): {description}")
            silent.append(f"anchor not unique: {description}")
            continue
        write(path, text.replace(anchor, replacement))
        code, report = run_laws()
        laws = failing_laws(report)
        if code == 0 or not laws:
            print(f"[{index:>2}] SILENT: {description}")
            silent.append(description)
            continue
        print(f"[{index:>2}] FIRED {','.join(laws)}: {description}")
        print(f"     {first_assertion(report)}")

    reset()
    print()
    if silent:
        print(f"{len(silent)} of {len(CASES)} mutations did not fail the build:")
        for item in silent:
            print(f"  - {item}")
        return 1
    print(f"all {len(CASES)} mutations fired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
