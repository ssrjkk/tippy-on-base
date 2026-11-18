"""Refresh every image README shows in docs/screenshots/.

    python -X utf8 scripts/make_docs_screenshots.py --only menu
    python -X utf8 scripts/make_docs_screenshots.py --origin http://127.0.0.1:8000 --user-id 9000001

Each PNG is stamped with the surface it pictures and a fingerprint of the source
that surface is built from — the palette tokens, the headings, the button labels.
tests/test_docs.py recomputes that fingerprint from the current tree, so an image
cannot quietly outlive what it shows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screenshots"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STAMP_SURFACE = "tippy-surface"
STAMP_FINGERPRINT = "tippy-fingerprint"

MENU_SHOT = "bot.png"

# The web surfaces, each as the file, the path, the viewport and whether the picture
# is of a signed-in screen. `tests/test_docs.py` measures every PNG against this
# table, so a picture cannot be resized — or shot logged-out — out of what it claims.
WEB_SHOTS = (
    ("website.png", "/", (1440, 812), False),
    ("website-mobile.png", "/", (420, 900), False),
    ("dashboard.png", "/app", (420, 900), True),
)
DEVICE_SCALE = 2

# Which surface each picture shows. The gate walks the directory, so a file here
# that is not on disk — or a picture on disk that is not listed — fails.
SURFACE_OF = {
    "website.png": "landing",
    "website-mobile.png": "landing",
    "dashboard.png": "mini",
    MENU_SHOT: "menu",
}

# The menu mock: Telegram's own chat colours, because the picture is of Telegram's
# chrome around our keyboard, not of our stylesheet.
MENU_BG = "#0f1419"
MENU_BUTTON = "#1e2732"
MENU_BORDER = "#2f3b47"
MENU_TEXT = "#e7e9ea"
MENU_CAPTION = "#8b98a5"
MENU_PAD_X = 24
MENU_PAD_TOP = 22
MENU_CAP_H = 30
MENU_ROW_H = 76
MENU_ROW_GAP = 12
MENU_BTN_GAP = 12
MENU_BTN_W = 200
MENU_PANEL_GAP = 36
MENU_FOOT_H = 64
MENU_PAD_BOTTOM = 24


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _css_block(css: str, selector: str) -> str:
    """The brace group that follows `selector`, matched by counting braces."""
    at = css.find(selector)
    if at < 0:
        raise ValueError(f"no {selector!r} block in style sheet")
    open_at = css.index("{", at)
    depth = 0
    for i in range(open_at, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1 : i]
    raise ValueError(f"unbalanced {selector!r} block")


def palette_tokens(css: str, selectors: tuple[str, ...]) -> list[str]:
    """Every `--name: value` pair in the given blocks, normalised to one line each.

    A declaration is ended at `;` **or** at the end of the block: the last token of a block
    written without its trailing semicolon is still a colour the picture is painted with.
    """
    found: list[str] = []
    for selector in selectors:
        body = _css_block(css, selector)
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", body):
            found.append(f"{name}:{' '.join(value.split())}")
    if not found:
        raise ValueError(f"no custom properties read from {selectors}")
    return found


def _text_of(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", "", fragment).split())


def headings(markup: str) -> list[str]:
    """Heading text in document order, tags and markup noise removed."""
    out: list[str] = []
    for inner in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", markup, re.S | re.I):
        plain = _text_of(inner)
        if plain:
            out.append(plain)
    return out


def tab_labels(app_html: str) -> list[str]:
    """The Mini App's bottom navigation, by the i18n key each tab carries."""
    nav = re.search(r'<nav class="tab-bar".*?</nav>', app_html, re.S)
    if not nav:
        raise ValueError("no .tab-bar in app.html")
    keys = re.findall(r'data-i18n="([a-z_]+)"', nav.group(0))
    if len(keys) < 2:
        raise ValueError(f"tab bar yielded {len(keys)} labels")
    return keys


