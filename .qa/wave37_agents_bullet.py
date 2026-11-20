"""Insert the wave-37 design bullet into AGENTS.md without disturbing LF endings."""

from pathlib import Path

PATH = Path("AGENTS.md")
ANCHOR = "  readers on markup that is not the shipped file.\n"
BULLET = """- **One product speaks with one label voice, and a frame is only as good as the cells inside it.**
  The Mini App was rebuilt on the landing's measured recipes this wave, and the rebuild left two kinds
  of difference nothing could see: a label, and a row.
  `test_a_tracked_uppercase_voice_always_takes_the_mono_face` takes every rule in **both** stylesheets
  that sets `text-transform: uppercase` beside a `letter-spacing` — measured as 14 classes in
  `style.css` and 5 in `app.html`, 17 names over the two files — and refuses one that names no
  `font-family`, because `.gauge-cap` was exactly that: the only tracked caption in the product painted
  in the interface face while `.pal-kind`, `.ws-cur` and `.option-label::after` beside it used the money
  face. `test_a_label_voice_both_stylesheets_paint_is_one_voice` then compares the label classes the two
  surfaces share — `eyebrow` and `stat-label`, floor 2 — on family, weight, colour and tracking, and it
  found the second defect: the Mini App's `.stat-label` painted `400` against the landing's `500`, so
  the same caption came lighter on the phone than on the site. The size stays per surface, since the
  landing gradates a chip label against a micro annotation and the law governs the family, not the step.
  `test_the_tracked_voice_reader_reads_the_shapes_a_label_rule_takes` holds that reader to a grouped
  selector, a last declaration with no trailing `;`, `.1em`, a `::after`, caps without tracking,
  tracking without caps, and a comment that only looks like a rule.
  The row half: `web/static/app.html` paints a key/value cell (`.wd-row`) only inside a frame
  (`.list-group`) that owns the 1 px hairline ground between cells, so containment is a property of the
  app's **call graph**, not of its template text — the frame is opened by `rowFrame()` around rows an
  emitter handed back. `test_a_key_value_row_is_never_painted_outside_its_frame` takes the emitters (2
  measured, `availableLine()` and `infoRow()`) and requires an emitter that never opens a frame to have
  callers and **every** caller to reach one, which is why `infoRow()`'s bare cell is legal only while
  both sheets that call it wrap it.
  `test_the_frame_and_the_cell_it_holds_paint_two_different_grounds` keeps the divider real: the cell's
  background and the frame's must differ, and `.list-group` must keep `gap: 1px` **and** its own
  background, because losing either silently removes every hairline the group draws.
  `test_the_frame_reader_reads_the_shapes_a_sheet_builder_takes` runs both readers on synthetic builders,
  including `view.rowFrame(...)` and `rowFrames()` — a property and a longer name are not this call.
  What the pair cannot see is stated rather than hidden: it proves a builder reaches a frame, so a sheet
  that frames one list and paints a second row loose beside it answers the law and not the user, and
  only the rendered layer in `tests/test_production_design.py` measures that. Thirteen mutations were
  proven to fire: `.gauge-cap` back to no font family, a Mini App label invented in the interface face,
  the tracked-voice reader blinded, `.stat-label` painted 400 against the landing's 500, the landing
  re-tracked to `0.12em`, the app renaming its `.eyebrow`, `availableLine()` handing back a bare row,
  the withdraw sheet dropping `rowFrame(rows)`, `rowFrame()` opening a plain `div`, every
  `class="wd-row"` renamed, `.wd-row` painted on the hairline ground, `.list-group` trading `gap: 1px`
  for `gap: 8px`, and `.list-group` losing its background.
"""

text = PATH.read_bytes().decode("utf-8")
assert "\r" not in text, "AGENTS.md is not LF"
assert text.count(ANCHOR) == 1, text.count(ANCHOR)
assert "One product speaks with one label voice" not in text
PATH.write_bytes((text.replace(ANCHOR, ANCHOR + BULLET)).encode("utf-8"))
print("inserted", len(BULLET.splitlines()), "lines")
