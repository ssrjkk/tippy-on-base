"""Rendered design gates, measured on the deployed site.

`tests/test_design_system.py` checks the stylesheet. This module checks what the
browser actually paints, which is where a design system stops being a list of
rules and becomes a picture: contrast after alpha compositing across every
ancestor, the width of a bar against the number the API returned, the height of
a button under a thumb.

Everything is numeric. A failure is a ratio, a pixel count or a percentage that
does not match the data — never an opinion about the look.

Marked `production`: it needs the live site, and it belongs to
`.github/workflows/prod-health-check.yml`, which installs chromium. Red here
means the site really is broken or the design broke with it.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_design_system import APP_HTML, CSS, mono_class_tokens

pytestmark = pytest.mark.production

# The gate measures whatever surface is deployed; DESIGN_GATE_URL points it at a
# local origin so a design change can be verified before it goes public.
PROD_URL = os.environ.get("DESIGN_GATE_URL", "https://tippy-egi.pages.dev").rstrip("/")
USER_AGENT = "tippy-design-gate/1.0"

# Each context gets its own XFF: the origin trusts the tunnel's loopback peer,
# so without it every CI probe would share one rate-limit bucket.
XFF_POOL = ["203.0.113.201", "203.0.113.202", "203.0.113.203", "203.0.113.204",
            "203.0.113.205", "203.0.113.206", "203.0.113.207", "203.0.113.208"]

MOBILE = (390, 844)
DESKTOP = (1440, 900)

# Every surface one page load each, shared by all the facet gates below.
FACETS = ["landing_phone", "landing_desktop", "market_page", "mini_app", "agent_dashboard"]

MEASURE_JS = """
async ({tokens}) => {
  /* Class names the stylesheet draws in the mono face, passed in per surface:
     the law is "whatever the CSS calls monospaced computes as monospaced with
     tabular digits", and /app has its own stylesheet, so it has its own list. */
  const MONO_TOKENS = tokens;
  const parse = (s) => {
    const m = String(s).match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (fg, bg) => {
    const a = fg[3];
    return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a),
            fg[2] * a + bg[2] * (1 - a), 1];
  };
  const lin = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const lum = (c) => 0.2126 * lin(c[0] / 255) + 0.7152 * lin(c[1] / 255) + 0.0722 * lin(c[2] / 255);
  const ratio = (fg, bg) => {
    const a = lum(fg), b = lum(bg);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  const path = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      const name = node.tagName.toLowerCase();
      const cls = (node.className || '').toString().trim().split(/\\s+/).slice(0, 2).join('.');
      parts.unshift(node.id ? '#' + node.id : (cls ? name + '.' + cls : name));
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  const backgroundUnder = (el) => {
    const chain = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      chain.push(node);
      const bg = parse(cs.backgroundColor);
      if (bg && bg[3] >= 1) break;
      node = node.parentElement;
    }
    let acc = [255, 255, 255, 1];
    for (const n of chain.reverse()) {
      const bg = parse(getComputedStyle(n).backgroundColor);
      if (bg) acc = over(bg, acc);
    }
    return acc;
  };

  const contrast = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let tn;
  while ((tn = walker.nextNode())) {
    const text = (tn.nodeValue || '').trim();
    if (!text) continue;
    const el = tn.parentElement;
    if (!el || el.closest('svg') || el.closest('[disabled]') || el.closest('[aria-hidden="true"]')) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height || !el.offsetParent && el.tagName !== 'BODY') continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) < 0.5) continue;
    const fg = parse(cs.color);
    if (!fg) continue;
    const size = parseFloat(cs.fontSize);
    const weight = parseInt(cs.fontWeight || '400', 10);
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    if (fg[3] < 1) continue;  // translucent type is a deliberate ghost, checked in source
    const bg = backgroundUnder(el);
    const value = ratio(fg.slice(0, 3).concat([1]), bg);
    const floor = large ? 3.0 : 4.5;
    if (value < floor) {
      const key = path(el) + '|' + text.slice(0, 24);
      if (seen.has(key)) continue;
      seen.add(key);
      contrast.push({where: key, text: text.slice(0, 40), size: size,
                     ratio: Number(value.toFixed(2)), floor: floor});
    }
  }

  const numeric = [];
  for (const el of document.querySelectorAll('[class]')) {
    if (!el.classList) continue;
    if (![...el.classList].some((c) => MONO_TOKENS.includes(c))) continue;
    if (!(el.textContent || '').trim()) continue;
    const cs = getComputedStyle(el);
    const mono = /mono/i.test(cs.fontFamily);
    const tabular = (cs.fontVariantNumeric || '').includes('tabular-nums');
    if (!mono || !tabular) {
      numeric.push({where: path(el), text: (el.textContent || '').trim().slice(0, 30),
                    mono: mono, tabular: tabular});
    }
  }

  // A row is a number and a bar. The two have to agree with each other before
  // either is compared with the API, or a stale page would match stale data.
  const optionRows = [];
  for (const row of document.querySelectorAll('.option, .detail-opt, .market-card')) {
    const fill = row.querySelector('.bar-fill, .prob-bar-fill, .detail-opt-bar-fill');
    if (!fill) continue;
    const track = fill.parentElement.getBoundingClientRect();
    const painted = fill.getBoundingClientRect();
    const label = (row.querySelector('.option-label, .detail-opt-label, h3, .m-question') || {}).textContent || '';
    // Same rule as probBars: read the percentage from the element that prints it.
    // A row's own text can carry another number — a threshold in the question, an
    // amount — and comparing that against the bar calls agreement a bug.
    const value = row.querySelector('.option-val, .detail-opt-val, .num') || row;
    const match = /(\\d+(?:[.,]\\d+)?)\\s*%/.exec(value.textContent || '');
    optionRows.push({
      label: label.trim().slice(0, 24),
      textPct: match ? Number(match[1].replace(',', '.')) : null,
      barPct: track.width ? Number((painted.width / track.width * 100).toFixed(2)) : null,
    });
  }

  const controls = [];
  // WCAG 2.5.5 exempts a target whose size is set by the line it sits in. That is
  // a property of the rendering, not of a class name, so it is measured here:
  // an inline link sharing its line with other text or links is sized by the copy.
  const inlineInText = (el) => {
    if (el.tagName !== 'A' || getComputedStyle(el).display !== 'inline') return false;
    const parent = el.parentElement;
    if (!parent) return false;
    return [...parent.childNodes].some((node) => node !== el && (
      (node.nodeType === 3 && node.textContent.trim()) || node.nodeType === 1));
  };
  for (const el of document.querySelectorAll('a, button, input, select, summary, [data-act]')) {
    if (el.closest('[disabled]') || el.classList.contains('m-link') || inlineInText(el)) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    if (getComputedStyle(el).display === 'none') continue;
    if (rect.height < 43.5) {
      controls.push({where: path(el), text: (el.textContent || '').trim().slice(0, 28),
                     height: Number(rect.height.toFixed(1))});
    }
  }

  const bars = [...document.querySelectorAll('.chart-bar')].map((b) => {
    const plot = b.parentElement.getBoundingClientRect();
    const bar = b.getBoundingClientRect();
    return plot.height ? Number((bar.height / plot.height * 100).toFixed(2)) : null;
  });

  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
  };

  // A shimmer is a promise that code will replace it. networkidle has already
  // passed, so a placeholder still on the screen is a container nothing wrote to.
  const skeletons = [...document.querySelectorAll('.skeleton, .skeleton-bar, .skeleton-card')]
    .filter(visible)
    .map((el) => ({where: path(el), text: (el.textContent || '').trim().slice(0, 20)}));

  // Text that runs out of its box and is not marked as running out is a silent
  // truncation: the number on the screen is not the number the ledger holds.
  const clipped = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.overflowX === 'visible' || el.scrollWidth <= el.clientWidth + 1) continue;
    const text = (el.textContent || '').trim();
    if (text.length < 3) continue;
    if (cs.textOverflow === 'ellipsis') continue;
    clipped.push({where: path(el), text: text.slice(0, 30), over: el.scrollWidth - el.clientWidth});
  }

  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((el) => el.tagName);
  const images = [...document.querySelectorAll('img')].map((im) => ({
    alt: im.getAttribute('alt'),
    width: im.getAttribute('width'),
    height: im.getAttribute('height'),
    visible: visible(im),
  }));
  const themeColors = [...document.querySelectorAll('meta[name="theme-color"]')].map(
    (m) => m.media || 'any');

  // A probability row in the Mini App is a label with a number and, under it, a
  // bar: the two are the same fact drawn twice, so they are compared pair by pair.
  const probBars = [];
  for (const group of document.querySelectorAll('.prob-bars')) {
    const labels = [...group.querySelectorAll('.prob-bar-label')];
    const bars = [...group.querySelectorAll('.prob-bar')];
    for (let i = 0; i < Math.min(labels.length, bars.length); i++) {
      // The price is the .num span. The label next to it can carry a number of
      // its own ("Under 60%"), and reading the row's text would compare that
      // threshold against the bar and call the disagreement a bug.
      const num = labels[i].querySelector('.num') || labels[i];
      const match = /(\\d+(?:[.,]\\d+)?)\\s*%/.exec(num.textContent || '');
      const track = bars[i].getBoundingClientRect();
      const fill = bars[i].firstElementChild.getBoundingClientRect();
      probBars.push({
        label: (labels[i].textContent || '').trim().slice(0, 24),
        textPct: match ? Number(match[1].replace(',', '.')) : null,
        barPct: track.width ? Number((fill.width / track.width * 100).toFixed(2)) : null,
      });
    }
  }

  const endpoints = ['/api/volume_history?days=14', '/api/predictions'];
  const marketId = (location.pathname.match(/^\\/m\\/(\\d+)/) || [])[1];
  if (marketId) endpoints.push('/api/market/' + marketId);
  const api = {};
  for (const endpoint of endpoints) {
    try {
      api[endpoint] = await (await fetch(endpoint, {credentials: 'same-origin'})).json();
    } catch (e) {
      api[endpoint] = {error: String(e)};
    }
  }

  const body = document.body.innerText || '';
  return {
    contrast, numeric, controls, bars, optionRows, probBars, api,
    skeletons, clipped, headings, images, themeColors,
    marketId: marketId ? Number(marketId) : null,
    title: document.title,
    theme: document.documentElement.dataset.theme || getComputedStyle(document.body).backgroundColor,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    leaked: ['NaN', 'undefined', 'Infinity', '__'].filter((token) => body.includes(token)),
    placeholders: (body.match(/__[A-Z][A-Z0-9_]*__/g) || []).slice(0, 5),
  };
}
"""

# Two documents, two stylesheets: /app never loads style.css, so judging its
# numbers by the site's class list would check names that do not exist there and
# ignore every class the Mini App actually defines.
MONO_TOKENS = {"site": mono_class_tokens(CSS), "mini": mono_class_tokens(APP_HTML)}


def mono_tokens_for(path: str) -> list[str]:
    return MONO_TOKENS["mini"] if path == "/app" else MONO_TOKENS["site"]


def open_context(browser, *, theme, viewport, xff, touch=False, reduced_motion=None):
    options = {
        "viewport": {"width": viewport[0], "height": viewport[1]},
        "color_scheme": theme,
        "extra_http_headers": {"user-agent": USER_AGENT, "x-forwarded-for": xff},
        "has_touch": touch,
        "is_mobile": touch,
    }
    if reduced_motion:
        options["reduced_motion"] = reduced_motion
    context = browser.new_context(**options)
    # `color_scheme` alone is not enough: the Mini App takes its theme from
    # Telegram's SDK, and outside Telegram that SDK answers "light" whatever the
    # browser prefers. Measured that way, every dark palette in the product was
    # silently tested in light. localStorage is what both surfaces actually read.
    context.add_init_script(
        f"try {{ localStorage.setItem('theme', '{theme}'); }} catch (e) {{}}"
    )
    return context


def measure(browser, path, theme, viewport, xff, touch=False):
    """One page load, all facets of the rendering returned as data.

    Every facet used to have its own test with its own goto, so a check cost the
    site a full page of requests; one load with assertions on the result keeps
    the shared Cloudflare quota intact and makes the numbers comparable.

    Transport failures are counted for this origin only. A blocked or unreachable
    third-party CDN — Google Fonts is one — is a property of the network the
    suite runs on, not of the product, and treating it as a design failure would
    train everyone to ignore the red.

    `playwright` is imported inside the function on purpose: the ordinary CI job
    has no playwright installed and collects this module to deselect it, and a
    module-level import turned that collection into an ImportError.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page_errors = []
    broken = []

    def note_request_failure(request):
        if request.url.startswith(PROD_URL):
            broken.append(f"{request.url} {request.failure}")

    def note_bad_response(response):
        # A refused request is not a broken one: /api/agent/* answers 403 to an
        # anonymous probe by design, and tests/test_production_health.py pins that.
        if (
            response.url.startswith(PROD_URL)
            and response.status >= 400
            and response.status not in (401, 403)
        ):
            broken.append(f"{response.url} HTTP {response.status}")

    context = open_context(browser, theme=theme, viewport=viewport, xff=xff, touch=touch)
    try:
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("requestfailed", note_request_failure)
        page.on("response", note_bad_response)
        response = page.goto(f"{PROD_URL}{path}", wait_until="load")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            # A page that polls — the workspace refetches every 10 s, the Mini App
            # keeps a handshake open — never goes quiet, and waiting forever for it
            # would measure nothing. The document and its first data have landed.
            pass
        assert response is not None and response.status == 200, (
            f"{path} не отдал 200 в {theme}/{viewport[0]}px: {response.status if response else 'нет ответа'}"
        )
        data = page.evaluate(MEASURE_JS, {"tokens": mono_tokens_for(path)})
        data["pageerrors"] = page_errors
        data["brokenrequests"] = broken
        data["status"] = response.status
        return data
    finally:
        context.close()


