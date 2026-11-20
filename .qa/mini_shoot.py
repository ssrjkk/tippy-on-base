import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from web.auth import make_session
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parent / "mini"
BASE = "http://127.0.0.1:8000"
TABS = {"wallet": 0, "markets": 1, "bets": 2, "more": 3}

with sync_playwright() as p:
    br = p.chromium.launch()
    for theme in ("dark", "light"):
        ctx = br.new_context(viewport={"width": 420, "height": 900}, device_scale_factor=2,
                             is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "tippy_session", "value": make_session(9000001),
                          "url": BASE}])
        pg = ctx.new_page()
        pg.goto(BASE + "/app", wait_until="networkidle")
        pg.evaluate("([t]) => localStorage.setItem('theme', t)", [theme])
        pg.goto(BASE + "/app", wait_until="networkidle")
        pg.wait_for_timeout(900)
        for label, idx in TABS.items():
            pg.click(f'.tab-item[data-tab="{idx}"]')
            pg.wait_for_timeout(650)
            pg.screenshot(path=str(OUT / f"{theme}-{label}.png"))
            print("shot", theme, label)
        ctx.close()
    br.close()
