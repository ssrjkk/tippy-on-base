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
from decimal import Decimal
from pathlib import Path

import pytest
from test_design_system import (
    APP_HTML,
    BADGE_FAMILIES,
    CSS,
    LIGHT,
    MINI_LIGHT,
    mono_class_tokens,
    resolve,
    state_vocabulary,
)

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

# Every surface, one page load each, shared by all the facet gates below. The
# list is the site's own inventory, not the pages that were convenient when the
# first gate was written: `/tos` used to be assembled in a Python f-string and
# was therefore painted by no check at all until its own defects were measured.
FACETS = ["landing_phone", "landing_desktop", "market_page", "mini_app", "agent_dashboard",
          "tos_page", "user_page", "oc_market_page", "dead_end_page"]

# The subset opened under a coarse pointer. The 44 px floor is a touch rule and
# lives in `@media (pointer: coarse)`, so a facet measured with a mouse cannot
# answer it: the browser reports the fine pointer, the CSS never grows the
# target, and the walk would be judging the desktop layout against a thumb.
TOUCH_FACETS = ["landing_touch", "mini_app", "tos_page", "user_page",
                "oc_market_page", "dead_end_page"]

MEASURE_JS = """
async ({tokens, sizes}) => {
  /* Class names the stylesheet draws in the mono face, passed in per surface:
     the law is "whatever the CSS calls monospaced computes as monospaced with
     tabular digits", and /app has its own stylesheet, so it has its own list. */
  const MONO_TOKENS = tokens;
  /* Every size the type scale can paint at this viewport, computed in Python from
     the tokens of the stylesheet serving this page. */
  const SIZES = sizes;
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

  /* Every size the browser really painted. The source gate can only read what a
     stylesheet says; this reads what the cascade resolves, so a size arriving from
     an inherited `em`, from a UA default no reset covers, or from an inline style
     has nowhere to hide. */
  const typeScale = [];
  const scaleSeen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    const direct = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!direct) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const size = Math.round(parseFloat(cs.fontSize) * 100) / 100;
    if (SIZES.some((step) => Math.abs(step - size) < 0.01)) continue;
    const key = path(el) + '|' + size;
    if (scaleSeen.has(key)) continue;
    scaleSeen.add(key);
    typeScale.push({where: path(el), size: size, text: (el.textContent || '').trim().slice(0, 28)});
  }

  /* A section the reveal observer never reached is `opacity: 0`, and every law
     above skips transparent pixels — so an unscrolled walk measures the hero and
     calls that the page. This counts what the walk left unseen; the gate under it
     refuses to let the other laws go blind. A section the page itself hides
     because it has no data paints nothing for a reason the reader can see, and
     the source gate `test_every_element_that_ships_hidden_can_appear` owns that
     case; a section left transparent while nothing hides it is this law's case. */
  const unrevealed = [...document.querySelectorAll('.reveal')]
    .filter((el) => parseFloat(getComputedStyle(el).opacity) < 0.99)
    .filter((el) => !el.hidden && !el.closest('[hidden]'))
    .map((el) => (el.id || (el.className || '').toString().trim().split(/\\s+/)[0])
                  + '@' + Math.round(parseFloat(getComputedStyle(el).opacity) * 100) / 100);

  const body = document.body.innerText || '';
  return {
    contrast, numeric, controls, bars, optionRows, probBars, api,
    skeletons, clipped, headings, images, themeColors, typeScale, unrevealed,
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


FIXED_STEP_RE = re.compile(r"(\d+(?:\.\d+)?)px")
FLUID_STEP_RE = re.compile(r"clamp\(\s*(\d+(?:\.\d+)?)px\s*,\s*([\d.]+)vw\s*,\s*(\d+(?:\.\d+)?)px\s*\)")


def scale_sizes(tokens: dict[str, str], width: int) -> list[float]:
    """Every size the type scale can paint in one stylesheet at this viewport.

    The twelve fixed steps are px literals; the two display sizes are `clamp()`, so
    their answer belongs to the window — evaluated here rather than hard-coded, which
    is what lets the same law hold a 390 px phone (40 and 30) and a 1440 px desktop
    (76 and 44) without either one being written into the test twice."""
    sizes = []
    for name in tokens:
        if not name.startswith("--t-"):
            continue
        value = resolve(tokens, name)
        fixed = FIXED_STEP_RE.fullmatch(value)
        if fixed:
            sizes.append(float(fixed.group(1)))
            continue
        fluid = FLUID_STEP_RE.fullmatch(value)
        if fluid:
            low, per_vw, high = (float(fluid.group(1)), float(fluid.group(2)), float(fluid.group(3)))
            sizes.append(max(low, min(high, per_vw * width / 100)))
    assert sizes, "no --t-* step resolved, so the type-scale law would measure nothing"
    return sorted(sizes)


def measure_arguments(path: str, width: int) -> dict:
    """What one surface's walk needs: the mono classes and the type scale of the
    stylesheet that actually serves it. `/app` ships its own of both."""
    tokens = MINI_LIGHT if path == "/app" else LIGHT
    return {"tokens": mono_tokens_for(path), "sizes": scale_sizes(tokens, width)}


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


# The webfonts the stylesheets ask for are the only thing these gates wait for,
# and `document.fonts.ready` settles on success and on failure alike. Raced
# against 3 s because a socket the local proxy has stalled never errors: the
# fallback the page falls back to is what a visitor on that same network reads.
FONTS_SETTLED_JS = """
async () => await Promise.race([
  document.fonts.ready,
  new Promise((resolve) => setTimeout(resolve, 3000)),
])
"""


def go(page, path: str):
    """Navigate to one page of the origin and stop when the page itself is
    measurable — not when its third-party requests finish.

    `wait_until="load"` made every rendered facet a hostage of fonts.gstatic.com
    and of the Telegram SDK script the Mini App pulls: on a network that cannot
    reach either, `load` does not fire, and a whole module of design checks died
    on a 30 s navigation timeout that said nothing about the design. A parsed
    document with its fonts settled is the readiness these gates actually read;
    the data still arrives under the bounded `networkidle`/`wait_for_timeout` the
    callers already keep, because a `fetch` never blocked `load` in the first
    place.
    """
    response = page.goto(f"{PROD_URL}{path}", wait_until="domcontentloaded")
    page.evaluate(FONTS_SETTLED_JS)
    return response


def measure(browser, path, theme, viewport, xff, touch=False, expect=200):
    """One page load, all facets of the rendering returned as data.

    Every facet used to have its own test with its own goto, so a check cost the
    site a full page of requests; one load with assertions on the result keeps
    the shared Cloudflare quota intact and makes the numbers comparable.

    Transport failures are counted for this origin only. A blocked or unreachable
    third-party CDN — Google Fonts is one — is a property of the network the
    suite runs on, not of the product, and treating it as a design failure would
    train everyone to ignore the red.

    `expect` is the answer this path is supposed to give. The dead-end page is the
    first surface whose own status is 404, and without it the walk would report
    the document it was sent to read as a broken request.

    `playwright` is imported inside the function on purpose: the ordinary CI job
    has no playwright installed and collects this module to deselect it, and a
    module-level import turned that collection into an ImportError.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page_errors = []
    broken = []
    target = f"{PROD_URL}{path}"

    def note_request_failure(request):
        if request.url.startswith(PROD_URL) and request.url != target:
            broken.append(f"{request.url} {request.failure}")

    def note_bad_response(response):
        # A refused request is not a broken one: /api/agent/* answers 403 to an
        # anonymous probe by design, and tests/test_production_health.py pins that.
        if (
            response.url.startswith(PROD_URL)
            and response.status >= 400
            and response.status not in (401, 403)
            and not (response.url == target and response.status == expect)
        ):
            broken.append(f"{response.url} HTTP {response.status}")

    context = open_context(browser, theme=theme, viewport=viewport, xff=xff, touch=touch)
    try:
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("requestfailed", note_request_failure)
        page.on("response", note_bad_response)
        response = go(page, path)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            # A page that polls — the workspace refetches every 10 s, the Mini App
            # keeps a handshake open — never goes quiet, and waiting forever for it
            # would measure nothing. The document and its first data have landed.
            pass
        assert response is not None and response.status == expect, (
            f"{path} не отдал {expect} в {theme}/{viewport[0]}px: "
            f"{response.status if response else 'нет ответа'}"
        )
        page.evaluate(REVEAL_ALL_JS)
        data = page.evaluate(MEASURE_JS, measure_arguments(path, viewport[0]))
        data["states"] = page.evaluate(STATE_ENUM_JS)
        data["pageerrors"] = page_errors
        data["brokenrequests"] = broken
        data["status"] = response.status
        return data
    finally:
        context.close()


