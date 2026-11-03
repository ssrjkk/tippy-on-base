# Agent Guidelines

## Verified environment (Windows host, Git Bash)

- Python 3.12 from `AppData\Local\Programs\Python\Python312`; `python -m pytest`, `python -m ruff`.
- Postgres 16 binaries are **not** on the default PATH: `/c/Program Files/PostgreSQL/16/bin/psql.exe`.
  Pass the connection string **last** (`psql -X -A -f query.sql "postgresql://…"`), psql stops parsing
  options after the first positional argument.
- Dev database is `tippy` (role `tippy`), the test database is `tipbot_test` (role `tipbot`).
  `tests/conftest.py` drops and recreates `tipbot_test` once per session, so dev data is never touched.
- `.env` points at Base **Sepolia**; `tests/` asserts chain id **8453** via `EXPECTED_CHAIN_ID`.
  Never assert a config literal in a test — read it from config.
- stdout of redirected Python on this console is cp1251: printing Cyrillic or `–` raises
  `UnicodeEncodeError`. Write JSON/text reports as files opened with `encoding="utf-8"`.
- Every text file is stored with LF and `.gitattributes` says so. A patch script must therefore
  rewrite with `Path.write_bytes(...encode("utf-8"))`, not `write_text()`: text mode on this host
  turns every `\n` into `\r\n` and the diff is the whole file. `web/server.py` and `web/frame.py`
  also carry a UTF-8 BOM — read them with `utf-8-sig` and put the BOM back, or the gate that parses
  their AST dies on `U+FEFF`.

## Gate

- `python -m ruff check bot web tests deploy agent scripts` — **directories only**. Passing
  `scripts/backup_db.sh` or a `.md` file to Ruff is an error, not a finding.
- `python -m pytest -q -m "not production"` — ~9 min, 1212 tests (57 more, marked `production`, run
  against the deployed site in `.github/workflows/prod-health-check.yml`). That
  workflow has no skip path: a 503 maintenance card makes every live test fail, and only a
  hand-dispatched `expect_down=true` keeps a run green (tests/test_web.py pins this shape).
  Never start a second pytest session against `tipbot_test` while one runs: `conftest.py`
  recreates that database per session, and the in-flight run reports errors it did not cause.
- Run targeted files first (`pytest tests/test_web.py -q --timeout=60`), the whole suite last.
  `--timeout=60 --timeout-method=thread` makes a hung test print its stack instead of freezing.
- `python scripts/check_i18n.py` checks that every key in `bot/i18n.py` exists in ru/en/zh. The Mini
  App keeps a separate dictionary inline in `web/static/app.html` (`I18N.ru/en/zh`) — a new `t('key')`
  has to be added to all three, and a key whose last reference is removed has to be removed too.
  `tests/test_web.py::test_mini_app_dictionaries_have_identical_key_sets` is that rule as a gate.
