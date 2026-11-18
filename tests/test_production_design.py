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

import ast
import json
import os
import re
import urllib.error
import urllib.request
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import pytest
from test_design_system import (
    APP_HTML,
    BADGE_FAMILIES,
    CSS,
    LIGHT,
    MINI_LIGHT,
    mono_class_tokens,
    read,
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
    const track = fill.parentElement;
    const painted = fill;
    const label = (row.querySelector('.option-label, .detail-opt-label, h3, .m-question') || {}).textContent || '';
    // Same rule as probBars: read the percentage from the element that prints it.
    // A row's own text can carry another number — a threshold in the question, an
    // amount — and comparing that against the bar calls agreement a bug.
    const value = row.querySelector('.option-val, .detail-opt-val, .num') || row;
    const match = /(\\d+(?:[.,]\\d+)?)\\s*%/.exec(value.textContent || '');
    // Layout width, not the painted rectangle: `.bar-fill` enters with
    // `animation: growX … backwards`, which scales the box for up to 1.5 s and
    // would make the same row measure 0 % or 60 % depending on when the probe
    // happened to run. What the row *claims* is the resolved `width`, and that is
    // also the only form a floor or a `min-width` cannot hide in.
    optionRows.push({
      label: label.trim().slice(0, 24),
      textPct: match ? Number(match[1].replace(',', '.')) : null,
      barPct: track.offsetWidth ? Number((painted.offsetWidth / track.offsetWidth * 100).toFixed(2)) : null,
      trackWidth: track.offsetWidth,
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
    if (rect.height < 43.5 || rect.width < 43.5) {
      controls.push({where: path(el), text: (el.textContent || '').trim().slice(0, 28),
                     height: Number(rect.height.toFixed(1)), width: Number(rect.width.toFixed(1))});
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
      const track = bars[i];
      const fill = bars[i].firstElementChild;
      probBars.push({
        label: (labels[i].textContent || '').trim().slice(0, 24),
        textPct: match ? Number(match[1].replace(',', '.')) : null,
        barPct: track.offsetWidth ? Number((fill.offsetWidth / track.offsetWidth * 100).toFixed(2)) : null,
        trackWidth: track.offsetWidth,
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
    pointer: {
      coarse: matchMedia('(pointer: coarse)').matches,
      anyCoarse: matchMedia('(any-pointer: coarse)').matches,
    },
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
    calls it the primary surface and it is where the money is actually spent.

    Both dimensions, because a target is an area: a field grown to 44 px tall and
    left 37 px wide is a control a thumb still misses, and the law used to report it
    as passing. And the pointer is proven coarse before anything is judged, because
    the CSS that grows these targets lives in `@media (pointer: coarse)`: if the
    browser answers a fine pointer, the grown sizes never apply and a green board
    would only mean the desktop layout was measured twice.
    """
    data = request.getfixturevalue(facet)
    assert data["pointer"]["coarse"], (
        f"{facet} is judged by a thumb rule while the browser reports a fine pointer "
        f"({data['pointer']}): the @media (pointer: coarse) blocks cannot apply")
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
    count is checked as coverage, not as equality.

    The slack is one pixel of that row's own track, plus the half-tick of the
    1 dp the text rounds to. It used to be a flat 1.5 %, because the template
    floored a small share at 2 % and the gate had to forgive the floor it was
    measuring; with the floor gone in `barWidth()`, the width IS the number."""
    record = market_page["api"].get(f"/api/market/{market_page['marketId']}")
    rows = market_page["optionRows"]
    assert rows, "the market page painted no option rows at all"
    assert record and record.get("options"), f"the API gave no record for market {market_page['marketId']}"

    for index, row in enumerate(rows):
        if row["textPct"] is None or row["barPct"] is None:
            continue
        one_pixel = 100.0 / row["trackWidth"] if row["trackWidth"] else 0.5
        assert abs(row["barPct"] - row["textPct"]) <= one_pixel + 0.05, (
            f"row {index} ({row['label']}): полоса {row['barPct']}%, написано {row['textPct']}% "
            f"(трек {row['trackWidth']}px, допуск {one_pixel + 0.05:.2f}pp)"
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


# ---------------------------------------------------------------------------
# the empty read
# ---------------------------------------------------------------------------

SERVER_SRC = (Path(__file__).resolve().parents[1] / "web" / "server.py").read_text(
    encoding="utf-8-sig")

# An id this ledger cannot have issued: the serials and the Telegram ids on the
# origin both still sit under twelve digits, so fifteen nines is a request the
# data has no way to answer. The law checks that the branch was really taken
# rather than trusting the number, because a probe that resolved would measure a
# data page and report it as an empty state.
PROBE_ID = "9" * 15


def path_parameter(node: ast.expr | None, source: str) -> str | None:
    """The literal shape of a `_static_page(path=…)` argument, holes as braces.

    `f'/u/{tg_id}'` is a JoinedStr and not a constant, so each piece is read on
    its own: the text between the holes verbatim, and every hole written back as
    `{` plus the source of whatever stands in it — a name, a subscript, an
    attribute — because the gate asks only whether the path is built from
    something, and a reader that recognised just the bare name would drop a route
    written any other way without a trace. A path taken off an object
    (`request.url.path`) is a path the visitor typed rather than a route this
    origin declares, so it is not a hole and answers None. A path carrying no
    `{…}` is returned as written and refused by the caller, which is what keeps
    `/tos` and `/agent` out of a gate about id-bearing pages."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = ast.get_source_segment(source, value.value)
                if inner is None:
                    return None
                parts.append("{" + inner + "}")
            else:
                return None
        return "".join(parts)
    return None


def id_bearing_page_routes(source: str = SERVER_SRC) -> list[tuple[str, str]]:
    """(document, path) for every page the origin builds for a path carrying an
    id, read out of the arguments of its own `_static_page` calls.

    The pair is one call's two arguments — the file it renders and the path it
    was asked to render it at — so a fourth id-bearing surface joins this gate by
    being written and not by being named here. The count is floored because the
    whole subject set is one AST walk: a scan that parsed nothing would
    parametrise nothing, and every empty state would go unmeasured in a green run."""
    routes: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_static_page"):
            continue
        if not node.args or not (isinstance(node.args[0], ast.Constant)
                                 and isinstance(node.args[0].value, str)):
            continue
        page = node.args[0].value
        path = next((path_parameter(kw.value, source)
                     for kw in node.keywords if kw.arg == "path"), None)
        if path and "{" in path:
            routes.append((page, path))
    routes = sorted(set(routes))
    assert len(routes) >= 3, f"only {len(routes)} id-bearing pages in server.py — the scan is blind"
    return routes


EMPTY_STATE_JS = """
() => {
  const note = document.querySelector('.empty');
  const headings = [...document.querySelectorAll('h1')];
  const shown = (el) => {
    const box = el.getBoundingClientRect();
    return box.height > 0 && box.width > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  return {
    h1s: headings.map((el) => el.textContent.trim()),
    noteVisible: !!note && shown(note),
    noteText: note ? note.textContent.trim() : '',
    outsideHeading: !note || !headings.some((el) => el.contains(note)),
    chrome: !!document.querySelector('header .brand') && !!document.querySelector('footer'),
    controls: [...document.querySelectorAll('main a, main button')].filter(shown).length,
  };
}
"""


@pytest.mark.parametrize("page_name,path_with_id", id_bearing_page_routes())
def test_an_empty_read_is_still_a_page(browser, page_name, path_with_id):
    """A not-found is an answer, and the answer is still a document.

    Three surfaces used to take a failed read as a licence to replace the whole
    article: the branch that painted its note into the container carrying the
    `<h1>` removed the heading, the controls under it and the outline a screen
    reader walks, so a visitor following a link to a pool that had been closed
    landed in a page with no title. The source gate
    (`tests/test_design_system.py`) refuses that shape in the files; this measures
    the painted result, because a heading is destroyed by what the browser
    composes and not by what a reader matched.

    Every premise is checked on the rendered page rather than assumed: the note
    must be visible — which is how the law knows it is looking at the empty branch
    and not at a load that found its data — and then the document must still carry
    exactly one heading, in the text the route named, outside the note, with its
    chrome and with something on it a visitor can act on."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    loader = re.search(r"<h1[^>]*>(.*?)</h1>", read(page_name), re.S | re.I)
    assert loader, f"{page_name} ships no <h1> for a browser to keep"
    context = open_context(browser, theme="dark", viewport=MOBILE, xff=XFF_POOL[7], touch=True)
    try:
        page = context.new_page()
        probed = re.sub(r"\{[^{}]+\}", PROBE_ID, path_with_id)
        go(page, probed)
        try:
            page.wait_for_selector(".empty", state="visible", timeout=15000)
        except PlaywrightTimeoutError:
            pytest.fail(f"{page_name} at {probed}: no empty note was painted, so this load "
                        "answered with data and the empty state was never measured")
        seen = page.evaluate(EMPTY_STATE_JS)
        assert len(seen["h1s"]) == 1, f"{page_name} at {probed}: headings are {seen['h1s']}"
        assert seen["h1s"][0] != loader.group(1).strip(), (
            f"{page_name}: the heading still says {seen['h1s'][0]!r}, which is what the "
            "document said before any read happened")
        assert seen["noteText"], f"{page_name}: the empty state paints no words"
        assert seen["outsideHeading"], f"{page_name}: the note is inside the heading it replaced"
        assert seen["chrome"], f"{page_name}: the empty read lost the page chrome"
        assert seen["controls"] >= 1, f"{page_name}: nothing on the empty page can be acted on"
    finally:
        context.close()


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
# the Mini App layers a thumb opens
# ---------------------------------------------------------------------------

REGEX_ALLOWED_AFTER = "([{,;:=!&|?+-*%<>~^"


def _opens_a_regex(text: str, index: int) -> bool:
    """Whether this `/` starts a literal rather than dividing what came before."""
    back = index - 1
    while back >= 0 and text[back] in " \t\r\n":
        back -= 1
    return back < 0 or text[back] in REGEX_ALLOWED_AFTER


def _skip_regex(text: str, index: int) -> int:
    """The offset just past the `/…/flags` literal that starts at `index`.

    A character class is its own world: app.html's own `esc()` writes `/[&<>"']/g`, and
    both quote characters sit inside it. A reader that opened a string there swallowed
    every `}` after it and called the whole file unclosed."""
    in_class = False
    scan = index + 1
    while scan < len(text):
        char = text[scan]
        if char == "\\":
            scan += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return scan + 1
        scan += 1
    return len(text)


def _js_body(text: str, brace: int) -> str:
    """The characters between a `{` and the `}` that closes it.

    A brace inside a string, a template literal, a regex or a comment is markup, not a
    block, and a template's `${…}` is code inside that markup: every layer in app.html
    paints itself out of a backtick template holding a whole screen of `<div>`s, and the
    tip sheet builds a nested template right inside one. A reader that counted the braces
    of that markup stopped wherever the template happened to close a tag, and a reader
    that closed the outer template on the inner backtick swallowed the rest of the file —
    either way the layer under it was reported as calling nothing. The pairing of layers
    against `openModal` call sites is what makes that failure loud rather than silent."""
    frames: list[str] = []
    index = brace
    quote = ""
    while index < len(text):
        char = text[index]
        if quote == "`":
            if char == "\\":
                index += 2
                continue
            if char == "`":
                quote = ""
            elif char == "$" and text[index + 1:index + 2] == "{":
                frames.append("interp")
                quote = ""
                index += 2
                continue
            index += 1
            continue
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"`":
            quote = char
        elif char == "/" and text[index + 1:index + 2] == "/":
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline
            continue
        elif char == "/" and text[index + 1:index + 2] == "*":
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        elif char == "/" and _opens_a_regex(text, index):
            index = _skip_regex(text, index)
            continue
        elif char == "{":
            frames.append("brace")
        elif char == "}":
            frame = frames.pop()
            if frame == "interp":
                quote = "`"
            if not frames:
                return text[brace + 1:index]
        index += 1
    raise AssertionError("an unclosed brace in app.html")


MODAL_CALL = "openModal("
LAYER_SIGNATURE = re.compile(r"function\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*\{")

# A layer opened from a row the app does not hold must be handed that row, so the
# row is written here. `mini_app_layers()` pairs this table against the fields each
# layer's own template reads and against the number of `openModal` call sites in
# app.html, so a new field or a new seeded layer fails the build instead of painting
# `undefined` where a withdrawal amount should be.
SEED_ARG_LAYERS: dict[str, dict[str, str]] = {
    "showWithdrawConfirm": {
        "token": "0f" * 32,
        "amount": "1 250,00",
        "fee": "6,25",
        "total": "1 256,25",
        "balance_after": "88,00",
        "address": "0x" + "a1b2" * 10,
    },
}


def _modal_callers(text: str) -> list[tuple[str, str, str]]:
    """`(name, arguments, body)` for every function in this JS that hands markup over."""
    callers = []
    for match in LAYER_SIGNATURE.finditer(text):
        if match.group(1) == "openModal":
            continue
        body = _js_body(text, match.end() - 1)
        if MODAL_CALL in body:
            callers.append((match.group(1), match.group(2).strip(), body))
    return callers


def modal_layers_in(text: str) -> list[tuple[str, str, str]]:
    """The layers of one JS source, paired against its own call sites.

    The pairing is what makes the reader accountable: a body cut short by a brace the
    scanner mistook for a block loses its layer, and one layer fewer beside the same
    number of `openModal` calls is an assertion, not a shorter test run.
    """
    callers = _modal_callers(text)
    sites = text.count(MODAL_CALL) - text.count(f"function {MODAL_CALL}")
    assert len(callers) == sites, (
        f"this source calls {MODAL_CALL} {sites} times but {[name for name, _, _ in callers]} "
        "were read as layers: the reader is losing a body, not the app hiding a layer")
    return callers


def mini_app_layers() -> tuple[str, ...]:
    """Every Mini App layer a thumb brings up over the screen it is on.

    The subject set is the app's source, not a list kept here. Three sheets used to
    be named by hand, and the two create screens — where a market is actually born,
    with their own inputs, option rows and buttons — were measured by no law below at
    all. Deriving the set instead of typing it turned up a sixth layer the hand-written
    name and the zero-argument reader both missed: `showWithdrawConfirm(d)`, the screen
    a withdrawal is approved on. A layer painted over the full screen by an id — the
    market detail — has its own gate.
    """
    callers = modal_layers_in(APP_HTML)
    assert len(callers) >= 5, f"only {len(callers)} layers read out of app.html: the reader went blind"
    for name, arguments, body in callers:
        if arguments:
            assert name in SEED_ARG_LAYERS, (
                f"{name}({arguments}) opens a layer from a row nothing supplies: it is measured "
                "by no law until SEED_ARG_LAYERS describes that row")
            reads = frozenset(re.findall(rf"\b{re.escape(arguments)}\.([a-z_][a-z0-9_]*)", body))
            assert reads == frozenset(SEED_ARG_LAYERS[name]), (
                f"{name}() reads {sorted(reads)} from {arguments} but the seed supplies "
                f"{sorted(SEED_ARG_LAYERS[name])}: one of them paints nothing")
        else:
            assert name not in SEED_ARG_LAYERS, (
                f"{name}() takes no argument, so {name} is seeded by a row nobody reads")
    return tuple(name for name, _, _ in callers)


def test_the_layer_reader_reads_a_body_the_app_writes_as_a_template():
    """The reader on markup that is not the file it usually gets.

    Every layer in app.html paints itself out of a backtick template holding a whole
    screen of `<div>`s, and a template's own text carries braces: the first version of
    this scanner counted one of those and stopped reading where the markup happened to
    close a tag, so the layer below it — the one the money is approved on — was simply
    not in the walk, and the walk was green.
    """
    closed_brace = (
        "function openTipSheet() {\n"
        "  const close = `}</div>`;\n"
        "  openModal(`<b>${close}</b>`);\n"
        "}\n"
        "function showWithdrawConfirm(d) { openModal(`<i>${d.token}</i>`); }\n"
        "function closeModal() { hide(); }\n"
    )
    assert [name for name, _, _ in modal_layers_in(closed_brace)] == ["openTipSheet", "showWithdrawConfirm"]

    in_a_comment_and_a_string = (
        "function openDepositSheet() {\n"
        "  // if (ready) { paint(); }\n"
        '  const label = "a } b";\n'
        "  openModal(`<p>${label}</p>`);\n"
        "}\n"
    )
    assert [name for name, _, _ in modal_layers_in(in_a_comment_and_a_string)] == ["openDepositSheet"]

    escaped_backtick = (
        "function openCreateMarket() { openModal(`<p>\\`}</p>`); }\n"
    )
    assert [name for name, _, _ in modal_layers_in(escaped_backtick)] == ["openCreateMarket"]

    # app.html's own `esc()` is written with both quote characters inside a regex
    # character class. A reader that opened a string on the first of them swallowed
    # every `}` after it — and then called the whole file unclosed.
    regex_class = (
        'function esc(s) { return String(s).replace(/[&<>"\']/g, c => ({'
        '"&": "&amp;", "<": "&lt;"})[c]); }\n'
        "function openWithdrawSheet() { openModal(`<p>${esc('x')}</p>`); }\n"
    )
    assert [name for name, _, _ in modal_layers_in(regex_class)] == ["openWithdrawSheet"]

    # The tip sheet builds a template inside the `${}` of a template. Closing the outer
    # one on the inner backtick left everything after it to be read as code.
    nested_template = (
        "function openTipSheet() {\n"
        "  const rows = list.map(r => `<b>${esc(r)}</b>`).join('');\n"
        "  openModal(`<div>${rows}</div>`);\n"
        "}\n"
    )
    assert [name for name, _, _ in modal_layers_in(nested_template)] == ["openTipSheet"]

    # A named function nested inside a layer is counted by both, and the pairing says
    # so instead of letting one layer answer for two.
    nested = "function openCreateBet() { function inner() { openModal(`<p>`); } inner(); }\n"
    with pytest.raises(AssertionError, match="the reader is losing a body"):
        modal_layers_in(nested)

    # The template this source is written in is the one the scanner must not mistake
    # for a block, so a body whose `${}` pairs are unbalanced is a real defect.
    with pytest.raises(AssertionError, match="unclosed brace"):
        modal_layers_in("function openBroken() { openModal(`<p>`);\n")


SHEET_SEED_JS = r"""
({name, row, balance, known}) => {
  /* A bottom sheet is not in the rendering until a thumb taps its button, so the
     wallet walk that covers /app sees the same empty document an anonymous
     visitor sees — and the sheets are the only screens on this product where
     money actually leaves. They are opened here through the very function the
     tab bar calls, which needs no session: the deposit sheet paints its QR from
     STATE, and STATE is what the page fills when there is nothing to fill. The
     one layer that opens from a server row is handed that row from Python, so
     what it paints is the shape the withdrawal response really carries. */
  const opener = window[name];
  if (typeof opener !== 'function') return {error: `the Mini App exposes no ${name}()`};
  if (balance !== undefined && balance !== null) {
    /* The account behind a money panel is the other input it paints from, and it
       is session data: an anonymous probe is handed an empty STATE, so the
       filtering has one balance to judge against and no more. Writing the figure
       into STATE is the same assignment the detail seed already makes to
       `STATE.markets` — the template, the primitives and the tap handler stay the
       shipped ones, and only the account behind them is supplied. `known: false`
       is the other real case: no identity resolved, so the panel must say it
       cannot tell rather than say the money is gone. */
    STATE.tg_id = known === false ? null : 1;
    STATE.balance_usdc = balance;
  }
  if (row === null) opener(); else opener(row);
  const sheet = document.getElementById('modalSheet');
  return {
    open: document.getElementById('modalOverlay').classList.contains('show'),
    controls: sheet.querySelectorAll('a[href], button, input, select, textarea').length,
    modal: sheet.getAttribute('aria-modal'),
    label: sheet.getAttribute('aria-label') || '',
    // The language the panel paints its labels in: it comes from the session, and
    // a law that looks for a row by its label has to look in that language.
    lang: STATE.lang,
    // The bounds as the visitor's browser sees them: `input.min`/`input.max` are
    // the properties the field's own validation runs on, and the hint line is the
    // sentence the sheet paints beside it.
    fields: [...sheet.querySelectorAll('input[type=number], input[inputmode=decimal]')].map((el) => ({
      id: el.id, min: el.min, max: el.max, step: el.step, value: el.value,
    })),
    hints: [...sheet.querySelectorAll('.hint')].map((el) => el.textContent.trim()).filter(Boolean),
    // One row, one meaning: the figures a panel states beside its fields — the
    // balance, the network, the confirmation window. Read as label/value pairs so
    // a law can ask which figure a named row carries instead of squinting at text.
    rows: [...sheet.querySelectorAll('.wd-row')].map((el) => ({
      label: el.querySelector('span') ? el.querySelector('span').textContent.trim() : '',
      value: el.querySelector('b') ? el.querySelector('b').textContent.trim() : '',
    })),
    // The sums a thumb can tap, with the value the tap would type. `pickChip` is
    // the amount family: the tip sheet paints a second `.chip` row above this one
    // for repeat recipients, and a handle is not a sum.
    chips: [...sheet.querySelectorAll('[data-act="pickChip"]')].map((el) => ({
      text: el.textContent.trim(), val: el.dataset.val, active: el.classList.contains('active'),
    })),
    links: [...sheet.querySelectorAll('a[href]')].map((el) => el.getAttribute('href')),
    // The figures the layer paints, so a law can ask whether the money a screen
    // shows is the money the app was handed. `attrs` is where a one-use token
    // lives: it rides back to the server on a button, it is never text.
    painted: sheet.textContent.replace(/\s+/g, " ").trim(),
    attrs: [...sheet.querySelectorAll("*")].map((el) => Array.from(el.attributes).map((a) => a.value).join(" ")).join(" "),
  };
}
"""


def open_layer(browser, layer, theme, balance=None, known=True, act=None, walk=True):
    """One Mini App layer, open, walked by a real Tab key on a phone.

    `aria-modal="true"` on `#modalSheet` is a promise to a screen reader that
    nothing outside the sheet exists. Measured before this helper, the keyboard
    broke that promise on every one of the three sheets: of ten Tab presses the
    stops that stayed inside were 0 in the deposit sheet, 2 in the withdraw
    sheet and 6 in the tip sheet, and the visitor who cannot see the scrim ended
    up able to activate a button behind it while the accessibility tree said it
    was gone.

    `balance` and `known` are the account the panel paints from, `act` is a
    JavaScript source run once the layer has settled, and `walk=False` skips the
    eleven keypresses: a law that judges painted figures does not need them and
    should not pay for them."""
    context = open_context(
        browser, theme=theme, viewport=MOBILE,
        xff=XFF_POOL[(mini_app_layers().index(layer) * 2 + (theme == "light")) % len(XFF_POOL)],
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
        seeded = page.evaluate(
            SHEET_SEED_JS,
            {"name": layer, "row": SEED_ARG_LAYERS.get(layer), "balance": balance, "known": known},
        )
        if seeded.get("error"):
            pytest.fail(seeded["error"])
        assert seeded["open"], f"{layer}() never opened in {theme}"
        # The sheet slides up over 0.3 s; a rect caught mid-transform is not a
        # size and a contrast sample taken there is not the paint the user sees.
        page.wait_for_timeout(500)
        data = {"acted": page.evaluate(act) if act else None}
        data.update(page.evaluate(MEASURE_JS, measure_arguments("/app", MOBILE[0])))
        data["pageerrors"] = errors
        data["counted"] = seeded
        data["sheet"] = layer
        stops = []
        if walk:
            for _ in range(10):
                page.keyboard.press("Tab")
                stops.append(page.evaluate(DIALOG_STOP_JS, "#modalSheet"))
        data["stops"] = stops
        return data
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("layer", mini_app_layers())
def test_an_open_layer_keeps_the_keyboard_it_promised(browser, layer, theme):
    """Every Tab stop stays inside the open layer and paints a ring, and the
    layer is a phone surface like the screen behind it, so it is held to the same
    touch floor, the same composited contrast and the same clipping rule — the
    three checks that had never once run against a sheet."""
    probe = open_layer(browser, layer, theme)
    counted = probe["counted"]
    assert counted["controls"] >= 1, f"{layer} in {theme} opened with nothing to tab to"
    assert counted["modal"] == "true", f"{layer}: aria-modal says {counted['modal']!r}"
    assert counted["label"], f"{layer}: aria-modal with no name is an anonymous dialog"
    row = SEED_ARG_LAYERS.get(layer)
    if row:
        # The pairing above proves this table names the fields the template reads;
        # this proves the render really carries them, so a layer cannot keep passing
        # by painting `undefined` where the money a thumb is about to approve should be.
        invisible = {field: value for field, value in row.items()
                     if value not in counted["painted"] and value not in counted["attrs"]}
        assert not invisible, (
            f"{layer} was handed {sorted(row)} and shows none of {sorted(invisible)} "
            f"in {theme}: {counted['painted']!r}")
    assert probe["pageerrors"] == [], probe["pageerrors"]
    escaped = [stop for stop in probe["stops"] if not stop["inside"]]
    assert not escaped, f"{layer} in {theme}: Tab left the sheet {json.dumps(escaped, ensure_ascii=False)}"
    inside = [stop for stop in probe["stops"] if stop["inside"]]
    naked = [stop for stop in inside if not paints_a_ring(stop)]
    assert not naked, json.dumps(naked, ensure_ascii=False)
    assert probe["controls"] == [], json.dumps(probe["controls"], ensure_ascii=False)
    assert probe["contrast"] == [], json.dumps(probe["contrast"], ensure_ascii=False)
    assert probe["clipped"] == [], json.dumps(probe["clipped"], ensure_ascii=False)
    assert probe["scrollWidth"] <= MOBILE[0], (
        f"the {layer} layer overflows a {MOBILE[0]} px screen: {probe['scrollWidth']} px")
    assert_on_the_scale(probe, f"the {layer} layer in {theme}")


# The field ids each layer types a bound figure into, and the published key that
# bounds each. `tests/test_web.py` ties these names to the route's own `refuse()`
# kwargs; what only a browser can answer is whether the layer painted them.
SHEET_BOUND_FIELDS = {
    "openTipSheet": [("tipAmt", {"max": "max_tip_usdc"})],
    "openWithdrawSheet": [("wdAmt", {"min": "min_withdraw_usdc"})],
    # A create sheet chooses a deadline, not a sum: the cap the route answers
    # `api_deadline_long` under is the same kind of bound a thumb can break.
    "openCreateMarket": [("mkHours", {"max": "max_deadline_hours"})],
    "openCreateBet": [("btHours", {"max": "max_deadline_hours"})],
}
assert set(SHEET_BOUND_FIELDS) <= set(mini_app_layers()), (
    f"a layer this table names is no longer a layer app.html opens: "
    f"{sorted(set(SHEET_BOUND_FIELDS) - set(mini_app_layers()))}")


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("layer", sorted(SHEET_BOUND_FIELDS))
def test_an_open_layer_states_the_bounds_this_origin_publishes(browser, layer, theme):
    """The layer is where the sum or the clock is chosen, and until now it chose
    them blind: the Mini App read no published bound at all, so its tip field
    accepted 5 000 against a `MAX_TIP_USDC` of 1 000, its create sheets offered any
    deadline against the ceiling `MARKET_MAX_DEADLINE_HOURS` sets, and the ledger
    answered each tap with a refusal. The attribute is what blocks the figure and the hint
    is what tells the visitor why, so both are measured here against this origin's
    `/api/info`."""
    limits = json.loads(_get("/api/info")[2])["limits"]
    counted = open_layer(browser, layer, theme)["counted"]
    painted_hints = [number for hint in counted["hints"] for number in _painted_decimals(hint)]
    for field_id, keys in SHEET_BOUND_FIELDS[layer]:
        fields = [f for f in counted["fields"] if f["id"] == field_id]
        assert fields, f"the {layer} layer paints no #{field_id}: {counted['fields']}"
        field = fields[0]
        for attr, key in keys.items():
            assert key in limits, f"/api/info publishes no {key}: {sorted(limits)}"
            assert field[attr] != "", f"#{field_id} carries no {attr} in {theme}"
            assert Decimal(field[attr]) == Decimal(str(limits[key])), (
                f"#{field_id}.{attr}={field[attr]} while this origin publishes "
                f"{key}={limits[key]} ({theme})")
            assert Decimal(field[attr]) in painted_hints, (
                f"#{field_id}.{attr}={field[attr]} is enforced but never said: "
                f"{counted['hints']}")


# ── the account a money panel chooses against ─────────────────────────────────
#
# A sheet that takes a sum has one input the earlier laws never supplied: what
# the account holds. The rows, chips and links below are read out of the open
# layer, and the balance is handed to the shipped primitive that paints them —
# the same assignment `DETAIL_SEED_JS` already makes to `STATE.markets`, so the
# template, the filtering and the tap handler under test are the shipped ones.


def _mini_app_functions() -> dict[str, str]:
    """`{name: body}` for every function app.html declares, at any depth.

    The scan is the file's own signature pattern, so a helper that paints a panel
    inside a layer is a function this reader can reach and account for."""
    out = {}
    for match in LAYER_SIGNATURE.finditer(APP_HTML):
        out[match.group(1)] = _js_body(APP_HTML, match.end() - 1)
    return out


def mini_app_surfaces() -> dict[str, str]:
    """Every full-screen layer the Mini App can bring up, and how it is opened.

    The modal sheets come out of the app's own `openModal` call sites; the market
    detail is the one layer painted into an id rather than a sheet, and
    `test_the_market_detail_keeps_the_keyboard_inside_itself` fails if it ever
    stops opening, so this table cannot quietly keep measuring a screen that is
    gone."""
    surfaces = dict.fromkeys(mini_app_layers(), "sheet")
    surfaces["openMarketDetail"] = "detail"
    return surfaces


def surfaces_painting(primitive: str) -> tuple[tuple[str, str], ...]:
    """`(layer, kind)` for every Mini App surface whose code paints `primitive`,
    whether its own body calls it or a panel it hosts does.

    The subject set is app.html: a money panel joins the laws below by calling the
    primitive, so a fourth panel needs no edit here and a panel that stops calling
    it leaves the law by the same door."""
    funcs = _mini_app_functions()
    surfaces = []
    for name, kind in sorted(mini_app_surfaces().items()):
        body = funcs.get(name)
        assert body is not None, f"{name} is a surface app.html no longer declares"
        painters = [body] if f"{primitive}(" in body else [
            funcs[helper] for helper in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\(", body)
            if helper in funcs and helper != name and f"{primitive}(" in funcs[helper]
        ]
        if painters:
            surfaces.append((name, kind))
    assert surfaces, (
        f"no Mini App surface paints {primitive}(): the primitive is dead, renamed, "
        "or this reader went blind")
    return tuple(surfaces)


def quick_amount_surfaces() -> tuple[tuple[str, str, str], ...]:
    """`(layer, kind, field id)` for every surface that offers a quick amount.

    `quickAmounts(target)` names the field its chips type into, which is what pairs
    a chip row with the sum it owes. A surface that calls it for two fields, or for
    none, is reported instead of guessed at."""
    funcs = _mini_app_functions()
    surfaces = []
    for name, kind in surfaces_painting("quickAmounts"):
        body = funcs[name]
        painters = [body] if "quickAmounts(" in body else [
            funcs[helper] for helper in re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\(", body)
            if helper in funcs and helper != name and "quickAmounts(" in funcs[helper]
        ]
        targets = {
            match.group(1)
            for painter in painters
            for match in re.finditer(r"quickAmounts\('(\w+)'\)", painter)
        }
        assert len(targets) == 1, (
            f"{name} offers quick amounts for {sorted(targets) or 'no field'}: one chip "
            "row owes exactly one amount field")
        surfaces.append((name, kind, targets.pop()))
    return tuple(surfaces)


def open_mini_app_surface(browser, name, kind, balance=None, known=True) -> dict:
    """One Mini App surface opened on a phone, with everything it painted.

    The keyboard walk is skipped: these laws judge the figures a panel paints, and
    the Tab order is judged by `test_an_open_layer_keeps_the_keyboard_it_promised`
    on the very same layers."""
    if kind == "sheet":
        return open_layer(browser, name, "dark", balance=balance, known=known, walk=False)
    return open_detail(browser, "dark", balance=balance, known=known, walk=False)


def quick_ladder() -> list[int]:
    """The sums app.html offers a thumb, read out of the one ladder it declares."""
    declared = re.search(r"const QUICK_LADDER = \[([0-9,\s]+)\];", APP_HTML)
    assert declared, "app.html declares no QUICK_LADDER for the money panels to filter"
    rungs = [int(value) for value in re.findall(r"\d+", declared.group(1))]
    assert len(rungs) >= 3, f"the ladder offers {rungs}: a ladder this short filters nothing"
    return rungs


def quick_default() -> int:
    """The rung a money field opens on, out of the same declaration."""
    standing = re.search(r"const QUICK_DEFAULT = (\d+);", APP_HTML)
    assert standing, "app.html declares no QUICK_DEFAULT"
    return int(standing.group(1))


_MONEY_FIGURE = re.compile(r"\d{1,3}(?:[  ,]\d{3})*(?:[.,]\d{2})?\b")


def _money_figures(text: str) -> list[Decimal]:
    """Every two-decimal money figure in painted text, as a number.

    `usdc()` writes through `toLocaleString` in the interface language, so the
    same figure arrives as «3,43» in one session and `3.43` in another, and the
    group separator is a space in ru and a comma in en. The two decimals the money
    mechanism owes (see `tests/test_design_system.py`) are what tell an en group
    separator from an ru decimal mark, which is why a count or a percentage never
    answers as a figure here."""
    figures = []
    for token in _MONEY_FIGURE.findall(text.replace(" ", " ")):
        if not re.search(r"[.,]\d{2}$", token):
            continue
        head = re.sub(r"[ ,]", "", token[:-3])
        figures.append(Decimal(f"{head}.{token[-2:]}"))
    return figures


def _i18n_word(lang: str, key: str) -> str:
    """One Mini App label, out of the dictionary the app itself ships for that
    language. A law that typed the English word would fail every Russian session
    and pass every English one, which is the defect the panel had."""
    for line in APP_HTML.splitlines():
        stripped = line.strip()
        if not stripped.startswith(f"{lang}: {{"):
            continue
        found = re.search(rf'\b{key}: "([^"]*)"', stripped)
        assert found, f"the {lang} dictionary carries no {key}"
        return found.group(1)
    raise AssertionError(f"app.html ships no {lang} dictionary")


# The account shapes a money panel can be measured on. `known=False` is not a
# number: with no identity resolved the panel knows nothing, and hiding every
# offer would say «the money is gone» on a screen that cannot tell whose it is.
BALANCE_SHAPES: tuple[tuple[str, float, bool], ...] = (
    ("an empty account", 0.0, True),
    ("an account below the ladder's standing default", 3.4, True),
    ("an account above every rung", 120.0, True),
    ("no identity resolved", 999.0, False),
)


@pytest.mark.parametrize("shape", range(len(BALANCE_SHAPES)))
def test_a_quick_amount_is_offered_only_when_the_account_covers_it(browser, shape):
    """A chip is an offer to tap, and an offer the balance cannot cover is a
    refusal the page could have predicted: the tip sheet and the buy panel typed
    the same four rungs by hand, so on a 3.40 USDC account both offered 25 and the
    ledger answered the tap.

    Measured against the ladder out of app.html, on every surface that paints one,
    for the four account shapes above — including the one that is not a figure at
    all, where nothing is filtered and the balance row says so."""
    label, balance, known = BALANCE_SHAPES[shape]
    rungs = quick_ladder()
    standing = quick_default()
    offered = rungs if not known else [v for v in rungs if v <= balance]
    opening = standing if standing in offered else (offered[-1] if offered else None)
    measured = 0
    for name, kind, field_id in quick_amount_surfaces():
        counted = open_mini_app_surface(
            browser, name, kind, balance=balance, known=known)["counted"]
        chips = counted["chips"]
        measured += 1
        assert [chip["val"] for chip in chips] == [str(v) for v in offered], (
            f"{name} on {label} offers {[chip['val'] for chip in chips]}, "
            f"the ladder filtered to {offered}")
        for chip in chips:
            figures = _money_figures(chip["text"])
            assert len(figures) == 1 and figures[0] == Decimal(chip["val"]), (
                f"{name}: a chip labelled {chip['text']!r} taps {chip['val']}")
        active = [chip for chip in chips if chip["active"]]
        assert len(active) == (1 if offered else 0), (
            f"{name} on {label} marks {len(active)} of {len(chips)} chips selected")
        if offered:
            assert Decimal(active[0]["val"]) == Decimal(opening), (
                f"{name} on {label} opens on {active[0]['val']}, not on {opening}")
        fields = [f for f in counted["fields"] if f["id"] == field_id]
        assert fields, f"{name} offers quick amounts and paints no #{field_id}"
        if opening is None:
            assert fields[0]["value"] == "", (
                f"#{field_id} on {label} opens on {fields[0]['value']!r} "
                "with nothing on the account")
        else:
            # The field's own value is the number the tap submits, not the figure
            # the chip shows: `1` here and `1.00` there are the same sum.
            assert Decimal(fields[0]["value"]) == Decimal(opening), (
                f"#{field_id} on {label} opens on {fields[0]['value']!r}, "
                f"the chip row says {opening}")
        figures = [value for row in counted["rows"] for value in _money_figures(row["value"])]
        if known:
            assert Decimal(str(balance)) in figures, (
                f"{name} on {label} paints {counted['rows']} and never the balance")
        else:
            assert any(row["value"] == "--" for row in counted["rows"]), (
                f"{name} on {label} paints {counted['rows']} for an account it cannot "
                "identify: a figure it has no right to, or silence about the unknown")
    assert measured >= 2, f"only {measured} quick-amount surfaces were measured"


def test_the_available_row_is_the_balance_this_origin_reports(browser):
    """The row was added this wave, and a row is only worth having if the figure
    in it is the figure the account holds: the sheet paints the balance
    `/api/mini/state` handed it, in the money face, labelled in the language the
    session is in. Compared as numbers, because the same balance is «1 250,00» in
    one interface language and `1,250.00` in another."""
    if not SESSION_COOKIE:
        pytest.skip(
            "the balance is session data (/api/mini/state): without DESIGN_GATE_SESSION "
            "every panel paints the unknown-figure branch and there is no number to compare"
        )
    state = json.loads(_get_with_session("/api/mini/state")[2])
    balance = Decimal(str(state["balance_usdc"]))
    measured = 0
    for name, kind in surfaces_painting("availableLine"):
        counted = open_mini_app_surface(browser, name, kind)["counted"]
        word = _i18n_word(counted["lang"], "available")
        labelled = [row for row in counted["rows"] if row["label"] == word]
        assert len(labelled) == 1, (
            f"{name} paints {len(labelled)} rows labelled {word!r} in {counted['lang']}: "
            "one row, one meaning")
        assert _money_figures(labelled[0]["value"]) == [balance], (
            f"{name} says {labelled[0]['value']!r} beside the sum for a session whose "
            f"/api/mini/state balance is {balance}")
        measured += 1
    assert measured >= 3, (
        f"only {measured} panels were measured against the published balance")


SHEET_MAX_CLICK_JS = """
() => {
  /* A real tap, through the delegated handler the shipped page installs, on the
     control the sheet paints when — and only when — the account can cover a
     withdrawal. */
  const btn = document.querySelector('#modalSheet [data-act="fillMax"]');
  if (!btn) return {error: 'the withdraw sheet paints no Max control'};
  btn.click();
  const target = document.getElementById(btn.dataset.target);
  if (!target) return {error: `Max names #${btn.dataset.target}, which the sheet does not have`};
  return {typed: target.value, target: btn.dataset.target};
}
"""


def test_the_max_control_types_a_sum_the_fee_still_fits(browser):
    """The withdrawal fee is added on top of the sum, so a balance is not a
    withdrawal: on a 3.4653 USDC account at the published percent the largest
    whole cent that still leaves room for the fee is 3.43, and 3.47 — the same
    figure rounded up — needs 3.51 the account never held. The sheet offered no
    maximum at all and left the ledger to refuse whatever the thumb typed.

    Nothing here knows the app's arithmetic: the answer is judged against the
    fee the origin publishes, both for fitting and for being the largest
    whole cent that fits, because a maximum that leaves a cent on the account is
    the same defect wearing a friendlier number."""
    info = json.loads(_get("/api/info")[2])
    fee_rate = Decimal(str(info["fees"]["withdraw_pct"])) / 100
    floor = Decimal(str(info["limits"]["min_withdraw_usdc"]))
    balance = Decimal("3.4653")
    probe = open_layer(
        browser, "openWithdrawSheet", "dark", balance=float(balance),
        act=SHEET_MAX_CLICK_JS, walk=False,
    )
    acted = probe["acted"]
    assert acted, "the Max tap never ran: the law would measure an empty dictionary"
    assert not acted.get("error"), acted.get("error")
    assert acted["target"] == "wdAmt", acted

    typed = Decimal(acted["typed"])

    def total(amount: Decimal) -> int:
        """What the ledger would debit: the amount plus its own fee, in micro."""
        micro = int(amount * 1_000_000)
        fee = max(int((Decimal(micro) * fee_rate).to_integral_value(rounding=ROUND_CEILING)), 1)
        return micro + fee

    held = int(balance * 1_000_000)
    assert typed >= floor, f"Max typed {typed}, below the {floor} this origin refuses under"
    assert total(typed) <= held, (
        f"Max typed {typed} whose total {total(typed)} micro is over a {balance} balance")
    assert total(typed + Decimal("0.01")) > held, (
        f"Max typed {typed} while {typed + Decimal('0.01')} also fits the {balance} balance")


def test_the_deposit_sheet_names_the_chain_the_asset_and_the_window_this_origin_publishes(browser):
    """An address alone is not enough to send money to: the same 42 characters
    mean a different token on a different chain, the funds land only after the
    sweep's confirmation window, and the address is shared custodial space. Every
    one of those figures is published by this origin, so the sheet quotes them
    from `/api/info` — and a link that goes anywhere else is a link the config
    never approved."""
    info = json.loads(_get("/api/info")[2])
    counted = open_layer(browser, "openDepositSheet", "dark", walk=False)["counted"]
    assert len(counted["rows"]) >= 3, f"the deposit sheet paints {counted['rows']}"
    values = [row["value"] for row in counted["rows"]]
    assert info["chain_name"] in values, f"{values} names no chain this origin publishes"
    assert "USDC" in values, f"{values} never says which asset is credited"
    confirmations = [
        number for value in values for number in _painted_decimals(value)
        if number == number.to_integral_value()
    ]
    window = Decimal(str(info["limits"]["deposit_confirmations"]))
    assert window in confirmations, (
        f"the sheet paints {values} while /api/info publishes a {window}-block window")
    assert info["usdc_address"] in counted["painted"], (
        "the deposit sheet shows no token contract beside its address")
    expected = f"{info['explorer']}/token/{info['usdc_address']}"
    assert expected in counted["links"], (
        f"the sheet links {counted['links']} where {expected} was published")
    for href in counted["links"]:
        assert href.startswith(f"{info['explorer']}/"), (
            f"a deposit link leaves the published explorer: {href}")


DETAIL_SEED_JS = """
({balance, known} = {}) => {
  /* The market detail is the screen a bet is actually placed on, and like the
     sheets it is not in the rendering until a card is tapped: `#detailView`
     ships `hidden`, its rows come from STATE.markets, and `openMarketDetail` is
     the same call the card makes — so the seeded row goes through the real
     template, not a copy of it. The balance is supplied the same way the sheet
     seed supplies it, because the buy panel inside this layer offers quick
     amounts and a panel that offers them cannot be judged without one. */
  if (typeof openMarketDetail !== 'function') return {error: 'the Mini App exposes no market detail'};
  if (balance !== undefined && balance !== null) {
    STATE.tg_id = known === false ? null : 1;
    STATE.balance_usdc = balance;
  }
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
    lang: STATE.lang,
    rows: [...view.querySelectorAll('.wd-row')].map((el) => ({
      label: el.querySelector('span') ? el.querySelector('span').textContent.trim() : '',
      value: el.querySelector('b') ? el.querySelector('b').textContent.trim() : '',
    })),
    chips: [...view.querySelectorAll('.chip')].map((el) => ({
      text: el.textContent.trim(), val: el.dataset.val, active: el.classList.contains('active'),
    })),
    fields: [...view.querySelectorAll('input[type=number], input[inputmode=decimal]')].map((el) => ({
      id: el.id, min: el.min, max: el.max, step: el.step, value: el.value,
    })),
  };
}
"""


def open_detail(browser, theme, balance=None, known=True, walk=True):
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
        seeded = page.evaluate(DETAIL_SEED_JS, {"balance": balance, "known": known})
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
    the two disagree, and a user reads the bar first.

    The slack is one pixel of that row's own track plus the half-tick of the 1 dp
    the text rounds to; it used to be a flat 1.5 %, which was the template's
    1 % floor to a zero row forgiven rather than a measurement."""
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
            one_pixel = 100.0 / row["trackWidth"] if row["trackWidth"] else 0.5
            assert abs(row["barPct"] - row["textPct"]) <= one_pixel + 0.05, (
                f"tab{tab} row {index} ({row['label']}): полоса {row['barPct']}%, "
                f"написано {row['textPct']}% (трек {row['trackWidth']}px, "
                f"допуск {one_pixel + 0.05:.2f}pp)"
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


def _get_with_session(path: str) -> tuple[int, str, str]:
    """The same probe a signed-in Mini App makes. `/api/mini/*` answers the
    session, so an anonymous GET would be a different document and a comparison
    against it would judge the probe rather than the page."""
    request = urllib.request.Request(
        f"{PROD_URL}{path}",
        headers={
            "user-agent": USER_AGENT,
            "x-forwarded-for": XFF_POOL[7],
            "cookie": f"{session_cookie_name()}={SESSION_COOKIE}",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.headers.get("content-type", ""), response.read().decode("utf-8", "replace")


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
