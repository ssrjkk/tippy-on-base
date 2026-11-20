"""Restore the hero canvas pulse alpha: a hex token never took the .replace()
that was supposed to inject it, so the three decorative discs painted fully
opaque and sat on top of the hero headline."""

from pathlib import Path

P = Path("web/static/site.js")
raw = P.read_bytes()
assert b"\r\n" not in raw, "CRLF in site.js"
text = raw.decode("utf-8")
assert not text.startswith("﻿"), "site.js must keep no BOM"

HELPER = """  /* `--accent` resolves to a hex, and a hex ignores the alpha a canvas needs. */
  function rgbTriplet(value) {
    var s = (value || '').trim();
    var short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(s);
    if (short) {
      return [1, 2, 3].map(function (i) { return parseInt(short[i] + short[i], 16); }).join(', ');
    }
    var full = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(s);
    if (full) {
      return [1, 2, 3].map(function (i) { return parseInt(full[i], 16); }).join(', ');
    }
    var rgb = /^rgba?\\(([^)]+)\\)$/i.exec(s);
    if (rgb) {
      return rgb[1].split(',').slice(0, 3).map(function (v) { return v.trim(); }).join(', ');
    }
    return '';
  }

  /* ---------- Hero canvas: geometric grid with accent pulses ---------- */
"""

OLD_HEAD = """  /* ---------- Hero canvas: geometric grid with accent pulses ---------- */
"""

OLD_COLORS = """      var borderColor = style.getPropertyValue('--border').trim() || '#efefef';
      var accentColor = style.getPropertyValue('--accent').trim() || '#0000ff';
"""
NEW_COLORS = """      var borderColor = style.getPropertyValue('--border').trim() || 'rgb(228, 228, 231)';
      var accentRgb = rgbTriplet(style.getPropertyValue('--accent').trim()) || '0, 0, 255';
"""

OLD_FILL = """        ctx.fillStyle = accentColor.replace(')', ', ' + alpha + ')').replace('rgb', 'rgba');
"""
NEW_FILL = """        ctx.fillStyle = 'rgba(' + accentRgb + ', ' + alpha.toFixed(3) + ')';
"""

for old in (OLD_HEAD, OLD_COLORS, OLD_FILL):
    assert text.count(old) == 1, (text.count(old), old[:60])

text = text.replace(OLD_HEAD, HELPER)
text = text.replace(OLD_COLORS, NEW_COLORS)
text = text.replace(OLD_FILL, NEW_FILL)

data = text.encode("utf-8")
assert b"\r\n" not in data
P.write_bytes(data)
print("site.js patched:", len(data), "bytes")
