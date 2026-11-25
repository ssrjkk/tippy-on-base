"""Design-system gates: tokens, contrast, structure, visualization geometry.

No browser and no database — the design contract in `web/static/*` is measured
numerically. Every rule below is a ratio, a set difference or a count, so a change
that breaks the system cannot pass by looking roughly like what it replaced.

Three deliberate choices:

* **Contrast is measured twice, in both stylesheets.** Once for the exact
  `color`/`background` pair a rule declares — on each surface's own boards, since
  the site alternates canvas with a grey band and the Mini App paints on its
  cards — and once for every colour used as `color:` against both page canvases.
  The first finds a broken button; the second finds a muted label that
  was legal on white and unreadable after it moved onto the grey band.
* **The site and the Mini App must agree on colours by value.** `app.html` carries
  its own palette with its own names (`--text3` where the site says `--muted`), so
  a mapped comparison is the only way drift shows up before a user sees two
  different greys in one product. Because that map exists, the rest of the palette
  laws — raw hex outside the tokens, a token nothing reads, a chain that never
  terminates, the inverse-text pairs, the coarse-pointer touch floor — are
  parametrized over both stylesheets rather than only the landing's. Measured this
  way the Mini App gave up two things: a `--warning` for a state it never painted,
  and the last declaration of each of its blocks, which a parser that ended
  declarations at `;` alone hid from every gate (`--safe-b`).
* **A dead token, an unsubstituted placeholder and a substitution no template
  asks for all fail.** They are the same drift in three costumes.
* **One number grammar, measured on both sides of the wire.** The bundle's
  formatters are judged for the precision their kind owes and for being handed a
  percentage rather than a ratio; the Python producers behind those fields are
  judged for writing every percentage through `bot/pct.py`, so the face a user
  reads is decided once — round-half-up at one decimal — and not re-derived in
  each of the languages that paint it.
"""

import ast
import json
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"

# Every document the bundle holds. A list of names here would be a list someone
# has to update, and a page added to the directory while the list still described
# the older set is a page every law below walks past — so the contract reads the
# directory, and a new file of any of these kinds is inside every law the moment
# it exists.
PAGES = tuple(sorted(p.name for p in STATIC.glob("*.html")))
SCRIPTS = tuple(sorted(p.name for p in STATIC.glob("*.js")))
STYLESHEETS = tuple(sorted(p.name for p in STATIC.glob("*.css")))
DOCUMENTS = (*PAGES, *SCRIPTS, *STYLESHEETS)

AA_TEXT = 4.5        # WCAG AA, body text
AA_LARGE = 3.0       # WCAG AA, >=24px or >=18.66px bold
LARGE_TEXT_PX = 24

# Values the redesign removed on purpose. A regression is invisible in review.
BANNED_HEX = ("#0052ff",)
BANNED_FONTS = ("inter", "jetbrains mono")

# The site's token name → the name app.html gives the same role. The Mini App is
# one self-contained document with its own palette; this map is what makes it
# possible to prove the two are still the same design.
SITE_TO_MINI = {
    "--blue": "--accent",
    "--blue-hover": "--accent2",
    "--blue-text": "--accent-text",
    "--blue-soft": "--blue-soft",
    "--blue-line": "--blue-line",
    "--canvas": "--bg",
    "--surface": "--card2",
    "--surface-hover": "--card3",
    "--border": "--border",
    "--border-strong": "--border-strong",
    "--ink": "--text",
    "--ink-2": "--text2",
    "--muted": "--text3",
    "--muted-2": "--text4",
    "--success": "--success",
    "--success-bg": "--success-bg",
    "--danger": "--danger",
    "--warning": "--warning",
    "--nav-bg": "--nav-bg",
    "--on-accent": "--on-accent",
}

PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8-sig")


CSS = read("style.css")
APP_HTML = read("app.html")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def block_body(css: str, selector: str) -> tuple[int, int]:
    """Character span of the body of the first rule that starts with `selector`."""
    start = css.index(selector)
    brace = css.index("{", start)
    depth = 0
    for i in range(brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return brace + 1, i
    raise AssertionError(f"unbalanced block after {selector}")


DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+?)\s*(?:;|\}|$)")


def declarations(css: str, span: tuple[int, int]) -> dict[str, str]:
    out: dict[str, str] = {}
    body = css[slice(*span)]
    for name, value in DECL_RE.findall(body):
        assert name not in out, f"{name} is declared twice in one block"
        out[name] = value.strip()
    # A semicolon is optional before `}`, so a parser that ends declarations at
    # `;` alone silently loses the last token of every block — which is how
    # `--safe-b` sat in the Mini App palette unread by a single gate.
    dropped = sorted({m.group(1) for m in re.finditer(r"(--[a-z0-9-]+)\s*:", body)
                      if m.group(1) not in out})
    assert not dropped, f"declared in a token block but unread by this parser: {dropped}"
    return out


LIGHT_SPAN = block_body(CSS, ':root, [data-theme="light"]')
DARK_SPAN = block_body(CSS, '[data-theme="dark"]')
LIGHT = declarations(CSS, LIGHT_SPAN)
DARK = {**LIGHT, **declarations(CSS, DARK_SPAN)}  # the cascade the browser applies

MINI_LIGHT_SPAN = block_body(APP_HTML, ":root,[data-theme=\"light\"]")
MINI_DARK_SPAN = block_body(APP_HTML, "[data-theme=\"dark\"]")
MINI_LIGHT = declarations(APP_HTML, MINI_LIGHT_SPAN)
MINI_DARK = {**MINI_LIGHT, **declarations(APP_HTML, MINI_DARK_SPAN)}


# The palette laws below are laws about a stylesheet, not about the landing. The
# Mini App keeps its own palette in its own document, so a gate that read only
# style.css proved one of the two surfaces and let the same defect ship twice —
# which is exactly how the 4.27:1 selected chip came back on a board nothing had
# opened. `readers` is who may consume these tokens: style.css is read by every
# page and script, app.html is a closed document that reads only itself.
TOKEN_SURFACES = [
    pytest.param(CSS, LIGHT, DARK, DARK_SPAN, (*PAGES, *SCRIPTS), id="style_css"),
    pytest.param(APP_HTML, MINI_LIGHT, MINI_DARK, MINI_DARK_SPAN, (), id="app_html"),
]


def resolve(tokens: dict[str, str], name: str, _seen: frozenset = frozenset()) -> str:
    """Follow a var() chain to a concrete value, the way the browser would."""
    if name in _seen:
        raise AssertionError(f"cyclic token chain at {name}")
    value = tokens.get(name)
    assert value is not None, f"{name} is not defined"
    match = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    return resolve(tokens, match.group(1), _seen | {name}) if match else value


def parse_color(value: str):
    """`#rgb`/`#rrggbb`/`rgba(…)`/keyword → (r, g, b, a) normalised to 0..1."""
    value = value.strip().lower()
    hex_match = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return (*[int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)], 1.0)
    rgb = re.fullmatch(
        r"rgba?\(\s*([\d.]+)\s*[,\s]\s*([\d.]+)\s*[,\s]\s*([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)", value
    )
    if rgb:
        channels = [float(rgb.group(i)) / 255 for i in (1, 2, 3)]
        return (*channels, float(rgb.group(4)) if rgb.group(4) else 1.0)
    return {"white": (1, 1, 1, 1), "black": (0, 0, 0, 1), "transparent": (0, 0, 0, 0)}.get(value)


def normalized(value: str) -> str:
    """A canonical form for comparing two palettes: same colour, one spelling."""
    parsed = parse_color(value)
    if parsed is None:
        return value.strip().lower()
    r, g, b, a = parsed
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}" + (
        "" if a >= 1.0 else f"/{a:.2f}"
    )


def composite(fg, bg):
    """Alpha-composite fg over bg — what a screenshot does and rgba() does not."""
    a = fg[3]
    return (*[fg[i] * a + bg[i] * (1 - a) for i in range(3)], 1.0)


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color) -> float:
    r, g, b, _ = color
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def as_color(value: str, tokens: dict[str, str]):
    """`var(--x)` or a literal → opaque RGB composited over the page canvas."""
    raw = resolve(tokens, value[4:-1]) if value.startswith("var(") else value
    parsed = parse_color(raw)
    if parsed is None:
        return None
    if parsed[3] >= 1.0:
        return parsed
    canvas = as_color(tokens["--canvas"], tokens)
    return composite(parsed, canvas)


def ratio(fg, bg) -> float:
    l1, l2 = luminance(fg), luminance(bg)
    high, low = max(l1, l2), min(l1, l2)
    return (high + 0.05) / (low + 0.05)


def rule_selector_and_body(css: str) -> list[tuple[str, str]]:
    """Top-level `selector { body }` pairs, comments removed."""
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        (selector.strip()[-90:], body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", stripped)
    ]


def _body_from_open_brace(text: str, brace: int) -> str | None:
    """The characters between a `{` and the `}` that closes it."""
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:i]
    return None


def js_function(text: str, name: str) -> str | None:
    """The body of a named JS function, braces matched. None if it is not there.

    Needed because the design contract is about specific screens — a count over a
    whole file lets one component borrow another component's empty state."""
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text)
    return None if match is None else _body_from_open_brace(text, match.end() - 1)


def js_functions(text: str) -> dict[str, str]:
    """Every named function in the file, body included, in source order.

    A law that names the screens it reads is a law that stops reading when a
    screen is renamed or a new one is added, so the subject set of the repaint
    laws below is the whole answer of this reader."""
    found = {}
    for match in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", text):
        body = _body_from_open_brace(text, match.end() - 1)
        if body is not None:
            found[match.group(1)] = body
    return found


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


def test_every_var_referenced_is_declared_somewhere_in_its_own_surface():
    """style.css and each page are separate documents: a token the Mini App
    defines for itself is legal, one referenced by nothing is not."""
    site_used = set(re.findall(r"var\((--[a-z0-9-]+)\)", CSS))
    missing = sorted(site_used - set(LIGHT))
    assert not missing, f"style.css reads tokens it never defines: {missing}"

    for name in PAGES:
        text = read(name)
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", text))
        declared = {t for t, _ in DECL_RE.findall(text)}
        undefined = sorted(used - declared - set(LIGHT))
        assert not undefined, f"{name} reads undeclared tokens: {undefined}"


@pytest.mark.parametrize("css,light,dark,dark_span,readers", TOKEN_SURFACES)
def test_dark_theme_overrides_no_token_the_light_theme_lacks(css, light, dark, dark_span, readers):
    extra = sorted(set(declarations(css, dark_span)) - set(light))
    assert not extra, f"[data-theme=dark] invents tokens: {extra}"


@pytest.mark.parametrize("css,light,dark,dark_span,readers", TOKEN_SURFACES)
def test_token_chains_terminate_in_both_themes(css, light, dark, dark_span, readers):
    """An unresolved var() invalidates the whole declaration, so the element falls
    back to the UA default and a colour or a font disappears with no error."""
    for name in light:
        for theme, tokens in (("light", light), ("dark", dark)):
            value = resolve(tokens, name)
            assert "var(" not in value, f"{name} in {theme} resolves to {value}"


PRINT_LINE = CSS[: CSS.index("@media print")].count("\n")

COLOUR_SURFACES = [
    # `print_line` is where paper begins on the site: paper is not a theme, so its
    # colours are allowed to be literals. `exempt` names the lines on a surface
    # whose hex is not a paint decision the theme flips.
    pytest.param(CSS, (LIGHT_SPAN, DARK_SPAN), PRINT_LINE, ("--chevron",), id="style_css"),
    pytest.param(APP_HTML, (MINI_LIGHT_SPAN, MINI_DARK_SPAN), None,
                 ('name="theme-color"',), id="app_html"),
]


@pytest.mark.parametrize("css,token_spans,print_line,exempt", COLOUR_SURFACES)
def test_no_raw_colour_outside_the_tokens(css, token_spans, print_line, exempt):
    """Every colour the product paints comes from a token, so the theme flip is
    one job and a brand change is one line. `@media print` is exempt on purpose:
    paper is not a theme, and routing paper white through a token would make the
    dark theme print dark grey. The Mini App's `theme-color` metas are exempt for
    the opposite reason — a browser chrome colour cannot read a CSS variable."""
    offenders = []
    offset = 0
    for number, line in enumerate(css.splitlines(), start=1):
        stripped = line.strip()
        end = offset + len(line)
        # Overlap, not containment: the Mini App writes its whole palette on one
        # line, so the line starts before its own `{` and the token block would
        # never be recognised from inside the declaration span.
        inside_tokens = any(offset < span[1] and end > span[0] for span in token_spans)
        offset = end + 1
        if inside_tokens or stripped.startswith(("*", "/*")) or any(text in line for text in exempt):
            continue
        if print_line is not None and number > print_line:
            continue
        if re.search(r"#[0-9a-f]{3,8}\b", line, re.I):
            offenders.append((number, stripped[:90]))
    assert not offenders, offenders


@pytest.mark.parametrize("literal", BANNED_HEX)
def test_banned_hex_is_absent(literal):
    hits = [name for name in DOCUMENTS if literal in read(name).lower()]
    assert not hits, f"{literal} is back in {hits}: that blue does not exist on base.org"


def test_banned_font_families_are_absent_from_every_font_stack():
    """In `font-family` position and in the token that feeds it: the stacks live
    in `--font-ui` / `--font-mono`, so a banned face added there would never
    appear in a `font-family:` declaration of its own. Only these positions —
    `Inter` is a substring of `counter`, `interface` and `important`, and a
    naive grep would fail the build over prose."""
    offenders = []
    for name in DOCUMENTS:
        text = read(name)
        stacks = re.findall(r"font-family:\s*([^;{}]+)", text)
        stacks += re.findall(r"^\s*--font-[a-z]+\s*:\s*([^;{}]+)", text, re.M)
        for stack in stacks:
            families = {f.strip().strip("\"'").lower() for f in stack.split(",")}
            bad = families & set(BANNED_FONTS)
            if bad:
                offenders.append((name, sorted(bad), stack.strip()[:70]))
    assert not offenders, offenders


@pytest.mark.parametrize("css,light,dark,dark_span,readers", TOKEN_SURFACES)
def test_no_token_is_defined_and_never_read(css, light, dark, dark_span, readers):
    """A token nobody reads is a colour waiting to be edited into disagreement
    with itself, and it is the same drift in the Mini App's own palette: it
    carried a `--warning` for a state it never painted."""
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)\)", css))
    for name in readers:
        referenced |= set(re.findall(r"var\((--[a-z0-9-]+)\)", read(name)))
    dead = sorted(set(light) - referenced)
    assert not dead, f"defined but never read: {dead}"


def test_site_and_mini_app_palettes_agree_value_for_value():
    """Two palettes, one design. Every role the Mini App shares with the site has
    to resolve to the identical colour in each theme, or the same grey means two
    different things depending on which surface the user is standing on."""
    differences = []
    for theme, site, mini in (("light", LIGHT, MINI_LIGHT), ("dark", DARK, MINI_DARK)):
        for site_token, mini_token in SITE_TO_MINI.items():
            if site_token not in site or mini_token not in mini:
                differences.append(f"{theme}: {site_token}/{mini_token} missing")
                continue
            a = normalized(resolve(site, site_token))
            b = normalized(resolve(mini, mini_token))
            if a != b:
                differences.append(f"{theme}: site {site_token}={a} but Mini App {mini_token}={b}")
    assert not differences, differences


def test_the_mini_app_keeps_its_grey_levels_separate():
    """`--text3` and `--text4` are two different weights of quiet text. When they
    collapse to one value the hierarchy is gone and only the gate notices."""
    for theme, tokens in (("light", MINI_LIGHT), ("dark", MINI_DARK)):
        assert normalized(tokens["--text4"]) != normalized(tokens["--text3"]), (
            f"{theme}: --text3 and --text4 are the same colour"
        )


# ---------------------------------------------------------------------------
# contrast
# ---------------------------------------------------------------------------


def _pairs_in_rules(css: str) -> list[tuple[str, str, str, str]]:
    out = []
    for selector, body in rule_selector_and_body(css):
        color = re.search(r"(?:^|;)\s*color:\s*([^;]+)", body)
        background = re.search(r"(?:^|;)\s*background(?:-color)?:\s*([^;]+)", body)
        size = re.search(r"font-size:\s*([\d.]+)px", body)
        if color and background:
            out.append((selector, color.group(1).strip(), background.group(1).strip(), size.group(1) if size else ""))
    return out


def _raw_colour(value: str, tokens: dict[str, str]):
    """Declared value as painted, with its alpha intact — `as_color` flattens
    rgba() onto the canvas, which is exactly the assumption that hid the band."""
    raw = resolve(tokens, value[4:-1]) if value.startswith("var(") else value
    return parse_color(raw.strip()) if raw else None


# Each surface carries its own ladder of boards: the site alternates the canvas
# with a grey band, the Mini App paints its cards, sheets and rows on --card.
PAIR_SURFACES = [
    pytest.param(CSS, LIGHT, DARK, "--canvas", "--surface", id="style_css"),
    pytest.param(APP_HTML, MINI_LIGHT, MINI_DARK, "--bg", "--card", id="app_html"),
]


@pytest.mark.parametrize("css,tokens_light,tokens_dark,canvas,band", PAIR_SURFACES)
def test_declared_text_on_background_pairs_clear_aa(css, tokens_light, tokens_dark, canvas, band):
    failures = []
    for selector, color, background, size in _pairs_in_rules(css):
        floor = AA_LARGE if (size and float(size) >= LARGE_TEXT_PX) else AA_TEXT
        for theme, tokens in (("light", tokens_light), ("dark", tokens_dark)):
            fg = _raw_colour(color, tokens)
            bg = _raw_colour(background, tokens)
            if fg is None or bg is None:
                continue  # gradients and currentColor are governed by their own rules
            boards = [(canvas, _raw_colour(f"var({canvas})", tokens))]
            if bg[3] < 1.0:
                # A translucent fill is only a tint of whatever is behind it, and
                # half of these rules paint on the band instead of the canvas: the
                # rendered gate measured 4.27:1 on a chip this test had been
                # passing at 4.5:1 against the white canvas — and it said nothing
                # about the Mini App at all until its own stylesheet joined, which
                # is how the same 4.27 came back a second time on a selected chip.
                boards.append((band, _raw_colour(f"var({band})", tokens)))
            for name, board in boards:
                laid = composite(bg, board) if bg[3] < 1.0 else bg
                painted = composite(fg, laid) if fg[3] < 1.0 else fg
                measured = ratio(painted, laid)
                if measured < floor:
                    failures.append(
                        f"{theme} {selector}: {color} on {background} over {name} "
                        f"= {measured:.2f}:1 (floor {floor})"
                    )
    assert not failures, sorted(set(failures))


def test_every_text_colour_clears_aa_on_both_canvases():
    """Text is not nailed to one background: a label legal on the white canvas has
    to survive being moved onto the grey band, which is exactly what the section
    alternation does to it."""
    colours = {
        value
        for _selector, body in rule_selector_and_body(CSS)
        for value in re.findall(r"(?:^|;)\s*color:\s*(var\(--[a-z0-9-]+\))", body)
    }
    # Text written for a filled surface is not page text: --on-accent and
    # --on-ink are checked against the fills they belong to, in their own test.
    on_fill = {"var(--on-accent)", "var(--on-ink)", "var(--canvas)", "var(--bg)"}
    failures = []
    for theme, tokens in (("light", LIGHT), ("dark", DARK)):
        canvas = as_color("var(--canvas)", tokens)
        surface = as_color("var(--surface)", tokens)
        for colour in colours:
            if colour in on_fill:
                continue
            fg = as_color(colour, tokens)
            if fg is None:
                continue
            for name, bg in (("--canvas", canvas), ("--surface", surface)):
                if fg == bg:
                    continue
                measured = ratio(fg, bg)
                if measured < AA_TEXT:
                    failures.append(f"{theme}: {colour} on {name} = {measured:.2f}:1")
    assert not failures, sorted(set(failures))


def test_mini_app_text_colours_clear_aa_on_their_canvases():
    """The same law applies to the palette the Mini App ships with itself — it is
    the surface most users actually look at."""
    failures = []
    for theme, tokens in (("light", MINI_LIGHT), ("dark", MINI_DARK)):
        canvas = as_color("var(--bg)", tokens)
        surface = as_color("var(--card2)", tokens)
        for name in ("--text", "--text2", "--text3", "--text4", "--accent-text", "--success", "--danger"):
            fg = as_color(f"var({name})", tokens)
            for bg_name, bg in (("--bg", canvas), ("--card2", surface)):
                measured = ratio(fg, bg)
                if measured < AA_TEXT:
                    failures.append(f"{theme}: {name} on {bg_name} = {measured:.2f}:1")
    assert not failures, sorted(set(failures))


def test_the_type_hierarchy_survives_the_theme_flip():
    """The grey ladder has to stay a ladder in both themes: strictly decreasing,
    every step AA-readable, no two steps so close they are the same colour. It
    says nothing about the size of the steps, because a light theme can fit four
    AA-readable greys between #666 and #111 while a dark theme with a #111 canvas
    cannot — that is a consequence of the gamma curve, not of the design."""
    ladder = ("--ink", "--ink-2", "--muted", "--muted-2")
    for theme, tokens in (("light", LIGHT), ("dark", DARK)):
        canvas = as_color("var(--canvas)", tokens)
        levels = [round(ratio(as_color(f"var({name})", tokens), canvas), 2) for name in ladder]
        assert levels == sorted(levels, reverse=True), f"{theme}: the grey ladder is not ordered: {levels}"
        assert len(set(levels)) == len(levels), f"{theme}: two ladder steps are the same colour: {levels}"
        assert all(level >= AA_TEXT for level in levels[1:]), f"{theme}: a ladder step is under AA: {levels}"


def test_borders_are_visible_but_quiet():
    """A hairline has to be findable without competing with the numbers: below
    ~1.1:1 nobody sees the card edge, above 6:1 the grid shouts at the content."""
    for theme, tokens in (("light", LIGHT), ("dark", DARK)):
        canvas = as_color("var(--canvas)", tokens)
        for name in ("--border", "--border-strong"):
            measured = ratio(as_color(f"var({name})", tokens), canvas)
            assert 1.06 <= measured <= 6.0, f"{theme} {name}: {measured:.2f}:1 against the canvas"
        assert ratio(as_color("var(--border-strong)", tokens), canvas) > ratio(
            as_color("var(--border)", tokens), canvas
        ), f"{theme}: --border-strong is not stronger than --border"


# Where a page inverts — white on the brand fill, dark on a black button — the
# text colour is a token of its own, so a theme flip cannot leave white text on
# a white button. The Mini App has no ink-filled surface: its active tab changes
# colour and paints an underline, so it contributes the accent pairs only.
INVERSE_TEXT_SURFACES = [
    pytest.param(LIGHT, DARK, [("--on-accent", "--blue"), ("--on-accent", "--blue-hover"),
                               ("--on-ink", "--ink")], id="style_css"),
    pytest.param(MINI_LIGHT, MINI_DARK, [("--on-accent", "--accent"),
                                         ("--on-accent", "--accent2")], id="app_html"),
]


@pytest.mark.parametrize("tokens_light,tokens_dark,pairs", INVERSE_TEXT_SURFACES)
def test_text_on_a_filled_surface_is_a_token_and_clears_aa(tokens_light, tokens_dark, pairs):
    """Buttons and tabs are the two places where the page colour inverts. Those
    text colours are tokens, so the fill/text pairs are checked here instead of
    being decided per rule — and both themes are checked, because the dark theme
    paints its accent lighter, not darker."""
    for text_token, _fill in pairs:
        assert text_token in tokens_light, f"no {text_token} token"
    for theme, tokens in (("light", tokens_light), ("dark", tokens_dark)):
        for text_token, fill in pairs:
            measured = ratio(as_color(f"var({text_token})", tokens),
                             as_color(f"var({fill})", tokens))
            assert measured >= AA_TEXT, f"{theme}: {text_token} on {fill} = {measured:.2f}:1"


# ---------------------------------------------------------------------------
# typography
# ---------------------------------------------------------------------------


def test_mono_implies_tabular_digits():
    """One law instead of a list of exceptions: anything drawn in the mono face
    carries fixed-width digits. A rule that forgets it looks right until a column
    gets a 1 and a 7 in the same place, and the `font:` shorthand resets the
    longhand — so a block that uses the shorthand has to name the variant after it.

    Both documents are measured: the Mini App keeps its own stylesheet, and a law
    asserted only on style.css leaves every timestamp column in /app jittering.
    """
    for surface, css in (("style.css", CSS), ("app.html", APP_HTML)):
        offenders = []
        for selector, body in rule_selector_and_body(css):
            if "var(--font-mono)" not in body:
                continue
            if "tabular-nums" not in body:
                offenders.append(f"{selector.strip().splitlines()[-1][:70]} — mono without tabular")
        resets = []
        for selector, body in rule_selector_and_body(css):
            if "var(--font-mono)" not in body:
                continue
            shorthand = re.search(r"(?:^|;)\s*font\s*:[^;]+", body)
            if not shorthand:
                continue
            variant = re.search(r"font-variant-numeric[^;]*", body)
            if variant is None or variant.start() < shorthand.start():
                resets.append(
                    f"{selector.strip().splitlines()[-1][:70]} — `font:` at {shorthand.start()} "
                    f"resets the variant declared at {variant.start() if variant else 'nowhere'}"
                )
        assert not offenders, f"{surface}: {offenders}"
        assert not resets, f"{surface}: the font: shorthand wipes the variant after it: {resets}"


TRACKED_VOICE_PROPS = ("font-size", "font-family", "font-weight", "letter-spacing",
                       "color", "font-variant-numeric")


def rule_declarations(body: str) -> dict[str, str]:
    """Every `prop: value` pair of one rule block, whitespace collapsed.

    Different from `declarations()`, which exists for the token blocks and refuses a
    property it did not read: this one is for ordinary rules, keeps the last word per
    property and ends a value at `}` as well as at `;`, because the last declaration of
    a one-line rule is written without its semicolon.
    """
    return {name.strip(): " ".join(value.split())
            for name, value in re.findall(r"([a-z-]+)\s*:\s*([^;{}]+)", body)}


def tracked_uppercase_voice(css: str) -> dict[str, dict[str, str]]:
    """Each class a sheet paints in tracked caps, with the declarations of that rule.

    The premise is the file's own answer, not a list of label classes: a rule counts
    when it says `text-transform: uppercase` and names a `letter-spacing`. A grouped
    selector claims the voice for every class in it, which is how the Mini App's
    `.eyebrow,.input-label,.mini-label` speaks for three, and a `::after` marker claims
    it for the classes it is drawn on because the marker is painted in their name.
    """
    found: dict[str, dict[str, str]] = {}
    for selector, body in rule_selector_and_body(css):
        decls = rule_declarations(body)
        if decls.get("text-transform") != "uppercase" or "letter-spacing" not in decls:
            continue
        for cls in re.findall(r"\.([a-z][a-z0-9-]*)", selector):
            found.setdefault(cls, decls)
    return found


def _letter_spacing(value: str) -> str:
    """`0.1em` and `.1em` are one tracking written two ways."""
    match = re.fullmatch(r"(-?[\d.]+)em", value)
    return f"{float(match.group(1)):.3f}em" if match else value


def _site_value_in_mini_words(value: str) -> str:
    """Rewrite the tokens of a site declaration into the names the Mini App uses."""
    return re.sub(r"var\((--[a-z0-9-]+)\)",
                  lambda m: f"var({SITE_TO_MINI.get(m.group(1), m.group(1))})", value)


def test_a_tracked_uppercase_voice_always_takes_the_mono_face():
    """Uppercase with a tracking is this product's annotation voice — a chip, a state
    tag, a caption under a figure — and every one of them is drawn in the money face.
    A rule that sets the caps and the tracking but names no family silently inherits
    the interface face, and the difference is two alphabets of the same word painted
    two ways on one screen: `.gauge-cap` did exactly that while its three siblings in
    `style.css` were mono.

    Subject set: every tracked rule of every shipped stylesheet, so a caption invented
    tomorrow is inside the law the moment it is written. The size is not judged — the
    voice steps down from `--t-chip` for a label above a thing to `--t-micro` for an
    inline annotation, and both are the design — the family is.
    """
    measured = 0
    for surface, css in (("style.css", CSS), ("app.html", APP_HTML)):
        voice = tracked_uppercase_voice(css)
        measured += len(voice)
        offenders = sorted(
            f"{cls} ({decls.get('font-family', 'no font-family at all')})"
            for cls, decls in voice.items()
            if decls.get("font-family") != "var(--font-mono)"
        )
        assert not offenders, f"{surface}: tracked caps in a face other than the mono one: {offenders}"
    assert measured >= 16, (
        f"only {measured} tracked rules read across both stylesheets — a reader that "
        "went blind would report a product with no annotation voice at all"
    )


def test_a_label_voice_both_stylesheets_paint_is_one_voice():
    """The Mini App is one document with its own palette, which is why the landing and
    the app can paint the same `.eyebrow` at two weights and no gate notices until a
    user moves between them. For a class both surfaces declare in tracked caps, every
    typographic declaration must be the same voice once the token names are translated —
    measured as `.eyebrow` and `.stat-label`, and the pair grows by being written.

    What is compared is what the two rules each say: a property one surface names and
    the other leaves to the cascade is reported too, because the cascade is where a
    second voice comes from.
    """
    site = tracked_uppercase_voice(CSS)
    mini = tracked_uppercase_voice(APP_HTML)
    shared = sorted(set(site) & set(mini))
    assert len(shared) >= 2, (
        f"the two sheets share {len(shared)} tracked label classes ({shared}) — below two "
        "this law reads nothing and cannot notice a voice that drifted"
    )

    for cls in shared:
        for prop in TRACKED_VOICE_PROPS:
            want, got = site[cls].get(prop), mini[cls].get(prop)
            if want is not None:
                want = _site_value_in_mini_words(want)
            if prop == "letter-spacing" and want is not None:
                want = _letter_spacing(want)
            if prop == "letter-spacing" and got is not None:
                got = _letter_spacing(got)
            assert want == got, (
                f".{cls}: the landing paints {prop}={want}, the Mini App paints {prop}={got}"
            )


def test_the_tracked_voice_reader_reads_the_shapes_a_label_rule_takes():
    """Both label laws judge a class by what its rule declares, so the reader is held
    to the shapes those declarations actually take: a grouped selector, a value written
    without its trailing semicolon, a `.1em` tracking, a `::after` marker, a rule that
    sets caps without a tracking (a heading, not an annotation) and a tracking without
    caps (prose, not an annotation)."""
    sheet = """
    .a,.b { text-transform: uppercase; letter-spacing: 0.1em; font-family: var(--font-mono) }
    .c { text-transform: uppercase; letter-spacing: .08em; font-family: var(--ui) }
    .d { text-transform: uppercase; font-family: var(--font-mono) }
    .e { letter-spacing: 0.1em; font-family: var(--font-mono) }
    .f .g::after { text-transform: uppercase; letter-spacing: 0.06em;
                   font-family: var(--font-mono); color: var(--muted) }
    /* .h { text-transform: uppercase; letter-spacing: 0.1em } */
    """
    voice = tracked_uppercase_voice(sheet)
    assert set(voice) == {"a", "b", "c", "f", "g"}, (
        f"the reader answered {sorted(voice)} on markup with a known answer")
    assert voice["a"] is voice["b"], "a grouped selector must be one voice claimed twice"
    assert voice["c"]["font-family"] == "var(--ui)", "the last word of a rule must survive"
    assert voice["f"]["color"] == "var(--muted)", (
        "a marker split over lines lost its declarations")

    assert _letter_spacing("0.1em") == _letter_spacing(".1em")
    assert _letter_spacing("0.1em") != _letter_spacing("0.11em")
    assert _site_value_in_mini_words("var(--muted)") == "var(--text3)"
    assert _site_value_in_mini_words("var(--font-mono)") == "var(--font-mono)", (
        "a token with no counterpart keeps its own name rather than becoming nothing")


INPUT_TAG_RE = re.compile(r"<input\b[^>]*?/?>", re.S)
LABEL_TEXT_RE = re.compile(r"""(?:class="input-label"|class="label")[^>]*>([^<]{1,160})""")
CLASS_ATTR_RE = re.compile(r"""class=[\"']([^\"']*)[\"']""")
MONEY_TAG_RE = re.compile(r"""type=[\"']number[\"']""")
DECIMAL_TAG_RE = re.compile(r"""inputmode=[\"']decimal[\"']|step=[\"']0\.\d+[\"']""")
ADDRESS_TAG_RE = re.compile(r"""placeholder=[\"']0x""", re.I)
ADDRESS_LABEL_RE = re.compile(r"""address|адрес""", re.I)


def typed_field_spans(text: str) -> list[tuple[int, int, str]]:
    """Every `<input>` this file types money or an address into, as a span.

    Money is a numeric field the markup itself calls decimal (`inputmode="decimal"`
    or a fractional `step`) or whose own label says USDC; an address is the field
    whose placeholder starts with `0x` or whose label names an address. A count —
    hours, shares — is neither, and stays in the interface face. The placeholder
    of a text field is prose («будет ли BTC выше 120k USDC?») and never decides
    the kind on its own."""
    labels = [(m.end(), m.group(1)) for m in LABEL_TEXT_RE.finditer(text)]
    found = []
    for tag in INPUT_TAG_RE.finditer(text):
        chunk = tag.group(0)
        nearby = [lab for pos, lab in labels if pos <= tag.start() < pos + 500]
        label = nearby[-1] if nearby else ""
        if MONEY_TAG_RE.search(chunk) and (DECIMAL_TAG_RE.search(chunk) or "USDC" in label):
            found.append((tag.start(), tag.end(), "money"))
        elif ADDRESS_TAG_RE.search(chunk) or ADDRESS_LABEL_RE.search(label):
            found.append((tag.start(), tag.end(), "address"))
    return found


