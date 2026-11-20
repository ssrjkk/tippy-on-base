"""Wave 41 (design v2): the component layer of web/static/style.css.

Every substitution asserts it matched exactly once, so a rule that moved since
this script was written fails the patch instead of silently skipping a surface.
"""
from pathlib import Path

P = Path("web/static/style.css")
t = P.read_text(encoding="utf-8-sig")


def sub(old: str, new: str) -> None:
    global t
    n = t.count(old)
    assert n == 1, "pattern found %d times, expected 1: %r" % (n, old[:120])
    t = t.replace(old, new)


# ---- Buttons: square, bordered, one hairline of brand on hover. -----------
sub(
    """.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 12px 22px; border-radius: var(--pill);
  text-decoration: none; font-weight: 500; font-size: var(--t-lead); cursor: pointer; border: none;
  font-family: var(--font-ui);
  transition: background-color 0.2s var(--ease), color 0.2s var(--ease), border-color 0.2s var(--ease);
}
.btn:active { opacity: 0.8; }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn-primary { background: var(--blue); color: var(--on-accent); }
.btn-primary:hover { background: var(--blue-hover); }
.btn-ghost {
  border: 1px solid var(--border-strong); color: var(--ink); background: transparent;
}
.btn-ghost:hover { border-color: var(--ink); background: var(--surface); }""",
    """/* v2: a control is a rectangle with a hairline, not a lozenge. The fill says
   primary, the border says secondary, and the brand colour arrives on the edge
   instead of tinting the label. */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 40px; padding: 0 18px; border-radius: 0;
  text-decoration: none; font-weight: 600; font-size: var(--t-small); cursor: pointer;
  border: 1px solid var(--border-strong); background: var(--surface); color: var(--ink);
  font-family: var(--font-ui);
  transition: background-color 0.16s var(--ease), color 0.16s var(--ease), border-color 0.16s var(--ease);
}
.btn:active { opacity: 0.8; }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn:hover { background: var(--surface-hover); border-color: var(--accent); }
.btn-primary { background: var(--blue); border-color: var(--blue); color: var(--on-accent); }
.btn-primary:hover { background: var(--blue-hover); border-color: var(--blue-hover); color: var(--on-accent); }
.btn-ghost {
  border: 1px solid var(--border-strong); color: var(--ink); background: transparent;
}
.btn-ghost:hover { border-color: var(--ink); background: var(--surface); }""",
)

# ---- Tabs: one segmented frame, hairline between cells. -------------------
sub(
    """.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin: 16px 0; }
.tab {
  background: transparent; border: 1px solid var(--border-strong);
  color: var(--muted); font: inherit; font-size: var(--t-small); font-weight: 500;
  padding: 8px 16px; border-radius: var(--pill); cursor: pointer;
  transition: color 0.2s var(--ease), border-color 0.2s var(--ease), background-color 0.2s var(--ease);
}
.tab:hover { color: var(--ink); border-color: var(--ink); }""",
    """/* v2: the choices of one control share a frame. Cells butt against each other
   behind a single hairline, so a group of three tabs is one object instead of
   three buttons that happen to be neighbours. */
.tabs {
  display: inline-flex; flex-wrap: wrap; margin: 16px 0;
  border: 1px solid var(--border); background: var(--surface-2);
}
.tab {
  background: transparent; border: 0; border-right: 1px solid var(--border);
  color: var(--muted); font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: var(--t-chip); font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
  padding: 8px 14px; cursor: pointer; text-decoration: none;
  transition: color 0.16s var(--ease), background-color 0.16s var(--ease);
}
.tab:last-child { border-right: 0; }
.tab:hover { color: var(--ink); background: var(--surface-hover); }""",
)

# ---- Fields: 42 px, hairline-strong, square. ------------------------------
sub(
    """.input {
  width: 100%; padding: 12px 14px;
  background: var(--canvas); color: var(--ink);
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  font: inherit; font-size: var(--t-body);""",
    """.input {
  width: 100%; min-height: 42px; padding: 8px 12px;
  background: var(--canvas); color: var(--ink);
  border: 1px solid var(--border-strong); border-radius: 0;
  font: inherit; font-size: var(--t-small);""",
)

# ---- Layers: the drawer docks right, the palette takes a brand edge. ------
sub(
    ".drawer[open] { display: flex; align-items: center; justify-content: center; }",
    ".drawer[open] { display: flex; align-items: stretch; justify-content: flex-end; }",
)
sub(
    """.drawer-panel {
  width: min(560px, calc(100vw - 24px)); max-height: min(86vh, 760px);
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--canvas); color: var(--ink);
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg);
  animation: drawerIn 0.22s var(--ease) both;
}""",
    """.drawer-panel {
  width: min(460px, 100vw); max-height: none; height: 100%;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--canvas); color: var(--ink);
  border: 0; border-left: 2px solid var(--blue); border-radius: 0;
  animation: drawerIn 0.22s var(--ease) both;
}""",
)
sub(
    ".palette[open] { display: flex; align-items: flex-start; justify-content: center; padding: 10vh 12px 12px; }",
    ".palette[open] { display: flex; align-items: flex-start; justify-content: center; padding: 12vh 12px 12px; }",
)
sub(
    """.palette-panel {
  width: min(660px, 100%); max-height: 100%;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--canvas); color: var(--ink);
  border: 1px solid var(--border-strong); border-radius: var(--radius-lg);
  animation: slideDown 0.18s var(--ease) both;
}""",
    """.palette-panel {
  width: min(540px, 100%); max-height: 100%;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--canvas); color: var(--ink);
  border: 2px solid var(--blue); border-radius: 0;
  animation: slideDown 0.18s var(--ease) both;
}""",
)

