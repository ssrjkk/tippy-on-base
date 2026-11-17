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
  The same quirk bites in the other direction: a `text=True` pipe with no `encoding=` is
  decoded with the **console codepage** while the child writes UTF-8, and the resulting
  `UnicodeDecodeError` dies inside the pipe-reader thread, so the parent gets `None` and
  nobody sees the output the failure was supposed to explain. Every child this repo reads
  as text therefore names its encoding on both sides — `-X utf8` for a Python child,
  `encoding="utf-8", errors="replace"` for the parent.
  `test_every_child_process_this_repo_reads_as_text_names_its_encoding` in `tests/test_admin.py`
  walks every spawn site in `bot|web|agent|deploy|scripts|alembic|tests` (10 measured, count
  asserted), and `deploy/launch.py` parsing cloudflared's URL out of that pipe is why this is not
  only a harness rule: a correctly started tunnel reads as one that never came up.
- Every text file is stored with LF and `.gitattributes` says so. A patch script must therefore
  rewrite with `Path.write_bytes(...encode("utf-8"))`, not `write_text()`: text mode on this host
  turns every `\n` into `\r\n` and the diff is the whole file. Ten files carry a UTF-8 BOM (`web/mini.py`, `web/auth.py`, `web/frame.py`,
  `web/x402.py`, `bot/main.py`, four `bot/handlers/` modules and one test) — read those with
  `utf-8-sig` and put the BOM back, or the gate that parses their AST dies on `U+FEFF`.
  Measured, not remembered: `web/server.py` has never had one.

## Gate

- `python -m ruff check bot web tests deploy agent scripts alembic` — **directories only**. Passing
  `scripts/backup_db.sh` or a `.md` file to Ruff is an error, not a finding. `alembic/` joined the
  list with a live `I001` already sitting in it: a directory no command names is a directory no lint
  has ever read.
- `python -m pytest -q -m "not production"` — 11–16 min (measured 643 s alone on the host, 949 s
  with another project's suite sharing it), 1373 tests (171 more, marked `production`, run
  against the deployed site in `.github/workflows/prod-health-check.yml`). That
  workflow has no skip path: a 503 maintenance card makes every live test fail, and only a
  hand-dispatched `expect_down=true` keeps a run green (tests/test_web.py pins this shape).
  Never start a second pytest session against `tipbot_test` while one runs: `conftest.py`
  recreates that database per session, and the in-flight run reports errors it did not cause.
  Each documented size is instead measured by **the run that is happening** —
  `test_the_documented_suite_size_is_the_size_of_this_run` (ordinary) and
  `test_the_documented_production_run_size_is_the_size_of_this_run` (`production`) compare
  `len(request.session.items)` to README, AGENTS.md and the three WHITEPAPER statements. The older
  `test_web.py` block only proves those five agree with *each other*, which is how they all kept
  saying "66 production" while the run deselected 74. Measuring by a nested `pytest --collect-only`
  is not available here: `conftest.py` recreates `tipbot_test` **at import**, so the child would
  destroy the database of the run that spawned it (verified — a sentinel table written before such a
  subprocess was gone when it finished).
- Run targeted files first (`pytest tests/test_web.py -q --timeout=60`), the whole suite last.
  `--timeout=60 --timeout-method=thread` makes a hung test print its stack instead of freezing.
- `python scripts/check_i18n.py` checks that every key in `bot/i18n.py` exists in ru/en/zh. The Mini
  App keeps a separate dictionary inline in `web/static/app.html` (`I18N.ru/en/zh`) — a new `t('key')`
  has to be added to all three, and a key whose last reference is removed has to be removed too.
  `tests/test_web.py::test_mini_app_dictionaries_have_identical_key_sets` is that rule as a gate.
- `tests/test_db_scripts.py` runs the operator shell scripts with bash — the recovery
  procedure was measured by nothing before it, and measuring found a `pg_dump` that dies
  mid-pipe leaving a valid 20-byte gzip of nothing, a `gzip -t` whose failure branch
  `set -e` never let run, and a row count read from a `pg_dump` option that does not exist.
  The laws: an archive is judged by its **payload** (`pg_archive_is_real`, never a byte
  count), a probe failing must not kill the run (`pg_path_probe` tests `"$d/$tool"` **and**
  `"$d/$tool.exe"` — `command -v psql` is false on a Windows service install), restore
  refuses a dump-less archive **before** the overwrite prompt and before it looks for
  `psql`, and `scripts/pg_env.sh` is the only place the `PostgreSQL/*/bin` glob may be
  written. The release gate is measured the same way: `check`/`warn_check` must replay the
  failing command's own output — a bare `[FAIL] pytest suite passes` with `>/dev/null`
  sends the operator to rediscover by hand what the gate exists to report — and the
  mktemp capture file is proven cleaned up from a **second** process, because the `EXIT`
  trap has not fired by the end of the script body. Every `*.sh` under `scripts/` and
  `deploy/` is parsed with `bash -n` and read for `\r` **by directory glob**, not by a
  hand-written list.
- `tests/test_docs.py::test_every_operator_script_is_named_by_something_a_human_reads`
  requires each `scripts/*.sh` and `deploy/*.sh` to be named by a doc, a CI job, the image
  or a calling script, and
  `test_every_operator_script_reaches_the_server_that_runs_it` requires it in `git ls-files`
  too: `.gitignore` says `scripts/*` with a hand-maintained whitelist of the production
  files, so `scripts/pg_env.sh` — the library all three database scripts `source` as their
  first executable statement — was invisible to git. Under `set -euo pipefail` an ignored
  library is not an inconvenience, it is every backup dying on a server built from the clone.