def menu_panels(lang: str = "ru") -> list[list[list[str]]]:
    """The two keyboards the bot actually builds, each as the rows of labels it paints."""
    from bot.handlers._common import _menu_kb, _more_kb

    return [
        [[btn.text for btn in line] for line in kb.inline_keyboard]
        for kb in (_menu_kb(lang), _more_kb(lang))
    ]


def capture_geometry(surface: str) -> list[str]:
    """The camera of every shot that pictures this surface, as `name@WxH@scale`.

    The tokens and headings say what a picture can show; this says what it was taken at.
    Without it a moved viewport or scale factor is invisible to the stamp, because the law
    that measures the pixels reads the very constant the table just changed.
    """
    rows = sorted(
        f"{name}@{width}x{height}@{DEVICE_SCALE}"
        for name, _path, (width, height), _signed in WEB_SHOTS
        if SURFACE_OF.get(name) == surface
    )
    if not rows:
        raise ValueError(f"no shot in WEB_SHOTS pictures {surface!r}")
    return rows


def surface_payload(surface: str) -> dict:
    if surface == "landing":
        return {
            "tokens": palette_tokens(_read("web/static/style.css"), (":root", '[data-theme="dark"]')),
            "headings": headings(_read("web/static/index.html")),
            "capture": capture_geometry(surface),
        }
    if surface == "mini":
        app_html = _read("web/static/app.html")
        return {
            "tokens": palette_tokens(app_html, (":root", '[data-theme="dark"]')),
            "tabs": tab_labels(app_html),
            "headings": headings(app_html),
            "capture": capture_geometry(surface),
        }
    if surface == "menu":
        panels = menu_panels()
        width, height = menu_canvas(panels)
        return {"panels": panels, "canvas": f"{width}x{height}"}
    raise ValueError(f"unknown surface {surface!r}")