CACHE: dict = {}


# The landing animates its sections in with an IntersectionObserver, and the
# observer unobserves each element the first time it appears: a page measured at
# scroll 0 keeps nine of its thirteen `.reveal` sections at `opacity: 0`, and
# every law in MEASURE_JS skips transparent pixels. Scrolling through the document
# is what a reader does, so it is what a measurement has to do too — the walk
# below is the only reason the contrast, mono, touch-target and clipping laws can
# see the leaderboard at all.
REVEAL_ALL_JS = """async () => {
  // `html { scroll-behavior: smooth }` retargets every scrollTo, so a loop that
  // steps faster than the animation is allowed to travel never reaches the bottom:
  // measured, nine sections stayed transparent with the animation left on. The walk
  // turns it off, walks, and hands the document back as it found it. The height is
  // re-read on each step because the landing fills its market rows after load.
  const html = document.documentElement;
  const was = html.style.scrollBehavior;
  html.style.scrollBehavior = 'auto';
  const step = Math.max(240, Math.floor(window.innerHeight * 0.6));
  for (let y = 0; y < html.scrollHeight; y += step) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 120));
  }
  window.scrollTo(0, 0);
  html.style.scrollBehavior = was;
  await new Promise((r) => setTimeout(r, 700));
}"""


# The badge families come from the same map the source gate walks, so «both
# families» means the same thing in both layers.
BADGE_CLASS = re.compile(rf"\b({'|'.join(BADGE_FAMILIES)})-([a-z]+)\b")


# The state a market is in is derived once, in `web/static/chain.js`, and the
# defect it replaced was visible on three surfaces at once: a pool past its
# deadline still read «открыт» with «дедлайн прошёл» in the same line. Only a
# browser can answer the two questions this leaves — is the shared source really
# the one loaded, and does its answer survive the shapes the API actually returns
# (a `status`, two booleans from the contract, a clock already passed) — so the
# enum is walked in each page's own scope. The rows are inputs, not data: no
# market has to be in a given state on the site for the law to be measured.
STATE_ENUM_JS = """
() => {
  const chain = !!document.querySelector('script[src="/chain.js"]');
  if (typeof marketState !== "function") return { chain: chain, derived: false, painted: false, rows: [] };
  const now = Math.floor(Date.now() / 1000);
  const day = 86400;
  const cases = [
    ["open, deadline ahead", { status: "open", close_at: now + day }],
    ["open, deadline passed", { status: "open", close_at: now - day }],
    ["open, the server says expired", { status: "open", close_at: now + day, expired: true }],
    ["open, no clock at all", { status: "open" }],
    ["resolved after its deadline", { status: "resolved", close_at: now - day }],
    ["cancelled before its deadline", { status: "cancelled", close_at: now + day }],
    ["contract flags it resolved", { resolved: true }],
    ["contract flags it cancelled", { cancelled: true }],
    ["contract flags it disputed", { resolved: true, disputed: true, close_at: now + day }],
    ["a row carrying only a passed clock", { close_at: now - day }],
  ];
  // Each surface names its own badge painter; the probe tries what is in scope
  // rather than being told which page it is reading. A map declared inside a
  // render function is not in scope, and `painted: false` says so honestly.
  let painter = null;
  try {
    painter =
      typeof statusBadge === "function" ? (m) => statusBadge(m) :
      typeof statusTag === "function" ? (m) => statusTag(m) :
      typeof CHIP === "object" && CHIP ? (m) => CHIP[marketState(m)] || "" :
      typeof STATUS === "object" && STATUS ? (m) => STATUS[marketState(m)] || "" :
      null;
  } catch (e) { painter = null; }
  return {
    chain: chain,
    derived: true,
    painted: painter !== null,
    rows: cases.map(([label, row]) => ({
      label: label,
      state: marketState(row),
      line: typeof deadlineLine === "function" ? deadlineLine(row) : null,
      chip: painter ? painter(row) : null,
      past: row.close_at ? row.close_at * 1000 <= Date.now() : false,
    })),
  };
}
"""

# What each of those ten rows has to answer. The two that used to lie are the
# third and the last: `expired` is the server's own word for a pool nobody has
# resolved, and a row whose only field is a clock in the past is the same market.
STATE_ANSWERS = {
    "open, deadline ahead": "open",
    "open, deadline passed": "awaiting",
    "open, the server says expired": "awaiting",
    "open, no clock at all": "open",
    "resolved after its deadline": "resolved",
    "cancelled before its deadline": "cancelled",
    "contract flags it resolved": "resolved",
    "contract flags it cancelled": "cancelled",
    "contract flags it disputed": "resolved",
    "a row carrying only a passed clock": "awaiting",
}


def cached(browser, path, theme, viewport, index, touch=False, expect=200):
    key = (path, theme, viewport, touch, expect)
    if key not in CACHE:
        CACHE[key] = measure(browser, path, theme, viewport,
                             XFF_POOL[index % len(XFF_POOL)], touch, expect)
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
    """A ring is something a keyboard visitor can see: an outline with a style, a
    width of at least 2 px, and a colour that is not transparent — `outline: 2px
    solid transparent` satisfies the first two and shows nothing, which is why all
    three are checked.

    A `box-shadow` is deliberately not accepted as a ring any more. The landing's
    fields answered `outline: none` with a 3 px halo of the brand blue at 16 %
    alpha, which composites to 1.11:1 on the dark canvas against the 3:1 every
    other indicator on this site is held to; and read a moment after a real Tab
    press the same shadow is `rgba(0, 0, 0, 0) 0px 0px 0px 0px` mid-transition,
    which paints nothing at all. A tint that only a probe can see is not a ring."""
    if ring["style"] in ("none", "hidden"):
        return False
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
        go(page, path)
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
        go(page, path)
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
        go(page, "/app")
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
            screens[tab] = page.evaluate(MEASURE_JS, measure_arguments("/app", viewport[0]))
            # Without this the walk could tap a dead selector, measure the wallet
            # card four times, and every gate below would pass over one screen.
            screens[tab]["active"] = page.evaluate(
                "() => (document.querySelector('.tab-page.active') || {}).id || '(none)'"
            )
        return screens
    finally:
        context.close()