CACHE: dict = {}


def cached(browser, path, theme, viewport, index, touch=False):
    key = (path, theme, viewport, touch)
    if key not in CACHE:
        CACHE[key] = measure(browser, path, theme, viewport, XFF_POOL[index % len(XFF_POOL)], touch)
    return CACHE[key]


# A mouse never shows a focus ring, so this is measured with real Tab presses:
# element.focus() from script does not match :focus-visible in Chromium, and a
# probe built that way would report a ring wherever there is none.
RING_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return {
    where: el.tagName.toLowerCase() + '.' + (el.className || '').toString().trim().split(/\\s+/)[0],
    width: parseFloat(cs.outlineWidth) || 0,
    style: cs.outlineStyle,
    color: cs.outlineColor,
    shadow: cs.boxShadow || 'none',
    offscreen: rect.width === 0 || rect.height === 0,
  };
}
"""


def paints_a_ring(ring) -> bool:
    """A ring is something a keyboard visitor can see: a style, a width, and a
    colour that is not transparent. `outline: 2px solid transparent` satisfies the
    first two and shows nothing, which is why all three are checked."""
    if ring["style"] in ("none", "hidden"):
        return False
    if ring["shadow"] not in ("", "none"):
        return True
    transparent = ring["color"].startswith("rgba(0, 0, 0, 0)") or ring["color"] == "transparent"
    return ring["width"] >= 2.0 and not transparent

MOTION_JS = """
() => {
  const running = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.animationName === 'none') continue;
    const seconds = parseFloat(cs.animationDuration) * (cs.animationDuration.includes('ms') ? 0.001 : 1);
    if (seconds > 0.002) {
      running.push({where: el.tagName.toLowerCase() + '.' +
                    (el.className || '').toString().trim().split(/\\s+/)[0],
                    name: cs.animationName, duration: cs.animationDuration});
    }
  }
  const hidden = [...document.querySelectorAll('.reveal')].filter((el) => {
    const cs = getComputedStyle(el);
    return parseFloat(cs.opacity) < 0.99 || cs.transform !== 'none';
  }).map((el) => (el.className || '').toString());
  return {running: running.slice(0, 12), runningCount: running.length, hidden: hidden.slice(0, 12)};
}
"""


def walk_keyboard(browser, path, *, theme, viewport, xff, stops=24):
    """Every distinct element a keyboard visitor can land on, with its ring."""
    context = open_context(browser, theme=theme, viewport=viewport, xff=xff)
    try:
        page = context.new_page()
        page.goto(f"{PROD_URL}{path}", wait_until="load")
        page.wait_for_timeout(1500)
        seen, rings = set(), []
        for _ in range(stops):
            page.keyboard.press("Tab")
            ring = page.evaluate(RING_JS)
            if ring is None or ring["where"] in seen or ring["offscreen"]:
                continue
            seen.add(ring["where"])
            rings.append(ring)
        return rings
    finally:
        context.close()


def under_reduced_motion(browser, path, *, theme, viewport, xff):
    context = open_context(browser, theme=theme, viewport=viewport, xff=xff,
                           reduced_motion="reduce")
    try:
        page = context.new_page()
        page.goto(f"{PROD_URL}{path}", wait_until="load")
        page.wait_for_timeout(1500)
        return page.evaluate(MOTION_JS)
    finally:
        context.close()


MINI_TABS = (0, 1, 2, 3)

# The Mini App loads its lists from /api/mini/state, which is session-only, so an
# anonymous probe sees empty tabs. Set DESIGN_GATE_SESSION to a signed
# `tippy_session` value (web.auth.make_session(user_id)) and the walk runs against
# the real screens: markets with their bars, bets, history, the stats grid.
SESSION_COOKIE = os.environ.get("DESIGN_GATE_SESSION", "")


def session_cookie_name() -> str:
    """Read out of web/auth.py instead of importing it: this module is collected on
    a CI runner that has no app config, and a literal copied here would drift."""
    source = (Path(__file__).resolve().parents[1] / "web" / "auth.py").read_text(encoding="utf-8-sig")
    return re.search(r"COOKIE_NAME\s*=\s*[\"']([^\"']+)[\"']", source).group(1)


def walk_mini_app(browser, *, theme, viewport, xff):
    """Every screen of the Mini App, from one page load.

    A tab is `display: none` until a thumb taps it, so measuring /app once looks
    only at the wallet card. The market rows, the bet list and the stats grid —
    where the bars, the money columns and the empty states live — were rendered by
    no gate at all.
    """
    context = open_context(browser, theme=theme, viewport=viewport, xff=xff, touch=True)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if SESSION_COOKIE:
            context.add_cookies([{"name": session_cookie_name(), "value": SESSION_COOKIE, "url": PROD_URL}])
        page = context.new_page()
        page.goto(f"{PROD_URL}/app", wait_until="load")
        page.wait_for_timeout(2500)
        screens = {}
        for tab in MINI_TABS:
            try:
                page.click(f'.tab-item[data-tab="{tab}"]', timeout=4000)
            except PlaywrightTimeoutError:
                # The tab button is gone or renamed. Recorded, not raised: the gate
                # below names the screen the walk could not reach, which is a better
                # failure than a stack from a click.
                pass
            page.wait_for_timeout(1200)
            screens[tab] = page.evaluate(MEASURE_JS, {"tokens": mono_tokens_for("/app")})
            # Without this the walk could tap a dead selector, measure the wallet
            # card four times, and every gate below would pass over one screen.
            screens[tab]["active"] = page.evaluate(
                "() => (document.querySelector('.tab-page.active') || {}).id || '(none)'"
            )
        return screens
    finally:
        context.close()


# Landing, both themes, phone and desktop: four page loads shared by all below.
@pytest.fixture(scope="module")
def landing_phone(browser):
    return cached(browser, "/", "dark", MOBILE, 0)


@pytest.fixture(scope="module")
def landing_desktop(browser):
    return cached(browser, "/", "light", DESKTOP, 1)


@pytest.fixture(scope="module")
def market_page(browser):
    first = None
    try:
        request = urllib.request.Request(
            f"{PROD_URL}/api/predictions", headers={"user-agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.loads(response.read().decode())
        if rows:
            first = rows[0].get("id") or rows[0].get("market_id")
    except (urllib.error.URLError, KeyError, TypeError, json.JSONDecodeError):
        first = None
    if first is None:
        pytest.skip("no market on the site to render — there is nothing to measure")
    return cached(browser, f"/m/{first}", "dark", MOBILE, 2)


@pytest.fixture(scope="module")
def mini_app(browser):
    """/app — the surface AGENTS.md calls primary, measured as such.

    It is opened on a phone with touch on, in the theme the app itself picks, and
    every facet below is asserted on it: the Mini App is where the money is
    actually spent, so a design rule that only holds on the landing page does not
    hold.
    """
    return cached(browser, "/app", "dark", MOBILE, 4, touch=True)


@pytest.fixture(scope="module")
def mini_screens(browser):
    """All four Mini App screens, tapped through in one page load."""
    return walk_mini_app(browser, theme="dark", viewport=MOBILE, xff=XFF_POOL[5])


@pytest.fixture(scope="module")
def agent_dashboard(browser):
    return cached(browser, "/agent", "dark", DESKTOP, 5)


# ---------------------------------------------------------------------------
# contrast
# ---------------------------------------------------------------------------

def test_painted_text_clears_aa_on_the_landing_phone(landing_phone):
    assert landing_phone["contrast"] == [], json.dumps(
        landing_phone["contrast"], ensure_ascii=False
    )


def test_painted_text_clears_aa_on_the_landing_desktop(landing_desktop):
    assert landing_desktop["contrast"] == [], json.dumps(
        landing_desktop["contrast"], ensure_ascii=False
    )


def test_the_contrast_pass_actually_measured_something(landing_phone):
    """A walk that returns no failures because it never found any text would be a
    green test over a blank page."""
    assert len(landing_phone["title"]) > 0
    assert landing_phone["scrollWidth"] > 0
    assert landing_phone["innerWidth"] == MOBILE[0]


# ---------------------------------------------------------------------------
# geometry: no sideways page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facet", FACETS)
def test_no_horizontal_overflow(request, facet):
    data = request.getfixturevalue(facet)
    assert data["scrollWidth"] <= data["innerWidth"] + 1, (
        f"{facet}: документ {data['scrollWidth']}px шире окна {data['innerWidth']}px"
    )


# ---------------------------------------------------------------------------
# touch targets
# ---------------------------------------------------------------------------


def test_every_control_on_the_phone_is_big_enough(browser):
    data = cached(browser, "/", "dark", MOBILE, 3, touch=True)
    assert data["controls"] == [], json.dumps(data["controls"], ensure_ascii=False)


# ---------------------------------------------------------------------------
# numbers stay monospaced and aligned
# ---------------------------------------------------------------------------


def test_painted_money_is_mono_and_tabular(landing_phone, market_page):
    offenders = landing_phone["numeric"] + market_page["numeric"]
    assert offenders == [], json.dumps(offenders, ensure_ascii=False)


# ---------------------------------------------------------------------------
# visualization
# ---------------------------------------------------------------------------


def test_chart_bar_heights_match_the_numbers_they_plot(landing_phone):
    """The bars are the only place a user reads volume from. Their painted height
    has to be the API value scaled by the same maximum the code claims to use —
    otherwise the chart is decoration."""
    data = landing_phone
    series = data["api"]["/api/volume_history?days=14"]
    if isinstance(series, dict) and series.get("error"):
        pytest.fail(f"volume history API failed: {series['error']}")
    if not series:
        assert not data["bars"], "empty series but the chart drew bars"
        return
    assert len(data["bars"]) == len(series), (
        f"API вернул {len(series)} дней, нарисовано {len(data['bars'])} столбцов"
    )
    volumes = [float(item["volume_usdc"]) for item in series]
    maximum = max(max(volumes), 1.0)
    expected = [max(v / maximum * 100, 2.0) for v in volumes]
    for index, (got, want) in enumerate(zip(data["bars"], expected)):
        assert abs(got - want) <= 1.5, (
            f"столбец {index}: нарисовано {got}% высоты, по данным должно {want:.2f}%"
        )


def test_option_bars_are_the_percentages_the_page_prints(market_page):
    """A market row is a bar and a number, the same fact drawn two ways: the
    painted width has to equal the printed percentage, and every option the API
    reports has to appear on the page. The row set is not assumed to be the only
    thing of its kind on the page — a page may repeat a figure elsewhere — so the
    count is checked as coverage, not as equality."""
    record = market_page["api"].get(f"/api/market/{market_page['marketId']}")
    rows = market_page["optionRows"]
    assert rows, "the market page painted no option rows at all"
    assert record and record.get("options"), f"the API gave no record for market {market_page['marketId']}"

    for index, row in enumerate(rows):
        if row["textPct"] is None or row["barPct"] is None:
            continue
        want = max(row["textPct"], 2.0)
        assert abs(row["barPct"] - want) <= 1.5, (
            f"row {index} ({row['label']}): полоса {row['barPct']}%, написано {row['textPct']}%"
        )

    painted = [row["textPct"] for row in rows if row["textPct"] is not None]
    for option in record["options"]:
        price = float(option["probability"])
        assert any(abs(price - shown) <= 0.6 for shown in painted), (
            f"вариант {option['label']} ({price}%) не напечатан ни в одной строке: {painted}"
        )


def test_a_bar_never_paints_an_impossible_number(landing_phone, market_page):
    values = [*landing_phone["bars"], *(r["barPct"] for r in market_page["optionRows"])]
    for value in values:
        assert value is None or 0.0 <= value <= 100.0, f"полоса вне 0..100%: {value}"


# ---------------------------------------------------------------------------
# the page is not quietly broken
# ---------------------------------------------------------------------------


def test_no_placeholder_or_nan_reaches_the_rendered_text(
    landing_phone, landing_desktop, market_page, mini_app, agent_dashboard
):
    for name, data in (
        ("phone", landing_phone),
        ("desktop", landing_desktop),
        ("market", market_page),
        ("app", mini_app),
        ("agent", agent_dashboard),
    ):
        assert not data["placeholders"], f"{name}: подстановка не сработала — {data['placeholders']}"
        assert not data["leaked"], f"{name}: в тексте виден {data['leaked']}"


def test_the_page_broke_nothing_while_loading(landing_phone, landing_desktop):
    for name, data in (("phone", landing_phone), ("desktop", landing_desktop)):
        assert not data["pageerrors"], f"{name}: {data['pageerrors'][:3]}"
        assert not data["brokenrequests"], f"{name}: {data['brokenrequests'][:3]}"


def test_the_landing_paints_a_real_title_not_a_placeholder(landing_phone):
    title = landing_phone["title"]
    assert title and "__" not in title and "undefined" not in title.lower()


# ---------------------------------------------------------------------------
# the other two surfaces: the Mini App and the operator dashboard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facet", ["mini_app", "agent_dashboard"])
def test_painted_text_clears_aa_on_every_surface(request, facet):
    """The landing page is not the product. AGENTS.md calls the Mini App the
    primary surface, and it is the one page the earlier gates never opened."""
    data = request.getfixturevalue(facet)
    assert data["contrast"] == [], json.dumps(data["contrast"], ensure_ascii=False)


def test_every_mini_app_control_is_big_enough(mini_app):
    """The 44 px floor was set for thumbs, and a thumb lives on /app, not on /."""
    assert mini_app["controls"] == [], json.dumps(mini_app["controls"], ensure_ascii=False)


def test_mini_app_numbers_are_mono_and_tabular(mini_app):
    assert mini_app["numeric"] == [], json.dumps(mini_app["numeric"], ensure_ascii=False)


def test_the_mini_app_broke_nothing_of_its_own(mini_app, agent_dashboard):
    """Third-party scripts (the Telegram bridge, the Base App SDK) are the
    network's business; a 404 on this origin or a broken request to it is ours."""
    for name, data in (("app", mini_app), ("agent", agent_dashboard)):
        assert not data["brokenrequests"], f"{name}: {data['brokenrequests'][:3]}"
        assert data["status"] == 200