def fingerprint_of(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def fingerprint(surface: str) -> str:
    return fingerprint_of(surface_payload(surface))


def stamp(path: Path, surface: str) -> str:
    """Write the provenance chunks, unless the file already carries exactly them.

    Re-encoding a PNG that needs no change rewrites every byte of it, so `--only menu`
    would leave three untouched pictures dirty in `git status`."""
    from PIL import Image, PngImagePlugin

    wanted = fingerprint(surface)
    image = Image.open(path)
    try:
        image.load()
        if image.info.get(STAMP_SURFACE) == surface and image.info.get(STAMP_FINGERPRINT) == wanted:
            return wanted
        info = PngImagePlugin.PngInfo()
        info.add_text(STAMP_SURFACE, surface)
        info.add_text(STAMP_FINGERPRINT, wanted)
        image.save(path, pnginfo=info)
    finally:
        image.close()
    return wanted


def menu_canvas(panels: list[list[list[str]]]) -> tuple[int, int]:
    """The size the mock is laid out to: two panels side by side, taller one wins."""
    width = 2 * MENU_PAD_X + 2 * (2 * MENU_BTN_W + MENU_BTN_GAP) + MENU_PANEL_GAP
    tallest = max(len(panel) for panel in panels)
    height = (
        MENU_PAD_TOP
        + MENU_CAP_H
        + tallest * MENU_ROW_H
        + (tallest - 1) * MENU_ROW_GAP
        + MENU_FOOT_H
        + MENU_PAD_BOTTOM
    )
    return width, height


def _panel_html(rows: list[list[str]], caption: str) -> str:
    from html import escape

    body = []
    for row in rows:
        cells = "".join(
            f'<div class="btn"><span>{escape(label)}</span></div>' for label in row
        )
        body.append(f'<div class="row">{cells}</div>')
    return (
        f'<section><div class="cap">{escape(caption)}</div>'
        f'<div class="kb">{"".join(body)}</div></section>'
    )


def menu_html(panels: list[list[list[str]]]) -> str:
    from bot.config import BOT_USERNAME

    width, height = menu_canvas(panels)
    style = f"""
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ width: {width}px; height: {height}px; background: {MENU_BG};
      font: 15px/1.25 'Segoe UI', 'Noto Sans', sans-serif; color: {MENU_TEXT}; }}
    .wrap {{ display: flex; gap: {MENU_PANEL_GAP}px; padding: {MENU_PAD_TOP}px {MENU_PAD_X}px 0; }}
    .cap {{ height: {MENU_CAP_H}px; color: {MENU_CAPTION}; font-size: 13px;
      letter-spacing: .12em; text-transform: uppercase; display: flex; align-items: center; }}
    .row {{ display: flex; gap: {MENU_BTN_GAP}px; height: {MENU_ROW_H}px; }}
    .row + .row {{ margin-top: {MENU_ROW_GAP}px; }}
    .btn {{ flex: 1 1 0; width: {MENU_BTN_W}px; background: {MENU_BUTTON};
      border: 1px solid {MENU_BORDER}; border-radius: 10px; display: flex;
      align-items: center; justify-content: center; text-align: center; padding: 0 8px; }}
    .btn span {{ display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
      overflow: hidden; }}
    .foot {{ height: {MENU_FOOT_H}px; display: flex; align-items: center; justify-content: center;
      color: {MENU_CAPTION}; font-size: 14px; }}
    """
    return (
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{style}</style></head>'
        f'<body><div class="wrap">{_panel_html(panels[0], "/MENU")}'
        f'{_panel_html(panels[1], "нажатие «Ещё»")}</div>'
        f'<div class="foot">@{BOT_USERNAME} · клавиатура из bot/handlers/_common.py</div></body></html>'
    )


def _wait_fonts(page) -> None:
    page.evaluate("() => document.fonts.ready.then(() => true)")
    page.wait_for_timeout(350)


def launch(playwright):
    """Bundled Chromium when this machine has the build Playwright pins, the
    installed Chrome otherwise — a docs image must be re-shootable on a laptop
    that only has the browser it shipped with."""
    try:
        return playwright.chromium.launch()
    except Exception as exc:  # noqa: BLE001 - the fallback is the whole point
        print(f"bundled chromium unavailable ({type(exc).__name__}), trying installed Chrome")
        return playwright.chromium.launch(channel="chrome")


def capture(browser, origin: str, session: str) -> None:
    from web.auth import COOKIE_NAME

    host = re.match(r"https?://([^/]+)", origin).group(1)
    for name, path, (width, height), signed_in in WEB_SHOTS:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=DEVICE_SCALE,
            color_scheme="light",
            locale="ru-RU",
        )
        if signed_in:
            context.add_cookies([{"name": COOKIE_NAME, "value": session, "domain": host, "path": "/"}])
        page = context.new_page()
        page.goto(f"{origin}{path}", wait_until="networkidle")
        _wait_fonts(page)
        page.screenshot(path=str(OUT_DIR / name))
        context.close()
        print(f"shot {name} {width * DEVICE_SCALE}x{height * DEVICE_SCALE}")


def shoot_menu(browser) -> None:
    panels = menu_panels()
    width, height = menu_canvas(panels)
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.set_content(menu_html(panels))
    _wait_fonts(page)
    page.screenshot(path=str(OUT_DIR / MENU_SHOT))
    context.close()
    print(f"shot {MENU_SHOT} {width}x{height}")


def _session_for(user_id: int) -> str:
    from web.auth import make_session

    return make_session(user_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="", help="running web origin, e.g. http://127.0.0.1:8000")
    parser.add_argument("--user-id", type=int, default=0, help="demo tg_id to sign a Mini App session with")
    parser.add_argument("--only", choices=("menu", "web", "all"), default="all")
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = launch(playwright)
        try:
            if args.only in ("menu", "all"):
                shoot_menu(browser)
            if args.only in ("web", "all"):
                if not args.origin:
                    parser.error("--origin is required to shoot the web surfaces")
                if not args.user_id:
                    parser.error("--user-id is required: dashboard.png pictures the signed-in Mini App")
                capture(browser, args.origin.rstrip("/"), _session_for(args.user_id))
        finally:
            browser.close()

    for name, surface in SURFACE_OF.items():
        print(f"stamped {name} {surface} {stamp(OUT_DIR / name, surface)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