- Two AST gates read the runtime tree, because prose review missed both:
  `test_every_ledger_call_site_resolves_on_the_real_class` (every `ledger.<method>()` call in
  `bot|web|agent|deploy|scripts` has to exist on `Ledger` — `bot/credit.py` called five methods
  that were never written, so `/credit` answered every user with `AttributeError`) and
  `test_no_pre_formatted_money_is_handed_a_numeric_placeholder` (a `{amount:.2f}` string given
  `_fmt()`'s text raises `ValueError: Unknown format code 'f'`; `/subscribe` and `/claim` looked
  broken forever because the broad `except` turned the successful debit into a format-hint card).
  `test_every_i18n_call_passes_the_placeholders_its_string_needs` covers the third shape: a missing
  kwarg is a `KeyError`, not a blank. A fourth reads every `i18n.t` argument that carries user text
  (`name`, `to_username`, `question`, `label`, …) and requires the escaper — the bot answers with
  `parse_mode=HTML`, so raw text either makes Telegram refuse a message whose money already moved or
  injects markup; the deadline notifiers in `bot/main.py` did that. Percentage fields have ONE
  rounder: `onchain_market.price_pct` (Decimal ROUND_HALF_UP) — the market list rounded with the
  builtin (banker's, over a float) while the market page used HALF_UP, so `0.00105` printed 0.1 in one
  place and 0.11 in the other (`test_onchain_list_and_market_page_print_the_same_price`).
- `tests/test_design_system.py` is the design contract, measured from the files: contrast of every
  declared `color`/`background` pair **and** of every text colour against both canvases, "mono implies
  tabular digits" **in both stylesheets**, no raw hex outside the token blocks, the site and Mini App
  palettes equal value for value, inline styles limited to clamped geometry, no unsubstituted
  `__PLACEHOLDER__`, no `@import` of a webfont (link it in `<head>` and preconnect), the weights the CSS
  asks for are the weights the font URL downloads, no viewport that blocks pinch zoom, `theme-color`
  present for both schemes and equal to the canvas token, `outline: none` legal only where a ring is
  painted in its place, the focus ring ≥ 3:1 on both canvases, every page with exactly one `<h1>` and no
  skipped heading levels, no dead `<symbol>` in the icon sprite, every `skeleton` owned by code that
  replaces it, every painted percentage taking the interface locale (a `${x.toFixed(1)}%` template is a
  failure), and the **edge maintenance card** in `functions/[[path]].js` held to the same document
  contract as the pages — it is served without the origin, so nothing else measures it.
  It reads files only — no browser, no database, so it runs in ordinary CI.
- `tests/test_production_design.py` measures the **rendered** page (Playwright, marked
  `production`, runs in `prod-health-check.yml`) across five surfaces — landing dark/light, a market
  page, `/app` on a phone with touch, and `/agent` — and taps **all four Mini App tabs** in one load,
  because a screen behind `display: none` is a screen no gate used to see. It asserts painted contrast,
  horizontal overflow at 390/1440, 44 px touch targets (inline links sharing a text line are the
  measured exception), bar width against the percentage the same row prints and against the API, page
  errors and same-origin 4xx (401/403 excluded — an anonymous probe is meant to be refused), no
  skeleton still shimmering after load, no text clipped without an ellipsis, one `<h1>` and two
  `theme-color` metas in the DOM, a ring on every element a real Tab key stops on, and nothing hidden
  when `prefers-reduced-motion` is set. Point it at a local origin with
  `DESIGN_GATE_URL=http://127.0.0.1:8000`, and add `DESIGN_GATE_SESSION=<signed tippy_session>`
  (`web.auth.make_session(user_id)`) to measure the Mini App's real lists instead of its empty states —
  without it the bar-vs-number gate skips with that reason printed.
- `web/static/style.css` must have no class used in HTML without being defined and no defined class
  unused by any page.
- `web/static` is the site's source **and** the Pages build output, so anything added there is a
  URL. The origin refuses its own plumbing — a path starting `/_` (`_headers`, `_redirects`) or
  ending `.md` (`tos.md` is the source of the rendered `/tos`, not a second copy of it) — and
  `test_the_bundle_directory_does_not_publish_its_own_plumbing` keeps the real assets reachable
  while it does. Share images live at the bundle root: `/miniapp-hero.png`, never
  `/static/miniapp-hero.png`.
- `tests/conftest.py` assigns `ADMIN_TG_ID` and `OWNER_ID` with `os.environ[...] =`, **not**
  `setdefault`: `bot/config.py` `load_dotenv`s the dev `.env` at import and `load_dotenv` never
  overrides, so a maintainer's real `OWNER_ID` folds `ADMIN_TG_ID` onto itself and every
  owner-gated endpoint refuses the session the tests mint. Six gates were green in CI and red on the
  laptop until the pin was made unconditional. It pins `MINI_APP_URL`/`WEBHOOK_URL` the same way for
  the same reason: canonicals, share cards and the sitemap are built from the public origin.
- `tests/test_web.py`'s SEO block measures the served bytes: `/robots.txt` is `text/plain` rules and
  `/sitemap.xml` announces only URLs that then answer 200; every indexable page has exactly one
  canonical, and `og:title`/`twitter:title` are the `<title>` of that same document; a market or
  on-chain market the ledger does not have is a 404 with `noindex`, while `/u/{id}` deliberately stays
  a 200 because `/api/user/{tg_id}` promises no enumeration oracle. `web/server.py` is the only
  writer of `og:`/`twitter:`/`canonical` — templates other than `app.html` (the Base App launch card,
  not a page of the site) declaring them is a failure — and absolute URLs come from
  `public_base_url()`, never from `X-Forwarded-Host`, which a visitor brings with them.
- Never push without an explicit word from the maintainer. Commit locally, report, wait.

## Ledger facts

- `self._lock` is `threading.RLock()` (`bot/ledger/_core.py`) — **reentrant**, so a public method may
  call another public method inside the same block. It serializes one process only: uvicorn, the bot
  and the agent are separate processes with separate connections, and cross-process safety comes from
  `SELECT … FOR UPDATE` and guarded `INSERT … WHERE` statements, not from the lock.
- `tx_log.amount` is always positive. Sign, label and counterparty come from `kind` through
  `bot/tx_view.py` — never from the amount, and never re-derived per page.
- The solvency number is `total_liabilities()` (`bot/ledger/_withdraw.py`) — SIX categories: user
  balances, open market escrow, open bet pools, community treasuries, unswept x402 credit, unclaimed
  pending deposits. `/api/solvency` prints `pending_deposits_usdc` as one of them, not as an extra;
  the landing labels the total «Все обязательства бота». Never call it «balances» — the label is a
  claim about the formula.
- "Volume" has one definition: `VOLUME_KINDS` in `bot/ledger/_views.py` — tip, deposit, bet,
  `market_create` (the creator's subsidy is a stake like any pool stake), `market_buy`, `token_buy`,
  `paywall`, `channel_pay`, x402. Receiver mirrors (`tip_in`, `rain_in`, `token_sale`, `paywall_earn`,
  `channel_earn`) and payouts (`bet_win`, `market_win`, `market_sell`, `*_cancel`, `withdraw_refund`,
  `dividend`, `creator_income`, `fee`) stay out, because they are the same movement booked once or
  money that already counted. `/api/stats`, `/api/volume_history`, `/metrics` and the landing legend
  all read this tuple; `test_landing_volume_hint_names_every_volume_kind` fails when the sentence and
  the tuple diverge.
- Money-moving `POST /api/mini/*` requires an `Idempotency-Key` header (see `docs/SECURITY.md`);
  withdraw is the exception and uses the staged single-use token.
- The AI daily answer budget and provider health are DB rows (`ai_usage_day`, `ai_provider` in
  `bot/ledger/_ai.py`), never module dicts: uvicorn, the bot and the agent spend **one** limit. A key
  is read from `os.environ` by name (`AI_PROVIDERS=name|url|KEY_ENV|model`) — `ai_provider` stores the
  variable's name, not the secret. The chain also fills itself: keys already in the environment
  (`bot/ai.py:DISCOVERABLE_KEYS`), any model server answering `GET /v1/models` on loopback (re-probed on
  a TTL, so starting Ollama needs no restart), and one keyless public fallback. `AI_FREE_ENDPOINT=0` is
  the switch for the only member that sends question text off this host. `/ask` has no "AI disabled"
  state: when no model answers it serves ledger data (`bot/ai_data.py`) and says so.
- `/admin` is the owner's panel (`OWNER_ID`, falling back to `ADMIN_TG_ID`): every screen is a batch
  of SELECTs, it rolls the shared connection back afterwards, and it refuses to render outside a
  private chat. `tests/test_admin.py` fails if the panel grows a ledger call outside its read-only
  allowlist.
- No new module, mixin or migration lands unless it is called from the live runtime path in the same
  change; verify with a repository-wide grep that excludes its own definition and its tests.
- The bot lends nothing: there is no loan, collateral or repayment path anywhere in the ledger. The
  `CreditScorer` / `/credit` feature (300-850 score, "Макс. займ: $100-1000") was deleted on
  2026-11-02 — it never returned a number, and the limit it printed promised money the product
  cannot move. Do not re-add a score, a limit or a "credit" menu entry without a lending ledger
  operation behind it.

## Interface

- `https://www.base.org` is the only design reference, and it was measured, not assumed: brand
  `#0000ff` (light) / `#224bff` (dark), canvases `#ffffff` / `#111111`. `#0052FF` appears **zero**
  times on base.org — do not reintroduce it, and do not reintroduce Inter or JetBrains Mono, glows,
  or gradient "wallet hero" cards. Tokens live in `web/static/style.css` `:root` / `[data-theme="dark"]`.
- Money, addresses, odds and percentages use `--font-mono` with `font-variant-numeric: tabular-nums`.
- Show/hide is `hidden` (see `[hidden] { display: none !important }`), never `el.style.display`.
  …and an element that ships `hidden` is a promise someone clears: `#oc-scan` had its href filled
  and no line ever removed the attribute, so the contract link was invisible in exactly the case it
  exists for. `test_every_element_that_ships_hidden_can_appear` reads every page's own scripts (the
  direct `$("id").hidden = false`, a variable bound to the id, `removeAttribute`, and the generic
  `querySelectorAll('.panel')` loop `switchTab` uses) and fails on a control that can only stay
  invisible. Same family: `test_no_anchor_leads_nowhere` (no `href="#"`, and `/#x` must name an id
  the landing really has — `/#onchain` did not, the section is `#onchain-markets`) and
  `test_every_api_call_the_site_makes_is_a_route_the_server_serves`.
- CSP is strict: `script-src 'self' 'nonce-…'`. `_nonce_inject()` stamps a fresh nonce onto every
  inline `<script>`/`<style>` in any `text/html` response, so inline code works; `<script src=…>` is
  left alone and must come from a whitelisted origin. `img-src` allows `data:` URIs (the CSS chevrons
  and the inline favicon depend on that).
- Telegram Mini App is the primary mobile surface: touch targets ≥ 44 px, no horizontal overflow at
  360 px, dark theme is the default. The 44 px floor is enforced by the `@media (pointer: coarse)`
  blocks at the end of `web/static/style.css` and inside `web/static/app.html`; they grow `min-height`
  only, so the narrow-screen rules above them keep their visual density. `.m-link` inside a sentence
  is the deliberate exception (WCAG 2.5.5 exempts targets sized by the surrounding line height).
- Anything clickable is a `<button>` with `data-act`, never a `<div>`/`<span>` carrying
  `role="button"`: the delegated `click` handler never fires from Enter on a div, and the element is
  not focusable without `tabindex` either. Full-width card buttons therefore carry
  `display:block;width:100%;text-align:left` (`.market-card`, `.detail-opt`) — a button shrinks to
  its content and centres text by default. The only div with `data-act` is `#modalOverlay`, which is
  a dismissal scrim, not a control.
- `/tos` renders `web/static/tos.md` through `_legal_html()` into the site chrome. That renderer
  handles only the subset the file uses (ATX headings, `- ` bullets, `**bold**`, `` `code` ``); new
  Markdown constructs there will show up as raw markers.
