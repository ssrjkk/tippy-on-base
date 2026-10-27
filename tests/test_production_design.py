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
import urllib.error
import urllib.request

import pytest
from test_design_system import mono_class_tokens

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

MEASURE_JS = """
async () => {
  /* Class names the stylesheet draws in the mono face, injected by
     measure(): the law is "whatever the CSS calls monospaced computes as
     monospaced with tabular digits", so the list cannot drift from the source. */
  const MONO_TOKENS = %TOKENS%;
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
    const match = /(\\d+(?:[.,]\\d+)?)\\s*%/.exec(row.textContent || '');
    optionRows.push({
      label: label.trim().slice(0, 24),
      textPct: match ? Number(match[1].replace(',', '.')) : null,
      barPct: track.width ? Number((painted.width / track.width * 100).toFixed(2)) : null,
    });
  }

  const controls = [];
  for (const el of document.querySelectorAll('a, button, input, select, summary, [data-act]')) {
    if (el.closest('[disabled]') || el.classList.contains('m-link')) continue;
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
    contrast, numeric, controls, bars, optionRows, api,
    marketId: marketId ? Number(marketId) : null,
    title: document.title,
    theme: document.documentElement.dataset.theme || getComputedStyle(document.body).backgroundColor,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    leaked: ['NaN', 'undefined', 'Infinity', '__'].filter((token) => body.includes(token)),
    placeholders: (body.match(/__[A-Z][A-Z0-9_]*__/g) || []).slice(0, 5),
  };
}
""".replace("%TOKENS%", json.dumps(mono_class_tokens()))


def open_context(browser, *, theme, viewport, xff, touch=False):
    context = browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        color_scheme=theme,
        extra_http_headers={"user-agent": USER_AGENT, "x-forwarded-for": xff},
        has_touch=touch,
        is_mobile=touch,
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
    """
    page_errors = []
    broken = []

    def note_request_failure(request):
        if request.url.startswith(PROD_URL):
            broken.append(f"{request.url} {request.failure}")

    def note_bad_response(response):
        if response.url.startswith(PROD_URL) and response.status >= 400:
            broken.append(f"{response.url} HTTP {response.status}")

    context = open_context(browser, theme=theme, viewport=viewport, xff=xff, touch=touch)
    try:
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("requestfailed", note_request_failure)
        page.on("response", note_bad_response)
        response = page.goto(f"{PROD_URL}{path}", wait_until="networkidle")
        assert response is not None and response.status == 200, (
            f"{path} не отдал 200 в {theme}/{viewport[0]}px: {response.status if response else 'нет ответа'}"
        )
        data = page.evaluate(MEASURE_JS)
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


@pytest.mark.parametrize("facet", ["landing_phone", "landing_desktop", "market_page"])
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


def test_no_placeholder_or_nan_reaches_the_rendered_text(landing_phone, landing_desktop, market_page):
    for name, data in (
        ("phone", landing_phone),
        ("desktop", landing_desktop),
        ("market", market_page),
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
