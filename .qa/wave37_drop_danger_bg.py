from pathlib import Path

# The Mini App's red remove-disc was the only reader of --danger-bg on either
# surface, so the tint is now a colour no rule paints. Base keeps a state colour on
# the figure that carries it; a state with no painted surface has no token either.
EDITS = [
    ("web/static/style.css",
     "  --danger: #b42318;\n  --danger-bg: rgba(180, 35, 24, 0.07);\n",
     "  /* No --danger-bg: base paints a state on the figure that carries it, and\n"
     "     nothing in this product tints chrome with a danger fill. */\n"
     "  --danger: #b42318;\n"),
    ("web/static/style.css",
     "  --danger: #ff7a70;\n  --danger-bg: rgba(255, 122, 112, 0.12);\n",
     "  --danger: #ff7a70;\n"),
    ("web/static/app.html", "--danger:#b42318;--danger-bg:rgba(180,35,24,.07);", "--danger:#b42318;"),
    ("web/static/app.html", "--danger:#ff7a70;--danger-bg:rgba(255,122,112,.12);", "--danger:#ff7a70;"),
    ("tests/test_design_system.py", '    "--danger-bg": "--danger-bg",\n', ""),
]

for name, old, new in EDITS:
    path = Path(name)
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig" if bom else "utf-8")
    assert text.count(old) == 1, f"{name}: {old[:70]!r} matched {text.count(old)} times"
    path.write_bytes((("\ufeff" + text.replace(old, new)) if bom else text.replace(old, new)).encode("utf-8"))
    assert path.read_bytes().startswith(b"\xef\xbb\xbf") is bom, f"{name}: the BOM moved"
    print("edited", name)