# ---------------------------------------------------------------------------
# loading states, truncation, document outline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facet", FACETS)
def test_nothing_still_shimmers_when_the_data_has_arrived(request, facet):
    """A skeleton is a promise. If the page has loaded and something is still
    animating a placeholder, that region is code nobody writes into — the visitor
    watches a fake loading state forever and the number is never shown."""
    data = request.getfixturevalue(facet)
    assert data["skeletons"] == [], json.dumps(data["skeletons"], ensure_ascii=False)


@pytest.mark.parametrize("facet", FACETS)
def test_painted_text_is_never_cut_without_a_mark(request, facet):
    """`overflow: hidden` on a money value with no ellipsis deletes digits the
    user has no way to notice are gone."""
    data = request.getfixturevalue(facet)
    assert data["clipped"] == [], json.dumps(data["clipped"], ensure_ascii=False)


@pytest.mark.parametrize("facet", FACETS)
def test_the_document_names_its_own_heading_and_chrome(request, facet):
    data = request.getfixturevalue(facet)
    assert data["headings"].count("H1") == 1, f"{facet}: h1 in {data['headings']}"
    assert set(data["themeColors"]) == {
        "(prefers-color-scheme: light)",
        "(prefers-color-scheme: dark)",
    }, f"{facet}: theme-color metas are {data['themeColors']}"