- Seven more `tests/test_docs.py` laws read the **commands and promises** out of every
  tracked `*.md`, because an operator types what a runbook says during an incident and a
  contributor runs what a contributing guide says before a push. `MARKDOWN` is
  `git ls-files *.md` minus the vendored trees, not a hand-written list, because the list
  used to be `README.md` plus `docs/*.md` and the three policy files at the repository root
  were therefore read by no law at all — and GitHub renders exactly those root files as the
  project's policy. What the scan found once it could see them: `SECURITY.md` sent
  vulnerability reports to `security@ezoffer.ru`, a domain in no other file in the tree (a
  USDC custody bug mailed there is a bug nobody received), `CODE_OF_CONDUCT.md` still carried
  the Contributor Covenant's unfilled contact blank, and the root `CONTRIBUTING.md` told a
  contributor a gate command that is not what CI runs. The laws: every
  `docker compose <verb> <service>` names a service `docker-compose.yml` defines
  (`docs/DEPLOY.md` documented a `backup` service and a `backups_data` volume that never
  existed — there is no backup service in the stack, the crontab line is the automation);
  every `-U`/`-d` on a documented `psql`/`pg_dump`/`pg_isready` equals `POSTGRES_USER` /
  `POSTGRES_DB` from that same file, which are `tippy`/`tippy` — `tipbot` is the **test**
  role, and two recovery commands would have answered `database "tipbot" does not exist`;
  no document addresses a container by name — no bare `stop`, `restart` or `logs` against a
  container — because compose sets no `container_name`, so those names are a function of the
  checkout directory; no tracked policy document still carries a template blank
  (`_TEMPLATE_FILL` matches a square bracket whose words include INSERT, YOUR, TBD, FIXME or
  REPLACE_WITH, and the [FEATURE]/[ERROR] labels in issue templates and command comments are
  real output, so they are not blanks); where a policy exists in both places, the root copy may
  not name a contact the long one never mentions, and for `SECURITY.md`/`CODE_OF_CONDUCT.md` —
  documents whose purpose *is* a channel — the two copies must share one (`_CONTACT` reads
  emails and `t.me`/`x.com` handles); every documented gate command equals what CI actually
  runs (`_ci_gate_commands()` reads the ruff directory list and the pytest selection out of
  `.github/workflows/ci.yml`, so a lint line missing three of the seven directories and a suite
  line that pulls in the 171 `production` tests both fail, while a targeted-file run that still
  states its `-m` is allowed); and no document tells a human to reach this host on a port the
  tree does not listen on (`_ports_the_stack_answers_on()` derives the live set from the
  `postgresql://` authorities and `WEB_PORT`, measured this wave as 5432 and 8000 —
  `docs/CONTRIBUTING.md` promised a published database port of its own and `docs/OPERATIONS.md`
  had Prometheus scrape the app on a port that is not `WEB_PORT`; neither compose file has a
  ports key at all, so the stack answers on the compose network and `app:8000` is what a
  Prometheus attached to it reaches). Commands
  are read from fenced blocks and backtick spans only, and the argument list is cut at the
  first `# | < >` — prose after a closing backtick is not a service name.
- **The monetization prose is the same kind of document law, and §7.1 of the WHITEPAPER is what
  a counterparty reads about where the platform's money comes from.**
  `test_the_whitepaper_cites_the_statements_that_earn_the_platform_money` builds the subject set
  from the AST rather than from the doc: `_tx_log_writers()` walks every runtime `*.py` for an
  `INSERT INTO tx_log` **and** for a `credit(...)` call whose kind argument is a literal — that
  one method writes through the `%s` slot the INSERT can only name `<kind>` (`bot/ledger/_pay.py:9`
  is where the scan would otherwise be blind). Measured this wave: 36 statements write the ledger,
  exactly two of them carry `kind='fee'`, both in `bot/ledger/_withdraw.py`. §7.1's
  `` `file.py:NNN` `` citations must map **bijectively** onto that set — a citation naming a line
  no fee statement covers is a doc pointing at moved code, and a fee statement the doc never cites
  is platform revenue the WHITEPAPER does not list. The gate then reads the two expressions §7.1
  quotes (`creator_income = fee_sum + remainder`,
  `creator_fee = min(win_fee_micro(distributed), leftover)`) back out of `ast.get_source_segment`
  of the real assignments and compares them **whole**, so a doc paraphrasing half a sum survives
  review and fails the build; substring containment was the first version's hole and mutation M5
  is what closed it. `creator_income` may be written by no file outside the two payout engines.
  A unit test of the reader itself (`test_the_positional_reader_reads_the_kind_from_the_column_that_holds_it`)
  is the third gate, because a substring scan reads `INSERT … SELECT 'withdraw' … WHERE kind='fee'`
  as revenue: `_kinds_written_by()` splits the VALUES/SELECT lists on **top-level** commas only,
  takes the cell under the `kind` column, and reports an arity mismatch instead of guessing.
  `test_no_document_states_a_fee_rate_that_is_not_the_rate_that_fee_carries` then reads **every**
  tracked `*.md` — not the four files `tests/test_fees.py::test_help_text_percentages_are_the_configured_rates`
  still names — and attributes each `%` to the mechanism its own sentence says (withdrawal or win)
  within 60 characters, after `_prose()` drops fenced blocks and replaces file spans so a
  `bot/fees.py` mention cannot masquerade as a fee word. 13 claims measured (7 win, 6 withdrawal),
  and a rate with no mechanism beside it, or one sitting between two, is reported rather than
  guessed — which found one real defect: `docs/DEPLOY.md` had put both rates in one
  parenthetical as bare numbers, so an operator holding a `.env` diff could not tell which
  figure was the withdrawal fee and which the win fee; the line now names each fee beside its
  own number. Eight mutations were proven to fire (a paywall row written as `fee`, a
  `credit(…, 'fee')`, a moved citation, a renamed variable in code, a truncated quote in the doc,
  and three wrong rates in three different documents). What the laws cannot see is stated here: a
  rate written under proposal wording («proposed», «levers», «would») is exempt — §7.2's 0.5%
  scenario is a what-if, not a promise — and a document may still describe a fee qualitatively.