def test_every_money_and_address_field_is_drawn_in_the_mono_face():
    """A typed amount is read as a number, not as a word: in the interface face
    "1 000,00" and "11,00" start at different widths and a `0x` address is proof-
    read letter by letter, so the field the user checks before signing is the one
    field the contract says must be mono.

    The subject set is every `<input>` on every page, so a new money sheet joins
    by existing. Both directions are measured: a money field in the UI face
    fails, and a field that puts the money face on something that is neither
    money nor an address fails too. The Mini App is judged against its own
    stylesheet, because that is the one that paints it.
    """
    measured: list[tuple[str, str]] = []
    for page in PAGES:
        text = read(page)
        css = APP_HTML if page == "app.html" else CSS
        declared = [
            selector.strip()
            for selector, body in rule_selector_and_body(css)
            if ".mono" in selector.split(",")[0].split() or selector.strip() == ".mono"
        ]
        assert declared, f"{page}: nothing declares a .mono class in the stylesheet that paints it"
        mono_ok = any(
            "var(--font-mono)" in body and "tabular-nums" in body
            for selector, body in rule_selector_and_body(css)
            if re.search(r"(^|[\s,])\.mono([\s,{]|$)", selector)
        )
        assert mono_ok, f"{page}: .mono does not pair the mono face with tabular digits"

        kinds = {(start, end): kind for start, end, kind in typed_field_spans(text)}
        bare, wrong = [], []
        for tag in INPUT_TAG_RE.finditer(text):
            classes = " ".join(CLASS_ATTR_RE.findall(tag.group(0))).split()
            kind = kinds.get((tag.start(), tag.end()))
            if kind and "mono" not in classes:
                bare.append(f"{page}: {kind} field {tag.group(0)[:90]}")
            if "mono" in classes and not kind:
                wrong.append(
                    f"{page}: the money face on a field that is neither money nor address: {tag.group(0)[:90]}"
                )
        assert not bare, f"money/address fields painted in the interface face: {bare}"
        assert not wrong, wrong
        measured.extend((page, kind) for kind in kinds.values())

    money = sum(1 for _, kind in measured if kind == "money")
    addresses = sum(1 for _, kind in measured if kind == "address")
    assert money >= 8, f"only {money} money fields were read — this law has stopped measuring"
    assert addresses >= 2, f"only {addresses} address fields were read"


# A name is read from the payload fields the runtime answers a person with: the
# handle or username on a row, and the label the `recent_tips` rows carry — that
# array holds people, `o.label` holds market options and `tx.label` holds a
# ledger line, so the reader names the row it means.
NAME_READ_RE = re.compile(r"\.(?:handle|username|nickname|user_name)\b|\br\.label\b")
# An element that closes itself paints no children, so it never joins the chain.
VOID_TAGS = frozenset((
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr", "use", "path", "circle", "rect", "line",
    "polygon", "polyline", "ellipse", "stop",
))
TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w:.-]*)([^>]*)>", re.S)
CLASS_ATTR_RE = re.compile(r"""class\s*=\s*(\\?["'])(.*?)\1""", re.S)


def _class_tokens(tag_text):
    return {
        c
        for value in CLASS_ATTR_RE.findall(tag_text)
        for c in value.split()
        if re.fullmatch(r"[a-zA-Z][\w-]*", c)
    }


def _enclassed_chain(text, pos):
    """Class sets of the elements the offset sits inside, innermost first.

    The walk goes backwards from the name and balances on closes, so a template
    that opens a `<div>` in one string and closes it in another cannot corrupt it
    the way a forward pass over the whole file would."""
    start = max(0, pos - 20000)
    pending = 0
    chain = []
    for match in reversed(list(TAG_RE.finditer(text, start, pos))):
        closing, tag, attrs = match.group(1), match.group(2).lower(), match.group(3)
        if tag in VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        if closing:
            pending += 1
        elif pending:
            pending -= 1
        else:
            chain.append(_class_tokens(match.group(0)))
    return chain


def _font_rules(css):
    """`[(ancestor_requirements, matched_classes, specificity, order, family)]`
    for every rule that names a font family with a plain class as its subject."""
    rules = []
    for order, (selector, body) in enumerate(rule_selector_and_body(css)):
        found = re.findall(r"(font-family|font-variant-numeric)\s*:\s*([^;{}]+)", body)
        family = next((value for name, value in found if name == "font-family"), None)
        if family is None:
            continue
        parts = [p for p in re.split(r"[\s>+~]+", selector) if p]
        if not parts:
            continue
        matched = set(re.findall(r"\.([a-zA-Z][\w-]*)", parts[-1]))
        if not matched:
            continue
        ancestors = [set(re.findall(r"\.([a-zA-Z][\w-]*)", p)) for p in parts[:-1]]
        rules.append(
            (
                ancestors,
                matched,
                len(matched) + sum(len(a) for a in ancestors),
                order,
                family.strip(),
            )
        )
    return rules


def _family_at(chain, level, rules):
    """The family a browser would compute for the chain element at `level`: the
    most specific rule matching it, or the answer inherited from its parent."""
    candidates = [
        rule
        for rule in rules
        if rule[1] <= chain[level]
        and all(any(required <= other for other in chain[level + 1:]) for required in rule[0])
    ]
    if candidates:
        return max(candidates, key=lambda rule: (rule[2], rule[3]))[4]
    return _family_at(chain, level + 1, rules) if level + 1 < len(chain) else ""


def test_no_element_paints_a_person_name_in_the_mono_face():
    """The mono contract has two halves, and until now only one was measured.
    `test_every_money_and_address_field_is_drawn_in_the_mono_face` walks `<input>`
    tags, so it could not see the Mini App's recent-recipient chips: `.chip` puts
    the money face on everything in it, and the people a user already tips were
    drawn as if they were amounts — letters spaced for columns, `tabular-nums`
    aligning nothing, and a handle that reads like a checksum.

    The subject set is every shipped markup file and renderer, each judged against
    the stylesheet that actually paints it. A name is the payload field the
    runtime answers with (`handle`, `username`, `nickname`), so the law follows a
    new screen by reading the tree, not by remembering its class. The cascade is
    resolved the way a browser does it: the innermost element carrying a matching
    `font-family` answers, so `.chip.chip-name` resets the chip that carries it and
    a class added to the markup is what the gate requires, not a comment."""
    measured = []
    offenders = []
    for name in (*PAGES, *SCRIPTS):
        text = read(name)
        # `app.html` is the one document that ships its own stylesheet; every other
        # file in the directory, `app.js` included, is painted by `style.css`.
        css = APP_HTML if name == "app.html" else CSS
        rules = _font_rules(css)
        for hit in NAME_READ_RE.finditer(text):
            chain = _enclassed_chain(text, hit.start())
            if not chain:
                continue
            measured.append((name, hit.group(0)))
            family = _family_at(chain, 0, rules)
            if "--font-mono" in family:
                offenders.append(
                    f"{name}: {hit.group(0)} painted in {family} by the chain {chain[:3]}"
                )
    assert measured, "no shipped file paints a person's name — this law reads nothing"
    assert len(measured) >= 6, (
        f"only {len(measured)} name sites were read — the reader has gone blind"
    )
    assert not offenders, f"the money face is drawing a name: {offenders}"


def test_the_name_face_reader_refuses_the_two_shapes_it_exists_for():
    """A law that only ever answers "clean" is a reader that stopped reading. The
    chip is run against both mutations the finding actually was: the class taken
    out of the markup, and the reset taken out of the stylesheet."""
    rules = _font_rules(APP_HTML)
    reset = [rule for rule in rules if "chip-name" in rule[1]]
    assert reset, "nothing resets the chip face for a name"
    chip = [rule for rule in rules if rule[1] == {"chip"}]
    assert chip and "--font-mono" in chip[0][4], ".chip no longer puts the money face on a chip"

    at_the_chip = _enclassed_chain(APP_HTML, APP_HTML.index("esc(r.label)"))
    assert any("chip-name" in classes for classes in at_the_chip), at_the_chip
    class_removed = [
        classes - {"chip-name"} if "chip-name" in classes else classes for classes in at_the_chip
    ]
    assert "--font-mono" in _family_at(class_removed, 0, rules)

    reset_removed = [rule for rule in rules if "chip-name" not in rule[1]]
    assert "--font-mono" in _family_at(at_the_chip, 0, reset_removed)


def test_an_action_tile_is_one_word_in_every_language_the_mini_app_ships():
    """The three money tiles share one row: `.action-btn{flex:1}` at 390 px gives
    each of them roughly a third of a phone, and the tip tile was labelled
    `send_tip` — «Отправить чаевые» — while its neighbours read «Пополнить» and
    «Вывести». One label wrapped to two lines, so the row had a taller card, a
    ragged icon baseline and a label nothing else on the screen says.

    The subject set is the tiles the shipped markup carries, and the copy is the
    Mini App's own three dictionaries, so a fourth tile or a new translation is
    measured the day it joins. What the law refuses is whitespace: a label with a
    space in it fits one line here only by luck of the language.
    """
    def values(language):
        return dict(
            re.findall(
                r'([a-z][a-z0-9_]*)\s*:\s*"((?:[^"\\]|\\.)*)"', mini_dict_line(language)
            )
        )

    tiles = re.findall(r'<button class="action-btn"[\s\S]*?</button>', APP_HTML)
    assert len(tiles) >= 3, f"only {len(tiles)} action tiles were read"
    dicts = {language: values(language) for language in ("ru", "en", "zh")}
    offenders = []
    for tile in tiles:
        keys = re.findall(r'data-i18n="([a-z0-9_]+)"', tile)
        assert len(keys) == 1, f"a tile paints {len(keys)} labels: {tile[:120]}"
        for language in ("ru", "en", "zh"):
            assert keys[0] in dicts[language], f"{language} answers no {keys[0]}"
            copied = dicts[language][keys[0]]
            if copied.split() != [copied] or len(copied) > 10:
                offenders.append(f"{language}:{keys[0]}={copied!r}")
    assert not offenders, f"an action tile is labelled with more than one word: {offenders}"


def _class_of_nearest_tag(body: str, needle: str) -> set[str]:
    """Classes of the tag that opens the payload `needle` paints inside.

    The row is built in a template string, so the class a rendered element carries
    is read out of the renderer rather than from a selector someone remembers."""
    at = body.index(needle)
    attrs = re.findall(r'class="([^"]*)"', body[:at])
    assert attrs, f"nothing before {needle!r} carries a class — the reader has gone blind"
    return {
        token
        for token in attrs[-1].split("$")[0].split()
        if re.fullmatch(r"[a-zA-Z][\w-]*", token)
    }


def _row_classes(body: str) -> list[str]:
    """Every static class the renderer puts on an element, in template order."""
    found = []
    for value in re.findall(r'class="([^"]*)"', body):
        found += [t for t in value.split("$")[0].split() if re.fullmatch(r"[a-zA-Z][\w-]*", t)]
    return found


def _subject_classes(selector: str) -> set[str]:
    parts = [p for p in selector.split() if p]
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", parts[-1])) if parts else set()


def test_money_direction_is_painted_once_in_a_history_row():
    """The direction of a movement is one fact, and a history row said it three
    times: the sign, the colour of the amount, and a green or red disc behind the
    arrow. The third is the one that breaks the contract — `style.css` keeps
    `--success`/`--danger` on figures (`.wallet-row b.pos`, `.lb-amt`, `.row-v`),
    so a tinted icon made the Mini App the only surface in the product where a
    state colour paints a piece of chrome, and a row whose money is a refund
    rather than income looked like a withdrawal at a glance.

    The subject set is the row's own class family in app.html's stylesheet, so a
    new part of the row joins the law by existing. The count is pinned too: two
    rules, `.pos` and `.neg`, are what one direction-coloured fact looks like, and
    a third is either the disc back or the colour moved somewhere else. The JS half
    asks that the class and the sign of an amount are keyed off the same field — a
    cell that colours by `kind` while it signs by `incoming` is the same
    contradiction between two lines of one template."""
    family = [(sel, body) for sel, body in rule_selector_and_body(APP_HTML) if ".tx-" in sel]
    assert family, "app.html ships no .tx- rule — this reader reads nothing"
    state = [(sel, body) for sel, body in family
             if re.search(r"--(?:success|danger)(?:-bg)?\b", body)]
    offenders = [sel for sel, _ in state if "amount" not in sel]
    assert not offenders, f"a state colour paints {offenders}: the amount already says it"
    assert len(state) == 2, f"direction is painted by {len(state)} rules, not 2: {state}"

    row = js_function(APP_HTML, "renderHistory")
    assert row, "renderHistory is not a function the reader can find"
    amount = re.search(r'class="tx-amount \$\{([^}]*)\}"[^>]*>\s*\$\{([^}]*)\}', row)
    assert amount, "the amount cell no longer decides its class and its sign from two expressions"
    keys = [re.findall(r"(?:tx|row|mk|bet)\.\w+", group) for group in amount.groups()]
    assert keys[0] and keys[0] == keys[1], f"the sign and the colour disagree about {keys}"


def test_the_history_row_keeps_its_clock_in_the_money_face_and_its_note_out():
    """A timestamp is a figure — it belongs in the columns of a money ladder — and
    the reason, ref or note under it is a sentence. Until this wave the row painted
    both in one `.tx-time` span, so a note like `@mishka_tip` was drawn with the
    tabular money face: the same defect the chip law refuses, one element earlier.

    The two classes are read out of the renderer, not named here: the clock is the
    element wrapping `timeLabel(`, the note is the element wrapping the escaped
    counterparty. A new element carrying either payload is inside the law the
    moment it exists, and a note that moves into the clock's own span fails."""
    rules = _font_rules(APP_HTML)
    row = js_function(APP_HTML, "renderHistory")
    clock = _class_of_nearest_tag(row, "timeLabel(")
    note = _class_of_nearest_tag(row, "esc(sub)")
    assert clock and note, f"the row paints no classed clock or note: {clock} {note}"
    assert clock != note, f"one span {clock} carries both the timestamp and the note"
    clock_family = _family_at([clock], 0, rules)
    assert "--font-mono" in clock_family, f"the timestamp left the money face for {clock_family}"
    assert "--font-mono" not in _family_at([note], 0, rules), "the note is drawn as a checksum"
    tabular = [
        sel
        for sel, body in rule_selector_and_body(APP_HTML)
        if "tabular-nums" in body and clock <= _subject_classes(sel)
    ]
    assert tabular, f"{clock} takes the money face without the fixed digits that come with it"


def test_every_part_of_a_rendered_row_declares_its_own_weight():
    """`.tx-time` set a size and no weight, so the same class painted 500 while it
    sat inside `.tx-title` and 400 on the line below it — one label, two weights,
    decided by which branch of the cascade the markup happened to take. A part of a
    row that sizes its own text has to weight its own text.

    The subject set is the classes the renderer emits, and the stylesheet is read
    with the same last-part matching a browser resolves, so `.tx-amount.pos` answers
    for `tx-amount` and a class nobody weights is reported by name.
    """
    row = js_function(APP_HTML, "renderHistory")
    emitted = list(dict.fromkeys(_row_classes(row)))
    assert len(emitted) >= 5, f"only {len(emitted)} classes read out of the row: {emitted}"
    sized, offenders = [], []
    for cls in emitted:
        for sel, body in rule_selector_and_body(APP_HTML):
            if cls not in _subject_classes(sel) or "font-size" not in body:
                continue
            sized.append(cls)
            if "font-weight" not in body:
                offenders.append(f"{cls} — {sel}")
    assert len(set(sized)) >= 4, (
        f"only {sorted(set(sized))} row parts size their text: the reader lost the sheet"
    )
    assert not offenders, f"a row part takes its weight from a parent: {offenders}"


def test_the_smart_account_line_says_its_state_once():
    """The SmartAccount card answers one question — is the wallet deployed — and it
    answers it twice: a word (`smart_deployed` / `smart_not_deployed`) and a state
    class (`.ok` / `.warn`, the two colours `--success`/`--warning` are reserved
    for). The fetch and the paint were split apart this wave so a language switch
    repaints the line, and a split is exactly where the two halves drift: a card
    that reads `d.enabled` for its colour and `d.deployed` for its word tells the
    user the wallet works in green while the line says it is not deployed.

    The law pairs them **per element**, the way a user sees them: the node whose
    class carries the state and the node whose text carries it are matched by the
    variable each is painted through, and the field each ternary reads has to be
    the same. A first version read the whole function as one set and let a
    hardcoded word pass, because another line still had a ternary in it."""
    body = js_function(APP_HTML, "renderSmartWallet")
    assert body, "renderSmartWallet() is not a function the reader can find"
    toggled: dict[str, set[str]] = {}
    for var, field in re.findall(r"([A-Za-z_$][\w$]*)\.classList\.toggle\([^;]*?\bd\.(\w+)", body):
        toggled.setdefault(var, set()).add(field)
    worded: dict[str, set[str]] = {}
    for var, field in re.findall(
        r"([A-Za-z_$][\w$]*)\.textContent\s*=\s*\bd\.(\w+)\s*\?", body
    ):
        worded.setdefault(var, set()).add(field)
    assert toggled and worded, f"the card paints no state class ({toggled}) or state word ({worded})"
    assert set(toggled) == set(worded), (
        f"the class is toggled on {sorted(toggled)} and the word chosen on {sorted(worded)}"
    )
    for var in toggled:
        assert toggled[var] == worded[var], (
            f"{var} is coloured by {sorted(toggled[var])} and worded by {sorted(worded[var])}"
        )


def test_the_row_reader_refuses_the_shapes_the_three_laws_exist_for():
    """Each of the three laws above is run against the defect it was written for,
    on a copy of the shipped source with one thing moved."""
    rules = _font_rules(APP_HTML)
    assert "--font-mono" in _family_at([{"tx-amount"}], 0, rules)
    assert "--font-mono" in _family_at([{"tx-time"}], 0, rules)
    assert "--font-mono" not in _family_at([{"tx-who"}], 0, rules)

    tinted = APP_HTML.replace(
        ".tx-amount.pos{",
        ".tx-icon.in{background:var(--success-bg);color:var(--success)}\n.tx-amount.pos{",
        1,
    )
    state = [sel for sel, body in rule_selector_and_body(tinted)
             if ".tx-" in sel and re.search(r"--(?:success|danger)(?:-bg)?\b", body)]
    assert len(state) == 3, state
    assert any(".tx-icon" in sel for sel in state if "amount" not in sel), state

    merged = APP_HTML.replace('<span class="tx-who">', '<span class="tx-time">', 1)
    row = js_function(merged, "renderHistory")
    assert _class_of_nearest_tag(row, "timeLabel(") == _class_of_nearest_tag(row, "esc(sub)")

    unweighted = APP_HTML.replace(
        ".tx-time{font-size:var(--t-chip);font-weight:400;",
        ".tx-time{font-size:var(--t-chip);",
        1,
    )
    assert any(
        "font-size" in body and "font-weight" not in body
        for sel, body in rule_selector_and_body(unweighted)
        if "tx-time" in _subject_classes(sel)
    )


def test_no_font_family_bypasses_the_tokens():
    """`inherit` is legitimate — a form control has to take the stack of the page
    it sits in. A literal family name is not: it is a font nothing else uses.

    `@font-face` blocks are the one place a family is *defined* rather than used,
    so they are removed before the scan; every face they name is still held to by
    `test_the_faces_are_served_by_this_origin` and by the file/policy laws below."""
    offenders = []
    for name in DOCUMENTS:
        text = FONT_FACE_RE.sub("", read(name))
        for declaration in re.findall(r"font-family:\s*([^;{}]+)", text):
            value = declaration.strip()
            if value in {"inherit", "unset", "initial"} or value.startswith("var(--font-"):
                continue
            if "--font-ui" in value or "--font-mono" in value:
                continue
            offenders.append((name, value[:70]))
    assert not offenders, offenders


def test_the_ui_stack_carries_a_cyrillic_face():
    """The primary face is Latin-only; a site serving ru must name a face that has
    Cyrillic after it, or the ru UI quietly renders in Arial."""
    for label, stack in (("site", resolve(LIGHT, "--font-ui")), ("Mini App", resolve(MINI_LIGHT, "--font-ui"))):
        assert "golos text" in stack.lower(), f"{label} --font-ui has no Cyrillic face: {stack}"


HEADING_LEVELS = ("h1", "h2", "h3", "h4", "h5", "h6")


def size_declarations(css: str) -> list[tuple[str, str]]:
    """`(selector, value)` for every `font-size` a sheet declares."""
    return [
        (selector.strip()[-70:], value.strip())
        for selector, body in rule_selector_and_body(css)
        for value in re.findall(r"(?:^|;)\s*font-size\s*:\s*([^;]+)", body)
    ]


def test_every_heading_level_has_its_own_size_in_the_reset():
    """A heading is the loudest thing on a screen and the one element type every
    browser already sizes, so an unstyled one looks *almost* right.

    The reset declared family, weight, letter-spacing and colour for `h1..h6` and
    stopped, which left the four panel titles in `index.html` that carry no class —
    «Что доступно после входа», «Мои позиции», «Подтверждение вывода», «Что будет,
    если купить сейчас» — at the user-agent's 1.17em. That is 18.72px: a size on no
    token, sitting between `--t-h4` and `--t-h3`, and a different number in a
    browser whose root font is not exactly 16px. `h5` is 0.83em and `h6` is 0.67em,
    so the same hole waits on the two levels nobody has painted yet — which is why
    the floor is all six rather than the ones in use.

    Measured as a floor, not per occurrence: `.step h3` cannot cover a bare `<h3>`
    standing in another container, so the law asks the sheet to answer for the
    element itself."""
    offenders = []
    for label, css, tokens in (("style.css", CSS, LIGHT), ("app.html", APP_HTML, MINI_LIGHT)):
        floors: dict[str, str] = {}
        for selector, body in rule_selector_and_body(css):
            flat = " ".join(body.split())
            size = re.search(r"(?:^|;)\s*font-size\s*:\s*([^;]+)", flat)
            if not size:
                continue
            for part in selector.split(","):
                bare = part.strip()
                if bare in HEADING_LEVELS:
                    floors[bare] = size.group(1).strip()
        offenders += [f"{label}: {level} has no authored size — the UA sheet paints it"
                      for level in sorted(set(HEADING_LEVELS) - set(floors))]
        offenders += [
            f"{label}: {level} floors at {floors[level]}, which is not a step of the scale"
            for level in sorted(floors)
            if not re.fullmatch(r"var\(--t-[a-z0-9-]+\)", floors[level])
            or floors[level][4:-1] not in tokens
        ]
    assert not offenders, offenders


def test_no_sheet_declares_a_size_that_is_not_a_step_of_the_scale():
    """Twelve px steps, two fluid display sizes, and nothing else. The landing used
    to declare 28 distinct sizes, half of them half-pixel nudges (14.5, 15.5, 13.5,
    11.5, 12.5) that bought no hierarchy and made one word render at three
    different heights depending on which rule the last wave of edits touched.

    A size arrives as `var(--t-…)`, as `inherit`, or not at all: a literal `17px` —
    or a `1.17em` that rediscovers the user-agent ratio the gate above just closed —
    fails here whatever element it hides on, in either stylesheet."""
    offenders = []
    for label, css, tokens in (("style.css", CSS, LIGHT), ("app.html", APP_HTML, MINI_LIGHT)):
        for selector, value in size_declarations(css):
            if value == "inherit":
                continue
            token = re.fullmatch(r"var\((--t-[a-z0-9-]+)\)", value)
            if token is None:
                offenders.append(f"{label}: {selector} — font-size {value}")
            elif token.group(1) not in tokens:
                offenders.append(f"{label}: {selector} — {value} is not declared by {label}")
        for selector, body in rule_selector_and_body(css):
            flat = " ".join(body.split())
            for value in re.findall(r"(?:^|;)\s*font\s*:\s*([^;]+)", flat):
                if re.search(r"\d*\.?\d+(px|em|rem|%|pt)", value):
                    offenders.append(f"{label}: {selector} — font: {value} hides a size")
    assert not offenders, offenders


def test_the_two_stylesheets_share_one_type_scale():
    """`--t-ui` means "the default UI voice" on both surfaces, so it has to be one
    number. The palette is already pinned value for value; the scale was hand-copied
    into `app.html`, and a 1px nudge on one side is the same chip in two heights."""
    shared = sorted(set(LIGHT) & set(MINI_LIGHT))
    scale = [name for name in shared if name.startswith("--t-")]
    assert scale, "no --t-* token is declared on both surfaces"
    drift = [
        f"{name}: site {LIGHT[name]} but Mini App {MINI_LIGHT[name]}"
        for name in scale
        if normalized(resolve(LIGHT, name)) != normalized(resolve(MINI_LIGHT, name))
    ]
    assert not drift, drift


# ---------------------------------------------------------------------------
# shape and spacing
# ---------------------------------------------------------------------------


RADIUS_TOKENS = frozenset(
    f"var({name})"
    for name in ("--radius-2xs", "--radius-xs", "--radius-sm", "--radius", "--radius-lg", "--pill")
)

SPACING_PROPS = ("padding", "margin", "gap", "row-gap", "column-gap")


def spacing_declarations(css: str) -> list[tuple[str, str, str, str]]:
    """`(selector, property, value, flattened body)` for every box-spacing
    declaration in a sheet, longhands included. The body travels with it because
    the divider exemption is a property of the same rule, not of the file."""
    out = []
    for selector, body in rule_selector_and_body(css):
        flat = " ".join(body.split())
        for prop, value in re.findall(
            rf"(?:^|;)\s*({'|'.join(SPACING_PROPS)}(?:-(?:top|right|bottom|left))?)\s*:\s*([^;]+)", flat
        ):
            out.append((selector.strip()[-70:], prop, value.strip(), flat))
    return out


def test_a_one_pixel_gap_is_a_divider_or_a_mistake():
    """1px is the width of a hairline, not of a space. As `gap` behind a rule that
    paints `background: var(--border)` it is how `.markets` and `.rows` draw one
    continuous divider between rows; anywhere else — a padding, a margin, a gap
    over the canvas — it is a sub-pixel sliver that rounds to nothing on a 2x
    screen and to a grey seam on a 1x one, and it is what a chip looks like when
    nobody decided how much air it gets.

    `code` carried `padding: 1px 6px` against a 1px border, which made the border
    the entire vertical padding: the glyph touched the box. Both sites now read
    `2px 6px`, and this is the law that keeps them there."""
    offenders = []
    for label, css in (("style.css", CSS), ("app.html", APP_HTML)):
        for selector, prop, value, body in spacing_declarations(css):
            if not any(component == "1px" for component in value.split()):
                continue
            if prop == "gap" and "background: var(--border)" in body:
                continue
            offenders.append(f"{label}: {selector} — {prop}: {value}")
    assert not offenders, offenders


