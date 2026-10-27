"""Design-system gates: tokens, contrast, structure, visualization geometry.

No browser and no database — the design contract in `web/static/*` is measured
numerically. Every rule below is a ratio, a set difference or a count, so a change
that breaks the system cannot pass by looking roughly like what it replaced.

Three deliberate choices:

* **Contrast is measured twice.** Once for the exact `color`/`background` pair a
  rule declares, and once for every colour used as `color:` against both page
  canvases. The first finds a broken button; the second finds a muted label that
  was legal on white and unreadable after it moved onto the grey band.
* **The site and the Mini App must agree on colours by value.** `app.html` carries
  its own palette with its own names (`--text3` where the site says `--muted`), so
  a mapped comparison is the only way drift shows up before a user sees two
  different greys in one product.
* **A dead token, an unsubstituted placeholder and a substitution no template
  asks for all fail.** They are the same drift in three costumes.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"

PAGES = ("index.html", "app.html", "market.html", "oc.html", "user.html", "agent.html")
SCRIPTS = ("app.js", "site.js", "agent.js", "theme.js", "chain.js")

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


DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")


def declarations(css: str, span: tuple[int, int]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in DECL_RE.findall(css[slice(*span)]):
        assert name not in out, f"{name} is declared twice in one block"
        out[name] = value.strip()
    return out


LIGHT_SPAN = block_body(CSS, ':root, [data-theme="light"]')
DARK_SPAN = block_body(CSS, '[data-theme="dark"]')
LIGHT = declarations(CSS, LIGHT_SPAN)
DARK = {**LIGHT, **declarations(CSS, DARK_SPAN)}  # the cascade the browser applies

MINI_LIGHT_SPAN = block_body(APP_HTML, ":root,[data-theme=\"light\"]")
MINI_DARK_SPAN = block_body(APP_HTML, "[data-theme=\"dark\"]")
MINI_LIGHT = declarations(APP_HTML, MINI_LIGHT_SPAN)
MINI_DARK = {**MINI_LIGHT, **declarations(APP_HTML, MINI_DARK_SPAN)}


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


def test_dark_theme_overrides_no_token_the_light_theme_lacks():
    extra = sorted(set(declarations(CSS, DARK_SPAN)) - set(LIGHT))
    assert not extra, f"[data-theme=dark] invents tokens: {extra}"


def test_token_chains_terminate_in_both_themes():
    """An unresolved var() invalidates the whole declaration, so the element falls
    back to the UA default and a colour or a font disappears with no error."""
    for name in LIGHT:
        for theme, tokens in (("light", LIGHT), ("dark", DARK)):
            value = resolve(tokens, name)
            assert "var(" not in value, f"{name} in {theme} resolves to {value}"


PRINT_LINE = CSS[: CSS.index("@media print")].count("\n")


def test_no_raw_colour_outside_the_tokens():
    """Every colour the product paints comes from a token, so the theme flip is
    one job and a brand change is one line. `@media print` is exempt on purpose:
    paper is not a theme, and routing paper white through a token would make the
    dark theme print dark grey."""
    offenders = []
    offset = 0
    for number, line in enumerate(CSS.splitlines(), start=1):
        stripped = line.strip()
        inside_tokens = (
            LIGHT_SPAN[0] < offset < LIGHT_SPAN[1] or DARK_SPAN[0] < offset < DARK_SPAN[1]
        )
        offset += len(line) + 1
        if inside_tokens or stripped.startswith(("*", "/*")) or "--chevron" in line:
            continue
        if number > PRINT_LINE:
            continue
        if re.search(r"#[0-9a-f]{3,8}\b", line, re.I):
            offenders.append((number, stripped[:90]))
    assert not offenders, offenders


@pytest.mark.parametrize("literal", BANNED_HEX)
def test_banned_hex_is_absent(literal):
    hits = [name for name in (*PAGES, *SCRIPTS, "style.css") if literal in read(name).lower()]
    assert not hits, f"{literal} is back in {hits}: that blue does not exist on base.org"


def test_banned_font_families_are_absent_from_every_font_stack():
    """In `font-family` position and in the token that feeds it: the stacks live
    in `--font-ui` / `--font-mono`, so a banned face added there would never
    appear in a `font-family:` declaration of its own. Only these positions —
    `Inter` is a substring of `counter`, `interface` and `important`, and a
    naive grep would fail the build over prose."""
    offenders = []
    for name in (*PAGES, *SCRIPTS, "style.css"):
        text = read(name)
        stacks = re.findall(r"font-family:\s*([^;{}]+)", text)
        stacks += re.findall(r"^\s*--font-[a-z]+\s*:\s*([^;{}]+)", text, re.M)
        for stack in stacks:
            families = {f.strip().strip("\"'").lower() for f in stack.split(",")}
            bad = families & set(BANNED_FONTS)
            if bad:
                offenders.append((name, sorted(bad), stack.strip()[:70]))
    assert not offenders, offenders


def test_no_token_is_defined_and_never_read():
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)\)", CSS))
    for name in (*PAGES, *SCRIPTS):
        referenced |= set(re.findall(r"var\((--[a-z0-9-]+)\)", read(name)))
    dead = sorted(set(LIGHT) - referenced)
    assert not dead, f"defined in style.css but never read anywhere: {dead}"


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


def _pairs_in_rules() -> list[tuple[str, str, str, str]]:
    out = []
    for selector, body in rule_selector_and_body(CSS):
        color = re.search(r"(?:^|;)\s*color:\s*([^;]+)", body)
        background = re.search(r"(?:^|;)\s*background(?:-color)?:\s*([^;]+)", body)
        size = re.search(r"font-size:\s*([\d.]+)px", body)
        if color and background:
            out.append((selector, color.group(1).strip(), background.group(1).strip(), size.group(1) if size else ""))
    return out


def test_declared_text_on_background_pairs_clear_aa():
    failures = []
    for selector, color, background, size in _pairs_in_rules():
        floor = AA_LARGE if (size and float(size) >= LARGE_TEXT_PX) else AA_TEXT
        for theme, tokens in (("light", LIGHT), ("dark", DARK)):
            fg = as_color(color, tokens)
            bg = as_color(background, tokens)
            if fg is None or bg is None:
                continue  # gradients and currentColor are governed by their own rules
            if bg[3] < 1.0:
                bg = composite(bg, as_color(tokens["--canvas"], tokens))
            measured = ratio(composite(fg, bg), bg)
            if measured < floor:
                failures.append(
                    f"{theme} {selector}: {color} on {background} = {measured:.2f}:1 (floor {floor})"
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


def test_text_on_a_filled_surface_is_a_token_and_clears_aa():
    """Buttons and tabs are the two places where the page colour inverts. Those
    text colours are tokens, so the fill/text pairs are checked here instead of
    being decided per rule — and both themes are checked, because the dark theme
    paints its accent lighter, not darker."""
    assert "--on-accent" in LIGHT and "--on-ink" in LIGHT, "no inverse-text tokens"
    for theme, tokens in (("light", LIGHT), ("dark", DARK)):
        accent = as_color("var(--on-accent)", tokens)
        for fill in ("--blue", "--blue-hover"):
            measured = ratio(accent, as_color(f"var({fill})", tokens))
            assert measured >= AA_TEXT, f"{theme}: --on-accent on {fill} = {measured:.2f}:1"
        ink = as_color("var(--on-ink)", tokens)
        measured = ratio(ink, as_color("var(--ink)", tokens))
        assert measured >= AA_TEXT, f"{theme}: --on-ink on --ink = {measured:.2f}:1"


# ---------------------------------------------------------------------------
# typography
# ---------------------------------------------------------------------------


def test_mono_implies_tabular_digits():
    """One law instead of a list of exceptions: anything drawn in the mono face
    carries fixed-width digits. A rule that forgets it looks right until a column
    gets a 1 and a 7 in the same place, and the `font:` shorthand resets the
    longhand — so a block that uses the shorthand has to name the variant after it.
    """
    offenders = []
    for selector, body in rule_selector_and_body(CSS):
        if "var(--font-mono)" not in body:
            continue
        if "tabular-nums" not in body:
            offenders.append(f"{selector.strip().splitlines()[-1][:70]} — mono without tabular")
    resets = []
    for selector, body in rule_selector_and_body(CSS):
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
    assert not offenders, offenders
    assert not resets, f"the font: shorthand wipes the numeric variant after it: {resets}"


def test_no_font_family_bypasses_the_tokens():
    """`inherit` is legitimate — a form control has to take the stack of the page
    it sits in. A literal family name is not: it is a font nothing else uses."""
    offenders = []
    for name in (*PAGES, *SCRIPTS, "style.css"):
        text = read(name)
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


def test_touch_floor_exists_and_names_live_classes():
    """The 44 px floor is a `@media (pointer: coarse)` block that grows min-height
    only. A selector inside it that exists nowhere else means a control quietly
    lost its thumb coverage when it was renamed."""
    body = CSS[slice(*block_body(CSS, "@media (pointer: coarse)"))]
    selectors = set(re.findall(r"([.#][\w-]+)", body))
    assert selectors, "the coarse-pointer block is empty"
    assert re.search(r"min-height:\s*44px", body), "the touch floor is no longer min-height: 44px"
    rest = CSS.replace(body, "")
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


def test_chart_scaling_divides_by_a_non_zero_max():
    """`volume / max` over an empty or all-zero series is Infinity% — the chart
    vanishes instead of drawing a flat line. The guard lives where max is born."""
    for name in (*PAGES, *SCRIPTS):
        text = read(name)
        if "/ max" not in text:
            continue
        assert re.search(r"max\s*=\s*Math\.max\([^;]*,\s*1\)", text), (
            f"{name} scales a chart by a max that is not floored at 1"
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


# ---------------------------------------------------------------------------
# placeholders
# ---------------------------------------------------------------------------


def server_substitutions() -> set[str]:
    source = (ROOT / "web" / "server.py").read_text(encoding="utf-8-sig")
    return set(re.findall(r"['\"](__[A-Z][A-Z0-9_]*__)['\"]", source))


def test_placeholders_used_in_the_html_are_substituted_by_the_server():
    used: set[str] = set()
    for name in (*PAGES, "style.css"):
        used |= set(PLACEHOLDER_RE.findall(read(name)))
    unmanaged = sorted(used - server_substitutions())
    assert not unmanaged, f"{unmanaged} reach the visitor as literal text"


def test_no_substitution_key_is_dead():
    """The other half of the same contract: a marker the server replaces that no
    template contains is code maintaining a placeholder that no longer exists."""
    used: set[str] = set()
    for name in (*PAGES, "style.css", *SCRIPTS):
        used |= set(PLACEHOLDER_RE.findall(read(name)))
    unused = sorted(server_substitutions() - used)
    assert not unused, f"web/server.py replaces {unused} and no template asks for it"


def mono_class_tokens() -> list[str]:
    """Class names whose own element the stylesheet draws in the mono face.

    The rendered gate takes this list instead of a hand-written one: the law it
    checks is "whatever the CSS calls monospaced computes as monospaced, with
    tabular digits", and a hand-written list would drift the moment a component
    is renamed. Only the *subject* of a selector counts — `.x .label` styles
    `.label`, while `.label::after` paints a generated marker and not the
    element, so taking every class in a selector would flag text no rule touches.
    """
    tokens: set[str] = set()
    for selector, body in rule_selector_and_body(CSS):
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