def api_rows(endpoint: str) -> list:
    """Rows of one public list on the surface under test.

    An empty list is the answer for an unreachable site, a non-list body and a
    database with nothing in it, and every caller turns it into a skip with that
    reason printed: a facet that needs data is not allowed to fail because the
    network or the demo site is quiet."""
    try:
        request = urllib.request.Request(f"{PROD_URL}{endpoint}", headers={"user-agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.loads(response.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError):
        return []
    return rows if isinstance(rows, list) else []


# One page load per surface, shared by every gate below: the landing on a phone
# and on a desktop, a market page, the terms, a profile, an on-chain market, the
# dead end, the Mini App and the operator dashboard.
@pytest.fixture(scope="module")
def landing_phone(browser):
    return cached(browser, "/", "dark", MOBILE, 0)


@pytest.fixture(scope="module")
def landing_desktop(browser):
    return cached(browser, "/", "light", DESKTOP, 1)


@pytest.fixture(scope="module")
def market_page(browser):
    rows = [row for row in api_rows("/api/predictions") if isinstance(row, dict)]
    first = (rows[0].get("id") or rows[0].get("market_id")) if rows else None
    if first is None:
        pytest.skip("no market on the site to render — there is nothing to measure")
    return cached(browser, f"/m/{first}", "dark", MOBILE, 2)


@pytest.fixture(scope="module")
def tos_page(browser):
    """The terms, as served. Everything the site asks a user to agree to before
    the first deposit was assembled in a Python f-string and measured by nothing:
    no webfont (so Helvetica), no theme-color check, and a favicon whose attribute
    the browser closed early. A phone in daylight is where it is read."""
    return cached(browser, "/tos", "light", MOBILE, 3, touch=True)


@pytest.fixture(scope="module")
def user_page(browser):
    """/u/{tg_id} — the page a tip recipient is linked to, and the only surface
    that answers 200 whether or not the person has ever used the bot."""
    return cached(browser, "/u/777777777", "dark", MOBILE, 4, touch=True)


@pytest.fixture(scope="module")
def oc_market_page(browser):
    """The on-chain market page — a different template (oc.html), a different
    currency (USDC shares) and the only surface with an external explorer link."""
    rows = [row for row in api_rows("/api/onchain/markets") if isinstance(row, dict)]
    first = rows[0].get("id") if rows else None
    if first is None:
        pytest.skip("no on-chain market on the site to render")
    return cached(browser, f"/m/oc/{first}", "dark", MOBILE, 5, touch=True)


@pytest.fixture(scope="module")
def dead_end_page(browser):
    """The 404 document, which `web/server.py` builds for any unknown path.

    It is the only page a visitor reaches through a typo or a stale bookmark, and
    the only one whose own status is not 200 — so it is measured with `expect=404`
    and still walked by every gate below."""
    return cached(browser, "/no-such-page-9e1", "dark", MOBILE, 6, touch=True, expect=404)


@pytest.fixture(scope="module")
def landing_touch(browser):
    """The landing under a thumb — the same document as `landing_phone`, with the
    coarse pointer that makes the 44 px floor apply."""
    return cached(browser, "/", "dark", MOBILE, 3, touch=True)


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

# `test_painted_text_clears_aa_on_every_surface` walks the whole FACETS list; the
# gate here only proves that walk was not green over a blank page.

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


@pytest.mark.parametrize("facet", TOUCH_FACETS)
def test_every_control_on_the_phone_is_big_enough(request, facet):
    """The 44 px floor was set for thumbs, and a thumb lives on every page a phone
    opens — not only on the landing and on /app. Mini App first, because AGENTS.md
    calls it the primary surface and it is where the money is actually spent."""
    data = request.getfixturevalue(facet)
    assert data["controls"] == [], json.dumps(data["controls"], ensure_ascii=False)


# ---------------------------------------------------------------------------
# numbers stay monospaced and aligned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facet", FACETS)
def test_painted_money_is_mono_and_tabular(request, facet):
    """"mono implies tabular digits", measured on the pixels of every surface. The
    class list comes from the stylesheet that serves the page — the Mini App has
    its own — so `mono_tokens_for()` is what makes this one gate cover both."""
    data = request.getfixturevalue(facet)
    assert data["numeric"] == [], json.dumps(data["numeric"], ensure_ascii=False)


def assert_on_the_scale(probe: dict, surface: str) -> None:
    """No painted text size that the scale cannot account for.

    The source gate reads what a stylesheet declares; this reads what the cascade
    resolved. The difference is how the defect that named this law arrived: the
    landing's heading reset stopped after colour, so four panel titles in
    `index.html` took their size from the browser's own sheet and painted at
    18.72 px — a number no token holds, between `--t-h4` and `--t-h3`, visible in
    the screenshot and invisible to anyone diffing CSS. `em` inheritance and inline
    styles land in the same trap, and only a rendered walk can see them."""
    offenders = probe["typeScale"]
    assert offenders == [], f"{surface}: {json.dumps(offenders, ensure_ascii=False)}"


@pytest.mark.parametrize("facet", FACETS)
def test_no_painted_text_sits_off_the_type_scale(request, facet):
    """Every facet, both themes where the fixture pair runs them: the set of sizes
    a real browser paints has to be a subset of the sizes the tokens describe."""
    assert_on_the_scale(request.getfixturevalue(facet), facet)


@pytest.mark.parametrize("facet", FACETS)
def test_the_walk_saw_every_section_the_page_hides(request, facet):
    """The law that keeps the other laws honest. A section the reveal observer
    never reached paints nothing, and a probe that skips transparent pixels reports
    a clean page — so `contrast`, `numeric`, `controls` and `clipped` used to
    measure the hero of the landing and call the document verified, while nine of
    the thirteen `.reveal` sections below the fold had been read by nothing. The
    walk scrolls the whole document first; this asserts it worked."""
    data = request.getfixturevalue(facet)
    assert data["unrevealed"] == [], f"{facet}: still transparent — {data['unrevealed']}"


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
    volumes = [float(item["volume_usdc"]) for item in series or []]
    nonzero = [value for value in volumes if value > 0]
    if not nonzero:
        assert not data["bars"], "каждый день нулевой, а график нарисовал столбцы"
        return
    assert len(data["bars"]) == len(nonzero), (
        f"дней с объёмом {len(nonzero)}, нарисовано столбцов {len(data['bars'])}"
    )
    maximum = max(nonzero)
    expected = [max(value / maximum * 100, 2.0) for value in nonzero]
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
# the market state, as a browser answers it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facet", FACETS)
def test_the_state_a_page_reads_is_the_shared_derivation(request, facet):
    """`marketState` lives in one classic script, so it is in scope on exactly the
    documents that ship the tag and on none other. The two answers disagreeing
    means a page carries its own copy — which is the shape the site shipped while
    the landing hero, `/m/{id}` and `/oc/{id}` each read whichever of `status`,
    `expired` and `close_at` they happened to have, and called one expired market
    «открыт» in the same line as «дедлайн прошёл»."""
    states = request.getfixturevalue(facet)["states"]
    assert states["chain"] == states["derived"], (
        f"{facet}: "
        + ("names a market state without loading /chain.js"
           if states["derived"] else "loads /chain.js yet has no marketState in scope")
    )


@pytest.mark.parametrize("facet", FACETS)
def test_a_market_state_never_contradicts_its_own_clock(request, facet):
    """Ten shapes the API really returns — a `status`, the two booleans the
    contract sets, a server `expired`, a clock already passed, a row with nothing
    but a passed clock — walked in the page's own scope, where a fallback the
    Python side cannot see would show up as a different word. Two invariants hold
    on every one of them: a clock in the past never answers `open`, and a market
    that is not `open` never prints a countdown."""
    states = request.getfixturevalue(facet)["states"]
    if not states["derived"]:
        pytest.skip(f"{facet} loads no derivation, so it has no state to contradict")
    rows = {row["label"]: row for row in states["rows"]}
    assert set(rows) == set(STATE_ANSWERS), (
        f"{facet}: the enum walked {sorted(rows)} — not the shapes this law measures"
    )
    wrong = [
        f"{label}: ответ {rows[label]['state']}, ожидалось {want}"
        for label, want in STATE_ANSWERS.items()
        if rows[label]["state"] != want
    ]
    assert not wrong, f"{facet}: " + "; ".join(wrong)
    contradicting = [
        f"{row['label']} → {row['state']}, строка «{row['line']}»"
        for row in states["rows"]
        if (row["past"] and row["state"] == "open")
        or (row["state"] != "open" and row["line"] != "")
    ]
    assert not contradicting, f"{facet}: дедлайн и состояние разошлись — {contradicting}"


@pytest.mark.parametrize("facet", FACETS)
def test_the_badge_names_the_state_the_derivation_answers(request, facet):
    """A chip is the same answer written in markup. Where the page's own scope
    holds the badge map, every row has to be painted with the class of that row's
    state: a map that still says `open` for a market the derivation moved to
    `awaiting` is the drift the four surfaces fell into, and the class is what the
    stylesheet's colour hangs on."""
    states = request.getfixturevalue(facet)["states"]
    if not states["painted"]:
        pytest.skip(f"{facet} keeps its badge map inside a render function")
    mismatched = []
    for row in states["rows"]:
        match = BADGE_CLASS.search(row["chip"] or "")
        if match is None or match.group(2) != row["state"]:
            mismatched.append(f"{row['label']} → {row['state']} нарисовано как {row['chip']}")
    assert not mismatched, f"{facet}: " + "; ".join(mismatched)


def test_the_states_the_browser_answers_are_the_states_the_contract_defines(request):
    """One vocabulary, three independent witnesses: the states the DDL allows plus
    the deadline the shared derivation adds, the answers the rendered rows really
    produced, and the badge classes each family painted on a real page. A state
    nothing paints is a market shown with no chip; a chip painted for a state no
    row answers is a colour waiting for a bug that has not arrived yet.

    A facet whose page has no data to render is not a pass and not a failure — it
    is nothing — so it is left out of the walk rather than skipping the law, which
    is how this test kept a green board while measuring no surface at all."""
    vocabulary = state_vocabulary()
    answered: set[str] = set()
    painted: dict[str, set[str]] = {}
    for name in FACETS:
        try:
            states = request.getfixturevalue(name)["states"]
        except pytest.skip.Exception:
            continue
        answered |= {row["state"] for row in states["rows"]}
        for row in states["rows"]:
            match = BADGE_CLASS.search(row["chip"] or "")
            if match:
                painted.setdefault(match.group(1), set()).add(match.group(2))
    assert set(STATE_ANSWERS.values()) == vocabulary, sorted(
        STATE_ANSWERS.values() ^ vocabulary)
    assert answered, "no facet in the run derived a market state: this law measured nothing"
    assert answered == vocabulary, sorted(answered ^ vocabulary)
    assert set(painted) == set(BADGE_FAMILIES), (
        f"the run painted {sorted(painted)}, the stylesheets define {sorted(BADGE_FAMILIES)}")
    empty = [f"{family}: painted {sorted(painted[family])}"
             for family in painted if painted[family] != vocabulary]
    assert not empty, empty


@pytest.mark.parametrize("facet", FACETS)
def test_painted_text_clears_aa_on_every_surface(request, facet):
    """The landing page is not the product. AGENTS.md calls the Mini App the
    primary surface, and every facet in the list is one page load of something a
    visitor actually reads, so the contrast walk runs on all of them — including
    the terms and the dead end, which no browser gate used to open."""
    data = request.getfixturevalue(facet)
    assert data["contrast"] == [], json.dumps(data["contrast"], ensure_ascii=False)


# ---------------------------------------------------------------------------
# the page is not quietly broken
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("facet", FACETS)
def test_no_placeholder_or_nan_reaches_the_rendered_text(request, facet):
    data = request.getfixturevalue(facet)
    assert not data["placeholders"], f"{facet}: подстановка не сработала — {data['placeholders']}"
    assert not data["leaked"], f"{facet}: в тексте виден {data['leaked']}"


@pytest.mark.parametrize("facet", FACETS)
def test_the_page_broke_nothing_while_loading(request, facet):
    """Third-party scripts (the Telegram bridge, the Base App SDK, Google Fonts)
    are the network's business: only this origin's own 4xx and request failures
    count, and the navigated document is exempt when its status is the one the
    facet asked for — a 404 page is supposed to answer 404."""
    data = request.getfixturevalue(facet)
    assert not data["pageerrors"], f"{facet}: {data['pageerrors'][:3]}"
    assert not data["brokenrequests"], f"{facet}: {data['brokenrequests'][:3]}"


def test_the_landing_paints_a_real_title_not_a_placeholder(landing_phone):
    title = landing_phone["title"]
    assert title and "__" not in title and "undefined" not in title.lower()


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


SEED_JS = """
(args) => {
  const box = document.querySelector(args.container);
  if (!box) return {error: args.container + ' is not on the page'};
  if (typeof window[args.factory] !== 'function') {
    return {error: args.factory + ' is not a function the page exposes'};
  }
  box.innerHTML = window[args.factory](args.market, 0) + window[args.factory](args.market, 1);
  box.querySelectorAll('.market-card').forEach((c) => { c.style.animation = 'none'; });

  const clipped = [];
  for (const el of [box, ...box.querySelectorAll('*')]) {
    const cs = getComputedStyle(el);
    // No `overflow: visible` exemption here, unlike the page-level facet: this
    // grid clips with `overflow: hidden`, so content wider than its box is cut
    // by the container whether or not the element itself reports a scrollbar.
    if (el.scrollWidth <= el.clientWidth + 1) continue;
    const text = (el.textContent || '').trim();
    if (text.length < 3 || cs.textOverflow === 'ellipsis') continue;
    clipped.push({
      where: el.tagName.toLowerCase() + '.' + (el.className || '').toString().trim().split(/\\s+/)[0],
      text: text.slice(0, 40),
      over: el.scrollWidth - el.clientWidth,
    });
  }
  return {
    innerWidth: window.innerWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    boxClientWidth: box.clientWidth,
    boxScrollWidth: box.scrollWidth,
    cards: [...box.querySelectorAll('.market-card')].map(
      (c) => Math.round(c.getBoundingClientRect().width)),
    clipped,
  };
}
"""

# The widest market the product can hand the page: a question at the 200-char
# ceiling bot/handlers/markets.py enforces, option labels at the 64-char ceiling
# tests/test_telegram_limits.py pins for a button, a Telegram username at 32, and
# money big enough to carry every group separator.
WORST_MARKET = {
    "id": 987654321,
    "question": "Кто станет лидером по чаевым за эту неделю среди всех участников сообщества, "
                "которые наблюдают за ончейн-историей Base и ставят на каждый рынок одновременно?",
    "status": "open",
    "winner": None,
    "expired": False,
    "close_at": 4102444800,
    "pot_usdc": 1234567890.12,
    "liquidity_usdc": 1234567890.12,
    "volume_usdc": 987654321.98,
    "total_backers": 999999,
    "traders": 999999,
    "creator": {"id": 397602448, "username": "abcdefghijklmnopqrstuvwxyz0123"},
    "options": [
        {
            "index": i,
            "label": "abcdefghijklmnopqrstuvwxyz_0123456789_abcdefghijk_lmnopqrstuvwx",
            "pool_usdc": 1234567890.12,
            "probability": 33.3,
            "price_pct": 33.3,
            "backers": 999999,
        }
        for i in range(4)
    ],
}


@pytest.mark.parametrize(
    "factory,container",
    [("marketCard", "#markets-list"), ("predictionCard", "#predictions-list")],
)
def test_the_card_template_fits_the_phone_it_ships_to(browser, factory, container):
    """The clipping gate above measures whatever the deployed database holds, and
    an empty list holds nothing: a market card 117px wider than the 390px page
    stayed green in CI because nobody had opened a market there.

    So the page renders its own template — the real `marketCard()`/`predictionCard()`
    it already ships — with the widest legal market the ledger can produce, and the
    layout engine answers whether the card still fits. A rigid line inside a card
    that pushes the grid track past the viewport fails here with or without data."""
    findings = []
    context = open_context(browser, theme="dark", viewport=MOBILE, xff=XFF_POOL[3], touch=True)
    try:
        page = context.new_page()
        go(page, "/")
        page.wait_for_function(
            f"() => typeof window['{factory}'] === 'function'", timeout=20000
        )
        # The page renders its own list from /api/predictions a moment later, and
        # that render would replace the cards seeded below. Under `load` the
        # initial fetch had usually landed by itself; the navigation now stops at
        # the document, so the settle is stated instead of inherited.
        page.wait_for_timeout(1500)
        painted = page.evaluate(SEED_JS, {
            "factory": factory, "container": container, "market": WORST_MARKET,
        })
        if painted.get("error"):
            pytest.fail(painted["error"])
        widest = max(painted["cards"], default=0)
        findings.append({
            "factory": factory,
            "clipped": painted["clipped"],
            "cardWidth": widest,
            "boxWidth": painted["boxClientWidth"],
            "boxScrollWidth": painted["boxScrollWidth"],
            "docScrollWidth": painted["docScrollWidth"],
            "innerWidth": painted["innerWidth"],
        })
    finally:
        context.close()

    for one in findings:
        assert one["clipped"] == [], json.dumps(one, ensure_ascii=False)
        assert one["cardWidth"] <= one["boxWidth"], json.dumps(one, ensure_ascii=False)
        assert one["boxScrollWidth"] <= one["boxWidth"], json.dumps(one, ensure_ascii=False)
        assert one["docScrollWidth"] <= one["innerWidth"], json.dumps(one, ensure_ascii=False)


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


PALETTE_SEED_JS = """
(args) => {
  if (typeof searchIndex === 'undefined' || typeof SEARCH_KINDS === 'undefined') {
    return { error: 'the landing carries no search index to seed' };
  }
  Object.keys(SEARCH_KINDS).forEach((kind, i) => {
    searchIndex[kind] = [{
      id: args.firstId + i,
      question: 'Рынок ' + (args.firstId + i) + ' — seeding for the keyboard gate',
      liquidity_usdc: 1000 + i,
      pot_usdc: 2000 + i,
      close_at: args.epoch,
    }];
  });
  return { kinds: Object.keys(SEARCH_KINDS) };
}
"""


def test_the_market_search_answers_the_keyboard(browser):
    """The palette is the only landing control with no pointer path at all: it
    opens on a key, moves on arrows and leaves on Escape, so nothing but a real
    browser can tell whether it works. Two of those were broken in the shape this
    gate now pins — a row was reachable by mouse but Enter never fired its
    handler, and Escape with text in the field was eaten by `input type=search`,
    which clears itself first, so the panel stayed on screen until a second press.

    The index is seeded here rather than read from the deployed database, because
    an environment with no markets open gives the palette zero rows and every
    keyboard assertion would pass by measuring nothing."""
    context = open_context(browser, theme="dark", viewport=DESKTOP, xff=XFF_POOL[5])
    try:
        page = context.new_page()
        go(page, "/")
        page.wait_for_function("() => typeof openSearch === 'function'", timeout=20000)
        seeded = page.evaluate(PALETTE_SEED_JS, {"firstId": 94101, "epoch": 4102444800})
        if seeded.get("error"):
            pytest.fail(seeded["error"])
        kinds = seeded["kinds"]
        assert kinds, "the landing indexes no market lists at all"

        page.keyboard.press("Control+k")
        state = page.evaluate(
            "() => ({open: document.getElementById('searchDialog').open,"
            " focus: document.activeElement.tagName + '#' + document.activeElement.id,"
            " rows: document.querySelectorAll('#searchResults .pal-row').length,"
            " chips: document.querySelectorAll('#searchKinds button').length})"
        )
        assert state["open"] is True, state
        assert state["focus"] == "INPUT#searchInput", state
        assert state["rows"] == len(kinds), f"one row per list, not {state}"
        assert state["chips"] == len(kinds) + 1, f"'Все' plus one per kind, not {state}"

        page.keyboard.type("94102")
        matched = page.evaluate(
            "() => [...document.querySelectorAll('#searchResults .pal-row')].map("
            "r => r.querySelector('.pal-q').textContent.trim())"
        )
        assert len(matched) == 1, matched

        # Escape with a non-empty field is the case the search input used to eat.
        page.keyboard.press("Escape")
        assert page.evaluate("() => document.getElementById('searchDialog').open") is False

        page.keyboard.press("Control+k")
        page.keyboard.press("ArrowDown")
        focused = page.evaluate(
            "() => document.activeElement.tagName + '|' + document.activeElement.className"
        )
        assert focused.startswith("BUTTON|") and "pal-row" in focused, focused
        page.keyboard.press("Enter")
        assert page.evaluate("() => document.getElementById('searchDialog').open") is False

        page.keyboard.press("Control+k")
        page.click(f'#searchKinds button[data-kind="{kinds[1]}"]')
        filtered = page.evaluate(
            "() => ({rows: document.querySelectorAll('#searchResults .pal-row').length,"
            " kinds: [...document.querySelectorAll('#searchResults .pal-kind')]"
            " .map(k => k.textContent.trim()),"
            " active: [...document.querySelectorAll('#searchKinds button.active')]"
            " .map(c => c.dataset.kind)})"
        )
        assert filtered["rows"] == 1, filtered
        assert filtered["active"] == [kinds[1]], filtered
        page.keyboard.press("Escape")
        assert page.evaluate("() => document.getElementById('searchDialog').open") is False
    finally:
        context.close()


# ---------------------------------------------------------------------------
# the two surfaces that only exist once a dialog opens
# ---------------------------------------------------------------------------

DRAWER_SEED_JS = """
(market) => {
  /* The panel paints its option rows from `predictions`, which only the market
     list fills, and openQuote() reads the same entry. A deployed origin with no
     market open would hand this gate an empty drawer, so the entry is seeded
     here — and the id is a real one whenever the origin has it, because that is
     the only way the quote rows, the figures a trade is decided on, get painted. */
  if (typeof openQuote !== 'function' || typeof predictions === 'undefined') {
    return { error: 'the landing exposes no quote panel' };
  }
  const options = (market.options || []).length >= 2
    ? market.options.map((o, i) => ({
        index: o.index === undefined ? i : o.index,
        label: o.label || ('Вариант ' + (i + 1)),
        price_pct: o.price_pct === undefined ? 50 : o.price_pct,
      }))
    : [{index: 0, label: 'Да', price_pct: 24.9}, {index: 1, label: 'Нет', price_pct: 75.1}];
  predictions = [{
    id: market.id,
    question: market.question || 'Рынок для замера торговой панели',
    options,
  }];
  openQuote(market.id);
  return {
    marketId: market.id,
    rows: document.querySelectorAll('#quoteOptions .opt-btn').length,
    chips: document.querySelectorAll('#quoteChips .amount-chip').length,
  };
}
"""

DIALOG_COUNT_JS = """
(selector) => {
  const panel = document.querySelector(selector);
  const controls = [...panel.querySelectorAll('a, button, input, select, summary')];
  const texts = [...panel.querySelectorAll('*')].filter(
    (el) => !el.children.length && (el.textContent || '').trim());
  return {selector, controls: controls.length, texts: texts.length};
}
"""

DIALOG_STOP_JS = """
(selector) => {
  const el = document.activeElement;
  const cs = getComputedStyle(el);
  return {
    inside: el.closest(selector) !== null,
    where: el.tagName.toLowerCase() + '.' + (el.className || '').toString().trim().split(/\\s+/)[0],
    width: parseFloat(cs.outlineWidth) || 0,
    style: cs.outlineStyle,
    color: cs.outlineColor,
    shadow: cs.boxShadow || 'none',
  };
}
"""

DIALOG_XFF = {("drawer", "dark"): 0, ("drawer", "light"): 1,
              ("palette", "dark"): 2, ("palette", "light"): 3}


def open_dialog(browser, surface, theme):
    """Open one of the landing's two `<dialog>` surfaces and run the page facet
    walk over it.

    A closed dialog is `display: none`, so every probe that had ever run on `/`
    walked straight past the trade panel and the search palette — the two places
    on this site where a user decides something. That is how a 35.3 px search
    field got onto a phone: the touch floor simply never saw the element.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    context = open_context(browser, theme=theme, viewport=MOBILE,
                           xff=XFF_POOL[DIALOG_XFF[(surface, theme)]], touch=True)
    try:
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        go(page, "/")
        page.wait_for_function(
            "() => typeof openQuote === 'function' && typeof searchIndex !== 'undefined'",
            timeout=20000,
        )
        if surface == "drawer":
            market = page.evaluate(
                "async () => (await (await fetch('/api/predictions')).json())[0] || {id: 94101}"
            )
            seeded = page.evaluate(DRAWER_SEED_JS, market)
            dialog = "quoteDrawer"
        else:
            seeded = page.evaluate(PALETTE_SEED_JS, {"firstId": 94111, "epoch": 4102444800})
            page.keyboard.press("Control+k")
            dialog = "searchDialog"
        if seeded.get("error"):
            pytest.fail(seeded["error"])
        try:
            page.wait_for_function(f"() => document.getElementById('{dialog}').open", timeout=10000)
        except PlaywrightTimeoutError:
            pytest.fail(f"{dialog} never opened in {theme}")
        # slideDown is 0.18 s and it moves the panel: a rect caught mid-animation
        # is not a size, and a height measured against it would be a number about
        # nothing.
        page.wait_for_timeout(400)
        data = page.evaluate(MEASURE_JS, measure_arguments("/", MOBILE[0]))
        data["pageerrors"] = errors
        data["counted"] = page.evaluate(DIALOG_COUNT_JS, "#" + dialog)
        data["dialog"] = dialog
        if dialog == "quoteDrawer":
            # The drawer is the one surface that tells a visitor what they may
            # spend, so its sentence is read as painted text against the limits
            # this origin publishes — the number the page says and the number the
            # server refuses with have to be the same number.
            try:
                page.wait_for_function(
                    "() => document.getElementById('quoteLimit').textContent.length > 0",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                pytest.fail("the quote drawer painted no bounds sentence in "
                            + theme + " — /api/info publishes nothing for it to show")
            data["bounds"] = page.evaluate("""
                async () => {
                  const info = await (await fetch('/api/info')).json();
                  const limit = document.getElementById('quoteLimit');
                  const field = document.getElementById('quoteAmount');
                  const L = info.limits || {};
                  return {
                    sentence: limit ? limit.textContent.trim() : null,
                    min: field ? field.min : null,
                    floor: L.min_trade_usdc ?? null,
                    cap: L.max_trade_usdc ?? null,
                  };
                }
            """)
        # `showModal()` traps Tab: inside a dialog a keyboard visitor can neither
        # leave nor see where they are unless every stop paints a ring, so an
        # invisible control here costs more than on the page behind the scrim.
        stops = []
        for _ in range(8):
            page.keyboard.press("Tab")
            stop = page.evaluate(DIALOG_STOP_JS, "#" + dialog)
            if stop["inside"]:
                stops.append(stop)
        data["stops"] = stops
        return data
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("surface", ["drawer", "palette"])
def test_a_dialog_paints_as_honestly_as_the_page_behind_it(browser, surface, theme):
    """The facet list is the one the landing itself is judged by, run with the
    dialog open: touch targets, composited contrast, text cut without an
    ellipsis, horizontal overflow. Measured on the document would miss it — the
    panel paints over the page, and its own scroll container clips its own rows."""
    probe = open_dialog(browser, surface, theme)
    counted = probe["counted"]
    assert counted["controls"] >= 3 and counted["texts"] >= 3, (
        f"{probe['dialog']} in {theme} opened with nothing to measure: {counted}")
    assert probe["pageerrors"] == [], probe["pageerrors"]
    assert probe["controls"] == [], json.dumps(probe["controls"], ensure_ascii=False)
    assert probe["contrast"] == [], json.dumps(probe["contrast"], ensure_ascii=False)
    assert probe["clipped"] == [], json.dumps(probe["clipped"], ensure_ascii=False)
    assert probe["scrollWidth"] <= MOBILE[0], (
        f"{probe['dialog']} overflows a {MOBILE[0]} px screen: {probe['scrollWidth']} px")
    assert_on_the_scale(probe, f"{probe['dialog']} in {theme}")
    distinct = {stop["where"] for stop in probe["stops"]}
    assert len(distinct) >= 3, (
        f"{probe['dialog']} in {theme}: Tab reached only {sorted(distinct)} — nothing to judge")
    naked = [stop for stop in probe["stops"] if not paints_a_ring(stop)]
    assert not naked, json.dumps(naked, ensure_ascii=False)


def _painted_decimals(text):
    """Every money figure in a painted Russian sentence, as a number.

    `toLocaleString('ru-RU')` separates thousands with U+00A0 and writes the
    comma as the decimal mark, so the reader's «1 000,00» is the API's 1000.0 —
    compared as numbers, because comparing strings would judge the formatter
    rather than the figure."""
    spaced = text.replace("\u00a0", " ").replace("\u202f", " ")
    return [Decimal(tok.replace(" ", "").replace(",", "."))
            for tok in re.findall(r"\d[\d  ]*(?:[.,]\d+)?", spaced)]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_open_drawer_states_the_bounds_this_origin_publishes(browser, theme):
    """The drawer promised a range it had typed itself: the cap came from the
    server, the floor came from whoever last edited the sentence. Only the
    painted text carries both, so this is the one place that can see the two
    disagree — and a visitor told one bound and refused at another has been
    misled by the page they were trusting with money."""
    bounds = open_dialog(browser, "drawer", theme)["bounds"]
    assert bounds["floor"] is not None, f"/api/info publishes no trade floor: {bounds}"
    assert Decimal(str(bounds["min"])) == Decimal(str(bounds["floor"])), bounds
    painted = _painted_decimals(bounds["sentence"])
    assert painted == [Decimal(str(bounds["floor"])), Decimal(str(bounds["cap"]))], bounds


# ---------------------------------------------------------------------------
# the three Mini App sheets that move money
# ---------------------------------------------------------------------------

SHEET_OPENERS = ("tip", "deposit", "withdraw")

SHEET_SEED_JS = """
(name) => {
  /* A bottom sheet is not in the rendering until a thumb taps its button, so the
     wallet walk that covers /app sees the same empty document an anonymous
     visitor sees — and the sheets are the only screens on this product where
     money actually leaves. They are opened here through the very function the
     tab bar calls, which needs no session: the deposit sheet paints its QR from
     STATE, and STATE is what the page fills when there is nothing to fill. */
  const opener = window['open' + name[0].toUpperCase() + name.slice(1) + 'Sheet'];
  if (typeof opener !== 'function') return {error: 'the Mini App exposes no ' + name + ' sheet'};
  opener();
  const sheet = document.getElementById('modalSheet');
  return {
    open: document.getElementById('modalOverlay').classList.contains('show'),
    controls: sheet.querySelectorAll('a[href], button, input, select, textarea').length,
    modal: sheet.getAttribute('aria-modal'),
    label: sheet.getAttribute('aria-label') || '',
    // The bounds as the visitor's browser sees them: `input.min`/`input.max` are
    // the properties the field's own validation runs on, and the hint line is the
    // sentence the sheet paints beside it.
    fields: [...sheet.querySelectorAll('input[type=number]')].map((el) => ({
      id: el.id, min: el.min, max: el.max, step: el.step, value: el.value,
    })),
    hints: [...sheet.querySelectorAll('.hint')].map((el) => el.textContent.trim()).filter(Boolean),
  };
}
"""


def open_sheet(browser, sheet, theme):
    """One Mini App money sheet, open, walked by a real Tab key on a phone.

    `aria-modal="true"` on `#modalSheet` is a promise to a screen reader that
    nothing outside the sheet exists. Measured before this helper, the keyboard
    broke that promise on every one of the three sheets: of ten Tab presses the
    stops that stayed inside were 0 in the deposit sheet, 2 in the withdraw
    sheet and 6 in the tip sheet, and the visitor who cannot see the scrim ended
    up able to activate a button behind it while the accessibility tree said it
    was gone."""
    context = open_context(
        browser, theme=theme, viewport=MOBILE,
        xff=XFF_POOL[(SHEET_OPENERS.index(sheet) * 2 + (theme == "light")) % len(XFF_POOL)],
        touch=True,
    )
    try:
        if SESSION_COOKIE:
            context.add_cookies([{"name": session_cookie_name(), "value": SESSION_COOKIE, "url": PROD_URL}])
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        go(page, "/app")
        page.wait_for_timeout(2500)
        seeded = page.evaluate(SHEET_SEED_JS, sheet)
        if seeded.get("error"):
            pytest.fail(seeded["error"])
        assert seeded["open"], f"the {sheet} sheet never opened in {theme}"
        # The sheet slides up over 0.3 s; a rect caught mid-transform is not a
        # size and a contrast sample taken there is not the paint the user sees.
        page.wait_for_timeout(500)
        data = page.evaluate(MEASURE_JS, measure_arguments("/app", MOBILE[0]))
        data["pageerrors"] = errors
        data["counted"] = seeded
        data["sheet"] = sheet
        stops = []
        for _ in range(10):
            page.keyboard.press("Tab")
            stops.append(page.evaluate(DIALOG_STOP_JS, "#modalSheet"))
        data["stops"] = stops
        return data
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("sheet", SHEET_OPENERS)
def test_the_money_sheet_keeps_the_keyboard_it_promised(browser, sheet, theme):
    """Every Tab stop stays inside the open sheet and paints a ring, and the
    sheet is a phone surface like the screen behind it, so it is held to the same
    touch floor, the same composited contrast and the same clipping rule — the
    three checks that had never once run against a sheet."""
    probe = open_sheet(browser, sheet, theme)
    counted = probe["counted"]
    assert counted["controls"] >= 1, f"{sheet} in {theme} opened with nothing to tab to"
    assert counted["modal"] == "true", f"{sheet}: aria-modal says {counted['modal']!r}"
    assert counted["label"], f"{sheet}: aria-modal with no name is an anonymous dialog"
    assert probe["pageerrors"] == [], probe["pageerrors"]
    escaped = [stop for stop in probe["stops"] if not stop["inside"]]
    assert not escaped, f"{sheet} in {theme}: Tab left the sheet {json.dumps(escaped, ensure_ascii=False)}"
    inside = [stop for stop in probe["stops"] if stop["inside"]]
    naked = [stop for stop in inside if not paints_a_ring(stop)]
    assert not naked, json.dumps(naked, ensure_ascii=False)
    assert probe["controls"] == [], json.dumps(probe["controls"], ensure_ascii=False)
    assert probe["contrast"] == [], json.dumps(probe["contrast"], ensure_ascii=False)
    assert probe["clipped"] == [], json.dumps(probe["clipped"], ensure_ascii=False)
    assert probe["scrollWidth"] <= MOBILE[0], (
        f"the {sheet} sheet overflows a {MOBILE[0]} px screen: {probe['scrollWidth']} px")
    assert_on_the_scale(probe, f"the {sheet} sheet in {theme}")


# The field id each money sheet types its amount into, and the published key that
# bounds it. `tests/test_web.py` ties these names to the route's own `refuse()`
# kwargs; what only a browser can answer is whether the sheet painted them.
SHEET_BOUND_FIELDS = {
    "tip": ("tipAmt", {"max": "max_tip_usdc"}),
    "withdraw": ("wdAmt", {"min": "min_withdraw_usdc"}),
}


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("sheet", sorted(SHEET_BOUND_FIELDS))
def test_the_open_sheet_states_the_bounds_this_origin_publishes(browser, sheet, theme):
    """The money sheet is where the sum is chosen, and until now it chose it blind:
    the Mini App read no published bound at all, so its tip field accepted 5 000
    against a `MAX_TIP_USDC` of 1 000 and the ledger answered the tap with a
    refusal. The attribute is what blocks the sum and the hint is what tells the
    visitor why, so both are measured here against this origin's `/api/info`."""
    limits = json.loads(_get("/api/info")[2])["limits"]
    counted = open_sheet(browser, sheet, theme)["counted"]
    field_id, keys = SHEET_BOUND_FIELDS[sheet]
    fields = [f for f in counted["fields"] if f["id"] == field_id]
    assert fields, f"the {sheet} sheet paints no #{field_id}: {counted['fields']}"
    field = fields[0]
    painted_hints = [number for hint in counted["hints"] for number in _painted_decimals(hint)]
    for attr, key in keys.items():
        assert key in limits, f"/api/info publishes no {key}: {sorted(limits)}"
        assert field[attr] != "", f"#{field_id} carries no {attr} in {theme}"
        assert Decimal(field[attr]) == Decimal(str(limits[key])), (
            f"#{field_id}.{attr}={field[attr]} while this origin publishes "
            f"{key}={limits[key]} ({theme})")
        assert Decimal(field[attr]) in painted_hints, (
            f"#{field_id}.{attr}={field[attr]} is enforced but never said: "
            f"{counted['hints']}")


DETAIL_SEED_JS = """
() => {
  /* The market detail is the screen a bet is actually placed on, and like the
     sheets it is not in the rendering until a card is tapped: `#detailView`
     ships `hidden`, its rows come from STATE.markets, and `openMarketDetail` is
     the same call the card makes — so the seeded row goes through the real
     template, not a copy of it. */
  if (typeof openMarketDetail !== 'function') return {error: 'the Mini App exposes no market detail'};
  STATE.markets = [{
    id: 1, question: 'Рынок для замера скрытого экрана', traders: 12,
    options: [
      {index: 0, label: 'Да', price_pct: 24.9, pool_usdc: 1250.5},
      {index: 1, label: 'Нет', price_pct: 75.1, pool_usdc: 3800.25},
    ],
  }];
  openMarketDetail(1);
  const view = document.getElementById('detailView');
  return {
    open: !view.hidden,
    inert: view.inert === true,
    appInert: document.getElementById('app').inert === true,
    controls: view.querySelectorAll('a[href], button, input, select, textarea').length,
  };
}
"""


def open_detail(browser, theme):
    """The Mini App's third full-screen layer, open, walked the same way."""
    context = open_context(browser, theme=theme, viewport=MOBILE,
                           xff=XFF_POOL[6 if theme == "dark" else 7], touch=True)
    try:
        if SESSION_COOKIE:
            context.add_cookies([{"name": session_cookie_name(), "value": SESSION_COOKIE, "url": PROD_URL}])
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        go(page, "/app")
        page.wait_for_timeout(2500)
        seeded = page.evaluate(DETAIL_SEED_JS)
        if seeded.get("error"):
            pytest.fail(seeded["error"])
        assert seeded["open"], f"the market detail never opened in {theme}"
        page.wait_for_timeout(500)
        data = page.evaluate(MEASURE_JS, measure_arguments("/app", MOBILE[0]))
        data["pageerrors"] = errors
        data["counted"] = seeded
        stops = []
        for _ in range(10):
            page.keyboard.press("Tab")
            stops.append(page.evaluate(DIALOG_STOP_JS, "#detailView"))
        data["stops"] = stops
        return data
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_market_detail_keeps_the_keyboard_inside_itself(browser, theme):
    """A layer painted over every pixel owns the keyboard, whether or not it
    calls itself a dialog: two of ten Tab presses left this panel before the
    shell was made inert — onto the document body and onto the header's theme
    toggle behind it — and a visitor with no mouse can activate a control they
    cannot see. The shell being `inert` is the other half — it takes the covered
    screen out of the accessibility tree, which is what makes the sheet's
    `aria-modal` true instead of decorative."""
    probe = open_detail(browser, theme)
    counted = probe["counted"]
    assert counted["controls"] >= 3, f"the detail opened with nothing to tab to: {counted}"
    assert counted["appInert"], f"{theme}: the screen under the detail is still in the tab order"
    assert not counted["inert"], f"{theme}: the detail made itself inert, so nothing in it is reachable"
    assert probe["pageerrors"] == [], probe["pageerrors"]
    escaped = [stop for stop in probe["stops"] if not stop["inside"]]
    assert not escaped, json.dumps(escaped, ensure_ascii=False)
    naked = [stop for stop in probe["stops"] if not paints_a_ring(stop)]
    assert not naked, json.dumps(naked, ensure_ascii=False)
    assert probe["controls"] == [], json.dumps(probe["controls"], ensure_ascii=False)
    assert probe["contrast"] == [], json.dumps(probe["contrast"], ensure_ascii=False)
    assert probe["clipped"] == [], json.dumps(probe["clipped"], ensure_ascii=False)
    assert probe["scrollWidth"] <= MOBILE[0], (
        f"the market detail overflows a {MOBILE[0]} px screen: {probe['scrollWidth']} px")
    assert_on_the_scale(probe, f"the market detail in {theme}")


def test_the_solvency_ring_paints_the_percentage_it_prints(browser):
    """The ring is a picture of one number: the share of liabilities the address
    can actually cover. It used to divide the *free reserve* — the difference
    between what is held and what is owed — by the liabilities, and a difference
    is not a fraction: on a treasury holding 20.00 USDC against 383.24 USDC of
    obligations it printed «-94,8%» and, because `stroke-dashoffset` past the dash
    period wraps, painted 94.7 % of the circle full for a bot the same page calls
    «недостаточно». So all three are measured against each other: the API, the
    figure on the screen, and the arc the browser really drew."""
    geometry = """
() => {
  const arc = document.getElementById('gaugeArc');
  const length = arc.getTotalLength();
  const dash = parseFloat(getComputedStyle(arc).strokeDasharray.split(/[\\s,]+/)[0]);
  const offset = parseFloat(getComputedStyle(arc).strokeDashoffset);
  let painted = 0;
  for (let t = 0; t < length; t += 0.05) {
    if (((t + offset) % (2 * dash)) < dash) painted += 0.05;
  }
  return {
    paintedPct: Math.round(painted / length * 10000) / 100,
    printed: document.getElementById('gaugePct').textContent.trim(),
    aria: document.getElementById('solvencyGauge').getAttribute('aria-label'),
    detail: document.getElementById('gaugeDetail').textContent.trim(),
  };
}
"""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    context = open_context(browser, theme="dark", viewport=MOBILE, xff=XFF_POOL[4])
    try:
        page = context.new_page()
        go(page, "/")
        try:
            page.wait_for_function(
                """() => document.getElementById('gaugeCap').textContent === 'нет данных'
                       || document.getElementById('gaugePct').textContent !== '–'""",
                timeout=20000,
            )
        except PlaywrightTimeoutError:
            pytest.fail("/api/solvency never reached the ring: the landing still shows its placeholder")
        # The arc animates its offset for 0.7 s. Reading the dash pattern before
        # it lands measures a fraction the page is on its way to, not the one it
        # paints — and a gate that sometimes agrees is worse than no gate.
        page.wait_for_timeout(900)
        api = page.evaluate("async () => await (await fetch('/api/solvency')).json()")
        ring = page.evaluate(geometry)
        if api["reserve_usdc"] is None:
            assert ring["printed"] == "–", ring
            assert ring["paintedPct"] == 0.0, f"no reserve read, yet the ring paints {ring['paintedPct']}%"
            return
        owed = api["liabilities_usdc"]
        held = (api["vault_balance_usdc"] if api["reserves_source"] == "vault"
                else api["hot_wallet_balance_usdc"])
        # Nothing owed is fully covered by definition — and it is still a number
        # the assertions below need, so the branch computes it rather than
        # skipping them.
        expected = 100.0 if owed <= 0 else max(0.0, min(held / owed * 100, 100.0))
        printed = ring["printed"]
        assert "-" not in printed, f"a coverage cannot be negative: {ring}"
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", printed)
        assert match, ring
        shown = float(match.group(1).replace(",", "."))
        assert abs(shown - expected) <= 0.05, (shown, expected, held, owed)
        # The arc is the number. 0.2 pp is the width of one dash-period rounding
        # at r=52: below it the two agree, above it the picture is a second claim.
        assert abs(ring["paintedPct"] - shown) <= 0.2, ring
        assert "-" not in ring["aria"], ring["aria"]
    finally:
        context.close()


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


def test_no_mini_app_screen_paints_a_size_off_the_scale(mini_screens):
    """All four tabs in one load, the same law the pages are held to — and the Mini
    App is the surface whose stylesheet was written twice over, so a size that
    exists in `style.css` and not in `app.html` is exactly what this catches."""
    bad = findings(mini_screens, "typeScale")
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


# ---------------------------------------------------------------------------
# the crawler's documents, on the host the crawler is told about
# ---------------------------------------------------------------------------

def _get(path: str) -> tuple[int, str, str]:
    request = urllib.request.Request(
        f"{PROD_URL}{path}",
        headers={"user-agent": USER_AGENT, "x-forwarded-for": XFF_POOL[7]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")
        return response.status, response.headers.get("content-type", ""), body


def require_public_origin() -> None:
    """The three crawler documents below are written from `MINI_APP_URL` /
    `WEBHOOK_URL` — the origin the operator tells the world about — and not from
    the address the probe happens to have used. Point `DESIGN_GATE_URL` at a
    loopback server and `Sitemap: https://…` cannot agree with
    `http://127.0.0.1:8000` for any change in the tree: the mismatch is the local
    config, not the site. Saying so keeps every red in this file meaning a broken
    crawler document, which is what CI — where the default https origin is what
    runs — actually needs."""
    if not PROD_URL.startswith("https://"):
        pytest.skip(f"{PROD_URL} is not the public origin: the crawler documents name the configured host")


def test_robots_txt_is_rules_on_the_deployed_site():
    """The origin had no robots.txt, so the Pages Function answered it from the
    asset bundle and Pages returned the landing page for a path that is not in
    it: 200 and text/html where a robot expected rules. Measured here because
    only the deployed edge decides which of the two answers a visitor gets."""
    require_public_origin()
    status, content_type, body = _get("/robots.txt")
    assert status == 200
    assert content_type.startswith("text/plain"), f"robots.txt arrived as {content_type!r}"
    assert body.startswith("User-agent: *"), body[:120]
    assert "Disallow: /api/" in body
    assert f"Sitemap: {PROD_URL}/sitemap.xml" in body, body[-120:]


def test_the_deployed_sitemap_points_back_at_itself_and_resolves():
    require_public_origin()
    status, content_type, body = _get("/sitemap.xml")
    assert status == 200
    assert content_type.startswith("application/xml"), f"sitemap arrived as {content_type!r}"
    import xml.etree.ElementTree as ET

    locs = [node.text for node in ET.fromstring(body).iter(
        "{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert locs, "the deployed sitemap names no page at all"
    host = PROD_URL.split("://", 1)[1]
    for loc in locs:
        assert loc.startswith(PROD_URL + "/"), f"{loc} is not on the host being measured"
        assert host in loc
        page_status, page_type, _ = _get(loc[len(PROD_URL):])
        assert page_status == 200, f"{loc} is announced and answers {page_status}"
        assert page_type.startswith("text/html"), f"{loc} is announced as {page_type!r}"


def test_the_landing_canonicalizes_the_host_it_is_served_from():
    """The canonical is written by the origin from MINI_APP_URL/WEBHOOK_URL. If
    the operator sets that to the tunnel instead of the Pages origin, every
    page on the public host points at a different host and the crawler follows
    the hint — so the two hosts being equal is the thing to measure.
    `require_public_origin` keeps a loopback probe from reporting that equality
    as a design failure it has no way to measure."""
    require_public_origin()
    status, _, body = _get("/")
    assert status == 200
    canonical = re.findall(r'<link rel="canonical" href="([^"]*)"', body)
    assert len(canonical) == 1, f"{len(canonical)} canonicals on the landing"
    assert canonical[0].rstrip("/") == PROD_URL, f"{canonical[0]} vs {PROD_URL}"
    og_url = re.findall(r'<meta property="og:url" content="([^"]*)"', body)
    assert og_url == canonical, (og_url, canonical)
    title = re.findall(r"<title>(.*?)</title>", body, re.S)
    og_title = re.findall(r'<meta property="og:title" content="([^"]*)"', body)
    assert len(title) == 1 and og_title == [title[0].strip()], (title, og_title)


def test_an_asset_from_the_edge_carries_the_same_protection_as_a_page():
    """The origin sends nosniff on everything it answers; the CSS, the scripts
    and the images are answered by the Pages bundle instead, and an asset
    without the header is the half of the site where a mislabelled upload can be
    executed. Measured here because only the deployed edge decides which of the
    two servers answers the request."""
    import urllib.request

    for path in ("/style.css", "/site.js", "/miniapp-hero.png"):
        request = urllib.request.Request(f"{PROD_URL}{path}", headers={"user-agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200, path
            assert response.headers.get("x-content-type-options", "").lower() == "nosniff", (
                f"{path} arrived as {response.headers.get('content-type')} without nosniff"
            )
            assert response.headers.get("referrer-policy", "") == "no-referrer", path


def _fetch(path: str) -> tuple[int, str, str, str]:
    """Status, content type, body and Location — without following redirects.

    urllib answers a 3xx by fetching its target, which is exactly what would
    hide the thing the second half of this check is about: that a template URL
    now points at the page that renders it instead of answering the template.
    """

    class _Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_Stop)
    request = urllib.request.Request(f"{PROD_URL}{path}", headers={"user-agent": USER_AGENT})
    try:
        with opener.open(request, timeout=30) as response:
            return (response.status, response.headers.get("content-type", ""),
                    response.read(400_000).decode("utf-8", "replace"),
                    response.headers.get("location", ""))
    except urllib.error.HTTPError as e:
        return (e.code, e.headers.get("content-type", ""),
                e.read(400_000).decode("utf-8", "replace"), e.headers.get("location", ""))


def test_the_deployed_site_answers_a_dead_link_with_a_page():
    """The Pages function proxies everything that is not an asset, so on the
    deployed host it decides which answer a wrong URL gets — and whether a
    template in the bundle is still reachable beside the page that renders it.
    Measured here because the origin alone cannot tell that the edge is the one
    a visitor reaches."""
    status, content_type, body, _ = _fetch("/this-page-does-not-exist")
    assert status == 404, status
    assert content_type.startswith("text/html"), f"a dead link arrived as {content_type!r}"
    assert "<h1" in body, body[:200]
    assert 'name="robots" content="noindex"' in body, "the dead end asks to be indexed"

    moved, _, moved_body, location = _fetch("/index.html")
    assert moved == 301, f"/index.html answered {moved} at {location!r}"
    assert location == "/", location
    assert "__" not in moved_body, f"the raw template is still served: {moved_body[:120]!r}"


def test_the_documented_production_run_size_is_the_size_of_this_run(request):
    """The ordinary session cannot count this run: its 74 cases are the ones that
    run deselects, and no nested `pytest --collect-only` can measure it either,
    because `conftest.py` recreates `tipbot_test` at import and would wipe the
    database from under the run that spawned it. So each documented run measures
    itself, and the five places that publish the number are pinned to the
    collection that is actually happening — which is how a README claim of "66
    production" survived a tree that collected 74."""
    root = Path(__file__).resolve().parents[1]
    markexpr = (request.config.option.markexpr or "").strip().strip('"')
    if markexpr != "production":
        pytest.skip(f"-m {markexpr!r} is not the documented production run")
    if request.config.option.keyword:
        pytest.skip(f"a `-k {request.config.option.keyword}` run says nothing about "
                    "the size of the production suite")

    def holds_production_cases(path: Path) -> bool:
        text = path.read_text(encoding="utf-8-sig")
        return "pytest.mark.production" in text

    collected = {Path(item.fspath).name for item in request.session.items}
    expected = {p.name for p in (root / "tests").glob("test_*.py") if holds_production_cases(p)}
    if collected != expected:
        pytest.skip(f"this run covers {sorted(collected)}, not the production suite "
                    f"{sorted(expected)}")

    measured = len(request.session.items)
    paper = (root / "docs" / "WHITEPAPER.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8-sig")
    readme = (root / "README.md").read_text(encoding="utf-8")
    claims = {
        # Russian numeral agreement gives «теста»/«боевых» after 1432 where 1408 took
        # «тестов»/«боевой»; the captured digits are the statement, the ending is not.
        "whitepaper ru": re.search(r"\d+ обычных тест(?:ов|а) \+ (\d+) боев(?:ой|ых)", paper),
        "whitepaper en": re.search(r"\d+ ordinary \+ (\d+) production", paper),
        "whitepaper short": re.search(r"\d+ \+ (\d+) tests", paper),
        "AGENTS.md": re.search(r"\d+ tests \((\d+) more, marked", agents),
        "README excluded line": re.search(
            r"The\s+(\d+) `production`-marked tests are excluded", readme),
    }
    assert all(claims.values()), [k for k, v in claims.items() if not v]
    stated = {key: int(match.group(1)) for key, match in claims.items()}
    assert set(stated.values()) == {measured}, (measured, stated)