def test_every_corner_is_a_token_a_zero_or_a_circle():
    """A radius is a system decision: 4, 6, 8, 12, pill, or a circle. The landing
    once shipped a 7px on one card, which is `--radius` and `--radius-sm` arguing
    in the same screenshot, and a component-level rounding difference like that is
    read as sloppiness long before it is read as a number."""
    offenders = []
    for label, css in (("style.css", CSS), ("app.html", APP_HTML)):
        for selector, body in rule_selector_and_body(css):
            for value in re.findall(r"(?:^|;)\s*border-radius\s*:\s*([^;]+)", " ".join(body.split())):
                for component in value.split():
                    if component == "0" or component.endswith("%") or component in RADIUS_TOKENS:
                        continue
                    offenders.append(f"{label}: {selector.strip()[-60:]} — border-radius {component}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# interaction structure
# ---------------------------------------------------------------------------


def test_nothing_hides_itself_with_style_display():
    hits = [name for name in (*PAGES, *SCRIPTS) if ".style.display" in read(name)]
    assert not hits, f"{hits} toggle style.display — use the `hidden` attribute"


MODAL_SCRIM = 'id="modalOverlay"'


def test_anything_clickable_is_a_button():
    """role="button" on a div never fires on Enter and is not focusable, so the
    delegated handler silently stops working for the keyboard. `data-act` on a
    non-button is allowed for exactly one element: the dismissal scrim, which is
    not a control."""
    offenders = []
    for name in PAGES:
        html = read(name)
        if 'role="button"' in html:
            offenders.append(f'{name}: role="button"')
        for tag in re.findall(r"<(div|span|a|li)\b([^>]*)>", html):
            if "data-act=" not in tag[1]:
                continue
            if MODAL_SCRIM in tag[1]:
                continue
            offenders.append(f"{name}: <{tag[0]} {tag[1][:60]}> carries data-act but is not a button")
    assert not offenders, offenders


TOUCH_SURFACES = [
    pytest.param(CSS, id="style_css"),
    pytest.param(APP_HTML, id="app_html"),
]


@pytest.mark.parametrize("css", TOUCH_SURFACES)
def test_touch_floor_exists_and_names_live_classes(css):
    """The 44 px floor is a `@media (pointer: coarse)` block that grows min-height
    only. A selector inside it that exists nowhere else means a control quietly
    lost its thumb coverage when it was renamed. /app is the primary touch
    surface, so its block is measured with the same law as the site's."""
    body = css[slice(*block_body(css, "@media (pointer: coarse)"))]
    selectors = set(re.findall(r"([.#][\w-]+)", body))
    assert selectors, "the coarse-pointer block is empty"
    assert re.search(r"min-height:\s*44px", body), "the touch floor is no longer min-height: 44px"
    rest = css.replace(body, "")
    orphans = sorted(s for s in selectors if s not in rest)
    assert not orphans, f"sized for a thumb but defined nowhere else: {orphans}"


# The only properties a template may set inline: geometry that only data knows.
INLINE_PROPERTIES = {"width", "height", "animation-delay"}
LITERAL_GEOMETRY = re.compile(r"[\d.]+(?:%|ms|px)")


def is_clamped(value: str) -> bool:
    """Generated geometry is bounded, in one of the two honest ways: it goes
    through the bundle's own bar primitive, or it carries its clamp in the
    template. A chart column whose label is an amount may floor for legibility;
    a percentage bar may not, and the law below is what keeps this one from
    approving that shape again."""
    return "barWidth(" in value or "Math.max" in value


def test_inline_styles_are_only_clamped_geometry():
    """Layout belongs in CSS. An inline style may carry exactly one thing: a
    bar's width/height or an animation delay — and a data-driven one has to be
    clamped, because an unclamped 0% bar is invisible and an unclamped stagger
    animates a long list for minutes. Syntax is not checked: what matters is that
    no styling decision escapes the stylesheet, and that the geometry is bounded."""
    offenders = []
    for name in (*PAGES, *SCRIPTS):
        for attr in re.findall(r'style="([^"]*)"', read(name)):
            for declaration in (d for d in attr.split(";") if d.strip()):
                prop, _, value = declaration.partition(":")
                prop, value = prop.strip(), value.strip()
                if prop not in INLINE_PROPERTIES:
                    offenders.append((name, f"{prop} inlined: {declaration.strip()[:80]}"))
                elif LITERAL_GEOMETRY.fullmatch(value):
                    continue
                elif prop == "animation-delay" and "Math.min" not in value:
                    offenders.append((name, f"stagger not capped: {value[:60]}"))
                elif prop in ("width", "height") and not is_clamped(value):
                    offenders.append((name, f"bar not clamped: {value[:60]}"))
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# visualization
# ---------------------------------------------------------------------------


def test_every_inline_svg_has_a_view_box_or_is_a_sprite_sheet():
    """An SVG without a viewBox has no intrinsic scale: it stops tracking the type
    size and overflows a 360 px screen. A hidden sprite sheet is exempt — it is
    never drawn, only referenced by <use>."""
    offenders = []
    for name in (*PAGES, *SCRIPTS):
        for tag in re.findall(r"<svg\b[^>]*>", read(name), re.S):
            if "viewBox" in tag or "sprite" in tag:
                continue
            offenders.append((name, tag[:90]))
    assert not offenders, offenders


def test_generated_bars_are_clamped_and_their_stagger_capped():
    """A 0% bar is invisible and an uncapped animation-delay staggers a long list
    for minutes. Both are geometry bugs the DOM never complains about."""
    offenders = []
    for name in (*PAGES, *SCRIPTS):
        for attr in re.findall(r'style="[^"]*"', read(name)):
            if ("width:${" in attr or "height:${" in attr) and not is_clamped(attr):
                offenders.append(f"{name}: {attr[:80]} — unclamped bar")
            if "animation-delay:${" in attr and "Math.min" not in attr:
                offenders.append(f"{name}: {attr[:80]} — uncapped stagger")
    assert not offenders, offenders


# Every generated bar width in the bundle, and the one primitive that may own it.
# A percentage bar is the number beside it drawn as geometry: `Math.max(pct, 2)`
# stood a 2 % fill under a row that printed «0,4 %», on nine bars — six on the
# site (four in `app.js`, one each in `market.html` and `oc.html`), two in the
# Mini App, one in the agent dashboard's capacity gauge — and on a two-option
# market it set a 0 % share beside a 100 % one and asked the eye to compare
# them. The floor is gone and this law is what keeps it gone: the clamp may
# bound a bar to 0..100 and may not thicken it.
INLINE_STYLE = re.compile(r'style="[^"]*"')
# A generated width inside one inline style. The attribute is taken whole first
# because a bar usually shares it with a stagger (`width:${…}%;animation-delay:
# ${…}ms`), and a pattern that had to end at the closing quote read only the
# bars whose attribute happens to end at the percentage.
PERCENT_WIDTH = re.compile(r'(?<![-\w])width:\$\{(.{0,90}?)\}%')
# ...and the same width when a builder concatenates its way through the number
# instead of interpolating it: `agent.js` closes the quoted piece with `'%"`,
# so the percentage's own quote is the JS string's opener, not another `'%'`
# pair. A reader that only understood `${}` counted that file as shipping no
# bars at all, which is the same hole the class-name parser fell through.
CONCAT_WIDTH = re.compile(r"width:'\s*\+\s*(.{0,90}?)\s*\+\s*'%")


def test_a_percentage_bar_paints_the_percentage_its_row_prints():
    """No bar in this bundle may take its width from anything but the one
    clamping primitive, because a floor in a template is invisible in review and
    is the half of the defect the rendered gate can only catch on a page that
    happens to hold a small share. The rule holds for the agent dashboard's
    capacity gauge too, where the row prints «0.00 / 100 USDC» rather than a
    percentage: a 2 % fill there still claims money the label says is unspent."""
    sites, offenders = 0, []
    for name in (*PAGES, *SCRIPTS):
        for attr in INLINE_STYLE.findall(read(name)):
            for expr in (*PERCENT_WIDTH.findall(attr), *CONCAT_WIDTH.findall(attr)):
                sites += 1
                if not expr.strip().startswith("barWidth("):
                    offenders.append(f"{name}: {expr.strip()[:70]}")
    assert sites >= 9, f"only {sites} generated bar widths read out of the bundle"
    assert not offenders, offenders


def test_the_bar_primitive_clamps_and_never_thickens():
    """`barWidth` is the whole answer for what a bar may be, so the primitive is
    measured rather than trusted: an added minimum is the floor back again, in
    one place instead of seven, and it would pass every template law above.
    The prose is taken out first — this very file names the `Math.max(pct, 2)`
    the primitive replaced, and a reader that scans comments reports it as the
    clamp the code finally settled on."""
    body = js_function(read("chain.js"), "barWidth")
    assert body, "chain.js defines no barWidth(): the bars have no primitive"
    code = re.sub(r"/\*.*?\*/", "", body, flags=re.S)

    def constants(call: str) -> list[str]:
        arguments = re.findall(rf"Math\.{call}\(([^)]*)\)", code)
        assert arguments, f"barWidth never calls Math.{call}(): nothing bounds a bar"
        return [[part.strip() for part in pair.split(",")] for pair in arguments]

    for parts in constants("max"):
        floor = [part for part in parts if part.replace(".", "").isdigit()]
        assert floor == ["0"], f"barWidth floors a bar at {floor} instead of at 0"
    for parts in constants("min"):
        ceiling = [part for part in parts if part.replace(".", "").isdigit()]
        assert ceiling == ["100"], (
            f"barWidth bounds a bar at {ceiling}, not at the 100 % a track holds")


def test_chart_scaling_never_divides_by_a_zero_max():
    """`volume / max` over an all-zero series is NaN% — the bars vanish and every
    column height becomes an invalid CSS assertion. Zero days are real rows now,
    so the function must take its empty branch before it divides."""
    for name in (*PAGES, *SCRIPTS):
        body = js_function(read(name), "loadVolumeChart")
        if body is None or "/ max" not in body:
            continue
        assert re.search(r"max\s*=\s*Math\.max\([^;]*,\s*0\)", body), (
            f"{name} scales a chart by a max that is not read from the series"
        )
        guard = re.search(r"if\s*\(\s*max\s*<=\s*0\s*\)", body)
        assert guard, f"{name} has no all-zero branch for the chart"
        assert body.index(guard.group(0)) < body.index("/ max"), (
            f"{name} divides by max before checking that it is non-zero"
        )


def test_chart_columns_carry_their_value_as_text():
    """A bar nobody can read is decoration: every column needs its number in a
    title and its label on the axis."""
    for name in (*PAGES, *SCRIPTS):
        text = read(name)
        if "chart-col" not in text:
            continue
        assert 'title="${' in text, f"{name} renders chart columns with no readable value"
        assert "chart-day" in text, f"{name} renders chart bars with no axis label"


def test_painted_percentages_take_the_interface_locale():
    """`${pct.toFixed(1)}%` and `${String(o.probability)}%` paint a dot into a
    Russian interface whose money is written with a comma, so two surfaces show
    the same odds differently. A percent is a number like any other: it goes
    through a helper that asks the locale. Only the template shapes are checked —
    `x.toFixed(1) + '%'` inside a `style="width:…"` is geometry, not text."""
    raw_percent = re.compile(r"\$\{[^}]*?(?:\.toFixed\(|String\()[^}]*?\}%")
    offenders = [
        f"{name}:{number}: {line.strip()[:80]}"
        for name in (*PAGES, *SCRIPTS)
        for number, line in enumerate(read(name).splitlines(), 1)
        if raw_percent.search(line)
    ]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# number formatters
# ---------------------------------------------------------------------------

FUNCTION_HEAD = re.compile(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
ARROW_HEAD = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*"
)
FRACTION_KEY = re.compile(r"(minimumFractionDigits|maximumFractionDigits|maximumSignificantDigits)\s*:\s*(\d+)")

# What a formatter's own name promises, and the one precision that promise owes.
# A number whose precision is an argument is a number whose precision is decided
# by whichever call site forgot to pass one, so the mechanism is read from the
# name and the digits from the body, and the two have to agree everywhere.
MECHANISMS = {
    "fee": (re.compile(r"fee", re.IGNORECASE), {"maximumSignificantDigits": "15"}),
    "raw": (re.compile(r"micro", re.IGNORECASE), {"maximumFractionDigits": "6", "minimumFractionDigits": "0"}),
    "clock": (re.compile(r"time|date|day", re.IGNORECASE), {}),
    "odds": (re.compile(r"pct|percent|chance|odds|probability", re.IGNORECASE),
             {"minimumFractionDigits": "1", "maximumFractionDigits": "1"}),
    "money": (re.compile(r"usdc|usd|money|balance|share", re.IGNORECASE),
              {"minimumFractionDigits": "2", "maximumFractionDigits": "2"}),
    "count": (re.compile(r"count|^num$", re.IGNORECASE),
              {"minimumFractionDigits": "0", "maximumFractionDigits": "0"}),
}


def _span_end(text: str, start: int) -> int:
    """Index just past a function body: brace-matched for a block, past the
    statement-ending semicolon for an expression."""
    if text[start] != "{":
        end = text.find(";", start)
        return len(text) if end == -1 else end + 1
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _all_occurrences(text: str, needle: str) -> list[int]:
    out, at = [], text.find(needle)
    while at != -1:
        out.append(at)
        at = text.find(needle, at + len(needle))
    return out


def _span_to(text: str, start: int) -> str:
    return text[start:_span_end(text, start)]


def js_number_formatters(name: str) -> list[tuple[str, dict[str, str]]]:
    """(formatter name, fraction-digit options) for every function in a shipped
    document that turns a number into text through `toLocaleString`."""
    text = read(name)
    found: list[tuple[str, dict[str, str]]] = []
    for head in FUNCTION_HEAD.finditer(text):
        body = _span_to(text, head.end() - 1)
        if "toLocaleString(" in body:
            found.append((head.group(1), dict(FRACTION_KEY.findall(body))))
    for head in ARROW_HEAD.finditer(text):
        body = _span_to(text, head.end())
        if "toLocaleString(" in body:
            found.append((head.group(1), dict(FRACTION_KEY.findall(body))))
    return found


def mechanism_of(formatter: str) -> list[str]:
    return [key for key, (pattern, _) in MECHANISMS.items() if pattern.search(formatter)]


def every_formatter() -> list[tuple[str, str, dict[str, str]]]:
    return [
        (document, formatter, digits)
        for document in (*PAGES, *SCRIPTS)
        for formatter, digits in js_number_formatters(document)
    ]


def test_every_painted_number_states_the_precision_its_mechanism_owes():
    """One product, one grammar per kind of number.

    `maximumFractionDigits` alone let a column of odds paint "25%" beside
    "24,9%" and a balance paint "38,5" on the site beside "38,50" in the Mini
    App — the same USDC written two ways on the same day by the same product.
    Money is two decimals, odds one, counts none, a fee states the configured
    rate in significant digits, and a raw on-chain amount keeps its six.
    """
    offenders: list[str] = []
    seen: dict[str, int] = {}
    for document, formatter, digits in every_formatter():
        kinds = mechanism_of(formatter)
        if len(kinds) > 1 and set(kinds) != {"fee", "odds"}:
            offenders.append(f"{document}:{formatter} names two mechanisms {kinds}")
            continue
        if not kinds:
            offenders.append(f"{document}:{formatter} formats a number and names no mechanism")
            continue
        kind = "fee" if "fee" in kinds else kinds[0]
        seen[kind] = seen.get(kind, 0) + 1
        want = MECHANISMS[kind][1]
        if digits != want:
            offenders.append(f"{document}:{formatter} is {kind} and declares {digits}, not {want}")
    assert not offenders, offenders
    # Floors, so the law cannot go vacuous by the scan losing every formatter —
    # measured on this tree as 23 formatters: money 8, odds 6, count 3, clock 4, fee 1, raw 1.
    assert seen.get("money", 0) >= 6, f"the scan read {seen} money formatters"
    assert seen.get("odds", 0) >= 5, f"the scan read {seen} odds formatters"
    assert seen.get("count", 0) >= 2, f"the scan read {seen} count formatters"
    assert seen.get("clock", 0) >= 3, f"the scan read {seen} clock formatters"
    assert seen.get("raw", 0) == 1, f"a raw on-chain amount is painted by {seen.get('raw', 0)}, seen {seen}"
    assert seen.get("fee", 0) == 1, f"the rate is stated by {seen.get('fee', 0)} formatters, seen {seen}"
    assert sum(seen.values()) >= 20, f"the scan read {seen}, which is not the tree"


def formatter_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of every named function body in a document."""
    spans = []
    for head in FUNCTION_HEAD.finditer(text):
        spans.append((head.end() - 1, _span_end(text, head.end() - 1)))
    for head in ARROW_HEAD.finditer(text):
        spans.append((head.end(), _span_end(text, head.end())))
    return spans


def test_no_number_is_formatted_outside_a_named_formatter():
    """A call site that formats inline is a precision no law reads: the shipped
    documents may reach `toLocaleString` only from inside a function whose name
    states what it is counting."""
    total = 0
    for document in (*PAGES, *SCRIPTS):
        text = read(document)
        outside = [
            text[:at].count("\n") + 1
            for at in _all_occurrences(text, "toLocaleString(")
            if not any(start <= at < end for start, end in formatter_spans(text))
        ]
        assert not outside, (
            f"{document} formats a number at lines {outside}, outside any named formatter"
        )
        total += text.count("toLocaleString(")
    assert total >= 20, f"the scan read {total} number calls, which is not this bundle"


# ---------------------------------------------------------------------------
# odds: one rounder, measured in both languages this product ships
# ---------------------------------------------------------------------------

# `bot/pct.py` is the only place a ratio becomes the printed percentage, and its
# whole vocabulary is three names. Anything else that multiplies by 100 or formats
# at a typed precision decides the face by hand — and Python's builtin rounds
# half-to-even over a binary float while the primitive rounds Decimal half-up at
# `ODDS_DP`. The two rules disagree on figures this product actually paints, so a
# hand face is not a cosmetic duplicate: it is a different number on a page a
# person reads. The bundle is the third language with the same trap — ICU's
# `toLocaleString` also rounds half-to-even — so the same premise is measured in
# JS as well, and `ODDS_DP` is tied to the digits the pages paint.
ODDS_PRIMITIVES = ("share", "as_pct", "from_pct")
ODDS_WORDS = re.compile(r"pct|percent|probability|chance|odds", re.IGNORECASE)
FEE_WORDS = re.compile(r"fee", re.IGNORECASE)
# `:.1f` / `%.1f` — the builtin's rounder, at whatever precision a call site typed.
FIXED_FLOAT_SPEC = re.compile(r"\.\d+f")
HAND_RATIO = re.compile(r"\*\s*100\b")
HAND_QUANTIZERS = re.compile(r"\b(?:int|round)\(")
# An assignment gets only `round(`: `pct = int(raw)` is what `/sell 12 1 50%`
# parses out of the argument text, and a parse converts a string, it does not
# decide the face of a number the ledger already rounded.
HAND_ROUNDER = re.compile(r"\bround\(")
# A printf conversion slot, so the digits a format string types can be paired with
# the argument that fills it. `%%` is not a slot: it is the literal glyph.
_PRINTF_SPEC = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?[a-zA-Z]")
RUNTIME_PACKAGES = ("bot", "web", "agent")
# A `%` after one of these is a CSS unit, not a percentage: the bar fills and the
# volume chart paint geometry in the same character a chance is stated with.
CSS_GEOMETRY_PROP = re.compile(
    r"(?:width|height|min-width|max-width|inline-size|block-size|top|left|right|bottom)\s*(?::|=)[^;\n]{0,90}$"
)
ODDS_BAR_CALLS = ("probBars", "optRows")
ODDS_BAR_CALL_RE = re.compile(r"(?<![\w$.])(?:" + "|".join(ODDS_BAR_CALLS) + r")\(")


def tracked_runtime_py() -> list[str]:
    """Every tracked runtime module — the programs that answer a person.

    A test that names a literal percent is prose about a face, not a face, so the
    harness is not in the subject set; neither is `deploy/`, which speaks to an
    operator's terminal about processes and ports.
    """
    out = subprocess.run(
        ["git", "ls-files", *RUNTIME_PACKAGES],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return sorted(line for line in out.stdout.splitlines() if line.endswith(".py"))


def _py_tree(path: str) -> ast.AST:
    return ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))


def _spec_text(node) -> str:
    """The literal characters of an f-string format spec, without unparse's
    re-quoting: the `.1f` of `{x:.1f}`."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(str(p.value) for p in node.values if isinstance(p, ast.Constant))
    return ast.unparse(node)


def _has_percent_glyph(node: ast.JoinedStr) -> bool:
    return any(
        isinstance(part, ast.Constant) and isinstance(part.value, str) and "%" in part.value
        for part in node.values
    )


def _unit_follows(parts: list, index: int) -> bool:
    """Does the text directly after this interpolation state the percent unit?

    An implicitly concatenated alert is one `JoinedStr` node: `bot/solvency.py`
    prints the margin as a percent and the reserves as money with `:.2f` in the
    same node, and a glyph belonging to the margin would otherwise be read as the
    money's face. The unit is a claim about the figure it follows.
    """
    nxt = parts[index + 1] if index + 1 < len(parts) else None
    return bool(
        isinstance(nxt, ast.Constant) and isinstance(nxt.value, str) and nxt.value.lstrip().startswith("%")
    )


def _printf_faces(fmt: str, args: list[str]) -> list[str]:
    """Every hand face a printf format states, slot by slot.

    A conversion slot is paired with the argument that fills it, and the unit is
    read in the text that follows the slot before the next one — the printf shape
    of :func:`_unit_follows`, where a literal percent is written `%%`. A format
    that prints money and odds on one line (`"$%.2f and %s%%"`) therefore claims
    the percent only for the figure whose slot carries it, and not for the balance
    that shares the line.
    """
    specs = list(_PRINTF_SPEC.finditer(fmt))
    out: list[str] = []
    for index, spec in enumerate(specs):
        stop = specs[index + 1].start() if index + 1 < len(specs) else len(fmt)
        if "%%" not in fmt[spec.end():stop]:
            continue
        arg = args[index] if index < len(args) else " ".join(args)
        if FIXED_FLOAT_SPEC.search(spec.group(0)) or HAND_RATIO.search(arg) or HAND_QUANTIZERS.search(arg):
            out.append(f"`{fmt[:34]}` states {arg[:40]} at a face of its own")
    return out


def percent_faces_of(tree: ast.AST) -> list[tuple[int, str, str]]:
    """(line, kind, why) for every percentage a tree rounds or converts by hand.

    Six shapes, because a product writes a percent six ways: an f-string whose own
    format spec quantizes, an f-string whose value converts or truncates, a printf
    format, a logger call with its own face, an odds-named assignment, and an
    odds-named dictionary field. The kind splits the two laws: `face` is text a
    person reads, `field` is a published number a page re-paints. In every shape
    the unit is read beside the figure it states, because the same sentence prints
    money and odds and only the odds figure owes this grammar.
    """
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and _has_percent_glyph(node):
            for index, part in enumerate(node.values):
                if not isinstance(part, ast.FormattedValue) or not _unit_follows(node.values, index):
                    continue
                value = ast.unparse(part.value)
                spec = _spec_text(part.format_spec)
                if FIXED_FLOAT_SPEC.search(spec):
                    out.append((node.lineno, "face", f"a `{spec}` face decides the percentage of {value}"))
                elif HAND_RATIO.search(value) or HAND_QUANTIZERS.search(value):
                    out.append((node.lineno, "face", f"a percentage sentence quantizes {value} by hand"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
                and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            fmt, right = node.left.value, node.right
            args = [ast.unparse(e) for e in right.elts] if isinstance(right, ast.Tuple) else [ast.unparse(right)]
            out.extend((node.lineno, "face", why) for why in _printf_faces(fmt, args))
        elif isinstance(node, ast.Call) and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            out.extend(
                (node.lineno, "face", why)
                for why in _printf_faces(node.args[0].value, [ast.unparse(a) for a in node.args[1:]])
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = " ".join(ast.unparse(t) for t in targets)
            if node.value is None or not ODDS_WORDS.search(names):
                continue
            value = ast.unparse(node.value)
            if FEE_WORDS.search(value + names) or any(p in value for p in ODDS_PRIMITIVES):
                continue
            if HAND_RATIO.search(value) or HAND_ROUNDER.search(value) or FIXED_FLOAT_SPEC.search(value):
                out.append((node.lineno, "face", f"{names.strip()} is computed as {value[:56]}, outside bot/pct.py"))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if not ODDS_WORDS.search(key.value):
                    continue
                text = ast.unparse(value)
                if FEE_WORDS.search(key.value + text):
                    continue
                if HAND_RATIO.search(text) or FIXED_FLOAT_SPEC.search(text) or HAND_QUANTIZERS.search(text):
                    out.append((node.lineno, "field", f"{key.value!r} is {text[:56]}, not bot/pct.py"))
    return out


def hand_percent_faces(path: str) -> list[tuple[int, str]]:
    return [(line, why) for line, kind, why in percent_faces_of(_py_tree(path)) if kind == "face"]


def hand_quantized_odds_fields(path: str) -> list[tuple[int, str]]:
    return [(line, why) for line, kind, why in percent_faces_of(_py_tree(path)) if kind == "field"]


def odds_field_producers(path: str) -> tuple[int, int]:
    """(fee-named fields, odds-named fields the primitive or a published `_pct` makes).

    The fee count is the size of the exemption the law above grants, and the
    producer count is the shape the law exists to keep: a field whose own name
    states odds and whose value is either a primitive call or another odds-named
    figure handed through unchanged.
    """
    tree = _py_tree(path)
    fee = primitive = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            if not ODDS_WORDS.search(key.value):
                continue
            text = ast.unparse(value)
            if FEE_WORDS.search(key.value + text):
                fee += 1
            elif any(p in text for p in ODDS_PRIMITIVES) or ODDS_WORDS.search(text):
                primitive += 1
    return fee, primitive


def test_no_percentage_face_is_written_outside_the_odds_primitive():
    """One fact, one rounder: the shipped runtime may not quantize a percentage in
    an f-string, a log format or an assignment of its own.

    `bot/solvency.py` used to write `f"{delta / owed * 100:.1f}%"` beside a ledger
    that had already rounded the same margin, and `bot/handlers/agent.py` wrote
    `{int(confidence * 100)}%` where the bot's own market card wrote `99,9` — the
    second truncates, so the audit line read a percent lower than the market page
    for the same decision.
    """
    offenders: list[str] = []
    legal = scanned = 0
    for path in tracked_runtime_py():
        if path == "bot/pct.py":
            continue
        scanned += 1
        offenders.extend(f"{path}:{line}: {why}" for line, why in hand_percent_faces(path))
        legal += primitive_percent_faces(path)
    assert not offenders, offenders
    # Floors, so the law cannot go vacuous by the scan quietly losing the tree or
    # the legal shape losing its visibility: measured here as 86 runtime modules
    # tracked by git and 11 percentages written through `bot/pct.py`.
    assert scanned >= 80, f"the scan read {scanned} runtime modules, which is not this tree"
    assert legal >= 8, f"the scan read {legal} percentages written through bot/pct.py"


def primitive_percent_faces(path: str) -> int:
    """How many percentages this module states *through* the primitive — the
    non-vacuity side of :func:`hand_percent_faces`."""
    tree = _py_tree(path)
    found = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and _has_percent_glyph(node):
            if any(
                isinstance(part, ast.FormattedValue)
                and any(p in ast.unparse(part.value) for p in ODDS_PRIMITIVES)
                for part in node.values
            ):
                found += 1
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str) and "%" in node.args[0].value:
            rest = " ".join(ast.unparse(a) for a in node.args[1:])
            if any(p in rest for p in ODDS_PRIMITIVES):
                found += 1
    return found


def test_an_odds_named_field_is_produced_by_the_primitive_not_by_hand():
    """A `_pct` field is a percentage, and a page that paints it must not multiply
    it again — which is only safe if the producer stated the face.

    The fee fields are the other mechanism and are held by another law: a
    configured rate must be published **exactly**, so quantizing it through the
    odds primitive would change a published fee. That exemption is granted by the
    field or its value naming a fee, and the count of exempted fields is pinned
    below so the exemption cannot quietly become the whole set.
    """
    offenders: list[str] = []
    fee = produced = 0
    for path in tracked_runtime_py():
        if path == "bot/pct.py":
            continue
        offenders.extend(f"{path}:{line}: {why}" for line, why in hand_quantized_odds_fields(path))
        more_fee, more_produced = odds_field_producers(path)
        fee += more_fee
        produced += more_produced
    assert not offenders, offenders
    # Measured here: 2 fee fields published by `/api/info`, 16 odds fields whose
    # producer either calls the primitive or repeats a `_pct` the origin made.
    assert fee >= 2, f"the scan read {fee} fee fields, so the exemption is unproven"
    assert produced >= 12, f"the scan read {produced} odds fields made by the primitive or a published _pct"


def test_the_odds_rounder_is_half_up_where_pythons_builtin_is_bankers():
    """The premise of both laws above, executed rather than asserted.

    Half-up and half-to-even are the same rule until the figure lands on the step —
    and an LMSR price is a quotient, so it lands there often enough to matter:
    `0.1235` is `0.123499999999999996` as a float, which the builtin prints as
    `12,3` while the primitive prints `12,4`. A page and a card that pick different
    rules disagree about a market's odds in public.
    """
    from decimal import Decimal

    from bot.pct import ODDS_DP, as_pct, from_pct

    discriminating = []
    for raw in ["0.1235", "0.0025", "0.15", "1.25", "0.075", "0.5555", "0.1234"]:
        ratio = float(raw)
        primitive, builtin = as_pct(ratio), round(ratio * 100, ODDS_DP)
        assert primitive == float(f"{primitive:.{ODDS_DP}f}"), (raw, primitive)
        assert builtin == float(f"{builtin:.{ODDS_DP}f}"), (raw, builtin)
        if primitive != builtin:
            discriminating.append((raw, builtin, primitive))
    assert len(discriminating) >= 3, (
        f"the two rules disagreed only on {discriminating}; the fixtures no longer measure the difference"
    )
    assert as_pct(Decimal("0.1235")) == 12.4, "the shipped price face is 12,4, not 12,3"
    assert from_pct(12.4) == 12.4, "a percent the origin already published must survive being repeated"


def odds_primitive_dp() -> int:
    """`ODDS_DP`, read off the assignment in `bot/pct.py` rather than retyped."""
    for node in ast.walk(_py_tree("bot/pct.py")):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ODDS_DP":
                    return int(ast.unparse(node.value))
    raise AssertionError("bot/pct.py declares no ODDS_DP for the law to read")


def test_the_bundle_paints_the_precision_the_primitive_declares():
    """Two languages, one number of digits.

    `ODDS_DP` is why `bot/pct.py` quantizes to one decimal; `toLocaleString` is why
    the page paints one. Until the two were tied by name, the server could publish
    `12,35` and the card paint `12,4` — ICU rounds half-to-even, the same rule the
    hand faces above were refused for, so the browser had become the rounder nobody
    reviewed.
    """
    declared = odds_primitive_dp()
    painted = [
        (document, formatter, digits.get("maximumFractionDigits", ""))
        for document in (*PAGES, *SCRIPTS)
        for formatter, digits in js_number_formatters(document)
        if mechanism_of(formatter) == ["odds"]
    ]
    assert painted, "the scan read no odds formatter in the bundle"
    wrong = [
        f"{document}:{formatter} paints {digits} digits, the primitive declares {declared}"
        for document, formatter, digits in painted
        if digits != str(declared)
    ]
    assert not wrong, wrong


def _strip_js_comments(text: str) -> str:
    """Blank every comment, keeping the line numbering.

    This bundle ships a comment that quotes the defective face it replaced
    (`agent.js`: `Math.round(e.confidence * 100) + '%'`), and a reader that reads a
    comment as code reports a fix as an offender.
    """
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n") + " ", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", lambda m: " " * len(m.group(0)), text)


def _statement_window(text: str, start: int) -> str:
    """The characters from `start` to the end of its statement or line, whichever
    comes first — the scope a unit glyph is claimed inside."""
    ends = [len(text)] + [
        at + 1 for at in (text.find(";", start), text.find("\n", start)) if at != -1
    ]
    return text[start:min(ends)]


def _call_args(text: str, open_paren: int) -> str:
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    return text[open_paren + 1:]


def bundle_percent_arithmetic(text: str) -> tuple[list[tuple[int, str]], int, int]:
    """(offenders, CSS-geometry sites, `* 100` sites) for one shipped document.

    A `%` written after `* 100` in JS is a percentage decided at the paint site, in
    a third language, by ICU's rounder. The exclusion is geometry — `width:…%` and
    `height:…%` are CSS units and this bundle ships several of them — so the
    excluded sites are counted and returned rather than dropped: a reader that
    stopped finding them has gone blind, which is a different fact from the tree
    having gone clean.
    """
    code = _strip_js_comments(text)
    offenders: list[tuple[int, str]] = []
    geometry = conversions = 0
    for match in HAND_RATIO.finditer(code):
        conversions += 1
        at = match.start()
        before = code[max(0, at - 120):at]
        if CSS_GEOMETRY_PROP.search(before):
            geometry += 1
            continue
        statement = _statement_window(code, at)
        if "%" in statement:
            offenders.append((code[:at].count("\n") + 1, (before[-52:] + "⟂" + statement[:52]).strip()))
    return offenders, geometry, conversions


def odds_formatter_names(document: str) -> set[str]:
    """Names of the document's own formatters whose mechanism is odds."""
    return {name for name, _digits in js_number_formatters(document) if mechanism_of(name) == ["odds"]}


def odds_paint_sites(text: str, names: set[str]) -> list[tuple[int, str, str]]:
    """(line, formatter, first argument) for every call of an odds formatter.

    The formatter's own signature is skipped: `function fmtPct(x)` is a definition,
    and the parameter is not a number anybody published.
    """
    out: list[tuple[int, str, str]] = []
    for match in re.finditer(r"(?<![\w.$])([A-Za-z_$][\w$]*)\(", text):
        if match.group(1) not in names:
            continue
        head = text[max(0, match.start() - 40):match.start()]
        if re.search(r"(?:function|const|let|var)\s*$", head) or re.search(r"=\s*$", head):
            continue
        first = _call_args(text, match.end() - 1).split(",")[0].strip()
        out.append((text[:match.start()].count("\n") + 1, match.group(1), first))
    return out


def states_a_percentage(keys: list[str]) -> bool:
    """Does one of these field names say the number under it is a percent?"""
    return any(ODDS_WORDS.search(key) for key in keys)


def percent_bars_calls(text: str) -> list[tuple[int, list[str]]]:
    """(line, quoted arguments) for every painter called with the *name* of the
    field it paints as a percentage.

    `probBars(opts, 'price_pct', winnerOf(mk))` reaches the figure through a
    parameter, so the promise that the field holds a percentage is written as a
    string and has to be read as one.
    """
    out: list[tuple[int, list[str]]] = []
    for match in ODDS_BAR_CALL_RE.finditer(text):
        keys = [a.strip() for a in _call_args(text, match.end() - 1).split(",")]
        quoted = [k.strip("'\"") for k in keys if k[:1] in ("'", '"')]
        if quoted:
            out.append((text[:match.start()].count("\n") + 1, quoted))
    return out


def test_an_odds_formatter_is_handed_a_percent_not_a_ratio():
    """A formatter paints a percentage, so the expression beside it has to say it
    is one.

    `fmtPct(e.confidence)` was this law's first real finding, and it is a bug the
    rounder laws cannot see: the audit trail stores the model's confidence as a
    ratio, so the dashboard painted `1,0%` for a 99 % decision. `fmtPct(covered *
    100)` was the second — the ring's fraction converted at the paint site, where no
    field name carries the unit and `/api/solvency` already answers `held` and
    `owed`. Both now take a `_pct` the origin publishes, and the origin states that
    figure through `bot/pct.py` rather than dividing in the route.
    """
    offenders: list[str] = []
    checked = bars = 0
    key_offenders: list[str] = []
    for document in (*PAGES, *SCRIPTS):
        code = _strip_js_comments(read(document))
        names = odds_formatter_names(document)
        if names:
            for line, formatter, first in odds_paint_sites(code, names):
                checked += 1
                if not first or not ODDS_WORDS.search(first):
                    offenders.append(f"{document}:{line} hands {formatter}() {first[:44]!r}")
        for line, keys in percent_bars_calls(code):
            bars += 1
            if not states_a_percentage(keys):
                key_offenders.append(f"{document}:{line} reaches the bars through {keys}")
    assert not offenders, offenders
    assert not key_offenders, key_offenders
    # Floors: measured on this tree as 16 paint sites and 6 bar calls.
    assert checked >= 14, f"the scan read {checked} odds paint sites, which is not this bundle"
    assert bars >= 6, f"the scan read {bars} percentage-keyed bar calls, which is not this bundle"


def test_no_percent_sign_in_the_bundle_is_built_by_hand_arithmetic():
    """No shipped page converts a ratio to a percentage in its own script."""
    offenders: list[str] = []
    geometry = conversions = 0
    for document in (*PAGES, *SCRIPTS):
        bad, css, total = bundle_percent_arithmetic(read(document))
        offenders.extend(f"{document}:{line}: {why}" for line, why in bad)
        geometry += css
        conversions += total
    assert not offenders, offenders
    # Floors: measured on this tree as four `* 100` sites — the ring's and the
    # cap bar's ratio conversions, the scroll progress, and the money-share
    # summary — one of which paints a CSS height.
    assert conversions >= 4, f"the scan read {conversions} `* 100` sites, which is not this bundle"
    assert geometry >= 1, f"the scan excluded {geometry} CSS-geometry sites; the reader no longer sees them"


def test_the_percent_face_reader_refuses_the_shapes_it_exists_for():
    """The Python reader on sources that are not the shipped tree: every shape it
    claims to know, and the shapes it must not invent."""
    known = {
        'SPEC = f"{ratio:.1f}%"': "an f-string format spec",
        'TXT = f"{int(ratio * 100)}%"': "a truncating percent",
        'MIX = f"{a / b * 100}%"': "a hand ratio inside a percent sentence",
        'log.info("odds %.1f%%", ratio * 100)': "a logger with its own face",
        'MOD = "%.1f%%" % (ratio * 100)': "the %-operator with its own face",
        'MOD = "%s%%" % (ratio * 100)': "the %-operator converting in the argument",
        'chance_pct = (peak - now) / peak * 100': "an odds-named hand assignment",
        'OUT = {"price_pct": round(ratio, 2)}': "an odds-named field quantized by hand",
    }
    innocent = {
        'OK = f"{as_pct(ratio)}%"': "the primitive, written into text",
        'REPEAT = f"{from_pct(field_pct)}%"': "an already-rounded percent repeated",
        'PART = f"{share(part, whole)}%"': "the share primitive",
        'GUESS = f"{as_pct(ratio)} chance"': "no unit glyph, so no claim",
        'PLAIN = "%.1f" % ratio': "a decimal that states no unit",
        'LINE = "%s%% and $%.2f" % (as_pct(ratio), owed / _MICRO)': "the unit beside the odds slot only",
        'PCT = int(raw)': "a percent typed by a person, parsed not rounded",
        'FEE = {"withdraw_fee_pct": float(config.WITHDRAW_FEE_PCT)}': "a fee published exactly",
        'NOTHING = 1': "no percent anywhere",
    }
    for source, why in known.items():
        assert percent_faces_of(ast.parse(source)), f"{why} ({source}) was not reported"
    for source, why in innocent.items():
        found = percent_faces_of(ast.parse(source))
        assert not found, f"{why} ({source}) was read as a hand face: {found}"
    prose = ast.parse('def f():\n    """Prefer `f"{ratio:.1f}%"` to `int(ratio * 100)`."""\n    return 1')
    assert not percent_faces_of(prose), "a docstring quoting a defective face was read as one"
    # An alert is one node across several lines: the money lines keep `:.2f` and the
    # margin carries the unit. The face is read beside the figure it states, so the
    # money must stay invisible and the hand-rounded margin must not.
    alert = ast.parse(
        'ALERT = (\n'
        '    f"margin {as_pct(delta / owed)}%\\n"\n'
        '    f"Reserves: ${reserves_micro / _MICRO:.2f}\\n"\n'
        ')'
    )
    assert not percent_faces_of(alert), f"a money line in an alert that also states a margin: {percent_faces_of(alert)}"
    hand_alert = ast.parse(
        'ALERT = (\n'
        '    f"margin {delta / owed * 100:.1f}%\\n"\n'
        '    f"Reserves: ${reserves_micro / _MICRO:.2f}\\n"\n'
        ')'
    )
    assert len(percent_faces_of(hand_alert)) == 1, percent_faces_of(hand_alert)
    assert len(known) >= 8 and len(innocent) >= 9, "this reader test stopped covering the shapes"


def test_the_bundle_percent_reader_refuses_the_shapes_it_exists_for():
    """The JS readers on markup that is not the shipped bundle, including the decoys
    this tree actually ships."""
    code = _strip_js_comments(
        "// var leak = fmtPct(e.confidence); Math.round(ratio * 100) + '%'\n"
        "var ok = fmtPct(o.price_pct);\n"
        "var url = 'https://example.org/market/0/1';\n"
    )
    assert "leak" not in code, "the comment stripper left a quoted face visible"
    assert "example.org/market/0/1" in code, "the comment stripper ate a URL inside a string"

    bad, css, total = bundle_percent_arithmetic(
        'var a = "<span>" + Math.round(ratio * 100) + "%</span>";\n'
        'el.style.width = (used / total * 100) + "%";\n'
        'var bar = `<i style="height:${Math.max(vol / max * 100, 2)}%"></i>`;\n'
        "var ms = ts * 1000;\n"
    )
    assert [line for line, _why in bad] == [1], bad
    assert (css, total) == (2, 3), (css, total)

    sites = odds_paint_sites(
        "function fmtPct(x) { return x; }\nfmtPct(o.price_pct);\nfmtPct(covered * 100);\n", {"fmtPct"}
    )
    assert [arg for _line, _name, arg in sites] == ["o.price_pct", "covered * 100"], sites

    calls = percent_bars_calls("probBars(opts, 'price_pct', 'label');\noptRows(o, 'volume_usdc', 'x');")
    assert calls == [(1, ["price_pct", "label"]), (2, ["volume_usdc", "x"])], calls
    assert [keys for _line, keys in calls if not states_a_percentage(keys)] == [["volume_usdc", "x"]], calls
    assert not states_a_percentage([]), "a call with no key at all was read as a percentage"



def test_the_chart_has_both_an_empty_and_a_failed_state():
    """No data and a failed request are two different answers, and both are part
    of the design: a bare <div> in either branch renders as a blank rectangle
    indistinguishable from a broken page. Counted inside the chart's own function
    rather than in the whole file, because the file has other empty states and a
    global count stays green while the chart loses one of its two."""
    for name in (*PAGES, *SCRIPTS):
        body = js_function(read(name), "loadVolumeChart")
        if body is None:
            continue
        states = body.count('class="empty"')
        assert states == 2, (
            f"{name}:loadVolumeChart paints {states} styled state(s); the empty-data and the "
            "request-failed branches are two states and both belong to the design system"
        )


def test_a_progress_bar_declares_its_role():
    """A bar that only conveys by width is invisible to a screen reader; the
    element that carries the width needs role=progressbar and an aria-label."""
    offenders = []
    for name in (*PAGES, *SCRIPTS):
        text = read(name)
        for tag in re.findall(r'<div class="[^"]*(?:bar-fill|chart-bar|prob-bar-fill)[^"]*"[^>]*>', text):
            if "progressbar" in tag or "aria-" in tag:
                continue
            offenders.append((name, tag[:90]))
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# the winner of a market
# ---------------------------------------------------------------------------

WINNER_TEXT_PROPS = frozenset({
    "color", "font-weight", "font-size", "font-family", "font-variant-numeric",
    "letter-spacing", "text-transform", "margin-left", "content",
})
BAR_FILLS = frozenset({"bar-fill", "prob-bar-fill", "detail-opt-bar-fill", "chart-bar"})


def is_winner_token(token: str) -> bool:
    """`win`, `-win`, `-winner` — and not the JS identifier `isWinner`.

    `selected` and `active` name what the user is pointing at, which is a choice
    and is allowed to paint a fill; these three name what the market resolved.
    """
    return token == "win" or token.endswith("-win") or token.endswith("-winner")


def class_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_-]+", text)


def winner_tokens_in(text: str) -> list[str]:
    return [token for token in class_tokens(text) if is_winner_token(token)]


def bar_tokens_in(text: str) -> list[str]:
    return [token for token in class_tokens(text) if token in BAR_FILLS]


def winner_rules(css: str) -> list[tuple[str, dict[str, str]]]:
    """(winner class, declarations) for every rule that paints the resolved option."""
    out: list[tuple[str, dict[str, str]]] = []
    for selector, body in rule_selector_and_body(css):
        for token in winner_tokens_in(selector):
            out.append((token, dict(re.findall(r"([a-z-]+)\s*:\s*([^;{}]+)", body))))
    return out


def class_attribute_values(text: str) -> list[tuple[int, str]]:
    """(offset, value) for every `class="…"` attribute, template ternaries and all.

    A JS template keeps double quotes inside `${…}`, so the first quote after the
    opening one is not the end of the attribute; reading it as the end is how the
    winner class named in a ternary vanished from the scan.
    """
    out: list[tuple[int, str]] = []
    for match in re.finditer(r'class="', text):
        i = value_start = match.end()
        depth = 0
        while i < len(text):
            if text.startswith("${", i):
                depth += 1
                i += 2
                continue
            ch = text[i]
            if depth == 0 and ch == '"':
                break
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        out.append((match.start(), text[value_start:i]))
    return out


def bars_marked_as_won(text: str) -> list[tuple[int, str]]:
    """Every emitted class list that hands a probability bar the winner marker."""
    out: list[tuple[int, str]] = []
    for offset, value in class_attribute_values(text):
        if bar_tokens_in(value) and winner_tokens_in(value):
            out.append((text[:offset].count(chr(10)) + 1, value))
    return out


def test_the_winner_is_painted_in_the_label_and_never_on_the_bar():
    """One fact, one face: the resolved option is named in its label.

    `style.css` also carried a winner rule for the bar — two declarations that
    repeated exactly what `.bar-fill` already painted, brand blue at full opacity.
    The rule said nothing, five templates kept adding the class it named, and
    nothing in the tree could tell a reviewer whether the bar was a quantity or a
    state. The bar is a quantity: the fill is the brand token on every option, and
    winning is painted in the word beside it.
    """
    offenders: list[str] = []
    rules = marked = 0
    for document in (*PAGES, *SCRIPTS, *STYLESHEETS):
        text = read(document)
        if document.endswith((".html", ".css")):
            for token, props in winner_rules(text):
                rules += 1
                painted = sorted(set(props) - WINNER_TEXT_PROPS)
                if painted:
                    offenders.append(f"{document}:.{token} paints the winner with {painted}")
        for line, value in bars_marked_as_won(text):
            marked += 1
            offenders.append(f"{document}:{line} hands the bar the winner marker {value!r}")
    assert not offenders, offenders
    # Floors: measured on this tree as 4 winner rules and 0 marked bars.
    assert rules >= 4, f"the scan read {rules} winner rules, which is not this tree"
    assert marked == 0, f"{marked} bars carry the winner marker"


def test_the_winner_reader_refuses_a_state_colour_on_the_bar():
    """The readers on markup that is not the shipped bundle.

    The bar half has an empty shipped subject set, so a reader that quietly found
    no class lists would report a clean tree forever; the selector half is held to
    the state classes that are **not** winners, because `selected` is allowed to
    paint a fill.
    """
    guilty = '.prob-bar-fill.win{background:var(--success);color:var(--text)}\n'
    innocent = (
        ".detail-opt.selected{border-color:var(--accent);background:var(--blue-soft)}\n"
        ".tag-open{background:var(--blue-soft);color:var(--text)}\n"
    )
    found = winner_rules(guilty + innocent)
    assert [token for token, _props in found] == ["win"], found
    assert sorted(found[0][1]) == ["background", "color"], found[0][1]
    assert winner_rules(innocent) == [], winner_rules(innocent)
    assert is_winner_token("isWinner") is False, "a JS identifier is not a class"

    marked = bars_marked_as_won(
        '<div class="prob-bar-fill win" style="width:40%"></div>\n'
        '<div class="bar-fill" style="width:40%"></div>\n'
        '<div class="detail-opt selected"></div>\n'
    )
    assert [line for line, _value in marked] == [1], marked

    shipped_shape = bars_marked_as_won(
        '<div class="bar-fill${isWinner ? " bar-fill-win" : ""}" aria-hidden="true"></div>'
    )
    assert len(shipped_shape) == 1, shipped_shape
    assert bar_tokens_in("bar-fill") and not bar_tokens_in("prob-bar-label"), bar_tokens_in("bar-fill")


# ---------------------------------------------------------------------------
# language and copy
# ---------------------------------------------------------------------------


def mini_dict_line(language: str) -> str:
    line = next((l for l in APP_HTML.splitlines() if re.match(rf"\s*{language}: \{{", l)), "")
    assert line, f"the {language} dictionary is missing from app.html"
    return line


@pytest.mark.parametrize("language,forbidden", [("en", CYRILLIC_RE), ("zh", CYRILLIC_RE), ("ru", CJK_RE)])
def test_a_language_block_contains_only_its_own_script(language, forbidden):
    """One language per user: a Russian string left in the en/zh block puts Cyrillic
    in an English interface, which is the defect the i18n pass removed."""
    values = re.findall(r':\s*"((?:[^"\\]|\\.)*)"', mini_dict_line(language))
    assert values, f"no values parsed from the {language} block"
    bad = [v for v in values if forbidden.search(v)]
    assert not bad, f"{language} block carries foreign copy: {bad[:3]!r}"


def test_mini_app_dictionaries_have_identical_key_sets():
    defined = {
        language: set(re.findall(r'([a-z][a-z0-9_]*)\s*:\s*"', mini_dict_line(language)))
        for language in ("ru", "en", "zh")
    }
    for language in defined.values():
        assert language, "a dictionary parsed as empty — the extractor needs re-reading"
    reference = defined["ru"]
    for language in ("en", "zh"):
        difference = reference ^ defined[language]
        assert not difference, f"{language} differs from ru by {sorted(difference)}"


def localized_painters(text: str | None = None) -> tuple[dict, dict]:
    """JS functions that paint localized copy, split into two kinds.

    A *repainter* assigns `innerHTML`/`textContent` to something it looked up in
    the document, so the screen is rebuilt from the current language whenever it
    runs. A *builder* appends one row into a thing the user is assembling, and its
    content is the user's, not the app's — `renderAll()` cannot re-derive it
    without inventing rows. The split is what lets the law below state its blind
    spot instead of quietly excluding a file.

    `t(` has to be a call, not a substring: `setTimeout(`, `requestAnimationFrame`
    callbacks and `.split(` all carry a `t(`, and a reader that matches them
    reports every function that schedules anything as a language leak.
    """
    local = re.compile(r"(?<![\w$.])t\(")
    paint = re.compile(
        r"(?:\b[A-Za-z_$][\w$]*|document\.getElementById\([^)]*\))\s*\.(?:innerHTML|textContent)\s*="
    )
    repaint, build = {}, {}
    for name, body in js_functions(text if text is not None else APP_HTML).items():
        if not local.search(body):
            continue
        if "document.createElement" in body:
            build[name] = body
        elif paint.search(body):
            repaint[name] = body
    return repaint, build


def test_a_language_switch_repaints_every_list_the_mini_app_ships():
    """`setLang()` called `applyI18n()` and `renderMore()`. The first walks the
    `data-i18n` attributes — static labels only — and the second repaints one tab.
    Every history row, market row and bet row is built from `t()` *inside its own*
    render function, so switching language on the Settings screen changed the tab
    bar and left the money list in the language it was drawn in until the app was
    reopened. Two screens were in the same class and said nothing: the SmartAccount
    card, whose status line is painted from `t()` after a fetch nobody re-runs, and
    the detail sheet's buy button. A user who cannot read the language they are in
    has no way to find the control that fixes it.

    The subject set is the app's own painting functions, read out of the file: a
    new screen joins the law the moment it paints localized text into a document
    node, and `renderAll()` must reach it. What is a builder rather than a repaint
    is pinned by name, because that set is exactly the count of things the law
    cannot reach and a new one has to be argued about, not absorbed."""
    repaint, build = localized_painters()
    assert len(repaint) >= 6, (
        f"only {sorted(repaint)} localized painters read from app.html — the reader lost the file"
    )
    assert set(build) == {"addCreateOpt"}, (
        f"the builders this repaint law cannot reach changed: {sorted(build)} — "
        "a new one needs a repaint path, not an exception here"
    )
    every = js_function(APP_HTML, "renderAll")
    assert every, "no renderAll() to reach the screens from"
    unreachable = sorted(n for n in repaint if not re.search(rf"\b{n}\s*\(", every))
    assert not unreachable, f"{unreachable} paint localized text and no repaint pass reaches them"

    switch = js_function(APP_HTML, "setLang")
    assert switch, "setLang() is not a function the reader can find"
    assert "renderAll()" in switch, "a language switch no longer repaints the whole app"
    narrower = sorted({n for n in (*repaint, "applyI18n") if re.search(rf"\b{n}\s*\(", switch)})
    assert not narrower, f"setLang() repaints {narrower} beside renderAll(): the narrower call is the bug"


def test_the_repaint_reader_refuses_a_screen_it_cannot_reach():
    """The reader is run against markup that is not the shipped file: a screen
    renderAll never calls, a builder that appends into the user's own form, a
    language switch that settles for one tab, and the `setTimeout(` decoy that
    made the first version report every function with a timer as a language leak."""
    sample = """
      function renderAll() { applyI18n(); renderA(); renderC(); }
      function renderA() { const c = document.getElementById('a'); c.innerHTML = t('x'); }
      function renderB() { const c = document.getElementById('b'); c.innerHTML = t('y'); }
      function renderC() { document.getElementById('c').textContent = t('w'); }
      function addRow() { const r = document.createElement('div'); r.innerHTML = t('z'); }
      function snooze() { const el = document.getElementById('d'); el.textContent = 'x'; setTimeout(() => el.remove(), 9); }
      function setLang() { applyI18n(); renderA(); }
    """
    repaint, build = localized_painters(sample)
    assert set(repaint) == {"renderA", "renderB", "renderC"}, repaint
    assert set(build) == {"addRow"} and "snooze" not in repaint
    every = js_function(sample, "renderAll")
    assert re.search(r"\brenderB\s*\(", every) is None
    assert "renderAll()" not in js_function(sample, "setLang")


def test_no_emoji_is_used_as_an_icon_in_the_web_ui():
    """Icons here are inline SVG. An emoji is a colour image the theme cannot
    recolour and that every operating system draws differently."""
    offenders = []
    for name in (*PAGES, *SCRIPTS):
        text = read(name)
        for match in EMOJI_RE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append((name, line, ascii(match.group())))
    assert not offenders, offenders[:10]


def test_telegram_bot_ui_uses_typographic_marks_not_pictograms():
    """The bot, site and Mini App share one plain-text terminal voice. Telegram
    cannot theme an emoji, so the bot may use only the same typographic marks as
    the web surfaces: arrows, checks, crosses, dots and stars."""
    allowed = set("✓↗★●○→↘≈−≤└↑←↓▰▱▲▼▫")
    pattern = re.compile(r"[\U0001F000-\U0001FAFF\u2100-\u27FF\u2B00-\u2BFF]")
    files = [ROOT / "bot" / "i18n.py", ROOT / "bot" / "main.py", ROOT / "bot" / "commands_catalog.py"]
    files.extend(sorted((ROOT / "bot" / "handlers").glob("*.py")))
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        for match in pattern.finditer(text):
            if match.group() in allowed:
                continue
            line = text[: match.start()].count("\n") + 1
            offenders.append((path.relative_to(ROOT).as_posix(), line, ascii(match.group())))
    assert not offenders, offenders[:10]


def test_telegram_bot_button_labels_fit_the_client_limit():
    from bot import i18n

    offenders = [
        (key, lang, len(text))
        for key, translations in i18n.STRINGS.items()
        if key.startswith("btn_")
        for lang, text in translations.items()
        if len(text) > 64
    ]
    assert not offenders, offenders


def test_every_escaper_that_builds_html_escapes_quotes():
    """The four files below build markup by string concatenation and interpolate
    esc() INSIDE attributes (`data-val="${esc(r.to)}"`, `aria-label="${…}"`).

    app.html's escaper was a div.textContent round-trip: it escapes `& < >` for
    a text node but leaves a quote untouched, which ends the attribute early and
    turns the value into markup. Requiring the two quote entities keeps the
    escaper safe in both positions.
    """
    builders = ("app.html", "app.js", "site.js", "agent.js")
    for name in builders:
        text = read(name)
        bodies = [
            text[m.start(): m.start() + 400]
            for m in re.finditer(r"function\s+(?:esc|escapeHtml)\s*\(", text)
        ]
        assert bodies, f"{name} builds markup but has no escaper"
        for body in bodies:
            assert "&quot;" in body and "&#39;" in body, (
                f"{name}: the escaper leaves quotes intact — unsafe inside an attribute"
            )


# ---------------------------------------------------------------------------
# placeholders
# ---------------------------------------------------------------------------


def server_substitutions() -> set[str]:
    source = (ROOT / "web" / "server.py").read_text(encoding="utf-8-sig")
    return set(re.findall(r"['\"](__[A-Z][A-Z0-9_]*__)['\"]", source))


def test_placeholders_used_in_the_html_are_substituted_by_the_server():
    used: set[str] = set()
    for name in DOCUMENTS:
        used |= set(PLACEHOLDER_RE.findall(read(name)))
    unmanaged = sorted(used - server_substitutions())
    assert not unmanaged, f"{unmanaged} reach the visitor as literal text"


def test_no_substitution_key_is_dead():
    """The other half of the same contract: a marker the server replaces that no
    template contains is code maintaining a placeholder that no longer exists."""
    used: set[str] = set()
    for name in DOCUMENTS:
        used |= set(PLACEHOLDER_RE.findall(read(name)))
    unused = sorted(server_substitutions() - used)
    assert not unused, f"web/server.py replaces {unused} and no template asks for it"


# ---------------------------------------------------------------------------
# the document's identity: one writer for title, description and share card
# ---------------------------------------------------------------------------

# Two documents are not pages of the site: `app.html` is the launch card the Base
# App crawls (and therefore owns its own `og:` tags) and `not_found.html` is the
# dead end, which must never name itself as a canonical. Everything else in the
# bundle is expected to declare a title, a description and no hand-typed card.
NOT_PAGES = ("app.html", "not_found.html")
INDEXABLE_PAGES = tuple(name for name in PAGES if name not in NOT_PAGES)

CARD_RE = re.compile(r'<meta (?:property="og:|name="twitter:)|<link rel="canonical"')
TITLE_RE = re.compile(r"<title>")
DESC_RE = re.compile(r'<meta name="description" content="([^"]+)"')


def test_only_the_server_writes_a_share_card():
    """``og:`` and ``twitter:`` are derived from ``<title>`` and the description
    meta by `web/server.py`, so a template that also types them can only end up
    disagreeing with its own document — which is exactly what `user.html` did:
    the tab said «Профиль — Tippy» and the card said «Профиль в Tippy».

    `app.html` is the exception and owns it: it is not a page of the site, it is
    the launch card the Base App crawls, and its tags carry the Mini App's own
    copy.
    """
    for name in INDEXABLE_PAGES:
        found = CARD_RE.findall(read(name))
        assert not found, f"{name} writes {found} — the server already does"
    app = read("app.html")
    assert app.count('<meta property="og:title"') == 1
    assert app.count('<meta property="og:image"') == 1


def test_every_indexable_page_declares_its_title_and_its_description():
    """The two strings the server turns into a card. A page missing the
    description gets a card with no second line, and nothing downstream notices.
    """
    offenders = {}
    for name in INDEXABLE_PAGES:
        text = read(name)
        if len(TITLE_RE.findall(text)) != 1 or len(DESC_RE.findall(text)) != 1:
            offenders[name] = (len(TITLE_RE.findall(text)), len(DESC_RE.findall(text)))
    assert not offenders, offenders


def test_the_edge_never_answers_the_robots_documents_from_the_bundle():
    """``txt`` in the extension list made /robots.txt an edge asset. The bundle
    has no such file, Pages answers a missing path with the landing page, and a
    crawler was handed 200 + text/html where it expected rules.

    The regex is read out of the function rather than restated, so this fails
    when the routing changes and not when a copy of it goes stale.
    """
    source = EDGE.read_text(encoding="utf-8-sig")
    declared = re.search(r"const ASSET = /\\.\((\?:[^)]*)\)\$/i;", source)
    assert declared, "the edge asset pattern moved somewhere this gate cannot read"
    asset = re.compile(f"\\.({declared.group(1)})$", re.I)
    for origin_document in ("/robots.txt", "/sitemap.xml"):
        assert not asset.search(origin_document), f"{origin_document} is served from the edge"
    assert asset.search("/style.css"), "the extracted pattern matches nothing — a broken gate"


# ---------------------------------------------------------------------------
# the document: what the browser is told before it paints
# ---------------------------------------------------------------------------

VIEWPORT_RE = re.compile(r'<meta name="viewport" content="([^"]*)"')
ZOOM_BLOCKERS = ("user-scalable=no", "user-scalable=0", "maximum-scale", "minimum-scale")
THEME_COLOR_RE = re.compile(
    r'<meta\s+name="theme-color"\s+media="\(prefers-color-scheme: (light|dark)\)"\s+content="([^"]+)"'
)
FONT_STYLESHEET = "/fonts.css"
FONT_LINK_RE = re.compile(r'<link rel="stylesheet" href="([^"]+)"')
FONT_IMPORT_RE = re.compile(r"@import\s+url\(['\"]?(https?://[^'\")\s]+)")
# A scheme is what makes this a reference rather than prose: `fonts.css` explains
# in a comment why the faces are not on Google, and that sentence must not be a
# build failure.
FONT_HOST_RE = re.compile(r"https?://[^\s\"']*\bfonts\.(?:googleapis|gstatic)\.com")
FONT_FACE_RE = re.compile(r"@font-face\s*\{[^{}]*\}", re.S)
FACE_FAMILY_RE = re.compile(r"font-family:\s*'([^']+)'")
FACE_WEIGHT_RE = re.compile(r"font-weight:\s*(\d+)(?:\s+(\d+))?")
FACE_SRC_RE = re.compile(r"url\('(/fonts/[^']+)'\)")
OUTLINE_OFF_RE = re.compile(r"outline:\s*(?:none|0)\b", re.I)


def font_faces() -> dict[str, list[tuple[int, int]]]:
    """`{'Geist Mono': [(400, 600)], …}` read from the stylesheet this origin serves."""
    faces: dict[str, list[tuple[int, int]]] = {}
    for block in FONT_FACE_RE.findall(read("fonts.css")):
        family, weight = FACE_FAMILY_RE.search(block), FACE_WEIGHT_RE.search(block)
        assert family and weight, f"an @font-face with no family or weight: {block[:120]}"
        faces.setdefault(family.group(1), []).append(
            (int(weight.group(1)), int(weight.group(2) or weight.group(1)))
        )
    assert faces, "fonts.css declares no face at all — a broken gate"
    return faces


def test_no_page_locks_the_visitor_out_of_pinch_zoom():
    """A Mini App is read on a phone, and a small number is the one thing a user
    has to be able to make bigger. `user-scalable=no` fails WCAG 1.4.4 and buys
    nothing a layout rule needs."""
    offenders = []
    for name in PAGES:
        for content in VIEWPORT_RE.findall(read(name)):
            blocked = [token for token in ZOOM_BLOCKERS if token in content]
            if blocked:
                offenders.append((name, content, blocked))
    assert not offenders, offenders


def test_every_page_paints_the_browser_chrome_in_its_own_canvas():
    """Without theme-color a phone shows a white status bar over a #111111 page:
    the first square centimetre of the dark theme, drawn by the browser in the
    wrong colours. The values have to be the canvas tokens, not a new grey."""
    want = {
        "light": normalized(resolve(LIGHT, "--canvas")),
        "dark": normalized(resolve(DARK, "--canvas")),
    }
    offenders = []
    for name in PAGES:
        found = {scheme: content.strip().lower() for scheme, content in THEME_COLOR_RE.findall(read(name))}
        if set(found) != {"light", "dark"}:
            offenders.append((name, "declared schemes", sorted(found)))
            continue
        mismatched = {scheme: value for scheme, value in found.items() if value != want[scheme]}
        if mismatched:
            offenders.append((name, "off-canvas", mismatched))
    assert not offenders, offenders


def test_the_faces_are_served_by_this_origin_not_by_a_host_the_visitor_has_to_reach():
    """Measured, not assumed: with the faces on Google, every page load on the
    network this product is developed and partly read from logged ERR_FAILED for the
    stylesheet and for every `.woff2` — 18 to 22 console errors per surface — and the
    product painted in the browser's fallback face. A typeface the visitor cannot
    download is not a design system, so the faces come from here: one small file more
    per document, no third-party host on the critical path, and the CSP tightened to
    match.

    `@import` stays banned for the reason it was banned before: it is discovered only
    after the importing stylesheet has arrived, so the visitor waits through serial
    round-trips before the first glyph."""
    imported = [
        (name, match.group(1))
        for name in DOCUMENTS
        for match in FONT_IMPORT_RE.finditer(read(name))
    ]
    assert not imported, f"a render-blocking @import: {imported}"

    links = {name: FONT_LINK_RE.findall(read(name)) for name in PAGES}
    off_shape = {name: found for name, found in links.items()
                 if found.count(FONT_STYLESHEET) != 1}
    assert not off_shape, f"a page does not link {FONT_STYLESHEET} exactly once: {off_shape}"
    late = {name: found for name, found in links.items() if found[0] != FONT_STYLESHEET}
    assert not late, f"{FONT_STYLESHEET} linked after another stylesheet in {late}: the " \
                     f"first glyph waits for a second round-trip"

    shipped = [read(name) for name in DOCUMENTS] + [EDGE.read_text(encoding="utf-8")]
    leaking = sorted({host for text in shipped for host in FONT_HOST_RE.findall(text)})
    assert not leaking, f"a font host is still referenced: {leaking}"


def test_the_faces_the_stylesheet_declares_are_the_files_the_bundle_serves():
    """`web/static` is the site's source **and** the Pages build output, so both
    directions matter: a `src:` naming a file that is not on disk is a face that
    silently falls back — which is the failure above painted as the design — and a
    `.woff2` no rule references ships to every visitor for nothing. `font-display` is
    read in the same pass because `block` is invisible text and `optional` is a face
    that never arrives on a slow connection."""
    blocks = FONT_FACE_RE.findall(read("fonts.css"))
    assert blocks, "fonts.css declares no @font-face — a broken gate"
    referenced = []
    for block in blocks:
        src = FACE_SRC_RE.findall(block)
        assert len(src) == 1, f"a face with {len(src)} local files: {block[:120]}"
        referenced.append(src[0])
        assert "font-display: swap" in block, f"a face that does not swap: {block[:120]}"
        path = STATIC / src[0].lstrip("/")
        assert path.is_file() and path.stat().st_size > 0, f"{src[0]} is declared, not served"
    on_disk = {f"/fonts/{p.name}" for p in (STATIC / "fonts").glob("*.woff2")}
    drift = on_disk ^ set(referenced)
    assert not drift, f"declared but not shipped, or shipped and never declared: {sorted(drift)}"


FACE_RANGE_RE = re.compile(r"unicode-range:\s*([^;]+);")
# Cyrillic and Cyrillic Supplement: the blocks the ru UI is written in, and the
# reason `Golos Text` sits in the stack at all.
CYRILLIC = range(0x0400, 0x0530)


def unicode_spans(spec: str) -> list[tuple[int, int]]:
    """`U+0400-045F, U+2116` → the codepoint spans a browser hands to this face."""
    return [(int(low, 16), int(high or low, 16))
            for low, high in re.findall(r"U\+([0-9A-Fa-f]{4,6})(?:-([0-9A-Fa-f]{4,6}))?", spec)]


def face_unicode_ranges() -> dict[str, list[tuple[int, int]]]:
    """`{'Golos Text': [(0x400, 0x45F), …]}` read from the stylesheet this origin serves."""
    faces: dict[str, list[tuple[int, int]]] = {}
    for block in FONT_FACE_RE.findall(read("fonts.css")):
        family, spec = FACE_FAMILY_RE.search(block), FACE_RANGE_RE.search(block)
        assert family and spec, f"an @font-face with no family or unicode-range: {block[:120]}"
        faces.setdefault(family.group(1), []).extend(unicode_spans(spec.group(1)))
    assert faces, "fonts.css declares no face at all — a broken gate"
    return faces


def test_every_cyrillic_glyph_the_site_ships_is_a_glyph_a_served_face_draws():
    """The type contract promises the ru UI is drawn by `Golos Text` behind the
    Latin primary face, and the law that checked that sentence only ever read the
    *name* in the stack. A stack naming a face whose subsets do not cover the block
    is the same Arial: per-glyph fallback walks the stack, finds no webfont claiming
    the codepoint and hands the word to the operating system, so a document can pass
    every family, weight, file and CSP law around it and still paint its Russian
    labels in a face no designer drew.

    So the measure is the glyphs themselves: every Cyrillic codepoint that appears in
    a document this origin serves has to fall inside the `unicode-range` of some face
    the stack names. Both stacks, on both boards — `app.html` carries its own sheet
    and its own dictionary, and a micro-label set in `--font-mono` is Russian text
    just like a heading is."""
    faces = face_unicode_ranges()
    shipped = {ord(c) for name in (*PAGES, *SCRIPTS)
               for c in read(name) if ord(c) in CYRILLIC}
    assert shipped, "no Cyrillic anywhere in the site: this law measured nothing"
    offenders = []
    for label, stack in (
        ("site --font-ui", resolve(LIGHT, "--font-ui")),
        ("Mini App --font-ui", resolve(MINI_LIGHT, "--font-ui")),
        ("site --font-mono", resolve(LIGHT, "--font-mono")),
        ("Mini App --font-mono", resolve(MINI_LIGHT, "--font-mono")),
    ):
        web_families = [family for family in quoted_families(stack) if family in faces]
        if not web_families:
            continue  # a system-only stack promises no glyph of its own
        covered = [span for family in web_families for span in faces[family]]
        missing = sorted(cp for cp in shipped
                         if not any(low <= cp <= high for low, high in covered))
        if missing:
            offenders.append((label, sorted(web_families), len(missing),
                              [f"U+{cp:04X} ({chr(cp)})" for cp in missing[:6]]))
    assert not offenders, (
        "these Cyrillic glyphs are in no face the stack serves, so the browser "
        f"answers them with its own font: {offenders}")


CSP_DIRECTIVE_RE = re.compile(r"(style-src|font-src) ([^;\"]*)")


def test_the_content_policy_holds_the_same_line_as_the_stylesheet():
    """The header is what makes this a promise rather than a preference: a template
    that linked a font host would be refused by the browser, and a header that still
    allowed one would let any future page reintroduce it quietly. Both halves are read
    from the literal `web/server.py` stamps on every response."""
    source = (ROOT / "web" / "server.py").read_text(encoding="utf-8-sig")
    directives = dict(CSP_DIRECTIVE_RE.findall(source))
    assert set(directives) == {"font-src", "style-src"}, sorted(directives)
    for name, value in directives.items():
        assert "'self'" in value, f"{name} no longer serves from this origin: {value.strip()}"
        assert "http" not in value, f"{name} still admits a third-party host: {value.strip()}"


ICON_TAG_RE = re.compile(r'<link rel="icon" href="([^"]*)"\s*/?>')
ICON_LINK_RE = re.compile(r'<link\s+rel="icon"')
DATA_SVG_RE = re.compile(r"^data:image/svg\+xml,(.*)$", re.S)


def icon_mark(name: str) -> str:
    """The SVG a browser really gets out of the page's favicon, parsed the way the
    browser parses it: an attribute value ends at the first matching quote.

    `not_found.html` wrote the mark with `x="50"` inside the double-quoted href.
    The value stopped there, the rest of the drawing became junk attributes, and
    the tab showed no icon — while the seven pages that spelled the same
    attributes with `'` looked identical in review because they were. So the
    payload is percent-decoded and parsed as XML: a truncated tag is a parse
    error, not a small icon.
    """
    text = read(name)
    assert len(ICON_LINK_RE.findall(text)) == 1, f"{name}: not exactly one rel=icon link"
    match = ICON_TAG_RE.search(text)
    assert match, f"{name}: the icon href never terminates its tag — a quote is inside the value"
    payload = match.group(1)
    data = DATA_SVG_RE.match(payload)
    assert data, f"{name}: favicon is not an inline data:image/svg+xml"
    # A raw `#` in a data URI is the start of a fragment: everything after it is
    # dropped, and the page is left with a half a drawing.
    assert "#" not in data.group(1), f"{name}: a raw # in the data URI truncates the mark"
    import urllib.parse
    import xml.etree.ElementTree as ET

    svg = urllib.parse.unquote(data.group(1))
    # ElementTree folds `xmlns` into the tag name, so the namespace is checked in
    # the text and the element is matched on its local name.
    assert "xmlns='http://www.w3.org/2000/svg'" in svg, f"{name}: the icon has no xmlns"
    assert ET.fromstring(svg).tag.rsplit("}", 1)[-1] == "svg", f"{name}: the icon is not an <svg>"
    return svg


def test_the_favicon_is_one_mark_every_page_can_render():
    """One product, one mark — and a mark the browser can actually decode.

    The fills are read against the palette so the icon cannot drift into a colour
    the site refuses anywhere else: the plate is the brand token, the glyph is the
    text token that sits on it (`--on-accent`), and the pair is the one every
    inverse-text law already measures."""
    marks = {name: icon_mark(name) for name in PAGES}
    drifted = {name for name, svg in marks.items() if svg != marks["index.html"]}
    assert not drifted, f"pages ship different marks: {sorted(drifted)}"

    import xml.etree.ElementTree as ET

    want = {normalized(resolve(LIGHT, "--blue")), normalized(resolve(LIGHT, "--on-accent"))}
    fills = {normalized(node.get("fill"))
             for node in ET.fromstring(marks["index.html"]).iter() if node.get("fill")}
    assert fills == want, f"the mark paints {sorted(fills)}, the palette says {sorted(want)}"


EDGE = ROOT / "functions" / "[[path]].js"
MAINTENANCE_RE = re.compile(r"const MAINTENANCE = \([^)]*\) => `(.*?)`;\n\nfunction maintenance", re.S)


def test_the_edge_maintenance_card_obeys_the_same_document_contract():
    """The 503 card comes from the Pages function, not from web/static, so every
    gate above walks past it. It is still the first page a visitor sees when the
    origin is down: same faces, same canvas, and no @import on the critical path."""
    match = MAINTENANCE_RE.search(EDGE.read_text(encoding="utf-8"))
    assert match, "the edge maintenance card is gone or its template shape changed"
    html = match.group(1)

    viewport = VIEWPORT_RE.search(html)
    assert viewport, "the maintenance card has no viewport meta"
    assert not [token for token in ZOOM_BLOCKERS if token in viewport.group(1)]

    assert not FONT_IMPORT_RE.findall(html), "the maintenance card @imports a webfont"
    assert FONT_LINK_RE.findall(html) == [FONT_STYLESHEET], (
        "the maintenance card must take its faces from this origin, like every page"
    )
    assert not FONT_HOST_RE.findall(html), "the maintenance card reaches for a font host"

    want = {
        "light": normalized(resolve(LIGHT, "--canvas")),
        "dark": normalized(resolve(DARK, "--canvas")),
    }
    found = {scheme: content.strip().lower()
             for scheme, content in THEME_COLOR_RE.findall(html)}
    assert found == want, f"theme-color: {found} vs canvas {want}"

    for tokens, label in ((LIGHT, "light"), (DARK, "dark")):
        for token in ("--canvas", "--blue"):
            value = resolve(tokens, token)
            assert f"{token}: {value}" in html, (
                f"the card does not carry the {label} {token} token ({value})"
            )

    assert len(re.findall(r"<h1[ >]", html)) == 1, "the card needs exactly one h1"
    assert ":focus-visible" in html, "the card has links but paints no focus ring"


def quoted_families(value: str) -> list[str]:
    return re.findall(r'"([^"]+)"', value)


def weights_with_stack(css: str, token: str) -> set[int]:
    """Numeric weights declared on an element that explicitly takes `token`.

    Only explicit declarations are counted: inheritance cannot be resolved from a
    stylesheet by regex, and a rule that sets no family is not claiming a face.
    """
    used: set[int] = set()
    for _selector, body in rule_selector_and_body(css):
        if f"var({token})" not in body:
            continue
        used |= {int(weight) for weight in re.findall(r"font(?:-weight)?: *(\d{3})", body)}
    return used


def test_the_weights_the_ui_asks_for_are_the_weights_the_faces_can_draw():
    """A weight outside every declared range is painted as synthetic bold: the
    browser thickens a face that was never drawn that way, and money figures become
    the one place on the page where the type looks manufactured.

    The ranges are read from `fonts.css`, so asking the UI for a weight the served
    face cannot draw fails here instead of arriving as a glyph no designer drew."""
    faces = font_faces()
    offenders = []
    for token in ("--font-mono", "--font-ui"):
        for surface, css in (("style.css", CSS), ("app.html", APP_HTML)):
            board = LIGHT if surface == "style.css" else MINI_LIGHT
            ranges = [span for family in quoted_families(resolve(board, token))
                      for span in faces.get(family, [])]
            if not ranges:
                continue  # the stack is made of system faces: nothing declares a range
            missing = sorted(weight for weight in weights_with_stack(css, token)
                             if not any(low <= weight <= high for low, high in ranges))
            if missing:
                offenders.append((surface, token, missing, sorted(set(ranges))))
    assert not offenders, f"these weights are synthesised, not drawn: {offenders}"


# ---------------------------------------------------------------------------
# keyboard, motion, structure
# ---------------------------------------------------------------------------


def test_an_outline_is_only_erased_where_something_visible_is_drawn():
    """`outline: none` on `:focus` deletes the keyboard cursor, and this site's own
    history says why a tint is not a replacement: the landing's fields erased the
    ring behind a halo of the brand blue at 16 % alpha, which composites to
    1.11:1 on the dark canvas while every other indicator is held to 3:1 — and the
    eraser was more specific than the global `:focus-visible` rule, so nothing
    brought the ring back.

    So `:focus` never gets to erase it. A blanket `outline: none` on an element
    type stays legal only while a `:focus-visible` rule in the same document
    paints the ring, which is exactly how `app.html` reads."""
    offenders = []
    for surface, css in (("style.css", CSS), ("app.html", APP_HTML)):
        rules = rule_selector_and_body(css)
        has_focus_visible_ring = any(
            selector.strip().endswith(":focus-visible") and re.search(r"outline:\s*[1-9]", body)
            for selector, body in rules
        )
        for selector, body in rules:
            if not OUTLINE_OFF_RE.search(body):
                continue
            if ":focus" in selector and ":focus-visible" not in selector:
                offenders.append((surface, selector, "erases the ring inside a :focus rule"))
            elif ":focus" not in selector and not has_focus_visible_ring:
                offenders.append((surface, selector, "no :focus-visible ring in the document"))
    assert not offenders, f"{offenders} remove the outline with no :focus-visible ring behind them"


def test_the_focus_ring_is_visible_on_both_canvases():
    """2.4.11: a focus indicator is a non-text element, so it needs 3:1 against
    the surface it is drawn on — the same test a logo or an icon gets."""
    for surface, css, light, dark, canvas_token in (
        ("style.css", CSS, LIGHT, DARK, "--canvas"),
        ("app.html", APP_HTML, MINI_LIGHT, MINI_DARK, "--bg"),
    ):
        ring = next(
            (
                body
                for selector, body in rule_selector_and_body(css)
                if selector.strip().endswith(":focus-visible") and "outline:" in body
            ),
            None,
        )
        assert ring, f"{surface} has no :focus-visible rule at all"
        width = re.search(r"outline:\s*([0-9.]+)px", ring)
        assert width and float(width.group(1)) >= 2.0, f"{surface}: ring is {ring[:40]}"
        colour = re.search(r"outline:[^;]*?(var\(--[a-z0-9-]+\)|#[0-9a-f]{3,8})", ring)
        assert colour, f"{surface}: the ring colour is not a token — {ring[:40]}"
        for theme, tokens in (("light", light), ("dark", dark)):
            name = colour.group(1)
            paint = parse_color(resolve(tokens, name[4:-1]) if name.startswith("var(") else name)
            canvas = parse_color(resolve(tokens, canvas_token))
            assert paint and canvas, f"{surface} {theme}: unparseable ring {name}"
            measured = ratio(paint, canvas)
            assert measured >= 3.0, f"{surface} {theme}: focus ring {measured:.2f}:1 on the canvas"


def media_block(css: str, query: str) -> str:
    """The body of an `@media` block, braces matched."""
    start = css.index(f"@media {query}")
    brace = css.index("{", start)
    depth = 0
    for i in range(brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1 : i]
    raise AssertionError(f"unbalanced @media {query}")


def test_reduced_motion_still_reveals_everything():
    """A reveal is `opacity: 0` plus a class the observer adds. Under
    prefers-reduced-motion the transition is killed, and with a sloppy block the
    content stays invisible: the page renders empty and the visitor cannot tell
    that the design is the reason."""
    for surface, css, query in (
        ("style.css", CSS, "(prefers-reduced-motion: reduce)"),
        ("app.html", APP_HTML, "(prefers-reduced-motion: reduce)"),
    ):
        block = media_block(css, query)
        assert re.search(r"animation-duration:\s*[0-9.]+m?s\s*!important", block), surface
        assert re.search(r"transition-duration:\s*[0-9.]+m?s\s*!important", block), surface
        hidden = [
            selector
            for selector, body in rule_selector_and_body(css)
            if ".js " in selector and re.search(r"opacity:\s*0(?:\.0+)?\s*[;}]", body + "}")
        ]
        for selector in hidden:
            name = selector.split()[-1]
            assert name in block, f"{surface}: {name} stays invisible without motion"


def test_every_page_owns_its_heading_outline():
    """Screen readers navigate by heading. A page with no `<h1>` has no name, and
    a jump from `<h1>` to `<h3>` is a section that announces itself as a subsection
    of something that does not exist."""
    for name in PAGES:
        text = re.sub(r"<script.*?</script>", "", read(name), flags=re.S)
        levels = [int(level) for level in re.findall(r"<h([1-6])\b", text)]
        assert levels, f"{name} has no heading at all"
        assert levels.count(1) == 1, f"{name}: {levels.count(1)} h1 in one document"
        assert levels[0] == 1, f"{name}: the document opens at h{levels[0]}"
        jumps = [
            (before, after)
            for before, after in pairwise(levels)
            if after - before > 1
        ]
        assert not jumps, f"{name}: skipped heading levels {jumps}"


HEADING_BURN_RE = re.compile(
    r"""(?:\$\(\s*|getElementById\(\s*|querySelector\(\s*)['"]#?([\w-]+)['"]\s*\)"""
    r"""\s*\.\s*(?:innerHTML|outerHTML|textContent|innerText)\s*=(?!=)"""
)


def element_subtrees(html: str) -> dict[str, str]:
    """The inner markup of every identified element, `id` → its contents.

    The walk keeps the offset where each open tag ended, so the matching close can
    slice the subtree instead of guessing its length: a container's contents are
    exactly what a script replacing it destroys. Script, style and svg bodies are
    cut first — a template literal is not markup the browser parsed — and a void
    or self-closed tag owns an empty subtree."""
    markup = re.sub(r"<(script|style|svg)\b.*?</\1>", "", html, flags=re.S)
    subtrees: dict[str, str] = {}
    stack: list[tuple[str, str, int]] = []
    for match in TAG_RE.finditer(markup):
        closing, tag, attrs = match.groups()
        name = tag.lower()
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == name:
                    _, identifier, start = stack.pop(i)
                    del stack[i:]
                    if identifier:
                        subtrees.setdefault(identifier, markup[start:match.start()])
                    break
            continue
        identifier = attribute(attrs, ID_ATTR_RE)
        start = match.end()
        if name in VOID_TAGS or attrs.rstrip().endswith("/"):
            if identifier:
                subtrees.setdefault(identifier, "")
            continue
        stack.append((name, identifier, start))
    return subtrees


def test_no_script_repaints_the_container_that_carries_the_heading():
    """An empty state is a payload, not a page.

    The branch that paints its note into the article which also holds the `<h1>`
    deletes the heading, the controls under it and the outline every screen reader
    walks — and the rendered gate cannot see it, because the read that *succeeds*
    is the page it opens. So no id whose own static subtree carries a heading may
    be handed a content assignment by a script.

    Both sides are measured from the bundle: the containers by walking each
    document's tags, the writers by reading the ids `page_code()` names on the way
    to an `innerHTML`/`textContent` assignment. `+=` is not one of them — appending
    into a container removes no heading — and neither is a class the code reaches
    through a selector, which paints but does not replace."""
    containers = 0
    writers = 0
    offenders = []
    for name in PAGES:
        html = read(name)
        headed = {
            identifier
            for identifier, inner in element_subtrees(html).items()
            if re.search(r"<h1[\s>]", inner, re.I)
        }
        containers += len(headed)
        painted = set(HEADING_BURN_RE.findall(page_code(name)))
        writers += len(painted)
        offenders += [f"{name}: #{identifier}" for identifier in sorted(headed & painted)]
    assert containers >= 4, f"only {containers} heading-carrying containers found — the walk is blind"
    assert writers >= 100, f"only {writers} content writes found — the reader is blind"
    assert not offenders, f"these replace a subtree that holds the document's h1: {offenders}"


def test_the_heading_burn_reader_reads_a_subtree_the_way_markup_nests():
    """The reader that lets the defect through is the one that reports a clean tree.

    So the shapes the shipped pages take are held here: a heading nested two
    levels deep, a heading that is a sibling of the written container, the written
    heading itself (painting a title is not burning it), a void element inside the
    subtree, and the two other ways this bundle names an element."""
    markup = (
        '<article id="wrap"><span class="e">x</span><section><h1 id="title">T</h1></section></article>'
        '<div id="rows"><p>a</p></div><h1>outside</h1>'
        '<div id="voids"><img src="/x.png" alt=""><br><p>after</p></div>'
    )
    subtrees = element_subtrees(markup)
    assert "wrap" in subtrees and "<h1" in subtrees["wrap"]
    assert "title" in subtrees and "<h1" not in subtrees["title"]
    assert "rows" in subtrees and "<h1" not in subtrees["rows"]
    assert "voids" in subtrees and "<h1" not in subtrees["voids"]

    code = (
        '$("wrap").innerHTML = note;'
        "document.getElementById('rows').textContent = 'x';"
        'document.querySelector("#voids").innerText = "y";'
        '$("title").textContent = name;'
        '$("wrap").innerHTML += more;'
        "$('wrap').classList.add('open');"
    )
    assert HEADING_BURN_RE.findall(code) == ["wrap", "rows", "voids", "title"]


def test_the_empty_page_reader_reads_the_paths_a_route_can_take():
    """`id_bearing_page_routes` decides which empty states a browser is ever shown.

    The origin writes its holes more than one way, and a reader that recognised
    only the bare name would hand the rendered gate a shorter list while its own
    count still looked plausible. So the shapes are held here: a subscript, a
    dotted attribute and a plain name all state a parameter; a literal path, a
    path read off the request and a document that is not a literal state none.
    The floor is tested as a floor, because a reader that quietly returned
    nothing is the failure this whole family exists to refuse."""
    from test_production_design import id_bearing_page_routes

    source = """
_static_page('a.html', request, path=f'/a/{row["id"]}')
_static_page('b.html', request, path=f'/b/{bet_id}')
_static_page('f.html', request, path=f'/f/{market.id}')
_static_page('c.html', request, path='/c')
_static_page('not_found.html', request, path=request.url.path)
_static_page(PAGE, request, path=f'/d/{tg_id}')
"""
    assert id_bearing_page_routes(source) == [
        ("a.html", '/a/{row["id"]}'),
        ("b.html", "/b/{bet_id}"),
        ("f.html", "/f/{market.id}"),
    ]
    with pytest.raises(AssertionError, match="the scan is blind"):
        id_bearing_page_routes("_static_page('x.html', request, path=f'/x/{tg_id}')")


def test_every_sprite_symbol_is_drawn_by_something():
    """The Mini App defines its icons once; an id no template references is a
    glyph kept alive by nothing."""
    defined = set(re.findall(r'<symbol id="([a-z0-9-]+)"', APP_HTML))
    outside = re.sub(r'<symbol id="[a-z0-9-]+"', "", APP_HTML)
    used = set(re.findall(r"['\"]#?(i-[a-z-]+)['\"]", outside)) | set(
        re.findall(r'href="#([a-z0-9-]+)"', outside)
    )
    dead = sorted(defined - used)
    assert not dead, f"{dead} are defined in the sprite and never referenced"


# ---------------------------------------------------------------------------
# loading states
# ---------------------------------------------------------------------------

VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>", re.S)
ID_ATTR_RE = re.compile(r'\bid="([^"]+)"')
CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')


def skeleton_owners(html: str) -> set[str]:
    """The nearest enclosing id of every element that shimmers in the static markup.

    A placeholder is a promise that code will replace it. Walking the tag stack
    finds the container a script writes into, which is the only thing that keeps
    a page from shimmering at a visitor forever. Script and style bodies are cut
    first: a template literal is not markup the browser parsed, and treating its
    unclosed tags as elements would invent containers. A shimmer with no id above
    it is skipped — it is drawn by a template that replaces its whole subtree.
    """
    markup = re.sub(r"<(script|style|svg)\b.*?</\1>", "", html, flags=re.S)
    stack: list[str | None] = []
    owners: set[str] = set()
    for closing, tag, attrs in TAG_RE.findall(markup):
        name = tag.lower()
        if closing:
            if name not in VOID_TAGS and stack:
                stack.pop()
            continue
        identifier = attribute(attrs, ID_ATTR_RE)
        shimmers = "skeleton" in attribute(attrs, CLASS_ATTR_RE)
        if name in VOID_TAGS or attrs.rstrip().endswith("/"):
            if shimmers and identifier:
                owners.add(identifier)
            continue
        stack.append(identifier or None)
        if shimmers:
            for ancestor in reversed(stack):
                if ancestor:
                    owners.add(ancestor)
                    break
    return owners


def attribute(attrs: str, pattern: re.Pattern) -> str:
    match = pattern.search(attrs)
    return match.group(1) if match else ""


def test_a_shimmer_is_owned_by_code_that_replaces_it():
    """The fake-loading bug: markup ships a skeleton, no script ever writes into
    that container, and the page shimmers at a visitor forever. Every placeholder
    in the source has to name a container some script fills or clears."""
    sources = {name: read(name) for name in (*SCRIPTS, "app.html")}
    owners: set[str] = set()
    for name in PAGES:
        owners |= skeleton_owners(read(name))
    unowned = []
    for owner in sorted(owners):
        writers = [
            source
            for source in sources.values()
            if f'$("{owner}")' in source
            or f"$('{owner}')" in source
            or f"getElementById('{owner}')" in source
            or f'getElementById("{owner}")' in source
            or f"#{owner}'" in source
        ]
        if not writers:
            unowned.append((owner, "no script touches it"))
        elif not any(".innerHTML" in source or "classList.remove" in source or "replaceChildren" in source
                     for source in writers):
            unowned.append((owner, "a script reads it but never fills it"))
    assert not unowned, f"skeletons that will never be replaced: {unowned}"


def mono_class_tokens(css: str) -> list[str]:
    """Class names whose own element the stylesheet draws in the mono face.

    The rendered gate takes this list instead of a hand-written one: the law it
    checks is "whatever the CSS calls monospaced computes as monospaced, with
    tabular digits", and a hand-written list would drift the moment a component
    is renamed. Only the *subject* of a selector counts — `.x .label` styles
    `.label`, while `.label::after` paints a generated marker and not the
    element, so taking every class in a selector would flag text no rule touches.

    Per document, because there are two: /app never loads style.css, so the site's
    list would judge the Mini App's classes by rules that do not apply to them and
    would never look at the ones that do.
    """
    tokens: set[str] = set()
    for selector, body in rule_selector_and_body(css):
        if "var(--font-mono)" not in body:
            continue
        for compound in selector.split(","):
            parts = re.split(r"[\s>+~]+", compound.strip())
            if not parts or not parts[-1]:
                continue
            subject = parts[-1]
            if "::" in subject:
                continue  # a generated marker, not the element: no text of its own
            subject = re.sub(r":[\w-]+(?:\([^)]*\))?", "", subject)
            tokens |= set(re.findall(r"\.([A-Za-z][\w-]*)", subject))
    return sorted(tokens)


# ---------------------------------------------------------------------------
# controls: what a visitor can click, and what is allowed to appear at all
# ---------------------------------------------------------------------------

SCRIPT_SRC_RE = re.compile(r'<script[^>]*\bsrc="/([\w.-]+\.js)"')


def page_scripts(name: str) -> tuple[str, ...]:
    """The scripts a document loads from this bundle, read off the document.

    This was a hand-written map of page → scripts. A page added to `PAGES` and
    missing from that map raised `KeyError` in the middle of an unrelated gate,
    and a page whose script list changed silently kept being measured against the
    old one — so the pair is measured from the `<script src>` tags instead, in any
    attribute order a document may write them in."""
    return tuple(SCRIPT_SRC_RE.findall(read(name)))


def page_code(name: str) -> str:
    """Every line of script that runs against this document: the files it loads
    plus the inline blocks it carries."""
    return "".join(read(js) for js in page_scripts(name)) + inline_scripts(read(name))


def inline_scripts(html: str) -> str:
    return "".join(
        re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S))