def test_a_painted_image_has_a_name_and_a_box(landing_phone, mini_app):
    """An image with no dimensions makes the layout jump under a thumb that has
    already aimed at it, and one with no alt is a hole in the screen reader."""
    offenders = []
    for name, data in (("landing", landing_phone), ("app", mini_app)):
        for image in data["images"]:
            if not image["visible"]:
                continue
            alt = (image["alt"] or "").strip()
            if len(alt) < 4 or not image["width"] or not image["height"]:
                offenders.append((name, image))
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# keyboard and motion, measured in the browser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/app"])
def test_every_keyboard_stop_paints_a_ring(browser, path):
    """A focus ring that exists in CSS but is overridden by a component is the
    most common way a keyboard user gets lost, and no source scan can see it."""
    rings = walk_keyboard(browser, path, theme="dark", viewport=MOBILE, xff=XFF_POOL[6])
    ours = [ring for ring in rings if not ring["where"].startswith("iframe.")]
    # An embedded Telegram login widget is the only stop on this list that is not
    # our document: its focus rendering belongs to the widget and no rule here can
    # reach into it.
    assert len(ours) >= 3, f"{path}: the keyboard found {len(ours)} of our stops — nothing to measure"
    invisible = [ring for ring in ours if not paints_a_ring(ring)]
    assert not invisible, json.dumps(invisible, ensure_ascii=False)


