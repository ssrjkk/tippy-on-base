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
"""

import re
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
    "--danger-bg": "--danger-bg",
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


def js_function(text: str, name: str) -> str | None:
    """The body of a named JS function, braces matched. None if it is not there.

    Needed because the design contract is about specific screens — a count over a
    whole file lets one component borrow another component's empty state."""
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text)
    if not match:
        return None
    depth = 0
    for i in range(match.end() - 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[match.end():i]
    return None


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
                elif prop in ("width", "height") and "Math.max" not in value:
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
            if ("width:${" in attr or "height:${" in attr) and "Math.max" not in attr:
                offenders.append(f"{name}: {attr[:80]} — unclamped bar")
            if "animation-delay:${" in attr and "Math.min" not in attr:
                offenders.append(f"{name}: {attr[:80]} — uncapped stagger")
    assert not offenders, offenders


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
MAINTENANCE_RE = re.compile(r"const MAINTENANCE = `(.*?)`;\n\nfunction maintenance", re.S)


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

def page_scripts(name: str) -> tuple[str, ...]:
    """The scripts a document loads from this bundle, read off the document.

    This was a hand-written map of page → scripts. A page added to `PAGES` and
    missing from that map raised `KeyError` in the middle of an unrelated gate,
    and a page whose script list changed silently kept being measured against the
    old one — so the pair is measured from the `<script src>` tags instead."""
    return tuple(re.findall(r'<script src="/([\w.-]+\.js)"', read(name)))


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
# market state: the schema, the shared derivation, and both badge families
# ---------------------------------------------------------------------------

SCHEMA = (ROOT / "bot" / "ledger" / "_schema.py").read_text(encoding="utf-8-sig")

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


def schema_states() -> set[str]:
    """Every status a market row can hold, read off its own CREATE TABLE."""
    found: set[str] = set()
    for default, listed in re.findall(
            r"status\s+TEXT NOT NULL DEFAULT '(\w+)',?\s*--\s*([\w |]+)", SCHEMA):
        found.add(default)
        found |= {word.strip() for word in listed.split("|") if word.strip()}
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
