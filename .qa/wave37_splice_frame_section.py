"""Splice the generalized frame/cell section into tests/test_design_system.py (wave 37).

The old section judged one remembered pair (`.wd-row` inside `.list-group`); the new one
derives every frame and every cell from both stylesheets. Everything from the old section's
marker comment to the end of the file is replaced.

Run: python -X utf8 .qa/wave37_splice_frame_section.py
"""

from pathlib import Path

PATH = Path("tests/test_design_system.py")
SECTION = Path(".qa/wave37_frame_section.txt")
MARKER = "# a key/value row and the frame that owns its hairlines"

text = PATH.read_text(encoding="utf-8")
assert "\r" not in text, "the file is LF in git; a rewritten CRLF is a whole-file diff"
lines = text.split("\n")
index = next(i for i, line in enumerate(lines) if line.strip() == MARKER)
head = "\n".join(lines[: index - 1])
section = SECTION.read_text(encoding="utf-8")
assert "ROW_CELL" not in section and "ROW_FRAME" not in section

new_text = head.rstrip("\n") + "\n\n" + section
if not new_text.endswith("\n"):
    new_text += "\n"
PATH.write_bytes(new_text.encode("utf-8"))
print(f"head kept {index - 1} lines, section {len(section.splitlines())} lines, "
      f"total {len(new_text.splitlines())}")