def test_reduced_motion_leaves_nothing_invisible(browser):
    """`opacity: 0` until a transition reveals it is how the landing animates in.
    Ask for no motion and a sloppy block leaves the sections blank forever: the
    page looks empty and nothing in the console says why."""
    state = under_reduced_motion(browser, "/", theme="dark", viewport=MOBILE, xff=XFF_POOL[7])
    assert state["hidden"] == [], f"sections stay hidden without motion: {state['hidden']}"
    assert state["runningCount"] == 0, json.dumps(state["running"], ensure_ascii=False)


# ---------------------------------------------------------------------------
# every Mini App screen, not only the one that opens
# ---------------------------------------------------------------------------


def findings(screens, facet):
    """One facet collected per screen, keyed by tab so a failure says where it was."""
    return {f"tab{tab}": value for tab, value in screens.items() if value[facet]}


def test_the_mini_app_walk_actually_changed_screens(mini_screens):
    """Four taps that all landed on the same page would make every gate below green
    over one screen. The walk is only evidence if the active page moved."""
    active = [data["active"] for data in mini_screens.values()]
    assert active == [f"tab{tab}" for tab in MINI_TABS], active


def test_every_mini_app_screen_paints_readable_text(mini_screens):
    bad = findings(mini_screens, "contrast")
    assert not bad, json.dumps(bad, ensure_ascii=False)