- **The pictures a document ships are files in this tree, and `tests/test_docs.py` reads them as
  files.** `scripts/make_docs_screenshots.py` shoots every `docs/screenshots` PNG and writes a
  `tEXt` pair into each: `tippy-surface` (which surface the file pictures) and
  `tippy-fingerprint` (a digest of the payload that picture can show — the palette tokens and the
  heading outline of a landing or the Mini App, the tab-bar `data-i18n` grid of the Mini App, the
  label grid of the two keyboards the bot really builds — plus the `name@WxH@scale` camera
  geometry). Six laws hold it: every PNG on disk is shown by a tracked document **and** carried by
  git, and every image a document shows is on disk (the subject set is the directory glob, so a
  fifth picture joins by being added, not by being named here); the generator's `SURFACE_OF`
  registry, its shot table and the directory are one set; no picture is unstamped or stamped as a
  surface the registry denies; **the fingerprint is recomputed from the current tree**, so the edit
  that changes the design is the edit that fails the build; the pixels are the size the table lays
  out — a resize, crop or pad in an image editor moves no source token and is caught here or
  nowhere; and the four small readers inside the generator are tested on markup that is not the
  file they usually get (a token block whose last declaration has no `;`, a heading wrapping a
  nested tag, a decoy `data-i18n` outside the tab bar, a one-tab bar, a payload where one
  character moves), because a reader that quietly returns nothing would certify a design that no
  longer exists. The laws import the generator and call its functions — no browser, no server.
  What the stamp cannot see: it certifies the source and the geometry of a surface, not that the
  captured pixels look right, and the menu fingerprint is the Russian keyboard, so an English or
  Chinese label change moves no digest.
- **Every number a document prints about this tree is measured by the run that reads it.**
  §4.1, §4.2, §4.9 and Appendix A of the WHITEPAPER are inventories, and until wave 32 nothing
  re-measured them: §4.9 promised twenty-one ledger mixins after the second schema source had left
  eighteen, §4.2 counted 48 routes while `web/auth.py` had been serving five for waves, the line
  count was 4,700 lines short in `tests` and silent about `alembic` and `functions`, and the
  rendered design gate was described as five surfaces while it walks nine facets. Three laws read
  them back out of the tree: `_lines_of_code_by_package()` (every `git ls-files` path carrying a
  code suffix, grouped by top-level directory, so the vendored `contracts/lib/` and a git-ignored
  scratch file never enter), `_routes_by_module()` (the `@app.`/`@router.` decorators counted
  **per module**, so a new route-bearing file has to appear in the section) and
  `_ledger_mixins()`/`_rendered_gate_facets()` with the contract and deploy-script inventory (the
  bases of `Ledger`, `agent/*.py`, the `FACETS` tuple, `contracts/*.sol`, every cited script). A
  name-number pair the tree cannot produce fails, and so does a package the tree has that the
  sentence omits — the pairing is bidirectional in both directions by set equality. The
  WHITEPAPER's opening «Что уже написано» line joined that subject set the wave it was found
  stale: it still promised 48 routes and 21 mixins after §4.2 and §4.9 had been corrected,
  because the only law that read it counted routes over a hand-written triple of files
  (`web/server.py`, `web/mini.py`, `web/frame.py`) and answered 48 while `web/auth.py` had been
  serving five for waves. That law is now retired into `tests/test_docs.py`, where the set is
  `git ls-files web/*.py`, and `tests/test_web.py` keeps only the half that is not an inventory —
  five documents publishing one suite size must agree with each other, and
  `test_the_documented_suite_size_is_the_size_of_this_run` pins that figure to the run.
- **A roadmap promise is a claim about the tree, gated in both directions.** The README `##
  Roadmap` section is parsed from the file — a bullet that wraps is one bullet, `~~struck~~` is
  the markup for *done* — and the resolvers are the ones the rest of the docs use: environment
  names come from the AST of the runtime (310 read this wave), tracked paths from `git ls-files`,
  slash commands from the handlers that register them. A struck bullet must name at least one
  artifact that resolves, because a claim whose every citation is dead can never be re-checked; a
  struck bullet may not be reported as still pending, and a **live** bullet may not cite a file,
  a flag or a command the tree already has — which is how "shipped" creeps back into a promise.
  A URL is stripped before its path can be read as this repository's file, and `EIP-1167`,
  `ERC-1155`, `USDC` and `PROTOCOL` are not config names, so the spec numbers and the acronyms in
  a bullet do not become phantom knobs. Two unit tests hold the readers to those shapes, plus one
  that keeps a wrapped bullet one bullet.
