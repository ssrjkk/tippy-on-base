"""Wave 37: the two voices that the label probe found still spelled twice.

.style.css `.gauge-cap` — the one tracked-uppercase rule in the product that named no
font family, so the ring's caption fell back to the interface face while every other
uppercase annotation (`.pal-kind`, `.ws-cur`, `.option-label::after`) is drawn in mono.
.app.html `.stat-label` — weight 400 against the landing's 500: one label voice, two
surfaces, two weights.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(rel: str, encoding: str, pairs) -> None:
    path = ROOT / rel
    text = path.read_text(encoding=encoding)
    for old, new in pairs:
        assert text.count(old) == 1, f"{rel}: {text.count(old)} copies of {old[:48]!r}"
        text = text.replace(old, new)
    # `utf-8-sig` reads the BOM off and does not put it back; the checkout is byte-fixed.
    path.write_bytes(text.encode("utf-8-sig" if encoding == "utf-8-sig" else "utf-8"))


patch(
    "web/static/style.css",
    "utf-8-sig",
    [
        (
            ".gauge-cap {\n"
            "  fill: var(--muted); font-size: var(--t-micro); letter-spacing: 0.08em;\n"
            "  text-transform: uppercase; text-anchor: middle;\n"
            "}",
            ".gauge-cap {\n"
            "  fill: var(--muted); font-family: var(--font-mono); font-variant-numeric: tabular-nums;\n"
            "  font-size: var(--t-micro); letter-spacing: 0.08em;\n"
            "  text-transform: uppercase; text-anchor: middle;\n"
            "}",
        ),
    ],
)

patch(
    "web/static/app.html",
    "utf-8",
    [
        (
            ".stat-label{font-size:var(--t-chip);font-weight:400;color:var(--text3);",
            ".stat-label{font-size:var(--t-chip);font-weight:500;color:var(--text3);",
        ),
    ],
)

print("patched")