def unhidden_ids(code: str) -> set[str]:
    """ids the code names on its way to clearing `hidden` — by the attribute
    assignment or by removing it, directly or through a variable bound to them."""
    clear = r"(?:\.hidden\s*=\s*false|\.removeAttribute\(\s*['\"]hidden['\"]\))"
    found = set(re.findall(
        rf"""(?:\$\(\s*|getElementById\(\s*)['"]([\w-]+)['"]\s*\)\s*\??\s*{clear}""", code))
    for var, ident in re.findall(
            r"(?:const|let|var)\s+(\w+)\s*=\s*(?:\$\(\s*|document\.getElementById\(\s*)['\"]([\w-]+)['\"]", code):
        if re.search(rf"\b{re.escape(var)}{clear}", code):
            found.add(ident)
    return found


def unhidden_classes(code: str) -> set[str]:
    """Classes a loop assigns `.hidden` to — the generic shape `switchTab` uses
    over `.panel` elements, where no id is ever named."""
    covered: set[str] = set()
    for match in re.finditer(r"""querySelectorAll\(\s*['"]([^'"]+)['"]\s*\)""", code):
        if re.search(r"\.hidden\s*=", code[match.end():match.end() + 400]):
            covered |= set(re.findall(r"\.([\w-]+)", match.group(1)))
    return covered


