"""Wave 37: prove the four new label/frame laws fire, on a scratch copy in memory.

Nothing here edits the tree: the laws read the module-level `CSS` / `APP_HTML` strings,
so a mutation is one assignment and the shipped files never see it.
"""

import importlib.util
import sys

spec = importlib.util.spec_from_file_location("tds", "tests/test_design_system.py")
tds = importlib.util.module_from_spec(spec)
sys.modules["tds"] = tds
spec.loader.exec_module(tds)


def fires(label, law, **patches):
    original = {name: getattr(tds, name) for name in patches}
    for name, value in patches.items():
        setattr(tds, name, value)
    try:
        law()
    except AssertionError as exc:
        print(f"FIRED  {label}: {str(exc).splitlines()[0][:110]}")
        return True
    finally:
        for name, value in original.items():
            setattr(tds, name, value)
    print(f"QUIET  {label}  <-- the law does not fire")
    return False


results = []

# --- the mono face of the tracked voice -------------------------------------------------
results.append(fires(
    "M1  .gauge-cap loses its font family (the defect as it shipped)",
    tds.test_a_tracked_uppercase_voice_always_takes_the_mono_face,
    CSS=tds.CSS.replace(
        "  fill: var(--muted); font-family: var(--font-mono); font-variant-numeric: tabular-nums;\n"
        "  font-size: var(--t-micro); letter-spacing: 0.08em;\n"
        "  text-transform: uppercase; text-anchor: middle;",
        "  fill: var(--muted); font-size: var(--t-micro); letter-spacing: 0.08em;\n"
        "  text-transform: uppercase; text-anchor: middle;",
    ),
))
results.append(fires(
    "M2  a Mini App label invented in the interface face",
    tds.test_a_tracked_uppercase_voice_always_takes_the_mono_face,
    APP_HTML=tds.APP_HTML.replace(
        ".tag{", ".sheet-cap{text-transform:uppercase;letter-spacing:.08em;font-size:var(--t-chip)}\n.tag{",
    ),
))
results.append(fires(
    "M3  the reader goes blind",
    tds.test_a_tracked_uppercase_voice_always_takes_the_mono_face,
    tracked_uppercase_voice=lambda css: {},
))

# --- one voice across the two stylesheets -----------------------------------------------
results.append(fires(
    "M4  the Mini App paints .stat-label lighter than the landing does",
    tds.test_a_label_voice_both_stylesheets_paint_is_one_voice,
    APP_HTML=tds.APP_HTML.replace(
        ".stat-label{font-size:var(--t-chip);font-weight:500;",
        ".stat-label{font-size:var(--t-chip);font-weight:400;",
    ),
))
results.append(fires(
    "M5  the landing re-tracks its labels and the Mini App keeps the old tracking",
    tds.test_a_label_voice_both_stylesheets_paint_is_one_voice,
    CSS=tds.CSS.replace("letter-spacing: 0.1em", "letter-spacing: 0.12em"),
))
results.append(fires(
    "M6  the shared label set empties (the Mini App renames its eyebrow)",
    tds.test_a_label_voice_both_stylesheets_paint_is_one_voice,
    APP_HTML=tds.APP_HTML.replace(".eyebrow,.input-label,.mini-label", ".kicker,.input-label,.mini-label"),
))

# --- a row inside its frame ---------------------------------------------------------------
results.append(fires(
    "M7  availableLine hands back a bare row and nothing calls it",
    tds.test_a_key_value_row_is_never_painted_outside_its_frame,
    APP_HTML=tds.APP_HTML.replace(
        "return rowFrame(`<div class=\"wd-row\">", "return `<div class=\"wd-row\">", 1,
    ),
))
results.append(fires(
    "M8  the withdraw sheet drops its frame around the four rows",
    tds.test_a_key_value_row_is_never_painted_outside_its_frame,
    APP_HTML=tds.APP_HTML.replace("    ${rowFrame(rows)}\n", "    ${rows}\n", 1),
))
results.append(fires(
    "M9  rowFrame stops opening the frame it is the only builder of",
    tds.test_a_key_value_row_is_never_painted_outside_its_frame,
    APP_HTML=tds.APP_HTML.replace(
        "return rows ? `<div class=\"list-group\">${rows}</div>` : '';",
        "return rows ? `<div>${rows}</div>` : '';",
    ),
))
results.append(fires(
    "M10 the reader stops seeing the rows it exists for",
    tds.test_a_key_value_row_is_never_painted_outside_its_frame,
    APP_HTML=tds.APP_HTML.replace('class="wd-row"', 'class="sheet-line"'),
))
results.append(fires(
    "M11 the cell paints the ground the frame's hairline is drawn on",
    tds.test_the_frame_and_the_cell_it_holds_paint_two_different_grounds,
    APP_HTML=tds.APP_HTML.replace("color:var(--text3);background:var(--bg);padding:12px 14px}",
                                  "color:var(--text3);background:var(--border);padding:12px 14px}"),
))
results.append(fires(
    "M12 the frame trades its one pixel separator for a gap of eight",
    tds.test_the_frame_and_the_cell_it_holds_paint_two_different_grounds,
    APP_HTML=tds.APP_HTML.replace(".list-group{display:flex;flex-direction:column;gap:1px;",
                                  ".list-group{display:flex;flex-direction:column;gap:8px;"),
))
results.append(fires(
    "M13 the frame stops painting the ground its gap shows",
    tds.test_the_frame_and_the_cell_it_holds_paint_two_different_grounds,
    APP_HTML=tds.APP_HTML.replace(".list-group{display:flex;flex-direction:column;gap:1px;"
                                  "background: var(--border);",
                                  ".list-group{display:flex;flex-direction:column;gap:1px;"),
))

print()
print(f"{sum(results)} of {len(results)} mutations fired")
sys.exit(0 if all(results) else 1)
