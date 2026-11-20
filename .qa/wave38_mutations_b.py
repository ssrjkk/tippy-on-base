"""Second half of the frame/cell mutation battery: the three re-anchored cases."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(".")
SCRATCH = Path(".qa/scratch38")
SELECT = ("frame_divides or row_painted_in_markup or row_builder_hands "
          "or surface_that_paints or frame_and_cell_readers")

MUTATIONS = [
    ("rowFrame hands back the rows bare", "web/static/app.html",
     "return rows ? `<div class=\"list-group\">${rows}</div>` : '';",
     "return rows;"),
    ("history stops framing its rows", "web/static/app.html",
     "c.innerHTML = `<div class=\"list-group\">` + h.map(tx => {",
     "c.innerHTML = `` + h.map(tx => {"),
    ("class reader matches a prefix of the class", "tests/test_design_system.py",
     'rf"""class\s*=\s*(?:"[^"]*\b{re.escape(cls)}\b(?:"|\s)|\'[^\']*\b{re.escape(cls)}\b(\'|\s))""",',
     'rf"""class\s*=\s*(?:"[^"]*\b{re.escape(cls)}|\'[^\']*\b{re.escape(cls)})""",'),
]

FILES = {m[1] for m in MUTATIONS}


def reset() -> None:
    for rel in FILES:
        (SCRATCH / rel).write_bytes((ROOT / rel).read_bytes())


def run() -> tuple[str, list[str]]:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "tests/test_design_system.py",
         "-q", "-p", "no:cacheprovider", "--no-header", "-k", SELECT, "--tb=line"],
        cwd=SCRATCH, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    failed = sorted({line.split("::")[-1].split(" ")[0]
                     for line in proc.stdout.splitlines() if line.startswith("FAILED")})
    lines = [line for line in proc.stdout.strip().splitlines() if line]
    return (lines[-1] if lines else "<no output>", failed)


for name, rel, old, new in MUTATIONS:
    reset()
    path = SCRATCH / rel
    text = path.read_text(encoding="utf-8-sig")
    hits = text.count(old)
    if hits == 0:
        print(f"!! {name}: anchor not found in {rel}")
        continue
    path.write_bytes(text.replace(old, new).encode("utf-8"))
    summary, failed = run()
    print(f"{'FIRED ' if failed else 'SILENT'} ({hits}x) {name} -> {failed or summary}")