# ---- The v2 surfaces. -----------------------------------------------------
NEW_SECTION = """/* ================================================================
   v2 surfaces
   ================================================================ */

/* The strip above the navigation is the node speaking, not the marketing copy:
   which chain, which block, whether it can pay. Every figure in it is painted
   by app.js from /api/info and /api/solvency — the bar ships no number of its
   own, because a typed one is a second copy of a knob nobody diffs. */
.telemetry {
  border-bottom: 1px solid var(--border); background: var(--surface-2);
  padding: 7px 0; font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: var(--t-chip); color: var(--muted);
}
.telemetry-inner {
  max-width: 1320px; margin: 0 auto; padding: 0 24px;
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 8px 16px;
}
.telemetry-k { color: var(--ink-2); font-weight: 700; letter-spacing: 0.06em; }
.telemetry-link { color: var(--accent-text); text-decoration: none; }
.telemetry-link:hover { text-decoration: underline; }

/* A badge is a fact about a thing, set in the money face at the size of a
   caption. The market-state vocabulary stays with `.chip-*`, which the state
   law measures; this family is for everything that is not a market state. */
.badge {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px;
  border: 1px solid var(--border); background: var(--surface-2); border-radius: 0;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: var(--t-micro); font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--muted);
}
.badge-brand { background: var(--blue-soft); border-color: var(--blue-line); color: var(--ink); }
.badge-yes { background: var(--success-bg); border-color: var(--border); color: var(--success); }
.badge-no { background: transparent; border-color: var(--danger); color: var(--danger); }
.badge-warn { background: var(--warning-bg); border-color: var(--border); color: var(--warning); }

/* Reserves as one bar: what is owed, what is in flight, what is left over. The
   track is --surface-2, so the segments are the only colour in the row. */
.seg-bar {
  display: flex; height: 10px; margin: 12px 0;
  background: var(--surface-2); border: 1px solid var(--border); overflow: hidden;
}
.seg-owed { background: var(--blue); }
.seg-pending { background: var(--warning); }
.seg-surplus { background: var(--success); }
.seg-key { display: flex; flex-wrap: wrap; gap: 6px 14px; }
.seg-key-item {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: var(--t-meta); color: var(--ink-2);
}
.seg-swatch { width: 8px; height: 8px; flex: none; border-radius: 0; }

/* Two columns that are one object: the hairline between them is the frame
   showing through a 1px gap, which is how the KPI strip already divides. */
.workspace-row {
  display: grid; grid-template-columns: 1.55fr 0.95fr;
  border-bottom: 1px solid var(--border);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  background: var(--border);
  gap: 1px;
  margin: 0 24px 32px;
}
.workspace-col { display: flex; flex-direction: column; gap: 1px; background: var(--border); }
.wp-grid {
  display: grid; grid-template-columns: repeat(3, 1fr);
  border-bottom: 1px solid var(--border);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  background: var(--border);
  gap: 1px;
  margin: 0 24px 32px;
}
.wp-card { background: var(--surface); padding: 20px 18px; }
.wp-card h3 { font-size: var(--t-ui); font-weight: 700; margin: 10px 0 6px; }
.wp-card p { font-size: var(--t-meta); color: var(--ink-2); line-height: 1.55; }

/* An option cell in a price row: the choice and its odds in one hairline box. */
.out-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.out-cell {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  padding: 8px 12px; border: 1px solid var(--border); background: var(--canvas);
  font-size: var(--t-meta); font-weight: 700; color: var(--ink-2);
}
.out-cell b {
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: var(--t-ui); color: var(--ink); font-weight: 700;
}

/* Key/value rows inside a panel: the label and the figure, one hairline apart. */
.list-row {
  display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  padding: 10px 0; border-bottom: 1px solid var(--border); font-size: var(--t-small);
}
.list-row:last-child { border-bottom: 0; }
.list-row span { color: var(--muted); }
.list-row b {
  color: var(--ink); font-weight: 600;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
}
.list-row b.pos { color: var(--success); }
.list-row b.neg { color: var(--danger); }

@media (pointer: coarse) {
  /* The v2 field is 42 px on a keyboard and 44 under a thumb: the same control,
     the floor the touch law pays for. */
  .input { min-height: 44px; }
}

/* ================================================================
   Responsive
   ================================================================ */"""

sub(
    """/* ================================================================
   Responsive
   ================================================================ */""",
    NEW_SECTION,
)

# ---- The new grids collapse with the rest of the landing. -----------------
sub(
    "  .stats, .steps { grid-template-columns: repeat(2, 1fr); }",
    """  .stats, .steps { grid-template-columns: repeat(2, 1fr); }
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .analytics-row, .workspace-row, .wp-grid { grid-template-columns: 1fr; }""",
)

P.write_bytes(t.encode("utf-8"))
assert "\r\n" not in t
print("component layer written")
