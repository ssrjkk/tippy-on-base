"""Wave 41, stage 3c — the two laws the landing rewrite broke.

1. `test_the_weights_the_ui_asks_for_are_the_weights_the_faces_can_draw`: the money
   face on this origin is Geist Mono, whose served range is 400-600, so a 700 set
   beside `--font-mono` is synthesised bold. The v2 badge wants a loud caption; the
   heaviest weight the face is actually drawn at is 600, so that is what it takes.
2. `test_a_framed_tile_grid_paints_no_empty_cell`: the old hero strip was a framed
   tile grid (six cells, shared hairlines) and the reader counts them tree-wide with
   a floor of three. The KPI strip divides its cells with a 1px `gap` over a
   `--border` ground, which the reader classifies as a ragged card grid, so it
   dropped out of the subject set. The strip is a table, so it is drawn as one: the
   hairlines come from the cells' own borders, the same way `.stats` does it.

The dead `.hero-metrics` rules go with it: the wrapper left the markup in stage 3a,
and a class that styles nothing is a second answer to a question nobody asks.
"""

from pathlib import Path

CSS = Path("web/static/style.css")
APP = Path("web/static/app.html")

n = 0


def patch(path: Path, pairs: list[tuple[str, str]]) -> None:
    global n
    t = path.read_text(encoding="utf-8")
    for old, new in pairs:
        assert t.count(old) == 1, f"{path.name}: found {t.count(old)}x, expected 1: {old[:90]!r}"
        t = t.replace(old, new, 1)
        n += 1
    path.write_bytes(t.encode("utf-8"))
    assert "\r\n" not in t


css_pairs = [
    # --- 1. the loud caption is 600 in the money face ---
    (
        ".telemetry-k { color: var(--ink-2); font-weight: 700; letter-spacing: 0.06em; }",
        ".telemetry-k { color: var(--ink-2); font-weight: 600; letter-spacing: 0.06em; }",
    ),
    (
        "  font-size: var(--t-micro); font-weight: 700; letter-spacing: 0.06em;\n  text-transform: uppercase; color: var(--muted);",
        "  font-size: var(--t-micro); font-weight: 600; letter-spacing: 0.06em;\n  text-transform: uppercase; color: var(--muted);",
    ),
    (
        "  font-size: var(--t-ui); color: var(--ink); font-weight: 700;\n}",
        "  font-size: var(--t-ui); color: var(--ink); font-weight: 600;\n}",
    ),
    # --- 2. the KPI strip is a framed tile grid again ---
    (
        """/* ---------- Hero metric strip ---------- */
/* The same six numbers the cabinet reads, but as one line of evidence: hairlines
   top and bottom, no boxes, and figures wide enough to be the second thing a
   visitor reads after the headline. */
.hero-metrics {
  margin: 0 24px 56px;
  border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  animation: fadeUp 0.4s var(--ease) backwards;
}
.hero-metrics .stats { border-top: 0; border-left: 0; }
.hero-metrics .stat-card { padding: 24px 22px; }
.hero-metrics .stat-card:nth-child(n + 4) { border-bottom: 0; }
.hero-metrics .stat-value { font-size: var(--t-xl); }

/* ---------- Bento Grid: KPI Strip ---------- */
/* Hairline KPI strip: 6 cells separated by 1px borders, mono figures, tight spacing. */
.kpi-strip {
  display: grid; grid-template-columns: repeat(6, 1fr);
  border-bottom: 1px solid var(--border);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  background: var(--border);
  gap: 1px;
  margin: 0 24px 32px;
}
.kpi-cell {
  background: var(--surface);
  padding: 18px 16px;
}""",
        """/* ---------- Hero KPI strip ---------- */
/* The same six numbers the cabinet reads, as one line of evidence: a hairline table
   of six cells, the money face for the figures, the caption face for the labels. The
   cell count is a whole number of rows in every track count this sheet lays out
   (six in six, six in two), because a framed grid that ends in a half row closes its
   own frame around nothing. */
.kpi-strip {
  display: grid; grid-template-columns: repeat(6, 1fr);
  border-top: 1px solid var(--border);
  border-left: 1px solid var(--border);
  margin: 0 24px 40px;
  animation: fadeUp 0.4s var(--ease) backwards;
}
.kpi-cell {
  background: var(--surface);
  padding: 18px 16px;
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}""",
    ),
    (
        """  /* The strip keeps its hairlines but loses the side gutters, and the section
     heads stop pretending two columns fit above a phone. */
  .hero-metrics { margin: 0 0 56px; }
  .hero-metrics .stat-card { padding: 18px 14px; }
  .hero-metrics .stat-value { font-size: var(--t-h2); }""",
        """  /* The strip keeps its hairlines but loses the side gutters, and the section
     heads stop pretending two columns fit above a phone. */
  .kpi-strip { margin: 0 0 40px; }
  .kpi-cell { padding: 14px 12px; }""",
    ),
]

app_pairs = [
    (
        ".tag{display:inline-flex;align-items:center;gap:6px;flex:none;border-radius:0;"
        "border:1px solid var(--border);padding:3px 9px;font-size:var(--t-chip);font-weight:700;",
        ".tag{display:inline-flex;align-items:center;gap:6px;flex:none;border-radius:0;"
        "border:1px solid var(--border);padding:3px 9px;font-size:var(--t-chip);font-weight:600;",
    ),
]

patch(CSS, css_pairs)
patch(APP, app_pairs)
print(f"weights capped at the drawn range and the KPI strip re-framed ({n} rules)")
