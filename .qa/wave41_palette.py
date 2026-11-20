"""Wave 41: port the v2 palette onto the live tokens (style.css + app.html)."""
from pathlib import Path
import re

ROOT = Path(".").resolve()


def patch(rel: str, subs: list[tuple[str, str]], expect: list[str] = ()) -> None:
    p = ROOT / rel
    text = p.read_text(encoding="utf-8-sig")
    for old, new in subs:
        n = text.count(old)
        assert n == 1, f"{rel}: pattern found {n} times, expected 1: {old[:70]!r}"
        text = text.replace(old, new)
    for probe in expect:
        assert probe in text, f"{rel}: expected result missing {probe!r}"
    bom = (ROOT / rel).read_bytes()[:3] == b"\xef\xbb\xbf"
    payload = ("\ufeff" if bom else "") + text
    p.write_bytes(payload.encode("utf-8"))
    if "\n" in text:
        assert "\r\n" not in text, f"{rel}: CRLF introduced"
    print(f"patched {rel} ({len(subs)} substitutions)")


CSS = "web/static/style.css"
patch(
    CSS,
    [
        ("  --danger: #b42318;", "  --danger: #be123c;"),
        ("  --warning: #8a5a00;\n  --warning-bg: rgba(138, 90, 0, 0.08);",
         "  --warning: #a24b08;\n  --warning-bg: rgba(162, 75, 8, 0.10);"),
        ("  --code-bg: #f8f8f8;", "  --code-bg: #f2f2f4;"),
        ("  --radius: 8px;\n  --radius-2xs: 2px;\n  --radius-xs: 4px;\n  --radius-sm: 6px;\n  --radius-lg: 12px;",
         "  --radius: 0;\n  --radius-2xs: 0;\n  --radius-xs: 0;\n  --radius-sm: 0;\n  --radius-lg: 0;"),
        ("  --ease: cubic-bezier(0.2, 0.6, 0.2, 1);", "  --ease: cubic-bezier(0.2, 0, 0, 1);"),
        ("stroke='%236f6f6f'", "stroke='%2352525b'"),
        ("stroke='%239a9a9a'", "stroke='%23a1a1aa'"),
        ("  --t-hero: clamp(40px, 6.6vw, 76px);", "  --t-hero: clamp(34px, 4.4vw, 56px);"),
    ],
    expect=["--radius: 0;", "cubic-bezier(0.2, 0, 0, 1)", "%2352525b", "%23a1a1aa",
            "clamp(34px, 4.4vw, 56px)", "--danger: #fb7185;", "--warning: #fbbf24;"],
)

APP = "web/static/app.html"
MINI_LIGHT_OLD = None
txt = (ROOT / APP).read_text(encoding="utf-8-sig")
m = re.search(r"^:root,\[data-theme=\"light\"\]\{.*?\}$", txt, re.M)
assert m, "mini light palette line not found"
MINI_LIGHT_OLD = m.group(0)
m2 = re.search(r"^\[data-theme=\"dark\"\]\{.*?\}$", txt, re.M)
assert m2, "mini dark palette line not found"
MINI_DARK_OLD = m2.group(0)

MINI_LIGHT_NEW = (
    ':root,[data-theme="light"]{--accent:#0000ff;--accent2:#0000cc;--accent-text:#0000ff;'
    '--blue-soft:rgba(0,0,255,.07);--blue-line:rgba(0,0,255,.35);--bg:#ffffff;--card:#ffffff;'
    '--card2:#f7f7f8;--card3:#eaecef;--border:#e4e4e7;--border-strong:#c8c8ce;--text:#111111;'
    '--text2:#3f3f46;--text3:#52525b;--text4:#6b6b74;--success:#047857;--success-bg:rgba(4,120,87,.08);'
    '--danger:#be123c;--warning:#a24b08;--warning-bg:rgba(162,75,8,.10);--nav-bg:rgba(255,255,255,.92);'
    '--scrim:rgba(17,17,17,.45);--on-accent:#ffffff;--qr-plate:#ffffff;--radius-sm:0;--radius:0;'
    '--radius-lg:0;--pill:9999px;--font-ui:"Schibsted Grotesk","Golos Text","Helvetica Neue",Helvetica,Arial,sans-serif;'
    '--measure:68ch;--font-mono:"Geist Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;'
    '--t-chip:11px;--t-meta:12px;--t-small:13px;--t-ui:14px;--t-lead:15px;--t-body:16px;--t-h4:18px;'
    '--t-h3:20px;--t-h2:24px;--t-h1:28px;--t-xl:32px;--ease:cubic-bezier(.2,0,0,1);'
    '--safe-b:env(safe-area-inset-bottom,0px)}'
)
MINI_DARK_NEW = (
    '[data-theme="dark"]{--accent:#224bff;--accent2:#4266ff;--accent-text:#8f8fff;'
    '--blue-soft:rgba(34,75,255,.14);--blue-line:rgba(117,117,255,.42);--card:#1a1a1e;--card2:#161618;'
    '--card3:#222228;--border:#26262c;--border-strong:#3c3c46;--text:#ffffff;--text2:#c8c8c8;'
    '--text3:#a1a1aa;--text4:#91919b;--success:#34d399;--success-bg:rgba(52,211,153,.14);--danger:#fb7185;'
    '--danger-bg:rgba(251,113,133,.14);--warning:#fbbf24;--warning-bg:rgba(251,191,36,.14);'
    '--nav-bg:rgba(17,17,17,.92);--scrim:rgba(0,0,0,.7);--qr-plate:#1a1a1e;--skeleton-shine:rgba(255,255,255,.06)}'
)
patch(APP, [(MINI_LIGHT_OLD, MINI_LIGHT_NEW), (MINI_DARK_OLD, MINI_DARK_NEW)],
      expect=["--card2:#f7f7f8", "--text4:#6b6b74", "--card2:#161618", "--blue-line:rgba(117,117,255,.42)"])