def test_every_mini_app_screen_fits_the_phone(mini_screens):
    for tab, data in mini_screens.items():
        assert data["scrollWidth"] <= data["innerWidth"] + 1, (
            f"tab{tab}: документ {data['scrollWidth']}px шире окна {data['innerWidth']}px"
        )


def test_every_mini_app_screen_keeps_its_controls_thumb_sized(mini_screens):
    bad = findings(mini_screens, "controls")
    assert not bad, json.dumps(bad, ensure_ascii=False)


def test_every_mini_app_screen_sets_its_numbers_in_tabular_digits(mini_screens):
    bad = findings(mini_screens, "numeric")
    assert not bad, json.dumps(bad, ensure_ascii=False)


def test_no_mini_app_screen_is_still_loading(mini_screens):
    bad = findings(mini_screens, "skeletons")
    assert not bad, json.dumps(bad, ensure_ascii=False)


def test_no_mini_app_screen_cuts_its_text_without_a_mark(mini_screens):
    bad = findings(mini_screens, "clipped")
    assert not bad, json.dumps(bad, ensure_ascii=False)


def test_mini_app_bars_are_the_percentages_they_print(mini_screens):
    """A probability row is a number and a bar. The bar is decoration the moment
    the two disagree, and a user reads the bar first."""
    if not SESSION_COOKIE:
        pytest.skip(
            "the lists are session data (/api/mini/state): without DESIGN_GATE_SESSION "
            "the tabs render empty and there is no bar to compare with a number"
        )
    checked = 0
    for tab, data in mini_screens.items():
        for index, row in enumerate(data["probBars"]):
            if row["textPct"] is None or row["barPct"] is None:
                continue
            checked += 1
            want = max(row["textPct"], 1.0)  # the template clamps a zero to 1%
            assert abs(row["barPct"] - want) <= 1.5, (
                f"tab{tab} row {index} ({row['label']}): полоса {row['barPct']}%, "
                f"написано {row['textPct']}%"
            )
    assert checked >= 4, (
        f"only {checked} probability rows were measured — the screens are empty, "
        "so this gate proves nothing"
    )


def test_no_mini_app_bar_paints_an_impossible_number(mini_screens):
    for tab, data in mini_screens.items():
        for row in data["probBars"]:
            assert row["barPct"] is None or 0.0 <= row["barPct"] <= 100.0, (
                f"tab{tab}: полоса вне 0..100% — {row}"
            )