def hidden_elements(html: str) -> dict[str, set[str]]:
    """id -> classes, for every element the markup ships invisible. `aria-hidden`
    is a different promise (screen readers) and is not counted."""
    found: dict[str, set[str]] = {}
    for tag in re.findall(r"<[a-z]+[^>]*>", html, re.I):
        if not re.search(r"\shidden(\s|=|>)", tag) or "aria-hidden" in tag:
            continue
        classes: set[str] = set()
        attr = re.search(r"\bclass=\"([^\"]*)\"", tag)
        if attr:
            classes = set(attr.group(1).split())
        for ident in re.findall(r'\bid="([^"]+)"', tag):
            found[ident] = classes
    return found


def test_no_anchor_leads_nowhere():
    """A link that resolves to nothing is a button that does nothing, and only
    the visitor finds out. ``/#onchain`` was exactly that: the landing names its
    section ``#onchain-markets``, so every on-chain market page sent someone who
    just failed to find a market to the top of the home page instead of the list
    the link promised."""
    index_ids = set(re.findall(r'\bid="([^"]+)"', read("index.html")))
    offenders = []
    for name in PAGES:
        html = read(name)
        own_ids = set(re.findall(r'\bid="([^"]+)"', html))
        for href in re.findall(r'<a\b[^>]*href="([^"]*)"', html, re.I):
            if href in ("", "#"):
                offenders.append((name, href or "(empty href)", "leads nowhere"))
                continue
            crosses_pages = href.startswith("/#")
            target = href[2:] if crosses_pages else (href[1:] if href.startswith("#") else "")
            if not target:
                continue
            wanted = index_ids if crosses_pages or name == "index.html" else own_ids
            if target not in wanted:
                offenders.append((name, href, f"no id={target} where it points"))
    assert not offenders, offenders


def test_every_element_that_ships_hidden_can_appear():
    """``hidden`` in the markup is a promise that code will clear it.
    ``#oc-scan`` had its href filled in and nobody cleared the attribute, so the
    contract link stayed invisible in the one configuration it exists for: a
    feature the file implements and no visitor can reach."""
    unreachable = []
    for name in PAGES:
        code = page_code(name)
        ids, classes = unhidden_ids(code), unhidden_classes(code)
        for ident, element_classes in hidden_elements(read(name)).items():
            if ident in ids or (element_classes & classes):
                continue
            unreachable.append(f"{name}: #{ident} ships hidden and nothing clears it")
    assert not unreachable, unreachable


# ---------------------------------------------------------------------------
# what the deploy files tell the operator to publish, and what the edge sends
# ---------------------------------------------------------------------------


def test_the_market_search_reaches_every_list_it_indexes():
    """The palette is one index over the lists the page already painted, so the
    two halves have to stay in step: a fifth list no kind names is a market nobody
    can find, and a kind whose `list` or `section` id is not in the document is a
    search result that scrolls to nothing — the same shape `/#onchain` had, where
    the landing named the section `#onchain-markets` and the link went to the top
    of the page instead of the list it promised."""
    code = page_code("index.html")
    table = re.search(r"const SEARCH_KINDS = \{(.*?)\n\};", code, re.S)
    assert table, "SEARCH_KINDS is not in the code the landing runs"
    kinds = re.findall(
        r'(\w+): \{ label: "[^"]*", list: "([^"]*)", section: "([^"]*)" \}',
        table.group(1),
    )
    assert kinds, "SEARCH_KINDS names no kind with a list and a section"

    ids = set(re.findall(r'\bid="([^"]+)"', read("index.html")))
    dead = [
        f"{kind}: {field} #{target} is not a node the landing has"
        for kind, lst, section in kinds
        for field, target in (("list", lst), ("section", section))
        if target not in ids
    ]
    assert not dead, dead

    indexed = set(re.findall(r"searchIndex\.(\w+) =", code))
    assert indexed == {kind for kind, _, _ in kinds}, sorted(indexed ^ {k for k, _, _ in kinds})

MANIFEST = ROOT / "deploy" / "farcaster_manifest.example.json"
EDGE_HEADERS = STATIC / "_headers"


def test_the_manifest_template_publishes_paths_the_bundle_has():
    """The Base App reads iconUrl, imageUrl and splashImageUrl off this file, and
    the template said ``/static/icon.png`` — a path the bundle has never served,
    because ``pages_build_output_dir`` already is ``web/static``. An operator
    following the documented steps got a launch card with no picture, no icon and
    a splash that 404s, and the button title carried an emoji the Mini App itself
    does not use."""
    import json

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    miniapp = manifest["miniapp"]
    for key in ("iconUrl", "imageUrl", "splashImageUrl"):
        url = miniapp[key]
        assert "/static/" not in url, f"{key} publishes {url}"
        filename = url.rsplit("/", 1)[-1]
        assert (STATIC / filename).is_file(), f"{key} names {filename}, which the bundle does not have"
    assert miniapp["homeUrl"].endswith("/app")
    assert miniapp["webhookUrl"].endswith("/api/webhook-miniaction")
    canvas = normalized(resolve(DARK, "--canvas"))
    assert miniapp["splashBackgroundColor"].lower() == canvas, (
        f"splash {miniapp['splashBackgroundColor']} is not the dark canvas {canvas}"
    )
    title = re.search(r'"button":\{"title":"([^"]+)"', read("app.html"))
    assert title, "app.html no longer declares the fc:miniapp button title"
    assert miniapp["buttonTitle"] == title.group(1), (miniapp["buttonTitle"], title.group(1))


def test_the_edge_sends_the_headers_the_origin_sends():
    """The origin answers every response with nosniff and no-referrer; the Pages
    bundle answers the CSS, the scripts and the images itself, and until this
    file existed it sent nothing. One site, two answers to the same question is
    how a mislabelled asset starts being executed."""
    assert EDGE_HEADERS.is_file(), "the bundle has no _headers file"
    text = EDGE_HEADERS.read_text(encoding="utf-8")
    block = re.search(r"^/\*\n((?:  [^\n]+\n)+)", text, re.M)
    assert block, "_headers has no /* block"
    edge_keys = {line.strip().split(":", 1)[0] for line in block.group(1).splitlines()}
    assert {"X-Content-Type-Options", "Referrer-Policy"} <= edge_keys, edge_keys
    assert "nosniff" in text and "no-referrer" in text
    origin = (ROOT / "web" / "server.py").read_text(encoding="utf-8-sig")
    for header in edge_keys:
        assert header in origin, f"the origin never sets {header} — the two have drifted"


# ---------------------------------------------------------------------------
# the edge bundle: what Pages answers from itself, and what it promises about it
# ---------------------------------------------------------------------------

def header_blocks(text: str | None = None) -> dict[str, dict[str, str]]:
    """`{path pattern: {Header: value}}` read out of `web/static/_headers`.

    The file's own syntax: an unindented line names the pattern, indented lines
    name the headers. A parser that only read the first block would certify a
    policy that half the bundle never receives, so it walks the whole file and
    the caller asserts which blocks it saw. A line with no colon is not a header
    and is dropped here — which is why the law that calls this reads the shipped
    file's own lines too, so a `Cache-Control …` typed without its colon is
    reported as the typo it is instead of quietly becoming no window at all.
    """
    lines = (EDGE_HEADERS.read_text(encoding="utf-8") if text is None else text).splitlines()
    blocks: dict[str, dict[str, str]] = {}
    pattern = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            pattern = line.strip()
            blocks[pattern] = {}
        elif pattern is not None and ":" in line:
            key, _, value = line.strip().partition(":")
            blocks[pattern][key.strip()] = value.strip()
    return blocks


def bundle_asset_suffixes() -> set[str]:
    """Every extension the edge can answer as a file: what the bundle directory
    holds, minus the templates and Markdown the mount refuses and minus plumbing
    that carries no suffix. The directory is the subject set, so a new file type
    joins this law by being added, not by being named here."""
    return {p.suffix.lower() for p in STATIC.rglob("*")
            if p.is_file() and p.suffix and not p.name.endswith((".html", ".md"))}


def suffixes_covered_by(pattern: str, root: Path | None = None) -> set[str]:
    """The extensions one `_headers` pattern actually reaches, measured from the
    files it names rather than from what the pattern was written for."""
    root = STATIC if root is None else root
    if pattern.endswith("/*"):
        directory = root / pattern[1:-2].strip("/")
        if not directory.is_dir():
            return set()
        # A file with no extension names no class — the same rule the shipped-asset
        # walk uses, so the two readers cannot disagree about `_headers` itself.
        return {p.suffix.lower() for p in directory.rglob("*") if p.is_file() and p.suffix}
    match = re.fullmatch(r"/\*\.(\w+)", pattern)
    return {f".{match.group(1)}"} if match else set()


def window_for(path: str, promises: list[tuple[str, int]]) -> int:
    """The `Cache-Control` window a bundle-relative path is owed, walked through
    the two shapes `_headers` can be written in.

    `/fonts/*` names a *directory*, so the prefix keeps its slash: slicing the
    pattern as `pattern[1:-2]` drops that slash together with the `/*`, and then
    `fonts.css` — a stylesheet at the bundle root, next to the directory — reads
    as claimed by the font block and the stylesheet block at once. That is a name
    collision inside the law, not a defect on the edge: the live bundle answered
    the file with the code window and the law was the thing that was wrong."""
    wanted: int | None = None
    for pattern, value in promises:
        if pattern == "/*":
            continue
        hit = (pattern.endswith("/*") and path.startswith(pattern[1:-1])) \
            or (pattern.startswith("/*.") and path.endswith(pattern[2:]))
        if hit:
            assert wanted in (None, value), f"{path} is claimed by two blocks"
            wanted = value
    assert wanted is not None, f"{Path(path).suffix} reaches the edge with no block in _headers"
    return wanted


def test_one_file_one_caching_policy_in_both_answers():
    """Pages answered every asset with its own default — `max-age=0,
    must-revalidate` — and the origin answered the same file with `no-store`,
    stamped by the middleware's blanket default. Measured: 61 KB of CSS, seven
    scripts and eleven faces refetched on every page view of a site whose assets
    change only when someone ships a commit.

    The policy is therefore one table with three readers: the extension set the
    bundle directory carries, the `Cache-Control` lines `_headers` writes for
    those patterns, and `ASSET_CACHE` in `web/server.py` that the mount stamps
    onto the same files when a browser reaches the VPS directly. The pairing goes
    both ways in both layers, so a new `.svg` in the bundle fails until the edge
    names a window for it and the origin does too, and a suffix in a table that
    ships no file fails the same law.

    A document is not in any of it. The middleware's `no-store` is what the
    session, the nonce and the balance owe, and `.html`/`.md` are refused from
    the mount — so this law also refuses a `Cache-Control` on the `/*` block,
    which is the one pattern that would reach a page."""
    import ast

    malformed = [line for line in EDGE_HEADERS.read_text(encoding="utf-8").splitlines()
                 if line[:1].isspace() and line.strip() and not line.lstrip().startswith("#")
                 and ":" not in line]
    assert not malformed, f"_headers has line(s) that are neither a comment nor a header: {malformed}"

    policies: dict[str, dict[str, str]] = {}
    for pattern, rules in header_blocks().items():
        cache = [value for key, value in rules.items() if key.lower() == "cache-control"]
        assert len(cache) <= 1, f"{pattern} declares {cache}"
        if pattern == "/*":
            assert not cache, "a Cache-Control on /* is a Cache-Control on a document"
        for suffix in suffixes_covered_by(pattern):
            if cache:
                policies.setdefault(suffix, []).append(cache[0])
    ambiguous = {s: v for s, v in policies.items() if len(set(v)) > 1}
    assert not ambiguous, f"two windows for one extension: {ambiguous}"
    edge = {suffix: values[0] for suffix, values in policies.items()}

    source = (ROOT / "web" / "server.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    strings = {t.id: node.value.value
               for node in tree.body if isinstance(node, ast.Assign)
               for t in node.targets
               if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant)
               and isinstance(node.value.value, str)}
    origin: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "ASSET_CACHE" for t in node.targets)):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant):
                continue
            if isinstance(value, ast.Constant):
                origin[key.value] = value.value
            elif isinstance(value, ast.Name):
                assert value.id in strings, f"ASSET_CACHE names {value.id}, which is not a string"
                origin[key.value] = strings[value.id]
    assert origin, "web/server.py declares no ASSET_CACHE table"

    shipped = bundle_asset_suffixes()
    assert len(shipped) >= 4, f"the directory walk saw {sorted(shipped)} — no gate"
    assert set(origin) == shipped, (
        f"the origin stamps a policy for {sorted(set(origin) - shipped)} the bundle does not "
        f"carry, and none for {sorted(shipped - set(origin))} it does")
    assert set(edge) == shipped, (
        f"_headers names a window for {sorted(set(edge) - shipped)} that is not a shipped "
        f"asset class, and none for {sorted(shipped - set(edge))} the edge answers itself")
    mismatched = [f"{s}: _headers {edge[s]!r} vs BundleFiles {origin[s]!r}"
                  for s in sorted(shipped & set(edge) & set(origin)) if edge[s] != origin[s]]
    assert not mismatched, "one file, two promises — a visitor through Pages and a visitor " \
                           "at the VPS cache the same asset for different lengths:\n" + "\n".join(mismatched)


def test_the_edge_routes_every_class_of_file_the_bundle_ships():
    """`functions/[[path]].js` decides alone which paths come from the asset
    bundle and which travel to the origin, so a file type the bundle carries but
    the routing does not name is proxied — and while the origin is down it is the
    maintenance card standing where a stylesheet should be. The class the routing
    must NOT name is the one that made `/robots.txt` a whole website's markup:
    Pages answers a missing bundle path with `index.html`.

    The extension list is read out of the shipped function, not restated here."""
    source = EDGE.read_text(encoding="utf-8-sig")
    declared = re.search(r"const ASSET = /\\.\((\?:[^)]*)\)\$/i;", source)
    assert declared, "the edge asset pattern moved somewhere this gate cannot read"
    asset = re.compile(f"\\.({declared.group(1)})$", re.I)
    unrouted = sorted(s for s in bundle_asset_suffixes() if not asset.search(f"x{s}"))
    assert not unrouted, (
        f"the bundle ships {unrouted} but the edge does not treat them as assets, so each is "
        "proxied to the origin and replaced by the maintenance card when it is down")
    for proxied in (".html", ".md", ".txt", ".json", ".yaml", ".yml"):
        assert not asset.search(f"x{proxied}"), (
            f"{proxied} is answered from the bundle, where a missing path is index.html")


def test_every_asset_url_a_shipped_file_names_is_a_file_the_bundle_carries():
    """`web/static` is both the source and the build output, so a URL that is not
    a file there is not a 404 on Pages — it is the landing page, with a 200 and
    `text/html`, in the place where a stylesheet, a face or a picture was asked
    for. That is the `/robots.txt` defect in its other direction: the file was
    correct, the reference pointed at nothing.

    Quoted absolute paths with an asset extension, over every document, script
    and stylesheet in the bundle and over the function that routes them. What it
    cannot see is a path assembled at runtime (`'/fonts/' + name + '.woff2'`),
    which no static scan can resolve, and a URL without a suffix is an origin
    route, not a bundle file."""
    offenders = []
    named: set[str] = set()
    for name in (*DOCUMENTS, "functions/[[path]].js"):
        path = STATIC / name if name in DOCUMENTS else ROOT / name
        text = path.read_text(encoding="utf-8-sig")
        for match in re.finditer(
            r"""['"(](/[A-Za-z0-9._/-]+\.(?:css|js|mjs|png|jpe?g|gif|svg|ico|webp|avif"""
            r"""|woff2?|webmanifest))(?:\?[^'")]*)?['")]""", text):
            url = match.group(1)
            named.add(url)
            if not (STATIC / url.lstrip("/")).is_file():
                offenders.append(f"{name} asks the edge for {url}")
    assert len(named) >= 8, f"the scan resolved {sorted(named)} — no gate"
    assert not offenders, "\n".join(sorted(set(offenders)))


def test_the_edge_card_copies_the_tokens_the_stylesheets_ship():
    """The maintenance card carries its own copy of the palette because it has to
    paint before any asset arrives. A copy is a second source, and this one had
    drifted two waves behind: `--muted` was #6f6f6f and `--muted-2` #9a9a9a while
    `style.css` had moved to #666666 and #707070, so the card's own `.hint` line
    measured 2.85:1 on the light canvas — the exact failure the design gates were
    written to refuse, on the one page the origin does not answer.

    Every token the card declares must be a token the stylesheet ships with the
    same value, and a token the card invents is a value no gate measures."""
    card = MAINTENANCE_RE.search(EDGE.read_text(encoding="utf-8-sig"))
    assert card, "the edge maintenance card is gone or its template shape changed"
    html = card.group(1)
    light = re.search(r":root \{(.*?)\n  \}", html, re.S)
    dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*\n\s*:root \{(.*?)\n  \}", html, re.S)
    assert light and dark, "the card's :root blocks moved out of the shape this gate reads"

    def flat(value: str) -> str:
        return re.sub(r"\s+", "", value).lower()

    def declared(block: str) -> dict[str, str]:
        return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", block))

    wrong: list[str] = []
    for label, block, palette in (("light", light.group(1), LIGHT), ("dark", dark.group(1), DARK)):
        tokens = declared(block)
        assert len(tokens) >= 10, f"the parser read {sorted(tokens)} from the {label} block — no gate"
        for name, value in sorted(tokens.items()):
            if name not in palette:
                wrong.append(f"{label}: {name} is in the card and in no stylesheet")
            elif flat(value) != flat(resolve(palette, name)):
                wrong.append(
                    f"{label}: {name} is {value.strip()} in the card, {resolve(palette, name).strip()} "
                    "in style.css")
    assert not wrong, "the edge paints a palette the site does not ship:\n" + "\n".join(wrong)