- **A USDC figure written beside a knob is a claim about that knob's default.**
  `docs/OPERATIONS.md` told an operator to review payouts over `$1000` while the ledger has always
  flagged one at `WITHDRAW_LARGE_USDC_THRESHOLD` — 500 — and the same number sat twice more because
  the code read it through `getattr(config, …, 500)`, a second copy of the bound in the module
  that compares against it. Both are gone. The law takes every `NAME, "DEFAULT"` pair the AST
  parses as `int`/`float`/**`Decimal`** out of the runtime (74 knobs measured; `Decimal` is what
  this configuration uses for every money-sized knob, so reading only `int` reproduced the bounds
  and missed the amounts — that widening is what found the defect below), keeps the ones whose own
  name states the unit (12), and holds each USDC figure within ±60 characters of such a name to
  that default. A name defaulted to **two** numbers is reported as a conflict, because then no
  document can quote one. A figure sitting between two knobs with two different defaults is
  reported, not guessed; fenced code and proposal wording («proposed», «levers», «would») state no
  bound; and the count of attributed figures is floored, so the law cannot go vacuous by the prose
  forgetting to state a number. What the scan found the day it could read `Decimal`: a §4.5
  sentence put the paywall floor of `X402_MIN_USDC` in the same breath as the ceiling knob
  `MAX_TIP_USDC`, so nothing said which bound the first figure stated — both numbers were right
  and the sentence still needed a guess, and it is now two clauses, each naming its own knob
  beside its own figure. The law cannot tell a quoted defect from a live claim (it read this very
  paragraph until the figures left the window), which is why the write-up names knobs and not
  numbers. Its blind spot is the rule's own premise: a knob whose name does not say USDC —
  `AGENT_DAILY_CAP`, `MAX_WITHDRAWS_PER_DAY` — is invisible to it, since nothing in the name or the
  sentence tells a reader which unit a bare number means.
- Three AST gates read the runtime tree, because prose review missed all three:
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
  The third of them reads the ledger package itself: the methods whose own SQL writes a `tx_log` row **and**
  whose answer is a `bool` are the ones whose return value *is* the fact "the money moved" — measured this
  wave as `transfer`, `refund_withdraw`, `credit_x402`, `finalize_x402_credit`, `finalize_x402_paywall` —
  and a bare statement call of one in `bot|web|agent|deploy|scripts` fails. Six sites were exactly that:
  five `refund_withdraw()` in `bot/base.py`, where the sweep and the direct batch logged "refunding" for a
  row that was already terminal and had credited nobody, and the `ledger.transfer()` in `bot/main.py` that
  let a recurring payment record itself as executed when the payer could not cover the amount.
- `tests/test_telegram_limits.py` measures Telegram's hard limits: button text ≤64 chars, callback_data
  ≤64 bytes, message ≤4096 chars. Eight gates: every `InlineKeyboardButton(text=...)` is either a
  literal ≤64, an i18n call, a `LANG_NAME` lookup, or wrapped in `common.btn_text()` (an f-string that
  interpolates user data without the helper fails — hand-slicing at each call site is how the 64-char
  limit breaks); every i18n key used as a button label is ≤64 chars in all three languages (a translation
  that grows past the limit makes Telegram refuse the whole keyboard); no `esc()` or `html.escape()`
  inside any button text (buttons are plain text, not HTML); every `callback_data=` value is ≤64 bytes
  (f-strings with integer IDs, short tokens, or bounded amounts pass; unbounded expressions fail);
  `_resolve_buttons()` with adversarial input (30 `&` + 15 `<` + 15 `>`) produces labels ≤64 with no
  escaped substrings; `btn_text()` truncates to 64 chars with ellipsis; `_edit_menu()` logs refusals
  instead of swallowing them silently; docs name no paths outside the repo (no `D:/Dev/Temp/...` laptop
  paths, but server paths like `P:/opt/tipbot` and examples/warnings are allowed). The helper `btn_text()`
  in `bot/handlers/_common.py` is the one way to build a dynamic button label — it truncates with `…` at 64.
- `tests/test_design_system.py` is the design contract, measured from the files. **The files it
  reads are the directory**: `PAGES = glob("*.html")`, `SCRIPTS = glob("*.js")`, and the two lists
  of laws that used to be hand-written tuples are derived from them (`INDEXABLE_PAGES` is the pages
  minus `app.html`, the launch card, and `not_found.html`, the dead end). `legal.html` appeared in
  the directory while the tuple still named seven pages, and every law below would have kept walking
  past it — which is the same hole `cmd-group` fell through. Contrast of every
  declared `color`/`background` pair **and** of every text colour against both canvases — the pair gate
  runs against `style.css` **and** the Mini App's own stylesheet, each on its own boards (canvas plus
  the band/cards it paints on), because it measured only the site until this wave and the same 4.27:1
  selected-chip defect then came back a second time on a surface the gate had never read; "mono implies
  tabular digits" **in both stylesheets**, the site and Mini App palettes equal value for value, and the
  palette laws — no raw hex outside the token blocks, no token nothing reads, every `var()` chain
  terminates, `[data-theme="dark"]` invents no token the light block lacks, the inverse-text fill pairs
  clear AA, the `@media (pointer: coarse)` 44 px floor names only live classes — parametrized over
  **both** stylesheets instead of only the landing's. Reading the Mini App's own stylesheet gave up two
  defects the same day: it declared `--warning` for a state nothing painted (the SmartAccount line now
  carries `.ok`/`.warn` for it), and a parser that ended a declaration only at `;` silently lost the
  **last** token of each block, so `--safe-b` had been read by no gate at all — `declarations()` now
  asserts every `--name:` it can see in a block it also parsed. Inline styles are limited to clamped
  geometry, no unsubstituted
  `__PLACEHOLDER__`, the webfaces served **by this origin** (`/fonts.css` is linked first in every
  document, no `@import`, no font host in any shipped byte and none left allowed by the CSP, every
  declared `.woff2` on disk and every file on disk declared), the weights the CSS asks for inside the
  ranges those faces declare, and **every Cyrillic codepoint the site ships inside the `unicode-range`
  of a face its own stack names** — 57 of them, measured over the shipped `*.html` and `*.js`, because a
  stack that merely *names* a Cyrillic face still paints the operating system's font when the subset that
  face declares does not cover the block, and per-glyph fallback walks the stack silently. No viewport that blocks pinch zoom, `theme-color`
  present for both schemes and equal to the canvas token, and the **favicon read the way a browser
  reads an attribute**: an attribute value ends at the first matching quote, so `x="50"` inside
  `href="data:image/svg+xml,…"` truncated the drawing — `not_found.html` and the terms chrome that
  used to be an f-string in `server.py` both shipped that mark, and the seven pages that spelled the same
  attributes with `'` looked identical in review. The payload has to percent-decode, parse as one `<svg>` with an `xmlns`, carry
  no raw `#` (a fragment drops the rest), be byte-identical across every page, and paint only the
  `--blue` / `--on-accent` pair, `outline: none` refused inside any `:focus`
  rule — a `box-shadow` halo is a tint, not a ring: the site's own field glow is the brand blue at
  16 % alpha, which measures 1.11:1 against the dark canvas and 1.16:1 against the light one — and legal
  on an element type only while the same document paints a `:focus-visible` ring,
  the focus ring ≥ 3:1 on both canvases, every page with exactly one `<h1>` and no
  skipped heading levels, no dead `<symbol>` in the icon sprite, every `skeleton` owned by code that
  replaces it, every painted percentage taking the interface locale (a `${x.toFixed(1)}%` template is a
  failure), and the **edge maintenance card** in `functions/[[path]].js` held to the same document
  contract as the pages — it is served without the origin, so nothing else measures it.
  It reads files only — no browser, no database, so it runs in ordinary CI.
- **The alembic chain is the only writer of this database's schema, and that is measured
  against the code, not remembered.** `Ledger.__init__` runs `alembic upgrade head` and applies
  nothing after it; `bot/ledger/_schema.py` (the idempotent `CREATE TABLE IF NOT EXISTS` string
  `ensure_schema()` used to apply on top of the chain) is deleted, because a second source that
  is applied unconditionally is a second source nobody diffs: the chain lacked `recurring_payments`,
  `x402_invoices`, `login_nonces`, `paymaster_usage`, `gas_drips`, `market_subsidies`,
  `notification_outbox` and the three `creator_*` tables — every one of them named by SQL the
  runtime executes — while the string lacked `idempotency_keys` and `audit_logs`, and migration
  018 converged the two. The gates in `tests/test_ledger.py` build one database by
  `Ledger._run_alembic()` alone and hold it to: **every relation the shipped SQL names exists**
  (the names are read from the first argument of each `execute()` call through the AST, so prose
  like "the UPDATE that failed" cannot answer for a table, and the extractor asserts it found ≥30
  relations so a silent parser cannot make the law vacuous); the columns, indexes and CHECKs the
  deleted string owned are present; `bets.close_at`/`markets.close_at` are NOT NULL (015's whole
  point, which the string undid on every DDL-built database); **one unique fact has one index
  name** (`user_wallets` carried `user_wallets_tg_id_slot_key` *and* `uq_wallets_user_slot`);
  the `bets.status`/`markets.status` domain in the catalog equals the domain in its own
  `CREATE TABLE`; and no shipped `.py` outside `alembic/versions` carries a `CREATE TABLE`.
  The graph itself is measured by `test_the_chain_is_one_line_of_revisions_with_one_head`: every
  file in `alembic/versions` declares a `revision` id, no id appears twice, every `down_revision`
  names a revision the tree has, no revision merges or forks, exactly one head exists and nothing
  is unreachable from it — measured as 18 revisions with head `018`. That law is what the deletion
  made necessary: with no DDL string behind a failed upgrade, `MultipleHeadDetected` is a boot
  failure in production, and no other gate reads the graph.
  `test_the_chain_creates_no_table_the_runtime_never_reads` pins the dead weight it found —
  `treasury_proposals`, `treasury_votes`, `treasury_transactions`, created by 002 for a voting
  feature no code implements and holding zero rows everywhere — so a fifth unread table fails,
  and the trio's eventual `DROP` (a destructive change to live schemas, awaiting the
  maintainer's word) will fail it too until the list shrinks. A migration failure now
  **propagates** (`_run_alembic` retries only lock contention and an unreachable server,
  `_is_transient` walking the exception chain to the innermost Postgres state), so a process
  cannot serve money on a schema it never asked for; `test_a_failing_migration_stops_the_process`
  and `test_schema_contention_is_retried_and_a_stuck_lock_is_loud` measure both halves.
  The harness drops its scratch databases the same forced way: `chain_only_database` used a bare
  `DROP DATABASE`, one contended lock turned that teardown into an unbounded wait, and under
  `--timeout-method=thread` a wait in a teardown does not fail one test — it aborts the session and
  prints only a stack, which is how a full run here reported nothing at all after eleven minutes.
  `test_a_test_database_is_never_dropped_without_force` reads the call arguments of every
  `tests/*.py` — prose about a drop is not one, and an f-string is read whole rather than as its
  fragments — and requires `WITH (FORCE)` on each of the three drops, with the count asserted so a
  blind scan cannot pass; `conftest.py` had already learned this for `tipbot_test`.
- **The dust floor is one number, and the layers that carry it are read from the tree.**
  `config.MARKET_MIN_TRADE_MICRO` in `bot/config.py` is deliberately not an env knob: it is the only
  place the value is written. Both refusal paths compare against it and answer `api_amount_dust`
  naming that bound, `/api/info` and the quote response both publish it as `min_trade_usdc`, and the
  two landing surfaces build their bounds sentence and their amount input's `min` from that response
  instead of typing it. `test_the_dust_floor_is_one_number_read_from_that_name_everywhere` fails a
  comparison against the literal; `tests/test_mini_api.py::test_mini_trade_enforces_the_dust_floor_the_ledger_defines`
  holds the Mini App's `POST /api/mini/trade` to the same boundary; and
  `test_the_floor_a_dust_refusal_names_is_the_floor_the_inputs_ship` derives the route-to-input
  mapping from the directory — the routes are the handlers whose own AST answers `api_amount_dust`,
  the inputs are the ids that every `*.js` and `*.html` under `web/static` types an amount into — so a
  new trade surface joins the law without anyone adding it to a list, and a shipped sentence that
  states a floor the API does not publish is a failure. The rendered half is
  `tests/test_production_design.py::test_the_open_drawer_states_the_bounds_this_origin_publishes`
  (`production`): the painted bounds sentence, the input's own `min` and that origin's `/api/info` are
  read in one pass, because only the rendered text can show the two bounds disagreeing — and an origin
  that publishes no floor makes the drawer paint no bounds sentence, which the gate reports as itself
  instead of passing on an empty panel.
- **A market state is one word, and each of the layers that carries it is measured.**
  `bets.status` and `markets.status` store `open | resolved | cancelled` — read off the
  DDL's own comments (`schema_states()`), not typed here — and the interface answers a
  fourth, `awaiting`: a deadline that passed while nobody resolved the market, the state
  in which stakes sit on the site and can be handed back. `chain.js`'s `marketState()` is
  the only place that word is worked out. The laws: the vocabulary is
  `schema_states() | derived_states()` with `awaiting` belonging to the second; **both**
  badge families paint exactly it — the site's `.chip-*` in `style.css`, the Mini App's
  `.tag-*` in `app.html`, compared per family through `BADGE_FAMILIES` rather than as a
  union, which is how an `app.js` whose `CHIP` had no `awaiting` key passed the union law
  while the page answered «завершён» for it; every document calling `marketState` paints
  every state it can get back from it; the Mini App's `st_*` ru words each appear in every
  surface's own markup; no surface asks `!row.resolved && !row.cancelled` whether a market
  is tradable (the Mini App did, and offered `/oc_buy` on a market the bot refuses at
  `close_at`); and `docs/ARCHITECTURE.md`, the only place the stored/derived split is
  written down, names as stored exactly `schema_states()` and as added exactly the rest.
- `tests/test_production_design.py` measures the **rendered** page (Playwright, marked
  `production`, runs in `prod-health-check.yml`) across **nine facets** — landing dark/light, a
  market page, `/tos`, `/u/{id}`, an on-chain market, the 404 document, `/app` on a phone with
  touch, and `/agent` — one page load each, and the laws are driven by that list rather than by
  which pages someone remembered: contrast, "mono implies tabular", horizontal overflow, a
  skeleton still shimmering, text clipped without a mark, one `<h1>` plus two `theme-color` metas,
  no unsubstituted placeholder in the painted text, and no same-origin 4xx or page error. The
  44 px floor is parametrized over the subset opened under a coarse pointer (`TOUCH_FACETS`),
  because `@media (pointer: coarse)` is what makes it apply and a facet measured with a mouse
  cannot answer a thumb rule. `/no-such-page` is loaded with `expect=404`: the dead end is a real
  surface whose own status is the answer, and without that the walk reported the document it was
  sent to read as a broken request. It taps **all four Mini App tabs** in one load,
  because a screen behind `display: none` is a screen no gate used to see. It also **opens every layer
  that only exists after a tap** — both landing `<dialog>`s (the quote drawer and the ⌘K palette), the
  **three Mini App money sheets** and the **market detail**, each in both themes on a phone: a closed
  dialog is `display: none` and a sheet is an empty `innerHTML`, so nothing used to measure the surfaces
  where a user actually decides something — which is how a 35.3 px search field and an amount input
  whose `:focus` rule deleted its own ring reached a thumb. Every opened layer is then walked by a real
  Tab key: eight presses in the dialogs (`showModal()` already contains the key, so what is judged there
  is the ring each stop paints) and ten in the sheets and the detail, where **every stop must stay inside
  it** — measured before the shell was made `inert`, the stops that stayed inside were 0 in the deposit
  sheet, 2 in the withdraw sheet, 6 in the tip sheet and 8 in
  the market detail, and the rest were the theme toggle, the balance chip and the tab bar behind a scrim
  whose own `aria-modal="true"` said they did not exist. It also **answers the market state as a
  browser does**: `STATE_ENUM_JS` walks every market row a facet really paints and calls that page's own
  `marketState()` in that page's scope over the ten shapes of `STATE_ANSWERS`, and four laws refuse a page
  that reads the answer from somewhere other than `/chain.js`, a state that contradicts its own clock
  (`awaiting` only once `close_at` has passed, and no countdown line on a market that is not `open`), a
  badge whose class is not the class of that row's state, and any vocabulary drift between the DDL, the
  derivation, the four answers and **both** badge families painted on real pages. The aggregate used to
  walk every facet with `request.getfixturevalue`, and a facet that skips inside its fixture — a local
  origin with no on-chain market to render — skipped the law too, so a green board could mean nothing was
  measured; it now steps over those facets and asserts the set it collected is not empty.
  It asserts painted contrast,
  horizontal overflow at 390/1440, 44 px touch targets (inline links sharing a text line are the
  measured exception), bar width against the percentage the same row prints and against the API, the
  solvency ring's painted arc against the percentage it prints and against `/api/solvency`, page
  errors and same-origin 4xx (401/403 excluded — an anonymous probe is meant to be refused), no
  skeleton still shimmering after load, no text clipped without an ellipsis, one `<h1>` and two
  `theme-color` metas in the DOM, a ring on every element a real Tab key stops on — counted only when
  an `outline` of ≥2 px in a non-transparent colour paints it, because the field glow read mid-transition
  is `rgba(0, 0, 0, 0) 0px 0px 0px 0px`, and nothing hidden
  when `prefers-reduced-motion` is set. Point it at a local origin with
  `DESIGN_GATE_URL=http://127.0.0.1:8000`, and add `DESIGN_GATE_SESSION=<signed tippy_session>`
  (`web.auth.make_session(user_id)`) to measure the Mini App's real lists instead of its empty states —
  without it the bar-vs-number gate skips with that reason printed. The three crawler gates
  (`robots.txt`, `sitemap.xml`, the landing canonical) skip on a non-https `DESIGN_GATE_URL`: those
  documents are written from `MINI_APP_URL`/`WEBHOOK_URL`, so a loopback origin cannot agree with them
  for any change in the tree, and CI measures them against the host they describe.
- `web/static/style.css` must have no class used in HTML without being defined and no defined class
  unused by any page. **Which files are read is the directory's answer** — every `*.html` except
  `app.html` plus every `*.js` — because the hand-written list left the new legal shell outside the
  gate for a wave. A word sitting between two string literals in a JS template is code, not markup:
  `agent.js` builds `'…class="bar-fill' + cls + '"…'`, and the parser that read that as a class named
  `cls` reported a defined-class failure for a variable. What the gate therefore cannot see is a class
  that arrives only through a variable (`bar-fill-over`, inside `cls`) — stated here rather than hidden
  behind an exception list. `app.html` is measured against **its own** `<style>` block, never against
  `style.css` — the two documents are served separately, and the Mini App's own classes
  (`sw-state`, its `.ok`/`.warn` state colours) were invisible to the site's stylesheet while the
  gate called that parity.
- `web/static` is the site's source **and** the Pages build output, so anything added there is a
  URL. The origin refuses its own plumbing: `/_*` (`_headers`, `_redirects`) is a JSON 404 from
  the middleware because Pages reads those files itself and no browser is at that address, and
  every `*.html` / `*.md` source is refused **in the mount** (`BundleFiles.get_response`), not
  in the middleware: only there does the answer pass back through the layer that stamps the
  nonce, the CSP and `nosniff`. `TEMPLATE_PAGES` and `MARKDOWN_PAGES` 301 a source to the page
  that renders it (`/index.html` -> `/`, `/tos.md` -> `/tos`, `/legal.html` -> `/tos`); `/user.html`
  is a shell whose id comes from a path it does not have, so anything not in the maps is the dead-end
  page. Both maps are compared against the directory by the plumbing gate. Share images live at the
  bundle root: `/miniapp-hero.png`, never `/static/miniapp-hero.png`.
- A browser navigation never gets a JSON body. `not_found.html` is rendered with `noindex`, **no
  canonical and no share card** (`canonical=False` — a URL that 404s must not name itself as a
  page), for any 404 outside `/api/`; a `RequestValidationError` on a page path is the same dead
  end instead of a validation tree echoing what was typed, and under `/api/` both stay JSON
  because the reader is a bundle or an integrator. The rule is the path, not `Accept`: behind the
  Pages function this origin sees the function's request, whose headers say nothing about the
  browser.
- The configuration surface is measured, not remembered: `tests/test_env_contract.py` requires
  every `os.environ` name the code reads to appear in `.env.example`, `deploy/prod.env.example`
  or a doc (harness-only names are listed with their reason), forbids a template offering a knob
  nothing reads (`WETH_ADDRESS=` was one: `bot/config.py` keeps it a per-chain constant), rejects
  an **empty** value for a knob parsed by `int()`/`float()` without an `or` fallback (`PORT=` is
  one — the default only applies when the name is absent, not when it is blank), and demands
  every `*_KEY`/`*_TOKEN`/`*_PASSWORD` line be empty or an unmistakable placeholder.
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
  `bot/tx_view.py` — never from the amount, and never re-derived per page. A row is never deleted
  and its money columns are written once; the only `UPDATE tx_log` in the codebase sets a
  withdrawal's `status` and `tx_hash` (`bot/ledger/_withdraw.py`), which is why
  `docs/ARCHITECTURE.md` says "written once" rather than "immutable" — and
  `test_the_audit_trail_keeps_the_properties_the_architecture_documents` reads the SQL to keep the
  two from drifting.
- A recurring payment **claims its slot before it debits**. `recurring_mark_executed()` is a
  compare-and-set on `next_execution` that answers whether the claim was won, `bot/recurring.py`
  passes that verdict up, and `bot/main.py::recurring_pass()` skips a payment whose claim it lost.
  Debiting first and recording after left the slot due whenever anything failed in between, and the
  next pass paid the same period a second time — money the product cannot hand back. A refused debit
  spends the slot too: the payer's next interval is still theirs to claim, and retrying a period that
  already passed is the other way to pay twice. `tests/test_breakthrough_features.py` measures both
  halves (`test_a_recurring_pass_debits_only_for_a_slot_it_claimed`,
  `test_a_refused_recurring_transfer_is_not_counted_as_executed`).
- Custody has one wording. The hot wallet is custodial by design (`docs/SECURITY.md`), so
  "non-custodial" must not appear while `smart_wallet._owner()` derives from `HOT_WALLET_KEY`, and
  "trustless" must not appear while `bot/handlers/onchain.py` falls back to `owner_resolve` with the
  same key when `ORACLE_PRIVATE_KEY` is unset. Both are gated in `tests/test_docs.py` over every
  tracked `*.md` — which includes this file, so the gate reads a quoted word as the thing being
  banned (`_uncited()` strips `“…”`, `"…"` and backtick spans) rather than keeping a list of files
  it is allowed to skip; what it cannot see is a false claim written inside quotes, stated here
  instead of hidden in an exception list. Both
  gates flip: if a user key ever really does sign, the docs are required to say which model applies.
- The solvency number is `total_liabilities()` (`bot/ledger/_withdraw.py`) — SIX categories: user
  balances, open market escrow, open bet pools, community treasuries, unswept x402 credit, unclaimed
  pending deposits. `/api/solvency` prints `pending_deposits_usdc` as one of them, not as an extra;
  the landing labels the total «Все обязательства бота». Never call it «balances» — the label is a
  claim about the formula. Three gates hold that sentence open: the landing must name all six
  categories the SQL sums (`LIABILITY_WORDS` in `tests/test_web.py`), the docstring's count must
  equal the number of terms, and the figure is published under exactly one key — `owed_usdc` was
  removed because a second name for one number is two numbers waiting to disagree. The landing no
  longer states «обязательство не может превысить то, что лежит на адресе»: nothing enforces it, and
  the page's own chip reads «недостаточно» whenever reserves fall short.
- "Volume" has one definition: `VOLUME_KINDS` in `bot/ledger/_views.py` — tip, deposit, bet,
  `market_create` (the creator's subsidy is a stake like any pool stake), `market_buy`, `token_buy`,
  `paywall`, `channel_pay`, x402. Receiver mirrors (`tip_in`, `rain_in`, `token_sale`, `paywall_earn`,
  `channel_earn`) and payouts (`bet_win`, `market_win`, `market_sell`, `*_cancel`, `withdraw_refund`,
  `dividend`, `creator_income`, `fee`) stay out, because they are the same movement booked once or
  money that already counted. `/api/stats`, `/api/volume_history`, `/metrics` and the landing legend
  all read this tuple; `test_landing_volume_hint_names_every_volume_kind` fails when the sentence and
  the tuple diverge.
- A refusal is the only text a user gets instead of a receipt, so it is localized:
  `web/refusals.py`'s `refuse(request, 400, 'api_key')` answers from `bot/i18n.py` in the language
  `_user()` cached on `request.state` from `user_settings.lang`. The only bare `HTTPException`
  allowed in `web/mini.py` / `web/auth.py` is the 401 session probe — before identity there is no
  language to inherit, and both bundles answer 401/403 with their own line. A signature failure
  says *that* it failed, never *which* comparison broke; the reason goes to the log. A route the
  **browser navigates to** (`/api/auth/telegram`) answers no body at all — it 303s to
  `/?auth=<code>` and `site.js` renders its own line, because a JSON error there used to replace
  the whole site. `LoginRejected` carries the reason to the log; the code is a fixed enum the
  landing has a sentence for. The rate limiters answer `JSONResponse` from the middleware,
  outside any route, so they build their `detail` from the same table — a literal `detail`
  string there is a refusal nobody can read (the gate walks the middleware's AST).
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
- A translucent brand tint is a **fill, not a colour**: `background: var(--blue-soft)` under
  `color: var(--accent-text)` measures 4.65:1 on the `#111111` canvas and **4.27:1** on the `#1a1a1a`
  card/band — it passes on one board and fails on the other, and the label is 10-13 px, nowhere near a
  large-text pass. So a selected chip, tag or option keeps the **neutral text token** and lets the border
  plus the fill say "selected" — `.amount-chip.active` and `.chip-open` on the site, `.chip.active` and
  `.tag-open` in the Mini App all read `--text`/`--ink`.
- A layer that covers the screen owns the keyboard. `syncLayerStack()` in `web/static/app.html` is the
  only writer of `inert`: `#app` goes inert while a sheet or the market detail is open, and `#detailView`
  goes inert under a sheet. `inert` is what makes the sheet's `aria-modal="true"` and the detail's
  full-screen paint true — it removes the covered controls from the tab order **and** from the
  accessibility tree, which a painted scrim does not. Two consequences: a layer's opener takes focus
  through the same `focusablesIn()` list the Tab trap uses, and `syncLayerStack()` runs **before**
  restoring focus on close, because `focus()` on an element that is still inert is silently refused.
- `/tos` fills the shell `web/static/legal.html` through `_static_page(..., replacements={...})`:
  `__LEGAL_LANG__` from the script the Markdown is actually written in, `__LEGAL_H1__` from its first
  `#` (the title is the `<h1>`, so the renderer consumes that heading instead of emitting a second
  one), `__LEGAL_BODY__` from `_legal_html()` and `__CHAIN_NAME__` from `chain_label()`. That renderer
  handles only the subset the file uses (ATX headings, `- ` bullets, `**bold**`, `` `code` ``); new
  Markdown constructs there will show up as raw markers. Being a file is the point: while the chrome
  was an f-string in `server.py`, no gate read the document a user reads before depositing — its
  `theme-color`, heading outline and classes were measured by nothing and it linked no webfont, so the
  terms set themselves in Helvetica while every other page used the contract's face.
