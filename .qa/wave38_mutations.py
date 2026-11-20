"""Run each frame/cell mutation in a scratch tree and report which law fired."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(".")
SCRATCH = Path(".qa/scratch38")
SELECT = ("frame_divides or row_painted_in_markup or row_builder_hands "
          "or surface_that_paints or frame_and_cell_readers")

MUTATIONS = [
    ("list-group trades its hairline for air", "web/static/app.html",
     ".list-group{display:flex;flex-direction:column;gap:1px;",
     ".list-group{display:flex;flex-direction:column;gap:8px;"),
    ("language list loses its frame class", "web/static/app.html",
     '<div class="list-group" id="langList">', '<div id="langList">'),
    ("action row loses its frame class", "web/static/app.html",
     '<div class="action-row">', '<div>'),
    ("on-chain block stops framing its cards", "web/static/app.html",
     '`<div class="list-group">` + oc.map(ocCard)', '`<div>` + oc.map(ocCard)'),
    ("rowFrame hands back the rows bare", "web/static/app.html",
     r"return rows ? `<div class=\"list-group\">${rows}</div>` : ''",
     "return rows"),
    ("history stops framing its rows", "web/static/app.html",
     r"c.innerHTML = `<div class=\"list-group\">` + h.map(tx => {",
     r"c.innerHTML = `` + h.map(tx => {"),
    ("agent page ships no frame", "web/static/agent.html",
     'class="rows"', 'id="orphanRows"'),
    ("profile page ships no frame", "web/static/user.html",
     'class="rows"', 'id="orphanRows"'),
    ("Mini App hairline equals the card", "web/static/app.html",
     "--card3:#efefef;--border:#efefef;", "--card3:#efefef;--border:#f8f8f8;"),
    ("frame reader accepts any gap", "tests/test_design_system.py",
     'decls.get("gap", "").strip() == "1px"',
     'decls.get("gap", "").strip() != ""'),
    ("cell reader stops asking for an inset", "tests/test_design_system.py",
     'if ground is None or ground == HAIRLINE_TOKEN or "padding" not in decls:',
     'if ground is None or ground == HAIRLINE_TOKEN:'),
    ("class reader stops at a prefix", "tests/test_design_system.py",
     r'{re.escape(cls)}\b(?:"|\s)', r'{re.escape(cls)}(?:"|\s|x)'),
    ("call reader demands a parenthesis", "tests/test_design_system.py",
     r'{re.escape(name)}\s*(?=[(,)\]])', r'{re.escape(name)}\s*\('),
    ("rule reader reads a descendant as the class", "tests/test_design_system.py",
     r'own = re.fullmatch(r"\.([a-zA-Z][-\w]*)", part.strip())',
     r'own = re.match(r"\.([a-zA-Z][-\w]*)", part.strip())'),
    ("markup walk forgets closing tags", "tests/test_design_system.py",
     "        enclosing = sorted({cls for seen, _ in stack for cls in (seen & set(frames))})",
     "        enclosing = []"),
]


def reset() -> None:
    for rel in {m[1] for m in MUTATIONS}:
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


reset()
print("baseline:", run()[0])
fired = 0
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
    fired += bool(failed)
    print(f"{'FIRED ' if failed else 'SILENT'} ({hits}x) {name} -> {failed or summary}")
print(f"\n{fired}/{len(MUTATIONS)} mutations fired")