def test_the_edge_card_sends_the_headers_the_origin_sends():
    """The middleware stamps nosniff, `Referrer-Policy` and `no-store` on every
    response the origin writes, and the 503 card is the one document on this site
    that no origin writes — so it was the only page on the internet that answered
    a browser with neither, and a card whose own body is cacheable is a card that
    outlives the outage it describes."""
    source = EDGE.read_text(encoding="utf-8-sig")
    block = re.search(r"function maintenance\([^)]*\) \{.*?headers: \{(.*?)\n    \}", source, re.S)
    assert block, "maintenance() no longer builds its headers inline where this gate reads them"
    headers = {name.strip().lower(): value.strip()
               for name, value in re.findall(r"'([^']+)':\s*'([^']*)'", block.group(1))}
    assert headers.get("x-content-type-options") == "nosniff", headers
    assert headers.get("referrer-policy") == "no-referrer", headers
    assert headers.get("cache-control") == "no-store", headers
    assert headers.get("retry-after") == "300", headers
    assert headers.get("content-type", "").startswith("text/html"), headers
    assert "x-tippy-outage-reason" in block.group(1), (
        "the reason the card exists is only in its body, so `curl -I` of an outage "
        "reports nothing about it and the operator opens a browser to learn the cause"
    )


def test_the_edge_answers_a_silent_origin_inside_the_window_the_doc_promises():
    """The outage card is only kind if it arrives. Measured on the live host while
    the tunnel was mid-reconnect: `fetch` had no deadline, so a request sat **22.5 s**
    behind a spinner and then spent a second untimed window in the retry loop — the
    one behaviour a visitor cannot distinguish from a dead tab. So the edge names one
    `UPSTREAM_DEADLINE_MS`, arms it on the upstream request, releases it once the
    origin has begun answering (a body still arriving is a live page, not an outage),
    and treats a spent window as the end of the retry rather than a blink worth
    another wait. The paragraph of `docs/CLOUDFLARE.md` that names the constant has
    to bold the same figure, because a runbook promising a card at fifteen seconds
    that ships it at forty teaches the operator to distrust the runbook."""
    source = EDGE.read_text(encoding="utf-8-sig")
    declared = re.search(r"const UPSTREAM_DEADLINE_MS = (\d+);", source)
    assert declared, "the edge no longer names one upstream deadline constant"
    milliseconds = int(declared.group(1))
    assert milliseconds % 1000 == 0, f"{milliseconds} ms is not a figure a document can quote in seconds"
    seconds = milliseconds // 1000
    assert 5 <= seconds <= 25, (
        f"a {seconds} s window is either shorter than this origin's measured worst "
        "answer under load (7.8 s for a health read on the hosting machine) or longer "
        "than a browser tab a human is still watching"
    )
    armed = re.search(r"setTimeout\(\(\) => \{ expired = true; waiting\.abort\(\); \},"
                      r" UPSTREAM_DEADLINE_MS\)", source)
    assert armed, "the deadline is declared and never armed on the upstream request"
    upstream = re.search(r"const send = \(\) => fetch\(.*?\n {2}\}\);", source, re.S)
    assert upstream and re.search(r"\bsignal:\s*waiting\.signal\b", upstream.group(0)), (
        "the armed controller never reaches the upstream request"
    )
    loop = source[source.index("for (let attempt"):source.index("const answer")]
    assert re.search(r"if \(expired\) break;", loop), (
        "a spent window is retried: the deadline the card promises becomes two windows "
        "of silence, which is the defect this law was written after measuring"
    )
    tail = source[source.index("const answer"):]
    assert re.search(r"clearTimeout\(timer\)", tail), (
        "the timer survives the answer, so an origin that takes eight seconds to "
        "stream a document has its body cut off by a deadline that was only ever "
        "meant to bound the wait for the first byte"
    )
    doc = (ROOT / "docs" / "CLOUDFLARE.md").read_text(encoding="utf-8-sig")
    blocks = [block for block in doc.split("\n\n") if "UPSTREAM_DEADLINE_MS" in block]
    assert blocks, "no block of the runbook names the deadline the card is built on"
    with_a_figure = [b for b in blocks if re.search(r"\*\*(\d+) s\*\*", b)]
    assert len(with_a_figure) == 1, (
        f"{len(with_a_figure)} blocks bold a seconds figure beside UPSTREAM_DEADLINE_MS; "
        "two places state one number and only one of them can be right"
    )
    promised = re.findall(r"\*\*(\d+) s\*\*", with_a_figure[0])
    assert promised == [str(seconds)], (
        f"the doc bolds {promised} where the code arms {seconds} s"
    )
    for other in blocks:
        if other is with_a_figure[0]:
            continue
        assert not re.search(r"\d+(?:\.\d+)?\s*s\b", other), (
            f"a second mention of the deadline carries its own wait: {other[:120]!r}"
        )


def _outage_vocabulary(source: str) -> dict[str, str]:
    """The reason words the card may say, with the sentence each one paints."""
    block = re.search(r"const OUTAGE_REASONS = \{(.*?)\n\};", source, re.S)
    assert block, "the edge no longer keeps its outage reasons in one named object"
    pairs = re.findall(r"'([a-z0-9-]+)': '([^']+)'", block.group(1))
    assert pairs, "OUTAGE_REASONS holds nothing this gate can read"
    assert len(pairs) == len(dict(pairs)), f"a duplicated reason word: {pairs}"
    reasons = dict(pairs)
    assert len(reasons) >= 3, (
        f"{sorted(reasons)} — fewer than three reasons is a card that says less than "
        "the function measured, which is how `maintenance()` used to answer every "
        "outage with the same sentence"
    )
    assert len(set(reasons.values())) == len(reasons), f"two reasons share one sentence: {reasons}"
    return reasons


def _reason_chooser(source: str) -> str:
    body = re.search(r"function outageReason\(\{.*?\} = \{\}\) \{(.*?)\n\}", source, re.S)
    assert body, (
        "outageReason() moved or changed shape, so the word the card prints has no "
        "reader and the vocabulary below would be compared with nothing"
    )
    return body.group(1)


def test_the_edge_card_says_only_a_reason_the_measurement_can_produce():
    """The card is the only answer this edge writes by itself, so it is the only
    place an outage can be named: the origin that could have explained is the thing
    that is missing. Four things have to hold for that name to mean anything.

    The word list and the branch list are one set — a reason the chooser cannot
    return is a sentence nothing paints, and a returned word the card does not know
    prints `undefined` where a visitor asked for a reason. The reason reaches the
    page in a machine-readable attribute and in a response header, because an
    operator reading an outage with `curl -I` should not have to open a browser.
    Every card call takes its word from the chooser, so no call site can hand-pick
    a flattering one. And the chooser reads its three inputs and nothing else: a
    reason taken from an exception object would be this edge quoting its own
    internals on a page anyone can request. The fifth thing is the word against the
    sentence: each reason must state the fact only its branch can know."""
    source = EDGE.read_text(encoding="utf-8-sig")
    reasons = _outage_vocabulary(source)
    chooser = _reason_chooser(source)

    produced = set(re.findall(r"return '([a-z0-9-]+)';", chooser))
    assert produced == set(reasons), (
        f"the chooser can return {sorted(produced)} while the card knows {sorted(reasons)}"
    )
    assert not re.search(r"\b(?:error|err|exception|cause|message|stack)\b", chooser, re.I), (
        "the reason is read off a failure rather than off the branch taken"
    )

    # Which branch paints which word is proved by running the function; which
    # sentence a word deserves is proved here. A sentence moved between two reasons
    # keeps every call site, header and attribute intact and still lies to the
    # visitor: `refused` wearing the deadline's words tells an operator to wait for a
    # slow origin when nothing listens at all. One distinguishing noun per fact,
    # frozen beside the code it describes — the anchor must sit in its own reason's
    # sentence and in nobody else's, so a reword that drops the fact is reported.
    anchors = {
        "no-backend": "настроен",
        "deadline": "время",
        "refused": "соединение",
        "gateway-5xx": "прокси",
    }
    assert set(anchors) == set(reasons), (
        f"the sentences are keyed to {sorted(anchors)} and the card answers for {sorted(reasons)}"
    )
    for reason, anchor in anchors.items():
        assert len(anchor) >= 5, f"{anchor!r} is too short to name a fact"
        assert anchor in reasons[reason], (
            f"`{reason}` says {reasons[reason]!r}, which no longer states the "
            f"{anchor!r} its branch measured"
        )
        worn = sorted(word for word, sentence in reasons.items()
                      if word != reason and anchor in sentence)
        assert not worn, (
            f"the sentence of `{reason}` states the {anchor!r} that belongs to {worn}: "
            "two reasons now promise the same measurement and the card cannot tell them apart"
        )

    calls = source.count("maintenance(") - source.count("function maintenance(")
    wired = len(re.findall(r"maintenance\(outageReason\(", source))
    assert calls >= 2 and calls == wired, (
        f"{calls} places ask the edge for a card and only {wired} take the word from "
        "outageReason(): the rest choose their own excuse"
    )

    html = MAINTENANCE_RE.search(source)
    assert html, "the edge maintenance card is gone or its template shape changed"
    markup = html.group(1)
    interpolated = set(re.findall(r"\$\{[^{}]*\}", markup))
    assert interpolated == {"${reason}", "${OUTAGE_REASONS[reason]}"}, (
        f"the card interpolates {sorted(interpolated)} — only the reason word and its "
        "fixed sentence may reach a page that the origin never answered"
    )
    assert 'data-reason="${reason}"' in markup, (
        "the reason is painted as prose only, so nothing downstream can branch on it"
    )


_EDGE_REASON_DRIVER = """
import http from 'node:http';
import { onRequest } from './function.mjs';

const origin = http.createServer((req, res) => {
  const path = req.url.split('?')[0];
  if (path === '/html502') {
    res.writeHead(502, { 'content-type': 'text/html' });
    res.end('bad gateway from a proxy in front of uvicorn');
    return;
  }
  if (path === '/json502') {
    res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ detail: 'the app itself answered 502' }));
    return;
  }
  if (path === '/slow') { return; }
  if (path === '/reset') { res.socket.destroy(); return; }
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ status: 'ok' }));
});
await new Promise((ready) => origin.listen(0, '127.0.0.1', ready));
const base = `http://127.0.0.1:${origin.address().port}`;
const assets = { fetch: async () => new Response('missing', { status: 404 }) };

async function ask(path, backend) {
  const res = await onRequest({
    request: new Request(`https://tippy.test${path}`),
    env: { ASSETS: assets, BACKEND_URL: backend },
  });
  const body = res.status === 503 ? await res.text() : '';
  return {
    status: res.status,
    type: res.headers.get('content-type'),
    reason: res.headers.get('x-tippy-outage-reason'),
    attribute: (body.match(/data-reason="([^"]*)"/) || [])[1] ?? null,
    painted: (body.match(/data-reason="[^"]*">([^<]*)<br/) || [])[1] ?? null,
    blank: body.includes('undefined'),
    leaked: /ECONNRESET|socket|TypeError|fetch failed/i.test(body),
  };
}

const out = {
  'no-backend': await ask('/anything', ''),
  'serving': await ask('/anything', base),
  'app-own-502': await ask('/json502', base),
  'proxy-502': await ask('/html502', base),
  'refused': await ask('/reset', base),
  'silent': await ask('/slow', base),
};
console.log(JSON.stringify(out));
origin.close();
"""


