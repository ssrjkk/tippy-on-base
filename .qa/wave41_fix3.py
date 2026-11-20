"""Wave 41, stage 6 — BOM back, dead badge variants out, telemetry says three things.

* `web/static/style.css` is one of the twelve tracked files that carry a UTF-8 BOM, and
  an editor-side rewrite dropped it. A missing BOM is invisible in the browser and loud
  in `test_the_documented_bom_inventory_is_the_bom_inventory_of_the_tree`, so it goes back
  first.
* `.badge-no`/`.badge-warn` painted nothing: the status matrix's off-state is a neutral
  grey word, not an error, and no other surface reached for them.
* The telemetry strip dropped its reserve-source cell — the same sentence is written
  under the solvency panel by shipped markup, and a strip that repeats a line below it
  is not a summary, it is an echo.
"""

from pathlib import Path

CSS = Path("web/static/style.css")
INDEX = Path("web/static/index.html")
APPJS = Path("web/static/app.js")

n = 0


def patch(path: Path, pairs: list[tuple[str, str]], want_bom: bool) -> None:
    global n
    t = path.read_bytes().decode("utf-8-sig")
    for old, new in pairs:
        assert t.count(old) == 1, f"{path.name}: found {t.count(old)}x, expected 1: {old[:90]!r}"
        t = t.replace(old, new, 1)
        n += 1
    assert "\r\n" not in t
    out = t.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" + out) if want_bom else out)


patch(CSS, [
    (
        """.badge-yes { background: var(--success-bg); border-color: var(--border); color: var(--success); }
.badge-no { background: transparent; border-color: var(--danger); color: var(--danger); }
.badge-warn { background: var(--warning-bg); border-color: var(--border); color: var(--warning); }""",
        """.badge-yes { background: var(--success-bg); border-color: var(--border); color: var(--success); }""",
    ),
], want_bom=True)

patch(INDEX, [
    (
        """      <span>РЕЗЕРВЫ: <span id="telemetrySource">–</span></span>
      <span>ПОКРЫТИЕ: <span id="telemetryCoverage">–</span></span>""",
        """      <span>ПОКРЫТИЕ: <span id="telemetryCoverage">–</span></span>""",
    ),
], want_bom=False)

patch(APPJS, [
    (
        """function paintReserves(s, source) {
  const src = $("telemetrySource");
  if (src) src.textContent = source;
  const cov = $("telemetryCoverage");""",
        """function paintReserves(s) {
  const cov = $("telemetryCoverage");""",
    ),
    (
        "    paintReserves(s, source);",
        "    paintReserves(s);",
    ),
    (
        """    "Счёт за один HTTP-запрос выключен: эндпоинт отвечает 404, а не 402.");}""",
        """    "Счёт за один HTTP-запрос выключен: эндпоинт отвечает 404, а не 402.");
}""",
    ),
], want_bom=False)

print(f"bom restored and telemetry trimmed ({n} edits)")
