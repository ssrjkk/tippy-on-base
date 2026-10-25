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

## Gate

- `python -m ruff check bot web tests deploy agent scripts` — **directories only**. Passing
  `scripts/backup_db.sh` or a `.md` file to Ruff is an error, not a finding.
- `python -m pytest -q -m "not production"` — ~8 min, 1062 tests. The four `production` tests hit
  the deployed site and belong to `.github/workflows/prod-health-check.yml`, not to this gate. That
  workflow has no skip path: a 503 maintenance card makes all four fail, and only a hand-dispatched
  `expect_down=true` keeps a run green (tests/test_web.py pins this shape).
- Run targeted files first (`pytest tests/test_web.py -q --timeout=60`), the whole suite last.
  `--timeout=60 --timeout-method=thread` makes a hung test print its stack instead of freezing.
- `python scripts/check_i18n.py` checks that every key in `bot/i18n.py` exists in ru/en/zh. The Mini
  App keeps a separate dictionary inline in `web/static/app.html` (`I18N.ru/en/zh`) — a new `t('key')`
  has to be added to all three, and a key whose last reference is removed has to be removed too.
- `web/static/style.css` must have no class used in HTML without being defined and no defined class
  unused by any page.
- Never push without an explicit word from the maintainer. Commit locally, report, wait.

## Ledger facts

- `self._lock` is `threading.RLock()` (`bot/ledger/_core.py`) — **reentrant**, so a public method may
  call another public method inside the same block. It serializes one process only: uvicorn, the bot
  and the agent are separate processes with separate connections, and cross-process safety comes from
  `SELECT … FOR UPDATE` and guarded `INSERT … WHERE` statements, not from the lock.
- `tx_log.amount` is always positive. Sign, label and counterparty come from `kind` through
  `bot/tx_view.py` — never from the amount, and never re-derived per page.
- Money-moving `POST /api/mini/*` requires an `Idempotency-Key` header (see `docs/SECURITY.md`);
  withdraw is the exception and uses the staged single-use token.
- No new module, mixin or migration lands unless it is called from the live runtime path in the same
  change; verify with a repository-wide grep that excludes its own definition and its tests.

## Interface

- `https://www.base.org` is the only design reference, and it was measured, not assumed: brand
  `#0000ff` (light) / `#224bff` (dark), canvases `#ffffff` / `#111111`. `#0052FF` appears **zero**
  times on base.org — do not reintroduce it, and do not reintroduce Inter or JetBrains Mono, glows,
  or gradient "wallet hero" cards. Tokens live in `web/static/style.css` `:root` / `[data-theme="dark"]`.
- Money, addresses, odds and percentages use `--font-mono` with `font-variant-numeric: tabular-nums`.
- Show/hide is `hidden` (see `[hidden] { display: none !important }`), never `el.style.display`.
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