def test_the_reason_the_card_gives_is_the_reason_the_measured_shape_produced(tmp_path):
    """Read, the vocabulary above is four words in an object; run, it is a verdict
    about a wire. The shipped Function is imported and driven against a local origin
    that answers each of the shapes an outage actually takes — configured, serving,
    its own JSON 502, a proxy's HTML 502, a connection refused, a connection that
    says nothing — and the reason each pass prints has to be the one that shape
    deserves.

    The only edit is the window: `UPSTREAM_DEADLINE_MS` is shortened so the silent
    case costs 1.2 s instead of the 15 s the doc promises, and the deadline itself is
    measured by the law above against the unedited file. Without running it, the
    defect this law found stays invisible: `outageReason({ upstream })` reads a
    spent window as `refused`, because the branch flag was never handed over — the
    card then blamed a refused connection for an origin that simply said nothing."""
    source = EDGE.read_text(encoding="utf-8-sig")
    reasons = _outage_vocabulary(source)
    short, subs = re.subn(
        r"const UPSTREAM_DEADLINE_MS = \d+;",
        "const UPSTREAM_DEADLINE_MS = 1200;",
        source,
    )
    assert subs == 1, f"the deadline constant was replaced {subs} times — the run measures nothing"
    (tmp_path / "function.mjs").write_bytes(short.encode("utf-8"))
    (tmp_path / "driver.mjs").write_bytes(_EDGE_REASON_DRIVER.encode("utf-8"))

    result = subprocess.run(
        ["node", str(tmp_path / "driver.mjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=tmp_path, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])

    cards = {name: row for name, row in observed.items() if row["status"] == 503}
    said = {row["reason"] for row in cards.values()}
    assert said == set(reasons), (
        f"the shapes produced {sorted(said)} while the card knows {sorted(reasons)}"
    )
    expected = {
        "no-backend": "no-backend", "proxy-502": "gateway-5xx",
        "refused": "refused", "silent": "deadline",
    }
    assert set(cards) == set(expected), sorted(cards)
    for name, reason in expected.items():
        row = cards[name]
        assert row["reason"] == reason, (name, row)
        assert row["attribute"] == reason, (name, row)
        assert row["painted"] == reasons[reason], (name, row["painted"], reasons[reason])
        assert not row["blank"], f"{name} paints an undefined sentence: {row}"
        assert not row["leaked"], f"{name} leaks the failure text into the page: {row}"

    assert observed["serving"]["status"] == 200 and observed["serving"]["reason"] is None, (
        "a serving origin got the card"
    )
    assert observed["app-own-502"]["status"] == 502, (
        "the app's own 502 was replaced by the card: the reason would then describe a "
        "gateway that never spoke for the application"
    )
    assert "json" in (observed["app-own-502"]["type"] or ""), observed["app-own-502"]


_EDGE_PREVIEW_DRIVER = """
import { onRequest } from './function.mjs';

// What the bundle really answers on a Pages project with the single-page capture on:
// a path the project does not carry comes back `200 text/html` holding the landing
// document. Every driver above stubs `ASSETS` as always-404, which is precisely why
// none of them ever saw the preview branch — a 404 stub cannot be mistaken for a
// page, so the fallback looked inert and the site shipped serving the landing for
// `/api/health`, `/robots.txt` and a mistyped URL alike.
const LANDING = '<!doctype html><html lang="ru" data-signed-in="__SIGNED_IN__"><title>Tippy</title><body>the landing document</body></html>';
const APP_PAGE = '<!doctype html><html lang="en"><title>Tippy — Mini App</title><body>the mini app document</body></html>';
// Three documents a bundle can hold whose names are not pages. They are here on
// purpose: without them the capture probe below refuses every shape this law names,
// and the gates on the path would be measured by nothing but their own redundancy.
const API_PAGE = '<!doctype html><html><body>a document the bundle carries under an API name</body></html>';
const TEXT_PAGE = '<!doctype html><html><body>a document the bundle carries behind a suffix</body></html>';
const PLUMB_PAGE = '<!doctype html><html><body>a document the bundle carries behind the plumbing prefix</body></html>';

const CARRIED = {
  '/index.html': LANDING,
  '/app.html': APP_PAGE,
  '/api/health.html': API_PAGE,
  '/robots.txt.html': TEXT_PAGE,
  '/_headers.html': PLUMB_PAGE,
};

const assets = {
  fetch: async (req) => new Response(
    CARRIED[new URL(req.url).pathname] ?? LANDING,
    { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } },
  ),
};

// A port nothing listens on: the origin is configured here but dead, which is the
// second branch the preview was written into.
const DEAD = 'http://127.0.0.1:1';

async function ask(path, { accept = 'text/html', method = 'GET', demo = '1', backend = '' } = {}) {
  const res = await onRequest({
    request: new Request(`https://tippy.test${path}`, { method, headers: { accept } }),
    env: { ASSETS: assets, BACKEND_URL: backend, DEMO_HTML: demo },
  });
  return {
    status: res.status,
    type: res.headers.get('content-type'),
    preview: res.headers.get('x-tippy-demo-mode'),
    reason: res.headers.get('x-tippy-outage-reason'),
    cache: res.headers.get('cache-control'),
    nosniff: res.headers.get('x-content-type-options'),
    refer: res.headers.get('referrer-policy'),
    text: await res.text(),
  };
}

const out = {
  'preview-off': await ask('/', { demo: '0' }),
  'landing': await ask('/'),
  'named-page': await ask('/app'),
  'soft-404': await ask('/definitely-not-a-page'),
  'api-for-json': await ask('/api/health', { accept: 'application/json' }),
  'api-in-a-browser': await ask('/api/health'),
  'page-for-json': await ask('/app', { accept: 'application/json' }),
  'robots': await ask('/robots.txt'),
  'plumbing': await ask('/_headers'),
  'post': await ask('/', { method: 'POST' }),
  'origin-down-landing': await ask('/', { backend: DEAD }),
  'origin-down-api': await ask('/api/info', { accept: 'application/json', backend: DEAD }),
};
console.log(JSON.stringify(out));
"""


def test_the_edge_previews_the_bundle_only_for_a_browser_reading_a_page(tmp_path):
    """A design preview and a production outage are made of the same bytes, and only
    an agreement about which one to answer keeps them apart. Five questions decide it
    on this file's own side — did the project ask for a preview (`DEMO_HTML=1`), is
    the request a read, does the client accept HTML, is the path a page rather than
    `/api/`, `/_*` or a name carrying a format suffix, and does the bundle really hold
    that page rather than the single-page capture answering for it. This law drives
    the shipped Function against an ASSETS stub that behaves the way Pages does — `200
    text/html` for a path the project lacks — because every earlier harness stubbed a
    404 there and so watched none of this, and it also carries three documents whose
    names are not pages, so that each gate is measured by a row of its own instead of
    being covered by the capture probe.

    The shape each row refuses is a shape the live host was measured in on 2026-11-20:
    `GET /api/health` answered `200 text/html` carrying `x-tippy-demo-mode: true`,
    `/robots.txt` and `/sitemap.xml` answered the landing's 41,661 bytes instead of
    rules, an unknown URL answered a soft 200 where `not_found.html` owes a `noindex`
    dead end, and the card that names the fault — the one `docs/CLOUDFLARE.md` routes
    an operator by — appeared only for a POST."""
    source = EDGE.read_text(encoding="utf-8-sig")
    (tmp_path / "function.mjs").write_bytes(source.encode("utf-8"))
    (tmp_path / "driver.mjs").write_bytes(_EDGE_PREVIEW_DRIVER.encode("utf-8"))

    result = subprocess.run(
        ["node", str(tmp_path / "driver.mjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=tmp_path, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])

    wants = {
        "preview-off": (503, "no-backend"),
        "landing": (200, None),
        "named-page": (200, None),
        "soft-404": (503, "no-backend"),
        "api-for-json": (503, "no-backend"),
        "api-in-a-browser": (503, "no-backend"),
        "page-for-json": (503, "no-backend"),
        "robots": (503, "no-backend"),
        "plumbing": (503, "no-backend"),
        "post": (503, "no-backend"),
        "origin-down-landing": (200, None),
        "origin-down-api": (503, "refused"),
    }
    assert set(observed) == set(wants), (
        "the driver answered a different set of shapes than this law judges — the "
        "harness went blind, which is how the preview branch shipped untested"
    )
    previews = [name for name, (status, _) in wants.items() if status == 200]
    assert len(previews) >= 2, "no row is a preview, so the gates above prove nothing"

    for name, (status, reason) in wants.items():
        row = observed[name]
        assert row["status"] == status, (name, row["status"], row["reason"], row["text"][:90])
        assert row["reason"] == reason, (name, row["reason"], reason)
        card = "временно недоступен" in row["text"]
        if status == 200:
            assert not card, f"{name} is a card wearing a preview"
            assert row["preview"] == "true", (name, "a bundle document served without saying so")
            assert row["cache"] == "no-store" and row["nosniff"] == "nosniff", (name, row)
            assert row["refer"] == "no-referrer", (name, row)
            assert "text/html" in (row["type"] or ""), (name, row["type"])
        else:
            assert card, f"{name} answered neither the card nor the page: {row['text'][:90]}"
            assert row["preview"] is None, f"{name} stamps a card as a preview"
            assert "__SIGNED_IN__" not in row["text"], (
                f"{name} leaked a bundle document into the answer a card owes"
            )

    assert "the landing document" in observed["landing"]["text"], (
        "the landing is not standing in for itself, so nothing here is a preview"
    )
    assert "the mini app document" in observed["named-page"]["text"], (
        "/app was answered by the landing: the capture was taken for the page"
    )
    assert "the landing document" not in observed["soft-404"]["text"], (
        "a path the bundle does not carry answered the landing — a soft 404 is a page "
        "that exists everywhere, and a crawler cannot tell an outage from a typo"
    )
    for name, phrase in {
        "api-in-a-browser": "under an API name",
        "api-for-json": "under an API name",
        "robots": "behind a suffix",
        "plumbing": "behind the plumbing prefix",
        "page-for-json": "the mini app document",
    }.items():
        assert phrase not in observed[name]["text"], (
            f"{name} answered a bundle document that the path and Accept gates "
            "exist to refuse: one of them is now doing nothing"
        )


def test_the_doc_gives_every_outage_reason_its_own_remedy():
    """A reason the runbook does not name is a card an operator has to re-derive.
    The table has one row per word the Function can print, no row for a word the
    Function cannot print, and a remedy in each row — because the four reasons point
    at four different places to fix."""
    source = EDGE.read_text(encoding="utf-8-sig")
    reasons = _outage_vocabulary(source)
    doc = (ROOT / "docs" / "CLOUDFLARE.md").read_text(encoding="utf-8-sig")
    rows: dict[str, list[str]] = {}
    for line in doc.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        word = cells[0].strip("`")
        if word in reasons:
            assert word not in rows, f"`{word}` is answered twice in the runbook"
            rows[word] = cells
    assert set(rows) == set(reasons), (
        f"the runbook names {sorted(set(rows))} and the edge prints {sorted(reasons)}"
    )
    for word, cells in sorted(rows.items()):
        assert len(cells) >= 3, f"the `{word}` row has no remedy column: {cells}"
        remedy = cells[-1]
        assert len(remedy) >= 40, f"the `{word}` remedy is a stub: {remedy!r}"
        assert "`" in remedy or "watchdog" in remedy.lower(), (
            f"the `{word}` remedy names nothing an operator can run or read: {remedy!r}"
        )
    header = [line for line in doc.splitlines() if line.startswith("| Reason |")]
    assert len(header) == 1, f"{len(header)} reason tables; one vocabulary needs one place"


def test_the_headers_reader_reads_every_block_the_file_writes(tmp_path):
    """`header_blocks()` is four lines of parsing standing between the shipped
    policy and a green build, so a parser that quietly returned only the first
    block would certify a file whose last half nothing reads. Tested on markup that
    is not the file it usually gets: a comment, a blank line, a block carrying two
    headers, a block carrying none, and a line that is not a header at all."""
    text = "\n".join([
        "# a comment a naive split would call a pattern",
        "/*",
        "  X-Content-Type-Options: nosniff",
        "",
        "  Referrer-Policy: no-referrer",
        "/fonts/*",
        "  not a header line",
        "/*.css",
        "  Cache-Control: public, max-age=300, stale-while-revalidate=86400",
        "  Link: </fonts.css>; rel=preload",
    ])
    blocks = header_blocks(text)
    assert set(blocks) == {"/*", "/fonts/*", "/*.css"}, blocks
    assert blocks["/*"] == {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
    assert blocks["/fonts/*"] == {}, "a pattern with no headers must still be seen"
    # `public, max-age=300, …` splits on commas in the value, not on the colon.
    assert blocks["/*.css"]["Cache-Control"] == "public, max-age=300, stale-while-revalidate=86400"
    assert blocks["/*.css"]["Link"] == "</fonts.css>; rel=preload"


def test_a_pattern_reaches_the_files_that_actually_exist(tmp_path):
    """`suffixes_covered_by()` decides which shipped extensions a `_headers` window
    is promised to. Derived from the files, so `/fonts/*` over an empty directory
    claims nothing and `/sub/*` claims what is really under it — including a
    nested file a pattern written for one depth would have missed."""
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "a.woff2").write_bytes(b"x")
    (tmp_path / "fonts" / "deep").mkdir()
    (tmp_path / "fonts" / "deep" / "b.WOFF2").write_bytes(b"x")
    (tmp_path / "style.css").write_bytes(b"x")
    (tmp_path / "index.html").write_bytes(b"x")
    (tmp_path / "_headers").write_bytes(b"x")
    assert suffixes_covered_by("/fonts/*", tmp_path) == {".woff2"}
    assert suffixes_covered_by("/*.css", tmp_path) == {".css"}
    assert suffixes_covered_by("/*.svg", tmp_path) == {".svg"}, (
        "an extension the pattern names is claimed even when no file carries it — "
        "the caching law is what refuses a window for a class the bundle does not ship")
    assert suffixes_covered_by("/nope/*", tmp_path) == set()
    assert suffixes_covered_by("/style.css", tmp_path) == set(), "a literal path is not a class"
    assert suffixes_covered_by("/*", tmp_path) == {".woff2", ".css", ".html"}, (
        "the extension-less `_headers` joined the set, so `/*` would claim a class no file has")


def test_a_window_pattern_reaches_paths_and_not_names_that_look_alike():
    """`window_for()` is how the live-bundle law decides what a file was promised.
    The decoy is the real tree: `fonts.css` sits at the bundle root beside the
    `fonts/` directory, and a pattern matcher that reads `/fonts/*` as the prefix
    `fonts` claims it twice with two different windows — which is exactly how this
    law failed the day the bundle it measures was byte-for-byte correct."""
    promises = [("/*.css", 300), ("/*.js", 300), ("/fonts/*", 86400), ("/*.png", 86400)]
    assert window_for("fonts.css", promises) == 300, (
        "the directory block reached a root file whose name merely starts with it")
    assert window_for("fonts/golos-text-latin.woff2", promises) == 86400
    assert window_for("style.css", promises) == 300
    assert window_for("chain.js", promises) == 300
    with pytest.raises(AssertionError):
        window_for("legal.md", promises)
    with pytest.raises(AssertionError):
        window_for("a.css", [("/*.css", 300), ("/nope/*", 1), ("/*.css", 86400)])


# ---------------------------------------------------------------------------
# market state: the schema, the shared derivation, and both badge families
# ---------------------------------------------------------------------------

# The vocabulary is not a frontend opinion, and it is not a tuple kept here.
# `bets.status` and `markets.status` name their domain in the DDL that creates
# them, and the one state the page adds on top of the row is the market whose
# deadline passed while nobody decided it: until `chain.js` gave that a word of
# its own, the landing hero, `/m/{id}` and `/oc/{id}` each derived the answer
# from whichever field it happened to carry, and all three printed «дедлайн
# прошёл» in the same line as «открыт».
#
# So the set is measured from the three layers it has to agree across: the
# database, the derivation and the two stylesheets that have a paint for it.

# The DDL that creates these tables lives in exactly one place since 018: the
# tracked alembic chain. The list is the directory, so a table a later revision
# re-creates is still read where the chain last said what it holds.
CHAIN_DDL = "\n".join(p.read_text(encoding="utf-8-sig")
                      for p in sorted((ROOT / "alembic" / "versions").glob("*.py")))


def ddl_block(table: str) -> str:
    """The CREATE TABLE text the chain uses for this table (the last one wins)."""
    blocks = re.findall(rf"CREATE TABLE (?:IF NOT EXISTS )?{table} \((.*?)^\s*\);",
                        CHAIN_DDL, re.S | re.M)
    assert blocks, f"the alembic chain never creates {table}"
    return blocks[-1]


def schema_states() -> set[str]:
    """Every status a market row can hold, read off its own CREATE TABLE."""
    found: set[str] = set()
    for table in ("bets", "markets"):
        found |= {default for default, _ in re.findall(
            r"status\s+TEXT NOT NULL DEFAULT '(\w+)',?\s*--\s*([\w |]+)", ddl_block(table))}
        for _, listed in re.findall(
                r"status\s+TEXT NOT NULL DEFAULT '(\w+)',?\s*--\s*([\w |]+)", ddl_block(table)):
            found |= {word.strip() for word in listed.split("|") if word.strip()}
    assert found, "no status column in the chain names the domain it holds"
    return found


def derived_states() -> set[str]:
    """Every state word `marketState` can answer with, taken from its own body."""
    body = js_function(read("chain.js"), "marketState")
    assert body, "chain.js has no marketState() for the pages to share"
    return set(re.findall(r'"([a-z]+)"', body))


def state_classes(css: str, family: str) -> set[str]:
    """The states this stylesheet paints a badge for — `.chip-open`, `.tag-open`."""
    return set(re.findall(rf"\.{family}-([a-z]+)\s*[{{:,]", css))


# Which stylesheet owns which badge family. The site paints `.chip-*` in
# `style.css`, the Mini App `.tag-*` in its own block; the rendered gate walks the
# same map, so the two layers cannot disagree about what «both families» means.
BADGE_FAMILIES = {"chip": "style.css", "tag": "app.html"}


def state_vocabulary() -> set[str]:
    return schema_states() | derived_states()


# The one file that defines the derivation, read off the directory. A page that
# loads it for the chain labels (`/u/{id}`) does not thereby answer what state a
# market is in, and the operator dashboard borrows `.chip-open` for a machine that
# is «работает» — so "derives state" is a call site in the document's own code,
# measured with the definition taken out.
def derivation_script() -> str:
    found = [name for name in SCRIPTS if "function marketState" in read(name)]
    assert found, "no script defines marketState(): the shared derivation is gone"
    return found[0]


def own_code(name: str) -> str:
    shared = derivation_script()
    return ("".join(read(js) for js in page_scripts(name) if js != shared)
            + inline_scripts(read(name)))


def states_painted(code: str) -> set[str]:
    """Which states this code can name in a badge. Intersected with the
    vocabulary, because the site has other `chip`-ish classes (`chip-btn`,
    `amount-chip`) that are a shape, not a state."""
    return set(re.findall(r"\b(?:chip|tag)-([a-z]+)\b", code)) & state_vocabulary()


def state_surfaces() -> tuple[str, ...]:
    return tuple(name for name in PAGES if "marketState(" in own_code(name))


# The row stores three states; the interface answers four, because a market whose
# deadline passed while nobody resolved it is not tradable and the DDL has no word
# for it. That fourth state is a fact a reader of the architecture document needs —
# it is the state in which money sits on the site and can be handed back — so the
# document names the states, and this law compares its words with the vocabulary the
# code answers with instead of trusting the prose.
ARCHITECTURE = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8-sig")
STATE_SENTENCE = re.compile(r"- Market states:(.*?)(?=\n- |\n\n)", re.S)


def documented_market_states() -> tuple[set[str], set[str]]:
    """(stored, derived) — what the document says the column holds, and what it
    says the interface adds on top of the row."""
    found = STATE_SENTENCE.search(ARCHITECTURE)
    assert found, "docs/ARCHITECTURE.md no longer states the market states at all"
    stored, _, added = found.group(1).partition("the interface adds")

    def words(text: str) -> set[str]:
        return set(re.findall(r"`([a-z]+)`", text)) & state_vocabulary()

    return words(stored), words(added)


def test_the_architecture_names_the_states_the_code_can_answer():
    """`schema_states()` comes out of the DDL and `derived_states()` out of the one
    function that turns a deadline into a state. A migration adding a status, or a
    second word derived from the clock, has to be written down where the domain is
    described — which is the half of this domain the row cannot tell."""
    stored, added = documented_market_states()
    vocabulary = state_vocabulary()
    assert stored == schema_states(), sorted(stored ^ schema_states())
    assert added == vocabulary - schema_states(), sorted(added ^ (vocabulary - schema_states()))
    assert stored | added == vocabulary, sorted(vocabulary - stored - added)


def test_the_state_vocabulary_is_the_schemas_plus_the_deadline():
    """`awaiting` is the state the row does not carry, and the derivation is the
    only place it is invented: if `chain.js` stops naming it, the badge for a
    market past its deadline is a class no state can reach."""
    vocabulary = state_vocabulary()
    assert "open" in schema_states(), "bets.status no longer defaults to open"
    assert {"resolved", "cancelled"} <= schema_states(), sorted(schema_states())
    assert "awaiting" in derived_states() - schema_states(), sorted(derived_states())
    assert vocabulary, "no state anywhere: the badge laws below would measure nothing"


def test_both_stylesheets_paint_every_state_the_derivation_can_answer():
    """Two documents, two badge families, one vocabulary. A state with no class is
    a market that silently loses its badge, and a class no state reaches is a
    colour that explains nothing — the same drift the palette gates refuse."""
    vocabulary = state_vocabulary()
    assert BADGE_FAMILIES, "no badge family: the laws below would measure nothing"
    missing = [
        f"{family} ({stylesheet}) paints {sorted(state_classes(read(stylesheet), family) ^ vocabulary)}"
        for family, stylesheet in BADGE_FAMILIES.items()
        if state_classes(read(stylesheet), family) != vocabulary
    ]
    assert not missing, missing


def test_every_surface_that_derives_state_paints_every_state():
    """Four surfaces name a market's state, each with its own markup. The defect
    this refuses is the one they shipped: three of them derived the answer
    themselves, so the same expired market read «открыт» on the landing and
    «завершён» on its own page. A surface that asks the shared derivation has to
    be able to paint every answer it can get."""
    vocabulary = state_vocabulary()
    surfaces = state_surfaces()
    assert surfaces, "no document derives market state: the laws above measure nothing"
    missing = [
        f"{name}: no badge for {sorted(vocabulary - states_painted(own_code(name)))}"
        for name in surfaces
        if states_painted(own_code(name)) != vocabulary
    ]
    assert not missing, missing


def test_the_mini_app_names_each_state_the_way_the_site_does():
    """A state is a word as well as a colour. The Mini App takes its word from the
    i18n dictionary and the site writes it in the markup, so nothing but this law
    keeps «ждёт решения» from becoming «ожидает» on one surface and staying put on
    the other three."""
    keys = {
        key[3:]: value
        for key, value in re.findall(r"(st_[a-z]+): \"([^\"]+)\"", mini_dict_line("ru"))
    }
    assert set(keys) == state_vocabulary(), sorted(set(keys) ^ state_vocabulary())
    offenders = [
        f"{name}: «{word}» ({state}) never appears in its markup"
        for name in state_surfaces()
        for state, word in keys.items()
        if word not in own_code(name)
    ]
    assert not offenders, offenders


# Both flags negated on one line is the question "has anybody decided this yet",
# which is not the question a card asks. `#` names a line that reads a winner,
# which really is a fact about the flags.
NEGATED_FLAG_PAIR = re.compile(
    r"!\s*\w+\.resolved\s*&&\s*!\s*\w+\.cancelled|!\s*\w+\.cancelled\s*&&\s*!\s*\w+\.resolved")


def test_no_surface_asks_the_contract_flags_whether_a_market_is_tradable():
    """The Mini App asked `!resolved && !cancelled` and offered `/oc_buy` on a
    market whose deadline had passed — a command the bot itself refuses at
    close_at, so the app promised an action that could not happen. The shared
    derivation is the only place a deadline may turn into a state, and a document
    that reads it is not allowed to ask the raw fields as well."""
    offenders = [
        f"{name}: {line.strip()}"
        for name in state_surfaces()
        for line in own_code(name).splitlines()
        if NEGATED_FLAG_PAIR.search(line)
    ]
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# the Mini App's own market surfaces
# ---------------------------------------------------------------------------
# The laws above are keyed to `marketState(`, and the Mini App lists answered it
# by publishing no state at all: `/api/mini/state` gave a card a question, a
# close_at and the pools, and every other field the derivation reads stayed in
# the ledger. So the card could not say what it was, a pool whose deadline had
# passed was painted exactly like one still taking money, and the detail sheet
# offered its buy panel to both. A law that only reads the derivation cannot see
# a surface that never calls it — these three read the renderers themselves.

CARD_MARKUP = re.compile(r'class="market-card')
SHEET_OPEN = re.compile(r"\bshowDetail\(")
BUY_OFFER = re.compile(r'data-act="buy"|ocTradeHint\(|\bbuyPanel\(')
CONTRACT_FLAG_READ = re.compile(r"\.\s*(resolved|cancelled|expired|disputed)\b")


def mini_app_market_painters() -> dict[str, str]:
    """Every Mini App function that puts a market on the screen.

    The set is the markup each function emits, not a list of names: a card row, a
    detail sheet and a third market family that nobody has invented yet all join
    by building one of the two, and a renamed `renderMarkets` leaves immediately."""
    functions = js_functions(APP_HTML)
    found = {name: body for name, body in functions.items()
             if CARD_MARKUP.search(body) or SHEET_OPEN.search(body)}
    assert len(found) >= 5, (
        f"only {len(found)} market painters read out of app.html "
        f"({sorted(found)}): the reader has gone blind, not the app")
    return found


def test_every_mini_app_market_surface_paints_its_state_and_its_clock():
    """A market card that names no state is a card that says «open» forever, and
    a deadline the card never mentions is the one fact that decides whether the
    pool still takes money."""
    offenders = []
    for name, body in mini_app_market_painters().items():
        if "statusTag(" not in body:
            offenders.append(f"{name}: paints a market with no state badge")
        if "deadlineSpan(" not in body and "deadlineBlock(" not in body:
            offenders.append(f"{name}: paints a market with no deadline")
    assert not offenders, offenders


def test_the_mini_app_offers_a_buy_only_for_a_market_that_still_takes_one():
    """`/api/mini/trade` and `/oc_buy` both refuse a pool past `close_at`, so a
    sheet that paints the panel for one is a promise the origin breaks."""
    offenders = [
        f"{name}: offers a buy without asking «tradable()»"
        for name, body in mini_app_market_painters().items()
        if BUY_OFFER.search(body) and "tradable(" not in body
    ]
    assert not offenders, offenders


def test_the_mini_app_asks_the_derivation_what_state_a_market_is_in():
    """The contract flags say what was decided, not whether the market is still
    live: `resolved` and `cancelled` answered from the row are how a pool whose
    deadline passed kept offering a buy. The fields belong to `marketState`, and
    the premise is measured on the derivation so an app.html that simply deleted
    every field read cannot pass by having nothing to check."""
    offenders = [
        f"{name}: {line.strip()}"
        for name, body in js_functions(APP_HTML).items()
        for line in body.splitlines()
        if CONTRACT_FLAG_READ.search(line)
    ]
    assert not offenders, offenders
    derivation = js_function(read("chain.js"), "marketState")
    assert CONTRACT_FLAG_READ.search(derivation or ""), (
        "chain.js no longer reads the flags itself: this law has lost the file "
        "it exists to point at")


# ---------------------------------------------------------------------------
# the deadline line
# ---------------------------------------------------------------------------
# The laws above read `marketState()` and the renderers that call it. The Mini App
# has its own deadline helpers, written when its cards began to paint a state at
# all: they take their words from the inline dictionary and their claim about the
# money from whichever renderer called them, so no law above can see them.

ONCHAIN_MARKERS = ("ERC-1155", "oc_redeem_hint", "ocTradeHint")
DEADLINE_CALL = re.compile(r"(?<![\w$.])deadline(?:Span|Block)\(")
DICTIONARY_CALL = re.compile(r"""^t\(\s*['"]([a-z0-9_]+)['"]\s*\)$""")


def top_level_parts(arguments: str) -> list[str]:
    """A JS argument list split at its commas, outside every quote and bracket.

    A note arrives as `t('key')`, so a splitter blind to nesting would cut inside
    the call today and inside its placeholder object the day one is added, and
    report the note as a market argument."""
    parts: list[str] = []
    depth, quote, current = 0, "", []
    for char in arguments:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    assert depth == 0 and not quote, f"unbalanced argument list: {arguments!r}"
    return parts


def dictionary_value(language: str, key: str) -> str:
    match = re.search(rf"\b{key}: \"([^\"]*)\"", mini_dict_line(language))
    assert match, f"{key} is missing from the {language} dictionary"
    return match.group(1)


def deadline_lines(body: str) -> list[tuple[str, str | None]]:
    """`(arguments, note key)` for every clock one renderer paints."""
    found = []
    for match in DEADLINE_CALL.finditer(body):
        arguments = _call_args(body, match.end() - 1)
        parts = top_level_parts(arguments)
        assert 1 <= len(parts) <= 2, f"a deadline line takes a market and at most one note: {arguments!r}"
        if len(parts) == 1:
            found.append((arguments, None))
            continue
        note = DICTIONARY_CALL.match(parts[1])
        assert note, f"the note a deadline line passes is not a dictionary call: {parts[1]!r}"
        found.append((arguments, note.group(1)))
    return found


def test_a_deadline_line_speaks_only_while_the_clock_runs():
    """A stopped clock is a state, not a time.

    The Mini App painted «Завершение: 14 нояб., 09:00» beside a pool whose deadline
    had passed a week ago and beside one somebody had already resolved — the same
    absolute moment it paints on a live market, labelled as though it were still
    counting down, while every site surface answers that state with the sentence
    about the stakes. The helper now asks `marketState` and admits one state.

    What replaces the clock is a claim about custody, not about the deadline: a
    ledger pool hands its stakes back through the bot, an ERC-1155 position is
    settled by the contract. So the sentence is passed in by the renderer that knows
    which of the two it painted — a card promising a refund out of money the bot
    does not hold is a worse defect than the silence it replaces. A market family
    carrying none of the on-chain markers is read as ledger money and asked for the
    note, which is the loud half of this law.
    """
    derivation = js_function(APP_HTML, "deadlineText")
    assert derivation, "the Mini App no longer derives its deadline line in one place"
    assert '"open"' in derivation, f"the clock no longer asks the state: {derivation}"
    assert "awaiting" not in derivation, f"the clock still speaks for a stopped deadline: {derivation}"

    offenders, painted, noted, unnoted = [], [], 0, 0
    for name, body in sorted(mini_app_market_painters().items()):
        for arguments, key in deadline_lines(body):
            painted.append(name)
            onchain = any(marker in body for marker in ONCHAIN_MARKERS)
            if key is None:
                unnoted += 1
                if not onchain:
                    offenders.append(f"{name}() paints a ledger market with no note ({arguments})")
                continue
            noted += 1
            if onchain:
                offenders.append(f"{name}() promises {key!r} for a market the contract settles")
            for language in ("ru", "en", "zh"):
                if not dictionary_value(language, key).strip():
                    offenders.append(f"{key} is empty in {language}")
    assert not offenders, offenders
    # Floors, so the law cannot go vacuous by the scan losing every call site —
    # measured on this tree as 6 deadline lines: 4 ledger renderers pass the note
    # (two cards, two details) and 2 on-chain surfaces pass none.
    assert len(painted) >= 6, sorted(painted)
    assert noted >= 4 and unnoted >= 2, (noted, unnoted)


def test_the_mini_app_promises_the_refund_the_site_promises():
    """One product, one sentence about stakes that came back.

    `chain.js` publishes `awaiting`'s answer as `REFUND_NOTE` and the site paints
    it; the Mini App says the same thing through its dictionary, because a page
    cannot read a JS constant written in another document's scope. Quoting a
    promise in two files is how the landing ended up offering the money while the
    market page offered the stakes, so the Russian sentence the cards paint has to
    be the sentence the origin already publishes — read through the same reader as
    the law above, so a renderer that quietly switched to its own key is caught by
    this one.
    """
    published = re.search(r'const REFUND_NOTE = "([^"]*)"', read("chain.js"))
    assert published, "chain.js no longer states the awaiting sentence"
    used = {key for body in mini_app_market_painters().values() for _, key in deadline_lines(body) if key}
    assert used == {"awaiting_note"}, used
    flatten = lambda sentence: re.sub(r"[^\w]+", " ", sentence.lower()).strip()  # noqa: E731
    promised = dictionary_value("ru", "awaiting_note")
    assert flatten(promised) == flatten(published.group(1)), (promised, published.group(1))
    for language in ("en", "zh"):
        assert dictionary_value(language, "awaiting_note").strip(), f"{language} says nothing"


def test_the_deadline_reader_refuses_a_stopped_clock_and_a_borrowed_promise():
    """The readers above on argument lists this tree does not currently ship.

    `top_level_parts` decides every custody judgement below it, so it is held to the
    shapes a call site takes — a placeholder object with a comma inside, a note
    written as a sentence rather than a key, three arguments, a call whose brackets
    never close — and to reporting the shapes that are legal."""
    assert top_level_parts("mk") == ["mk"]
    assert top_level_parts("mk, t('awaiting_note')") == ["mk", "t('awaiting_note')"]
    assert top_level_parts("mk, t('note', {amt: 1, usdc: 'x'})") == ["mk", "t('note', {amt: 1, usdc: 'x'})"]
    assert top_level_parts("`${a}, b`") == ["`${a}, b`"]
    with pytest.raises(AssertionError):
        top_level_parts("mk, t('x'")
    with pytest.raises(AssertionError):
        deadline_lines('deadlineSpan(mk, "ставки можно вернуть в боте")')
    with pytest.raises(AssertionError):
        deadline_lines("deadlineSpan(a, b, c)")
    assert deadline_lines("x = deadlineBlock(mk)") == [("mk", None)]
    assert deadline_lines("x = deadlineBlock(mk, t('awaiting_note'))") == [
        ("mk, t('awaiting_note')", "awaiting_note")]


# ---------------------------------------------------------------------------
# framed tile grids, separators, and the sentence a dead deadline leaves
# ---------------------------------------------------------------------------


def _matching_brace(text: str, open_at: int) -> int:
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def top_level_rules(css: str) -> list[tuple[int, str, str]]:
    """Rules outside every at-block, as `(order, selector, body)`.

    `rule_selector_and_body` flattens `@media`, so a mobile override of `.stats`
    comes back with the same standing as its base declaration. A layout law has
    to measure the layout a wide visitor gets, and the whole point of a responsive
    rule is that it answers a different width — so the contents of an at-block are
    skipped here rather than trusted."""
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, i = [], 0
    while i < len(stripped):
        brace = stripped.find("{", i)
        close = _matching_brace(stripped, brace) if brace >= 0 else -1
        if close < 0:
            break
        selector = stripped[i:brace].strip()
        if selector and not selector.startswith("@"):
            rules.append((len(rules), selector, " ".join(stripped[brace + 1 : close].split())))
        i = close + 1
    return rules


def _subject_classes(selector: str) -> set[str]:
    """The classes a rule actually paints: the last compound of each comma part."""
    found: set[str] = set()
    for part in selector.split(","):
        tokens = part.strip().split()
        if tokens:
            found |= set(re.findall(r"\.([A-Za-z][\w-]*)", tokens[-1]))
    return found


def _tracks_of(value: str) -> list[str]:
    """Track sizes split on the spaces that separate them — not the ones inside
    `repeat(3, 1fr)`, which is the form the shipped stylesheet writes."""
    parts, buf, depth = [], [], 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char.isspace() and depth == 0:
            if buf:
                parts.append(re.sub(r"\s+", "", "".join(buf)))
                buf = []
            continue
        buf.append(char)
    if buf:
        parts.append(re.sub(r"\s+", "", "".join(buf)))
    return parts


def _equal_tracks(value: str) -> int | None:
    """N when the declaration is exactly N equal `1fr` columns, else None.

    `minmax()`, `auto-fill` and mixed tracks are content-sized: how many columns
    they lay out is the browser's answer, not the stylesheet's, so a fixed tile
    count is not owed to them and a short last row is not a hole."""
    total = 0
    for part in _tracks_of(value):
        repeated = re.fullmatch(r"repeat\((\d+),1fr\)", part)
        if repeated:
            total += int(repeated.group(1))
        elif part == "1fr":
            total += 1
        else:
            return None
    return total or None


def _element_inner(text: str, start: int) -> tuple[int, int] | None:
    """Offsets of the content of the element whose `<` sits at `start`."""
    opening = TAG_RE.match(text, start)
    if not opening:
        return None
    name, attrs = opening.group(2).lower(), opening.group(3)
    if name in VOID_TAGS or attrs.rstrip().endswith("/"):
        return None
    depth, inner_start = 1, opening.end()
    for tag in TAG_RE.finditer(text, inner_start):
        name, attrs = tag.group(2).lower(), tag.group(3)
        if name in VOID_TAGS:
            continue
        if tag.group(1):
            depth -= 1
            if depth == 0:
                return inner_start, tag.start()
        elif not attrs.rstrip().endswith("/"):
            depth += 1
    return None


def _direct_children(text: str, inner_start: int, inner_end: int) -> list[set[str]]:
    """Class sets of the top-level elements this document **paints** inside the span.

    An element carrying `hidden` is not painted, so it is not a cell: the shipped
    profile table had six markup tiles whose sixth was `hidden` until a signed-in
    visitor arrived, which is five drawn tiles in three columns and a hole the
    source tags did not show."""
    found, depth = [], 0
    for tag in TAG_RE.finditer(text, inner_start, inner_end):
        name, attrs = tag.group(2).lower(), tag.group(3)
        if tag.group(1):
            if name not in VOID_TAGS and not attrs.rstrip().endswith("/"):
                depth -= 1
            continue
        if depth == 0 and not re.search(r"\bhidden\b", attrs):
            found.append(_class_tokens(tag.group(0)))
        if name not in VOID_TAGS and not attrs.rstrip().endswith("/"):
            depth += 1
    return found


def _tile_grids_in(page: str, text: str, css: str) -> list[dict]:
    """Every framed N-column tile grid this document lays out, with its cell count."""
    tracks: dict[str, tuple[int, int]] = {}
    grids: set[str] = set()
    framed: set[str] = set()
    closing: set[str] = set()
    for order, selector, body in top_level_rules(css):
        subject = _subject_classes(selector)
        if not subject:
            continue
        columns = re.search(r"grid-template-columns\s*:\s*([^;]+)", body)
        if columns and (n := _equal_tracks(columns.group(1))):
            for cls in subject:
                tracks[cls] = (order, n)
        if re.search(r"display\s*:\s*(?:inline-)?grid\b", body):
            grids |= subject
        if re.search(r"border-(?:top|left)\s*:\s*[1-9]", body):
            framed |= subject
        if re.search(r"border(?:-(?:right|bottom))?\s*:\s*[1-9]", body):
            closing |= subject
    found = []
    for tag in TAG_RE.finditer(text):
        if tag.group(1):
            continue
        classes = _class_tokens(tag.group(0))
        owned = [tracks[c] for c in classes if c in tracks]
        if not owned:
            continue
        n = max(owned)[1]
        if n < 2 or not (classes & grids) or not (classes & framed):
            continue
        inner = _element_inner(text, tag.start())
        if inner is None:
            continue
        kids = _direct_children(text, inner[0], inner[1])
        # A container the renderer fills is measured where it is painted, by the
        # rendered gate; markup that carries no tiles promises no count.
        if not kids or not all(kid & closing for kid in kids):
            continue
        found.append({"page": page, "classes": sorted(classes), "tracks": n, "cells": len(kids)})
    return found


def framed_tile_grids() -> list[dict]:
    """The same measurement across the directory, each page against its own sheet.

    The subject set is built from three derived conditions, none of them typed: a
    class the stylesheet makes a grid of N equal `1fr` columns, that same rule
    painting the opening hairlines (`border-top`/`border-left`), and every child
    carrying a class that paints a closing hairline. Those two borders are one
    continuous table, so a last row that is short is a hole in the frame — which
    is why a grid with a `gap` between its cards (`.stats-grid`, `.market-card`)
    is not in scope: its short row is a ragged edge, not an empty cell.
    """
    return [
        grid
        for name in PAGES
        for grid in _tile_grids_in(name, read(name), APP_HTML if name == "app.html" else CSS)
    ]


def test_a_framed_tile_grid_paints_no_empty_cell():
    """The profile table was five **painted** tiles in a three-column hairline
    grid — six in markup, the sixth `hidden` until a signed-in visitor arrived —
    so every profile on every wide screen closed its own frame with one empty
    cell whose borders stopped where the missing tile began.

    A cell count that is not a multiple of the column count is only a defect when
    the cells share a border, so the premise of the law and the count it judges
    are both read from the shipped files: the track count from the stylesheet that
    paints the grid, the cells from the markup that fills it — and a markup child
    carrying `hidden` is no cell, because nothing draws it. `user.html` now states
    its figures in `.rows`, a list that declares no columns of equal tracks and so
    promises the reader no count to be a multiple of."""
    measured = framed_tile_grids()
    assert len(measured) >= 3, (
        f"only {len(measured)} framed tile grids were read — the reader has gone blind"
    )
    holes = [
        f"{g['page']} {'.'.join(g['classes'])}: {g['cells']} tiles in {g['tracks']} columns "
        f"leaves {g['tracks'] - g['cells'] % g['tracks']} empty cell(s)"
        for g in measured
        if g["cells"] % g["tracks"]
    ]
    assert not holes, holes


def test_the_tile_grid_reader_owes_a_cell_to_five_tiles_in_three_columns():
    """The law above can only pass by measuring, so the reader is run on markup
    that is not in the tree: the defect itself — six tiles of which the shipped
    one hid — its fix, the media-query trap, and the framed premise that keeps a
    ragged card grid out of scope."""
    framed = (
        ".stats { display: grid; grid-template-columns: repeat(3, 1fr);"
        " border-top: 1px solid #000; border-left: 1px solid #000 }"
        ".stat-card { border-right: 1px solid #000; border-bottom: 1px solid #000 }"
        "@media (max-width: 600px) { .stats { grid-template-columns: 1fr } }"
    )
    tiles = '<div class="stat-card">x</div>'
    hole = _tile_grids_in("synthetic.html", f'<div class="stats">{tiles * 5}</div>', framed)
    assert [(g["tracks"], g["cells"]) for g in hole] == [(3, 5)], hole
    # The shape the shipped tree really carried: six markup tiles, the last one
    # `hidden`. Source tags say a full row, the drawn page says a hole, and a
    # reader that counts tags certifies the hole instead of reporting it.
    six_with_one_hidden = f'<div class="stats">{tiles * 5}<div class="stat-card" hidden>x</div></div>'
    assert [(g["tracks"], g["cells"]) for g in
            _tile_grids_in("synthetic.html", six_with_one_hidden, framed)] == [(3, 5)]
    # A row that closes full is measured too — the law judges the count, the reader
    # does not filter it, so a reader that only reported holes could not be caught.
    full = _tile_grids_in("synthetic.html", f'<div class="stats">{tiles * 6}</div>', framed)
    assert [(g["tracks"], g["cells"]) for g in full] == [(3, 6)], full
    assert not [g for g in full if g["cells"] % g["tracks"]], full
    # The column count is read from the spelling the shipped sheet actually uses:
    # `repeat(3, 1fr)` carries a space inside the parentheses, and a reader that
    # split the value on whitespace answers None for it and measures nothing.
    assert _equal_tracks("repeat(3, 1fr)") == 3
    assert _equal_tracks("1fr 1fr 1fr") == 3
    assert _equal_tracks("repeat(auto-fill, minmax(180px, 1fr))") is None
    assert _equal_tracks("1fr 240px") is None

    # An unframed grid of the same shape is a card list, not a table: no law here.
    cards = (
        ".cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px }"
        ".card { border: 1px solid #000 }"
    )
    assert _tile_grids_in("synthetic.html", f'<div class="cards">{tiles * 5}</div>', cards) == []
    # ...and a framed grid whose children paint no closing hairline is out too.
    assert _tile_grids_in(
        "synthetic.html",
        '<div class="stats"><div>x</div><div>x</div></div>',
        framed,
    ) == []
    # Content-sized columns promise no count, so a short last row is not a hole.
    fluid = (
        ".stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));"
        " border-top: 1px solid #000 }"
        ".stat-card { border-right: 1px solid #000 }"
    )
    assert _tile_grids_in("synthetic.html", f'<div class="stats">{tiles * 5}</div>', fluid) == []


SEPARATOR_BOX_RE = re.compile(r"<(span|div|b|i|em|small|strong)\b([^>]*)>([^<]{1,12})</\1\s*>")
ENTITY_RE = re.compile(r"&(?:[a-zA-Z]+|#\d+);")
LETTER_OR_DIGIT_RE = re.compile(r"[0-9A-Za-z\u0400-\u04ff]")
# One entity paints one glyph, however many source characters it costs to say it.
_GLYPH = "·"


def separator_boxes(text: str) -> list[tuple[int, str]]:
    """Anonymous elements whose entire painted payload is punctuation.

    An element that carries an `id` or a `class` is a named thing — `–` inside
    `<b id="wallet-balance">` is a value waiting for the fetch, and the `.dot`
    beside the Mini App's live status is a designed indicator. What has no name
    and no letters is a separator: layout dressed as content."""
    found = []
    for match in SEPARATOR_BOX_RE.finditer(text):
        attrs, body = match.group(2), match.group(3)
        if re.search(r"\b(?:id|class)\s*=", attrs):
            continue
        payload = ENTITY_RE.sub(_GLYPH, body).strip()
        if not payload or len(payload) > 4 or LETTER_OR_DIGIT_RE.search(payload):
            continue
        found.append((match.start(), match.group(0)))
    return found


def test_no_meta_row_is_joined_with_an_anonymous_punctuation_box():
    """`.page-meta` is `display:flex` with `gap: 14px`, so its items are already
    separated by the layout. Five shipped documents joined them with
    `<span>·</span>` on top of that: a box of one glyph floating in the gap the
    layout already pays for, and a child of the accessibility tree that says
    nothing to anyone.

    The subject set is every document **and** script in the bundle: three of the
    five joiners were built inside JS string literals, and a law that read only
    tags would have certified those three as clean."""
    found = [
        f"{name}: {box!r}"
        for name in (*PAGES, *SCRIPTS)
        for _, box in separator_boxes(read(name))
    ]
    assert not found, f"punctuation is being painted as a layout child: {found}"


def test_the_separator_reader_still_reads_the_joiner_it_exists_to_refuse():
    """After the fix the subject set is empty, which is exactly how a blind reader
    passes forever. Every shape below is the one the finding actually was, and the
    one it was not."""
    joined = '<div class="page-meta"><span>Адрес</span><span>·</span><span>Base</span></div>'
    assert [box for _, box in separator_boxes(joined)] == ["<span>·</span>"]
    assert [box for _, box in separator_boxes("<span>&middot;</span>")] == ["<span>&middot;</span>"]
    assert separator_boxes('<span>|</span>') and separator_boxes('<i>—</i>')
    # A value placeholder is a named element holding a pending figure.
    assert separator_boxes('<b id="wallet-balance">–</b>') == []
    assert separator_boxes('<span class="dot">.</span>') == []
    # Text is text, however short, and so is a glyph with words beside it.
    assert separator_boxes("<span>b</span>") == []
    assert separator_boxes("<span>· Base</span>") == []


def documents_loading(script: str) -> list[str]:
    return [name for name in PAGES if f'src="/{script}"' in read(name)]


REFUND_NOTE_DEF_RE = re.compile(r"\b(?:const|let|var)\s+REFUND_NOTE\s*=")


def test_the_awaiting_note_is_written_once_in_the_file_that_derives_the_state():
    """A pool whose deadline passed and that nobody resolved is the fourth state,
    and the sentence beside it is the answer to «и что теперь?» — so it is the one
    line on the site a visitor may not get two versions of. The landing promised
    «можно вернуть деньги» while /m/{id} promised «ставки можно вернуть» for the
    same money, and nothing in the tree could notice, because two literals in two
    files are not a diff.

    The durable shape is the one every other answer to a derived state already
    uses: the sentence lives in `chain.js`, next to `marketState()` and
    `deadlineLine()` that produce the state it describes. The law then measures
    that shape, not the wording: exactly one definition, and every document that
    prints the sentence — or loads the script that does — loads the derivation
    before it, because a page that reads `REFUND_NOTE` without `chain.js` renders
    `undefined` where a refund should be promised."""
    definers = [n for n in (*PAGES, *SCRIPTS) if REFUND_NOTE_DEF_RE.search(read(n))]
    assert definers == ["chain.js"], f"the awaiting note is written in {definers}"
    assert documents_loading("chain.js"), "chain.js is loaded by no page"

    users = [n for n in (*PAGES, *SCRIPTS) if "REFUND_NOTE" in read(n)]
    assert len(users) >= 3, f"only {users} read the awaiting note — the reader is blind"
    for name in users:
        if name == "chain.js":
            continue
        if name.endswith(".html"):
            assert name in documents_loading("chain.js"), f"{name} prints the note without chain.js"
            continue
        pages = documents_loading(name)
        assert pages, f"{name} reads the note but no page loads it"
        for page in pages:
            text = read(page)
            assert text.index('src="/chain.js"') < text.index(f'src="/{name}"'), (
                f"{page} loads {name} before the derivation that defines the note"
            )


# ---------------------------------------------------------------------------
# A helper a page reaches is a file the page loads.
# ---------------------------------------------------------------------------

# The two halves of this bundle travel on different vehicles: the origin reads the
# markup off the disk at request time, while a `*.js` asset reaches the visitor from
# the hand-published Pages bundle. A symbol added on one side and called from the
# other is therefore a `ReferenceError` on the live host even when the checkout is
# one commit — measured on 2026-11-18, when the published `chain.js` carried no
# `barWidth` while `/app` already called it, and every market detail in the Mini App
# threw on open. The tree cannot re-measure a CDN, but it can refuse the shape on its
# own: a document whose global scope calls a function only another file defines,
# without loading that file, is the same break waiting for the first tap.

FUNC_DEF_RE = re.compile(r"^[ \t]*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.M)
ARROW_DEF_RE = re.compile(
    r"^[ \t]*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
    r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.M)
CALL_RE = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")


def defined_functions(text: str) -> set[str]:
    return set(FUNC_DEF_RE.findall(text)) | set(ARROW_DEF_RE.findall(text))


def bundle_function_names() -> set[str]:
    names: set[str] = set()
    for js in SCRIPTS:
        names |= defined_functions(read(js))
    assert len(names) >= 20, (
        f"only {len(names)} functions read out of {len(SCRIPTS)} bundle scripts — "
        "the definition reader has gone blind"
    )
    return names


def functions_a_page_reaches(scope: str, owned: set[str]) -> set[str]:
    """Bundle-owned names this scope calls, whether or not it defines them."""
    return {n for n in CALL_RE.findall(scope) if n in owned}


def unreached_functions(scope: str, owned: set[str]) -> set[str]:
    """Bundle-owned names this scope calls without defining."""
    return functions_a_page_reaches(scope, owned) - defined_functions(scope)


def test_every_bundle_function_a_page_reaches_is_one_the_page_loads():
    owned = bundle_function_names()
    definers = {js: defined_functions(read(js)) for js in SCRIPTS}
    scopes = {page: page_code(page) for page in PAGES}
    reached = {page: functions_a_page_reaches(scope, owned) for page, scope in scopes.items()}
    offenders = []
    for page, scope in scopes.items():
        for name in sorted(unreached_functions(scope, owned)):
            offenders.append({
                "page": page,
                "calls": name,
                "defined in": sorted(js for js, fns in definers.items() if name in fns),
                "page loads": list(page_scripts(page)),
            })
    assert len(scopes) == len(PAGES), f"measured {len(scopes)} of {len(PAGES)} documents"
    assert sum(1 for page in PAGES if page_scripts(page)) >= 4, (
        "fewer than four documents load a bundle script — the `<script src>` reader is blind"
    )
    # Measured this wave: 208 page/helper pairs over the eight documents — the landing
    # alone reaches 138, the Mini App 22, and every list page 10. Without this floor the
    # law would pass by the call reader going blind, which is the one failure this class
    # of gate always has: a `CALL_RE` that matched nothing would certify the same tree
    # that shipped `barWidth` to a page that never loaded the file defining it.
    assert sum(len(names) for names in reached.values()) >= 150, (
        f"only {sum(len(names) for names in reached.values())} page/helper calls read — "
        "the call reader has gone blind"
    )
    assert not offenders, "a page calls a helper no script in its scope defines:\n" + json.dumps(
        offenders, indent=2)


def test_the_scope_reader_refuses_the_call_shapes_a_page_ships():
    owned = {"barWidth", "marketState", "shortAddr"}

    def calls(text: str) -> set[str]:
        return unreached_functions(text, owned)

    assert calls("style.width = barWidth(pct)") == {"barWidth"}
    assert calls("function barWidth(v) { return v }\nbarWidth(3)") == set()
    assert calls("    const barWidth = (x) =>\n        x * 100;\n    barWidth(3)") == set()
    assert calls("    function barWidth(v) { return v }\n    barWidth(3)") == set()
    assert calls("const barWidth = (v) => v;\nbarWidth(3)") == set()
    assert calls("const shortAddr = v => v;\nshortAddr(a)") == set()
    assert calls("async function marketState(r) { return r }\nmarketState(r)") == set()
    assert calls("chain.barWidth(pct)") == set(), "a property call is not the global helper"
    assert calls("myBarWidth(pct) barWidthX(pct)") == set(), "a longer name is not this name"
    assert calls("<div>nothing here reaches the bundle</div>") == set()
    assert SCRIPT_SRC_RE.findall('<script src="/chain.js"></script><script defer src="/x.js">'
                                 '<script src="/y.js" defer></script>') == ["chain.js", "x.js", "y.js"], (
        "a `<script src>` the reader cannot reach is a helper the law cannot see"
    )
    assert SCRIPT_SRC_RE.findall('<script src="https://cdn.example/z.js"></script>') == [], (
        "a script from another origin is not this bundle's file"
    )


# ---------------------------------------------------------------------------
# A refusal and a crash are two answers, and a page that paints either owes a log.
# ---------------------------------------------------------------------------

# `/m/{id}` wraps its read *and* its paint in one `try`, so a throw anywhere in
# that block fell into the `catch` that says «не найден» — the page blamed the
# origin for its own crash, and wrote the verdict where a visitor reads it.
# Measured on the live host on 2026-11-18, while the published `chain.js` had no
# `barWidth`: all four open pools answered «Пул #N не найден» to a browser, and
# `/api/market/5` answered `200` with the same four pools to the same visitor
# twenty seconds later. Nothing in the console said why.

CATCH_RE = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
TRY_RE = re.compile(r"\btry\s*\{")
# A write into the document: replacing or appending a node's content, or handing
# it markup. A comparison is not one, which is why the assignment ends where a
# second `=` would begin.
DOCUMENT_WRITE_RE = re.compile(
    r"\.\s*(?:innerHTML|outerHTML|textContent|innerText)\s*\+?=(?!=)"
    r"|\.\s*insertAdjacentHTML\s*\(")
MISSING_RECORD_RE = re.compile(r"не найден|нет данных|not found", re.I)
LOG_RE = re.compile(r"console\s*\.\s*(?:error|warn)\s*\(")
GUARD_RE = re.compile(r"\bif\s*\(")


def block_after_brace(text: str, brace: int) -> str:
    """The body of the brace-matched block whose `{` sits at `brace`.

    Braces inside a string literal or a comment are counted, the same way every
    other markup reader in this file counts them: a template literal writing
    `${x}` or a CSS block inside a string stays balanced, and a block that is
    unbalanced outside one is a syntax error the browser refuses before this law
    ever sees it."""
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:i]
    raise AssertionError(f"the block opening at offset {brace} never closes")


def catch_and_try(text: str) -> list[tuple[str, str]]:
    """`[(catch body, body of the try it answers)]` for every block catch in `text`.

    The nearest `try` that opens before the `catch` is its own try — that is what
    decides whether «не найден» is honest: if the try stops at the read, nothing
    that reaches the catch painted anything and the record really was refused."""
    trys = [m.end() - 1 for m in TRY_RE.finditer(text)]
    out: list[tuple[str, str]] = []
    for m in CATCH_RE.finditer(text):
        preceding = [p for p in trys if p < m.start()]
        out.append((block_after_brace(text, m.end() - 1),
                    block_after_brace(text, max(preceding)) if preceding else ""))
    return out


def test_a_page_never_answers_a_crash_with_the_words_for_a_missing_record():
    """An empty state is an answer about the record; a crash is an answer about the page.

    Two clauses, because the live defect needed both: a `catch` that writes into
    the document must log what it caught — an error the console never sees is the
    reason this hid for a wave, behind a card that looked like a working 404 — and
    a `catch` that names a missing record while its own try also paints must
    *guard* that wording, because a try that reached the paint has already been
    answered by the origin."""
    offenders: list[dict] = []
    writers = missing_cards = 0
    pages = [name for name in PAGES if "fetch(" in inline_scripts(read(name))]
    for name in pages:
        for body, try_body in catch_and_try(inline_scripts(read(name))):
            if not DOCUMENT_WRITE_RE.search(body):
                continue
            writers += 1
            if not LOG_RE.search(body):
                offenders.append({"page": name,
                                  "why": "writes into the document and logs nothing",
                                  "catch": " ".join(body.split())[:180]})
            if not MISSING_RECORD_RE.search(body):
                continue
            missing_cards += 1
            if DOCUMENT_WRITE_RE.search(try_body) and not GUARD_RE.search(body):
                offenders.append({"page": name,
                                  "why": "says a record is missing for any throw of a try that also paints",
                                  "catch": " ".join(body.split())[:180]})
    assert len(pages) >= 4, f"only {pages} fetch from their own scripts — the inline reader is blind"
    # Measured this wave: four catches write the document (`/m`, `/oc`, the profile
    # copy control and the agent chat), two of them in the words for a missing
    # record. A reader that quietly lost them would report a clean tree forever.
    assert writers >= 4, f"only {writers} document-writing catches read — the write reader is blind"
    assert missing_cards >= 2, f"only {missing_cards} missing-record catches read — the wording reader is blind"
    assert not offenders, "a page dresses one failure as another:\n" + json.dumps(offenders, indent=2)


def test_the_catch_reader_reads_the_shapes_a_page_takes():
    """Both clauses above are only as good as the pairing this does."""
    def pair(text: str) -> tuple[str, str]:
        found = catch_and_try(text)
        assert len(found) == 1, found
        return found[0]

    refused = ("try { const r = await fetch('/a'); if (!r.ok) throw new Error();"
               " m = await r.json(); } catch (e) { $('b').innerHTML = 'не найден'; console.error(e); }")
    body, try_body = pair(refused)
    assert DOCUMENT_WRITE_RE.search(body) and LOG_RE.search(body)
    assert not DOCUMENT_WRITE_RE.search(try_body), "a try that only reads was read as a paint"

    painted = ("try { const r = await fetch('/a'); m = await r.json();"
               " if (m.text === 'x') $('b').innerHTML = m.text; }"
               " catch (e) { $('b').innerHTML = 'не найден'; }")
    body, try_body = pair(painted)
    assert DOCUMENT_WRITE_RE.search(try_body), "a painted try is the shape the guard clause exists for"
    assert not GUARD_RE.search(body), "an unguarded missing-record wording must be reported"

    assert DOCUMENT_WRITE_RE.search("chat.insertAdjacentHTML('beforeend', '<p>x</p>')")
    assert not DOCUMENT_WRITE_RE.search("if (x.textContent === 'a') { paint(); }"), (
        "a comparison is not a write")
    assert catch_and_try("fetch('/a').catch(() => ({}));") == [], (
        "an arrow `.catch` that hands back an object is not a caught block")

    nested = ("try { paint(); } catch (e) { if (a) { $('b').textContent = 'x'; }"
              " console.error('why', e); }")
    body, try_body = pair(nested)
    assert GUARD_RE.search(body) and DOCUMENT_WRITE_RE.search(body) and LOG_RE.search(body)
    assert try_body.strip() == "paint();", "a nested brace ended the try early"

# ---------------------------------------------------------------------------
# A frame owns the hairlines, and every cell belongs to one
# ---------------------------------------------------------------------------

HAIRLINE_TOKEN = "--border"

SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
MARKUP_TAG_RE = re.compile(r"<(/?)([a-zA-Z][-\w]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*)>")


CLASS_ATTR_PAIR_RE = re.compile(r"""class\s*=\s*(?:"([^"]*)"|'([^']*)')""")
CLASS_TOKEN_SPLIT_RE = re.compile(r"[\s$`{}]+")


def class_attribute_tokens(text: str) -> set[str]:
    """Every class token any `class` attribute in this text paints.

    Tokens are separated by whitespace, so a hyphen joins: `class="lb-row"` is the one class
    `lb-row`, and a reader that matched the cell `row` inside it (a word boundary sits between
    the hyphen and the `r`) reported every leaderboard row of the landing as an unframed
    `.row`. A `${…}` hole is split too, because the template writes the state class after the
    base one without closing the attribute first.
    """
    out: set[str] = set()
    for match in CLASS_ATTR_PAIR_RE.finditer(text):
        out.update(token for token in CLASS_TOKEN_SPLIT_RE.split(
            match.group(1) if match.group(1) is not None else match.group(2)) if token)
    return out


def _paints_class(text: str, cls: str) -> bool:
    """Whether a `class` attribute in this text paints `cls` as one of its tokens."""
    return cls in class_attribute_tokens(text)


def _classes_on(tag_rest: str) -> set[str]:
    match = CLASS_ATTR_PAIR_RE.search(tag_rest)
    if match is None:
        return set()
    return set(CLASS_TOKEN_SPLIT_RE.split(
        match.group(1) if match.group(1) is not None else match.group(2))) - {""}


def _token_painted(value: str) -> str | None:
    """The token a `background` declaration paints, or None when it paints anything else."""
    match = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value.strip())
    return match.group(1) if match else None


def own_declarations_by_class(css: str) -> dict[str, dict[str, str]]:
    """Each class the sheet paints by itself, with the declarations of that rule.

    `.markets .market-card` paints a child of a frame rather than a class, so a descendant
    part contributes nothing here — which is how a card that carries its own border and
    radius on an open page, and loses both inside the frame, stays out of the cell
    inventory. Two rules naming one class merge, the later declaration winning key by key.
    """
    out: dict[str, dict[str, str]] = {}
    for selector, body in rule_selector_and_body(css):
        decls = rule_declarations(body)
        for part in selector.split(","):
            own = re.fullmatch(r"\.([a-zA-Z][-\w]*)", part.strip())
            if own:
                out.setdefault(own.group(1), {}).update(decls)
    return out


def _own_rule(css: str, cls: str) -> dict[str, str]:
    """The declarations of the rule that paints `.cls` itself, not its children."""
    for selector, body in rule_selector_and_body(css):
        if any(part.strip() == f".{cls}" for part in selector.split(",")):
            return rule_declarations(body)
    return {}


def frame_classes(css: str) -> dict[str, str]:
    """Classes the sheet lays out as a frame: a one-pixel gap over the hairline token.

    The divider between two rows is the frame's own background seen through its gap, so
    being a frame is a property of those two declarations and never of a name — a list that
    trades `gap: 1px` for `gap: 8px` separates its cells with air and paints no hairline.
    """
    return {
        cls: HAIRLINE_TOKEN
        for cls, decls in own_declarations_by_class(css).items()
        if decls.get("gap", "").strip() == "1px"
        and _token_painted(decls.get("background", "")) == HAIRLINE_TOKEN
    }


def cell_classes(css: str) -> dict[str, str]:
    """Classes the sheet paints as a cell: padded, on a ground of its own, with no border.

    A rule that draws its own box is a card rather than a row, and a rule lifted out of the
    flow by `position` is a layer no gap can separate. Both are refused by what the rule
    declares, not by a remembered name.
    """
    out: dict[str, str] = {}
    for cls, decls in own_declarations_by_class(css).items():
        ground = _token_painted(decls.get("background", ""))
        if ground is None or ground == HAIRLINE_TOKEN or "padding" not in decls:
            continue
        if "position" in decls or any(key.startswith("border") for key in decls):
            continue
        out[cls] = ground
    return out


def markup_outside_code(text: str) -> str:
    """The document a browser parses, with the code the same file carries taken out.

    A template literal is not markup: the row inside a JS string is painted into a
    container no reader sees from this file, so its containment is judged by the code half.
    """
    return HTML_COMMENT_RE.sub(" ", SCRIPT_BLOCK_RE.sub(" ", STYLE_BLOCK_RE.sub(" ", text)))


def code_only(text: str) -> str:
    """The opposite half of a document: the code it carries, with the markup taken out."""
    return "".join(SCRIPT_BLOCK_RE.findall(text))


def markup_walk(text: str, frames):
    """Yield each element the markup paints: its classes, its id, its enclosing frames, its path.

    Scripts, styles and comments are taken out first — the walk answers what the browser parses
    as document, not what a template literal mentions.
    """
    stack: list[tuple[set[str], str]] = []
    for tag in MARKUP_TAG_RE.finditer(markup_outside_code(text)):
        closing, name, rest = tag.group(1), tag.group(2).lower(), tag.group(3)
        classes = _classes_on(rest)
        if closing:
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][1] == name:
                    del stack[index:]
                    break
            continue
        identifier = ID_ATTRIBUTE_RE.search(rest)
        enclosing = frozenset(cls for seen, _ in stack for cls in (seen & set(frames)))
        path = ">".join(n for _, n in stack) or "top of the document"
        yield (classes,
               (identifier.group(1) or identifier.group(2)) if identifier else None,
               enclosing, path)
        if name not in VOID_TAGS and not rest.rstrip().endswith("/"):
            stack.append((classes, name))


def frames_around_ids(text: str, frames) -> dict[str, frozenset[str]]:
    """Each id the markup paints, with the frame classes on that element or above it."""
    out: dict[str, frozenset[str]] = {}
    for classes, element_id, enclosing, _path in markup_walk(text, frames):
        if element_id:
            out[element_id] = enclosing | frozenset(classes & set(frames))
    return out


def cells_painted_in_markup(text: str, frames, cells) -> list[tuple[str, bool, str]]:
    """Every cell the markup itself paints, with whether a frame encloses it and where."""
    return [(cls, bool(enclosing), path)
            for classes, _id, enclosing, path in markup_walk(text, frames)
            for cls in sorted(classes & set(cells))]


def opens_a_frame(body: str, frames) -> bool:
    return any(_paints_class(body, frame) for frame in frames)


def writes_a_cell(body: str, cells) -> list[str]:
    """The cell classes a piece of code paints, beside any other class and in any order."""
    return sorted(cls for cls in cells if _paints_class(body, cls))


NODE_WRITE_RE = re.compile(
    r"\.(?:innerHTML|outerHTML|textContent|innerText)\s*(?:\+?=)(?!=)\s*|"
    r"\.(?:append|insertAdjacentHTML)\s*\("
)


def paints_into_a_node(body: str) -> bool:
    """Whether this function puts its own markup into the live document.

    Such a body is where the composition ends: what surrounds the rows it writes is whatever
    the page already painted, and no caller can insert a frame into a container this function
    chose for itself. A function that only returns a string has that caller, and is judged by
    the call graph instead.
    """
    return NODE_WRITE_RE.search(body) is not None


ID_HOLDER_RE = re.compile(
    r"""(?P<call>\$\(\s*['"][^'"]+['"]\s*\)"""
    r"""|document\.getElementById\(\s*['"][^'"]+['"]\s*\)"""
    r"""|document\.querySelector\(\s*['"][^'"]+['"]\s*\))"""
    r"""|(?P<name>[A-Za-z_$][\w$]*)"""
)
ID_ATTRIBUTE_RE = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""")
BOUND_NODE_RE = re.compile(
    r"(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<node>\$\(\s*['\"][^'\"]+['\"]\s*\)|document\.getElementById\(\s*['\"][^'\"]+['\"]\s*\)"
    r"|document\.querySelector\(\s*['\"]#[^'\"]+['\"]\s*\))"
)
ID_SELECTOR_RE = re.compile(r"""['"]#?([\w-]+)['"]""")
NODE_WRITE_ASSIGN_RE = re.compile(
    r"(?P<holder>\$\(\s*['\"][^'\"]+['\"]\s*\)|document\.getElementById\(\s*['\"][^'\"]+['\"]\s*\)"
    r"|document\.querySelector\(\s*['\"][^'\"]+['\"]\s*\)|[A-Za-z_$][\w$]*)"
    r"\s*\.(?:innerHTML|outerHTML)\s*="
)


def statement_value(text: str, start: int) -> str:
    """The expression a node write assigns, read to the `;` that ends *its own* statement.

    Cutting at the first semicolon lands inside a `.map(mk => { … })` callback, which is where
    half of these writes build their rows: the shipped `renderMarkets` paints its
    `.market-card` cells two `;` past the `=`. So braces, brackets, parens, strings and
    template literals are balanced here, and a closer at depth zero ends the read too, because
    that is the `}` of the block the statement sits in.
    """
    depth = 0
    quote: str | None = None
    index = start
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return text[start:index]
            depth -= 1
        elif char == ";" and depth == 0:
            return text[start:index]
        index += 1
    return text[start:]


LOCAL_DECL_RE = re.compile(
    r"(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<start>\S)", re.S)


def value_reaches_a_cell(value: str, body: str, name: str,
                         functions: dict[str, str], cells) -> list[str]:
    """The cell classes this expression paints — itself, or one hop away.

    A write rarely spells its rows inline: `topList.innerHTML = topRows ? …` keeps the
    `.top-item` markup in the `const topRows` two lines above, and a writer that composes
    through `rows.map(ocCard)` keeps it in a callee. Both are still the same composition
    decision made in this expression, so both are read here; without the hops the shipped
    top-5 list sat outside the law entirely, and the class it paints could have gone loose
    unnoticed.
    """
    painted = writes_a_cell(value, cells)
    if painted:
        return painted
    for other in sorted(functions):
        if other != name and calls(other).search(value):
            built = writes_a_cell(functions[other], cells)
            if built:
                return built
    for decl in LOCAL_DECL_RE.finditer(body):
        if decl.group("name") == name or not re.search(
                rf"\b{re.escape(decl.group('name'))}\b", value):
            continue
        local = statement_value(body, decl.start("start"))
        built = writes_a_cell(local, cells)
        if built:
            return built
        for other in sorted(functions):
            if other != name and calls(other).search(local):
                called = writes_a_cell(functions[other], cells)
                if called:
                    return called
    return []


def node_writes(body: str, cells, functions: dict[str, str],
                name: str) -> list[tuple[str | None, list[str], str]]:
    """Each node this function writes rows into: its id, the cells, and the value painted.

    Only the writes that paint rows are returned: a body that fills a card with a sentence
    composes nothing this law owns.
    """
    out: list[tuple[str | None, list[str], str]] = []
    for match in NODE_WRITE_ASSIGN_RE.finditer(body):
        value = statement_value(body, match.end())
        painted = value_reaches_a_cell(value, body, name, functions, cells)
        if painted:
            out.append((_holder_id(match.group("holder"), body), painted, value))
    return out


def value_opens_a_frame(value: str, name: str, functions: dict[str, str], frames) -> bool:
    """Whether this write's own expression frames the rows — itself, or through a callee.

    `c.innerHTML = rowFrame(rows)` is framed by a function two tokens away, so the callee
    half is real; what the check refuses to accept is a *caller* framing the result, because
    a function that writes into the document has already decided where the rows go.
    """
    if opens_a_frame(value, frames):
        return True
    return any(other != name and other in functions and calls(other).search(value)
               and reaches_a_frame(other, functions, frames) for other in functions)


def _holder_id(holder: str, body: str) -> str | None:
    """The element id a write's left-hand side names, or None when the code cannot say.

    `$("historyList").innerHTML =` names it outright; `box.innerHTML =` names it two lines
    earlier, through the `var box = $('historyList')` the same function body holds — and the
    landing's history and positions lists are painted the second way, so a reader that only
    resolved the first would report the page's own rows as unowned.
    """
    call = ID_HOLDER_RE.fullmatch(holder.strip())
    if call is None:
        return None
    if call.group("call"):
        return _node_id(call.group("call"))
    binding = next((match for match in BOUND_NODE_RE.finditer(body)
                    if match.group("name") == call.group("name")), None)
    return _node_id(binding.group("node")) if binding else None


def _node_id(selector: str | None) -> str | None:
    if selector is None:
        return None
    match = ID_SELECTOR_RE.search(selector)
    return match.group(1) if match else None


def calls(name: str) -> re.Pattern[str]:
    """A use of the global `name` — a call, or the builder handed to `.map()` as it is.

    `oc.map(ocCard)` frames rows exactly as `ocCard()` builds them, and a reader that
    demanded a parenthesis reported that builder as called by nothing. A property of
    something else (`view.ocCard()`) is not this call, a longer name (`ocCardRow()`) is not
    this name, and the definition is not a use of it.
    """
    return re.compile(rf"(?<![\w$.])(?<!function\s){re.escape(name)}\s*(?=[(,)\]])")


def reaches_a_frame(name: str, functions: dict[str, str], frames, seen: set[str] | None = None) -> bool:
    """Whether this function opens a frame, or calls something that reaches one.

    A sheet is composed at run time — a cell builder hands its markup to a frame builder —
    so containment is a fact about the call graph, not about the text of either function.
    A frame painted by a callee counts, and a cycle stops.
    """
    seen = {name} if seen is None else seen
    body = functions[name]
    if opens_a_frame(body, frames):
        return True
    return any(
        other not in seen
        and calls(other).search(body)
        and reaches_a_frame(other, functions, frames, seen | {other})
        for other in functions
    )


FRAME_SURFACES = [
    pytest.param(CSS, LIGHT, DARK, id="style_css"),
    pytest.param(APP_HTML, MINI_LIGHT, MINI_DARK, id="app_html"),
]

CODE_SURFACES = tuple((name, read(name)) for name in SCRIPTS + PAGES)


@pytest.mark.parametrize("css,light,dark", FRAME_SURFACES)
def test_a_frame_divides_with_the_hairline_and_a_cell_paints_another_ground(css, light, dark):
    """The recipe, read off both stylesheets rather than from one remembered pair.

    Measured this wave: `style.css` lays out two frames (`.markets`, `.rows`) and paints one
    cell (`.row`); the Mini App lays out three (`.action-row`, `.list-group`, `.stats`) and
    paints seven (`.action-btn`, `.lang-item`, `.market-card`, `.stat`, `.top-item`,
    `.tx-item`, `.wd-row`). Being a frame and being a cell are the declarations above, so
    what this law adds is the cross-check: no cell in either theme resolves to the colour
    the frame shows through its gap, and it is judged token by token, not name by name —
    a cell whose own token aliases onto `--border` paints no divider and would answer any
    law that only compared the two names.
    """
    frames, cells = frame_classes(css), cell_classes(css)
    assert len(frames) >= 2 and len(cells) >= 1, (
        f"this sheet lays out {len(frames)} frames and paints {len(cells)} cells — the "
        "recipe the design composes its lists from is no longer readable off its declarations"
    )
    for theme, tokens in (("light", light), ("dark", dark)):
        hairline = resolve(tokens, HAIRLINE_TOKEN)
        for cell, ground in sorted(cells.items()):
            assert resolve(tokens, ground) != hairline, (
                f".{cell} paints {ground} = {resolve(tokens, ground)} in the {theme} theme, "
                "the colour the frame separates its rows with: the divider draws nothing"
            )


def test_a_row_painted_in_markup_is_enclosed_by_its_frame():
    """A cell the document writes itself owes a frame the same document writes.

    The subject set is every page in the bundle, each judged against the stylesheet that
    styles it — the Mini App against its own `<style>`, the site pages against `style.css`.
    Measured this wave: 33 cells painted in markup — nine in the Mini App (three
    `.action-btn`, three `.lang-item`, three `.stat`), twelve on the landing and six each on
    the dead-end page and `/u/{id}` — and none of them loose. The language list is the
    shipped case: three `.lang-item` rows inside `<div class="list-group">`. The hairline
    between them, the inset that makes them read as one list and the radius that closes it
    all belong to that frame, so dropping its class is three stray lines in a box.
    """
    loose: list[tuple[str, str, str]] = []
    painted = 0
    for page in PAGES:
        text = read(page)
        css = text if page == "app.html" else CSS
        frames, cells = frame_classes(css), cell_classes(css)
        for cls, framed, path in cells_painted_in_markup(text, frames, cells):
            painted += 1
            if not framed:
                loose.append((page, cls, path))
    assert painted >= 24, (
        f"{painted} cells are painted in the markup of {len(PAGES)} pages while the shipped "
        "tree paints thirty-three — the walk that counts them stopped seeing the lists it "
        "judges"
    )
    assert not loose, (
        f"these cells are painted with no frame around them: {loose} — the divider, the inset "
        "and the closing radius belong to the frame, so each of these is a stray line"
    )


def test_a_mini_app_row_builder_hands_every_cell_it_paints_to_a_frame():
    """A builder that returns markup is framed by whoever takes it, and that is checkable.

    Measured this wave: seven functions in `app.html` paint a cell class. Four write their
    markup into a node of their own choosing — those are the code-surface law's business,
    because no caller stands between them and the screen. The remaining three hand back a
    string: `availableLine()` and `ocCard()` reach a frame through a callee (`rowFrame()` and
    `ocBlock()`), and `infoRow()` returns a bare `.wd-row` and is framed by every caller,
    which is the shape the withdraw sheet was written in when its four rows went out loose.
    A cell primitive is only correct while nobody takes it without a frame around the result.
    """
    frames, cells = frame_classes(APP_HTML), cell_classes(APP_HTML)
    functions = js_functions(APP_HTML)
    emitters = {name: body for name, body in functions.items()
                if writes_a_cell(body, cells) and not paints_into_a_node(body)}
    assert len(emitters) >= 3, (
        f"{len(emitters)} functions in the app hand back cell markup while the file ships "
        "three — fewer means the reader stopped seeing the sheets it exists to judge"
    )
    for name, body in sorted(emitters.items()):
        if reaches_a_frame(name, functions, frames):
            continue
        painted = writes_a_cell(body, cells)
        callers = sorted(other for other, other_body in functions.items()
                         if other != name and calls(name).search(other_body))
        assert callers, (
            f"{name}() paints {painted} and calls nothing that frames them, and no code calls "
            f"it at all, so those rows reach the screen loose: {body.strip()[:60]}…"
        )
        naked = [other for other in callers if not reaches_a_frame(other, functions, frames)]
        assert not naked, (
            f"{name}() returns a bare {painted}, and {naked} take it without opening a frame "
            "around the result — those cells are drawn without the hairline the frame is the "
            "only thing that paints"
        )


def test_a_row_written_into_a_node_is_framed_by_the_write_or_by_the_page():
    """A function that paints rows into a container owes the frame from one of two places.

    The site and the Mini App compose the same row two different ways, and both are legal:
    `site.js` returns bare `.row` divs into the `<div class="rows" id="historyList">` that
    `index.html` writes, while every Mini App writer opens its own `<div class="list-group">`
    around the rows it hands to a plain `<div id="marketsList">`. So a row write is framed
    when its own value opens a frame — directly or through a callee — or when the element the
    code names carries one, or sits inside one, in every document that carries the code.

    Measured this wave: eight row writes over four code surfaces — four in `app.html`
    (`renderBets`, `renderHistory`, `renderMarkets`, `renderMore`) framed by their own value,
    and four (`agent.js::loadAudit` into `#audit`, `app.js::paintX402Answer` into
    `#x402Answer`, `site.js::renderHistory` into `#historyList`, `site.js::renderPositions`
    into `#positionsList`) framed by the markup of the page that carries the script. Both
    halves of the disjunction are therefore live in the tree, and a mutation to either side
    of either one is caught.

    What the law cannot see is a writer that names no container — `container.innerHTML = rows`
    with `container` handed in as a parameter. Such a body is still judged, but only by its own
    value, which is why the message says so; and the pairing of a target id to a *specific*
    element is per document, so a page that carries the id twice answers with the union.
    """
    judged = 0
    for name, text in CODE_SURFACES:
        css = text if name == "app.html" else CSS
        frames, cells = frame_classes(css), cell_classes(css)
        code = text if name in SCRIPTS else code_only(text)
        functions = js_functions(code)
        documents = ([name] if name in PAGES else
                     [page for page in PAGES
                      if name in {src.rsplit("/", 1)[-1] for src in page_scripts(page)}])
        framed_by_id = {doc: frames_around_ids(read(doc), frames) for doc in documents}
        for fn, body in sorted(functions.items()):
            for element_id, painted, value in node_writes(body, cells, functions, fn):
                judged += 1
                if value_opens_a_frame(value, fn, functions, frames):
                    continue
                if element_id is None:
                    raise AssertionError(
                        f"{name}::{fn}() writes {painted} into a node it does not name, and "
                        "neither its own body nor anything it calls opens a frame, so nothing "
                        "in this tree says those rows are divided"
                    )
                carriers = {doc: sorted(framed_by_id[doc][element_id])
                            for doc in documents if element_id in framed_by_id[doc]}
                assert carriers, (
                    f"{name}::{fn}() writes {painted} into #{element_id}, which no document "
                    f"that carries this code paints — {documents} have no such element"
                )
                naked = sorted(doc for doc, found in carriers.items() if not found)
                assert not naked, (
                    f"{name}::{fn}() writes bare {painted} into #{element_id} on {naked}, "
                    f"which neither carries a frame ({sorted(frames)}) nor sits inside one: "
                    "those rows are drawn without the divider the frame is the only thing "
                    "that paints"
                )
    assert judged >= 6, (
        f"{judged} row writes were judged while the shipped tree has eight — the reader "
        "stopped seeing the assignments that compose the lists it exists to judge"
    )


def test_the_frame_and_cell_readers_read_the_shapes_a_surface_takes():
    """Four readers judge composition, so each is held to the shapes composition takes.

    A reader that quietly returned nothing would certify a tree where every list had lost
    its frame, which is the failure this section exists to refuse.
    """
    assert frame_classes(".a{gap:1px;background:var(--border)}") == {"a": HAIRLINE_TOKEN}
    assert frame_classes(".a{gap: 1px;background: var(--border)}") == {"a": HAIRLINE_TOKEN}
    assert frame_classes(".a{gap:8px;background:var(--border)}") == {}, "air is not a divider"
    assert frame_classes(".a{gap:1px;background:var(--card2)}") == {}, (
        "a gap over a surface paints no hairline")
    assert frame_classes(".a b{gap:1px;background:var(--border)}") == {}, (
        "a descendant selector lays out a frame's children, not the class")
    assert frame_classes(".a,.b{gap:1px;background:var(--border)}") == {
        "a": HAIRLINE_TOKEN, "b": HAIRLINE_TOKEN}, "a grouped selector frames both classes"

    assert cell_classes(".c{padding:12px;background:var(--bg)}") == {"c": "--bg"}
    assert cell_classes(".c{padding:12px;background:var(--bg);border:1px solid var(--border)}") == {}, (
        "a rule that draws its own box is a card, not a cell")
    assert cell_classes(".c{padding:12px;background:var(--bg);position:fixed}") == {}, (
        "a layer out of the flow has no gap to sit in")
    assert cell_classes(".c{background:var(--bg)}") == {}, "a panel with no inset is not a row"
    assert cell_classes(".c{padding:8px;background:var(--border)}") == {}, (
        "a cell painting the hairline erases the divider it lives on")
    assert cell_classes(".c{padding:12px;background:#1a1a1a}") == {}, (
        "a raw colour is no token, so the palette law owns it and this one cannot resolve it")

    merged = own_declarations_by_class(".c{padding:12px;background:var(--bg)}"
                                       "@media (pointer:coarse){.c{border:1px solid var(--border)}}")
    assert merged["c"]["border"].startswith("1px"), (
        "two rules for one class are one box, and the merge is the cascade the browser applies")

    markup = ('<div class="rows"><div class="row">a</div><section><p class="row">b</p></section>'
              '</div><i/><div class="rows-x"><b class="row d">e</b></div>'
              "<div class=\"list-group\"><span class='row'>f</span></div><div class=\"row\">c</div>")
    found = cells_painted_in_markup(markup, {"rows", "list-group"}, {"row"})
    assert [(cls, framed) for cls, framed, _ in found] == [
        ("row", True), ("row", True), ("row", False), ("row", True), ("row", False)], (
        "the walk lost an enclosure, read a self-closed tag as an ancestor, or missed a "
        "second class on a cell")
    assert found[2][2] == "div", "an unframed cell owes the chain of tags it was painted in"
    assert cells_painted_in_markup('<script>`<div class="row">x</div>`</script>', {"rows"}, {"row"}) == [], (
        "a template literal is not markup")
    assert [cls for cls, _f, _ in cells_painted_in_markup(
        '<ul class="rows-x"><li class="row">a</li></ul>', {"rows"}, {"row"})] == ["row"], (
        "a class that only starts like the frame frames nothing, and the cell under it is "
        "reported loose")
    assert code_only('<script>var a = 1;</script><p class="row">x</p>') == "<script>var a = 1;</script>"

    functions = {
        "framedHere": 'return rowFrame(`<div class="wd-row">a</div>`);',
        "rowFrame": 'return rows ? `<div class="list-group">${rows}</div>` : "";',
        "cell": 'return `<div class="wd-row">b</div>`;',
        "caller": "html = rowFrame(cell() + cell());",
        "mapped": "html = rowFrame(list.map(cell));",
        "stray": 'html = `<div class="wd-row">c</div>`;',
        "loose": "return cell();",
        "deep": "return second();",
        "second": "return rowFrame(cell());",
        "receller": "return receller();",
        "dotCall": "return view.rowFrame(cell());",
        "widerName": "return rowFrames();",
        "rowFrames": 'return "<div class=\\"wd-row\\">d</div>";',
    }
    frames = {"list-group"}
    assert reaches_a_frame("framedHere", functions, frames)
    assert reaches_a_frame("rowFrame", functions, frames)
    assert reaches_a_frame("caller", functions, frames)
    assert reaches_a_frame("mapped", functions, frames), "a builder handed to `.map` is called"
    assert reaches_a_frame("deep", functions, frames), "a frame two calls away is still a frame"
    assert not reaches_a_frame("cell", functions, frames)
    assert not reaches_a_frame("stray", functions, frames)
    assert not reaches_a_frame("loose", functions, frames)
    assert not reaches_a_frame("receller", functions, frames), "a cycle must not answer yes"
    assert not reaches_a_frame("dotCall", functions, frames), (
        "`view.rowFrame()` is a property of something else, not this frame builder")
    assert not reaches_a_frame("widerName", functions, frames), (
        "`rowFrames()` is a longer name, not a call of `rowFrame()`")

    assert writes_a_cell('<div class="tx-item active">', {"tx-item"}) == ["tx-item"], (
        "a cell class beside a state class is still this cell")
    assert writes_a_cell('<div class="tx-itemize">', {"tx-item"}) == [], (
        "a longer class name is not this cell")
    assert writes_a_cell("<div class='wd-row'>", {"wd-row"}) == ["wd-row"], (
        "a single-quoted class attribute paints the same cell")

    assert _own_rule(".a b{background:var(--card2)}", "a") == {}, (
        "a descendant selector paints a child, not the class this law asks about")
    assert _own_rule(".a{background:var(--bg)}.a b{background:var(--card2)}", "a") == (
        {"background": "var(--bg)"}), "the rule that paints the class must be the one read"
    assert _own_rule(".c,.a{gap:1px}", "a") == {"gap": "1px"}, "a grouped selector still paints the class"
    assert _own_rule(".a{color:red}", "b") == {}, "a class the sheet has no rule for is reported"

    assert class_attribute_tokens('<div class="lb-row">') == {"lb-row"}, (
        "a class token is split at whitespace, not at a hyphen: `lb-row` is one class, and "
        "reading the cell `row` inside it put every leaderboard row of the landing on trial")
    assert class_attribute_tokens('<div class=\'row active\'>') == {"row", "active"}, (
        "single quotes carry the same two classes")
    hole = class_attribute_tokens('class="chip${v === 1 ? \' active\' : \'\'}"')
    assert "chip" in hole, "a class a template interrupts is still read as itself"
    assert not any(token.startswith("chip$") for token in hole), (
        "the hole is split, not glued onto the base class")
    assert _paints_class('<div class="lb-row">', "row") is False
    assert _paints_class('<div class="row lb">', "row") is True

    assert statement_value('x = 1; y = 2', 4) == "1", "a statement ends at its own semicolon"
    assert statement_value('a.innerHTML = rows.map(function (r) {\n'
                           '  var i = 1;\n'
                           '  return "<b class=\\"row\\">" + i + "</b>";\n'
                           '}).join("");\nnext = 2;', 14) == (
        'rows.map(function (r) {\n  var i = 1;\n'
        '  return "<b class=\\"row\\">" + i + "</b>";\n}).join("")'), (
        "the read crosses the semicolons a `.map` callback is built from, and stops at the one "
        "that ends the assignment — cutting at the first `;` loses every row of the list")
    assert statement_value('if (a) { x = "<b>"; }', 13) == '"<b>"', (
        "a statement inside a block ends at its own semicolon, not at the block's brace")

    writer = ('function renderHistory() {\n'
              '  const c = document.getElementById(\'historyList\');\n'
              '  if (!m.length) { c.innerHTML = `<div class="empty">none</div>`; return; }\n'
              '  c.innerHTML = `<div class="list-group">` + m.map(marketCard).join("");\n'
              '}\n'
              'function marketCard(m) { return `<div class="market-card">${m.q}</div>`; }')
    writer_functions = js_functions(writer)
    assert paints_into_a_node(writer_functions["renderHistory"]) is True
    assert paints_into_a_node(writer_functions["marketCard"]) is False, (
        "a function returning a string paints nothing until someone writes it")
    writes = node_writes(writer_functions["renderHistory"], {"market-card"}, writer_functions,
                         "renderHistory")
    assert [element_id for element_id, _p, _v in writes] == ["historyList"], (
        f"a write through `const c = document.getElementById('historyList')` must still name "
        f"the element, and the empty-state line must not join the subject set: {writes}")
    assert writes[0][1] == ["market-card"], (
        f"the rows this write paints arrive through `marketCard`: {writes[0]}")
    assert value_reaches_a_cell('`<div class="empty">none</div>`', writer, "renderHistory",
                                writer_functions, {"market-card"}) == [], (
        "a line that says there is nothing to show paints no cell")
    assert value_reaches_a_cell(writer, writer, "renderHistory", writer_functions,
                                {"market-card"}) == ["market-card"], (
        "a body whose rows arrive through a callee is still a row writer")
    assert value_reaches_a_cell('<div class="stat">${num(v)}</div>', "", "s",
                                {"num": "return String(v);"}, {"stat"}) == ["stat"]
    assert value_reaches_a_cell('<div class="s">${row()}</div>', "var row = `<b class=\"top-item\">"
                                "</b>`;", "x", {"row": "return topItem();"}, {"top-item"}) == (
        ["top-item"]), "a local one hop away is read, which is how the top-5 list is built"

    framed_page = ('<div class="list-group" id="topList"></div>'
                   '<section class="stats"><div id="statUsers"></div></section>'
                   '<div id="loose"></div>')
    found = frames_around_ids(framed_page, {"list-group", "stats"})
    assert found["topList"] == frozenset({"list-group"}), "a frame paints the element itself"
    assert found["statUsers"] == frozenset({"stats"}), "a frame wraps it from the tag above"
    assert found["loose"] == frozenset(), "an element nothing frames answers with nothing"
