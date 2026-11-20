"""Wave 41, stage 4 — the landing's KPI band goes back to the shipped class family.

`.kpi-strip` invented a second vocabulary for an object the site already had: six
figures in a hairline row, painted from `/api/stats`, measured by the framed-grid law
as `.stats`. Two class families for one shape is the thing this repository calls a
number waiting to disagree, so the band is `.hero-metrics > .stats > .stat-card`
again and v2 shows through the styling instead — the square corners, the money face,
the caption voice — which is what the token layer already delivers.
"""

from pathlib import Path

CSS = Path("web/static/style.css")
INDEX = Path("web/static/index.html")

n = 0


def patch(path: Path, pairs: list[tuple[str, str]]) -> None:
    global n
    t = path.read_text(encoding="utf-8-sig")
    had_bom = path.read_bytes().startswith(b"\xef\xbb\xbf")
    for old, new in pairs:
        assert t.count(old) == 1, f"{path.name}: found {t.count(old)}x, expected 1: {old[:90]!r}"
        t = t.replace(old, new, 1)
        n += 1
    out = t.encode("utf-8")
    if had_bom:
        out = b"\xef\xbb\xbf" + out
    path.write_bytes(out)
    assert "\r\n" not in t


KPI_CSS = """/* ---------- Hero KPI strip ---------- */
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
}
.kpi-label {
  font-size: var(--t-micro);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 500;
}
.kpi-val {
  font-family: var(--font-mono);
  font-size: var(--t-h2);
  font-weight: 600;
  margin-top: 5px;
  letter-spacing: -0.03em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}"""

HERO_CSS = """/* ---------- Hero metric strip ---------- */
/* The same six numbers the cabinet reads, but as one line of evidence: hairlines top
   and bottom, square cells, and figures wide enough to be the second thing a visitor
   reads after the headline. Six cells in six tracks is one row, and in two tracks
   three rows — a framed grid never ends in a half row here. */
.hero-metrics {
  margin: 0 24px 56px;
  border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  animation: fadeUp 0.4s var(--ease) backwards;
}
.hero-metrics .stats { border-top: 0; border-left: 0; }
.hero-metrics .stat-card { padding: 20px 18px; }
.hero-metrics .stat-card:nth-child(n + 4) { border-bottom: 0; }
.hero-metrics .stat-value { font-size: var(--t-h2); letter-spacing: -0.03em; }"""

patch(CSS, [
    (KPI_CSS, HERO_CSS),
    (
        """@media (max-width: 1100px) {
  /* Six cells are two rows of three before they are one row too narrow to hold a
     twelve-character figure in the money face. */
  .kpi-strip { grid-template-columns: repeat(3, 1fr); }
}

@media (max-width: 900px) {
  .stats, .steps { grid-template-columns: repeat(2, 1fr); }
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }""",
        """@media (max-width: 900px) {
  .stats, .steps { grid-template-columns: repeat(2, 1fr); }""",
    ),
    (
        """  .kpi-strip { margin: 0 0 40px; }
  .kpi-cell { padding: 14px 12px; }""",
        """  .hero-metrics { margin: 0 0 56px; }
  .hero-metrics .stat-card { padding: 18px 14px; }""",
    ),
])

OLD_MARKUP = """    <section class="kpi-strip reveal" id="metrics" aria-label="Метрики сети">
      <div class="kpi-cell" id="stats">
        <div class="kpi-label">Объём, USDC</div>
        <div class="kpi-val skeleton" id="stat-volume">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Объём за 30 дней</div>
        <div class="kpi-val skeleton" id="stat-vol30">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Пользователей</div>
        <div class="kpi-val skeleton" id="stat-users">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Открытых ставок-пулов</div>
        <div class="kpi-val skeleton" id="stat-markets">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Транзакций</div>
        <div class="kpi-val skeleton" id="stat-tx">–</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">Собрано комиссий, USDC</div>
        <div class="kpi-val skeleton" id="stat-fees">–</div>
      </div>
    </section>"""

NEW_MARKUP = """    <section class="hero-metrics reveal" id="metrics" aria-label="Метрики сети">
      <div class="stats reveal-stagger" id="stats">
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-volume">–</div><div class="stat-label">Объём, USDC</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-vol30">–</div><div class="stat-label">Объём за 30 дней, USDC</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-users">–</div><div class="stat-label">Пользователей</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-markets">–</div><div class="stat-label">Открытых ставок-пулов</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-tx">–</div><div class="stat-label">Транзакций</div></div>
        <div class="stat-card hover-lift"><div class="stat-value skeleton" id="stat-fees">–</div><div class="stat-label">Собрано комиссий, USDC</div></div>
      </div>
    </section>"""

patch(INDEX, [(OLD_MARKUP, NEW_MARKUP)])

print(f"hero band restored to the shipped family ({n} edits)")
