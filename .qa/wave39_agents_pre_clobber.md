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
  turns every `\n` into `\r\n` and the diff is the whole file.
  Twelve tracked files carry a UTF-8 BOM (`bot/handlers/ai.py`, `bot/handlers/markets.py`,
  `bot/handlers/paywall.py`, `bot/handlers/stats.py`, `bot/main.py`, `tests/test_outcome_market_evm.py`,
  `tests/test_web.py`, `web/auth.py`, `web/frame.py`, `web/mini.py`, `web/static/style.css`,
  `web/x402.py`) — read those with `utf-8-sig` and put the BOM back, or the gate that parses their AST
  dies on `U+FEFF`: measured, every one of the eleven `.py` files in that list raises
  `SyntaxError: invalid non-printable character U+FEFF` when read as plain `utf-8` and parses after the
  sig, which is `test_a_bom_bearing_python_source_parses_only_after_the_sig`. The BOM survives where it
  is read by a consumer that forgives one — CPython compiles a BOM'd module, a browser renders a BOM'd
  stylesheet — and appears in no file an AST gate parses from a literal path (`bot/config.py`,
  `bot/ledger/*.py`, `web/server.py`).
  `test_the_documented_bom_inventory_is_the_bom_inventory_of_the_tree` re-measures that parenthesised
  list against `git ls-files` and the first three bytes of each file, so a BOM arriving in a thirteenth
  file, one leaving, and a count no longer equal to the names all fail the build instead of ageing this
  sentence; the reader is only as good as the shape it reads, which is why the law asks for exactly one
  such claim in the tracked markdown and fails on zero as loudly as on two — a reworded sentence that
  names no count is the same stale document wearing a green check. What neither law sees is a *reader*
  that reaches one of these twelve with plain `utf-8` through a variable or a glob: only literal
  filenames are resolvable statically, so every reader helper in `tests/` names `utf-8-sig` and a new
  one is expected to copy that, not this paragraph.
  Measured, not remembered: `web/server.py` has never had one.

## Gate

- `python -m ruff check bot web tests deploy agent scripts alembic` — **directories only**. Passing
  `scripts/backup_db.sh` or a `.md` file to Ruff is an error, not a finding. `alembic/` joined the
  list with a live `I001` already sitting in it: a directory no command names is a directory no lint
  has ever read.
- `python -m pytest -q -m "not production"` — 11–20 min (measured 643 s alone on the host, 949 s
  with another project's suite sharing it, 1,012 s alone on the run this wave pinned its counts to,
  and 1,204 s with four read-only audit agents and the live origin sharing the box — which is why
  the CI job carries 30 minutes rather than a 20-minute limit it now overshoots),
  1544 tests (199 more, marked `production`, run
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
  line that pulls in the 199 `production` tests both fail, while a targeted-file run that still
  states its `-m` is allowed); and no document tells a human to reach this host on a port the
  tree does not listen on (`_ports_the_stack_answers_on()` derives the live set from the
  `postgresql://` authorities, `WEB_PORT`, every loopback port a tracked `deploy/` program binds
  **and** the connector replica block a tracked `deploy/` script derives from its own
  `$MetricsPortBase` and `$ConnectorReplicas` — cloudflared's metrics `127.0.0.1:20241` and
  `20242` are ports this host answers on because `deploy/watchdog.ps1` starts two replicas and
  `docs/CLOUDFLARE.md` tells an operator to probe each one's `/ready`; the binding and the
  derived sources are each asserted non-empty, so a doc cannot gain a free port by the deploy
  tree going unread; measured this wave as 5432, 8000, 20241 and 20242 —
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
  own number. Eight mutations were proven to fire: a paywall row written as `fee`, a
  `credit(…, 'fee')`, a citation moved off the line that writes the fee, a `creator_income`
  renamed in code, a quote of a payout expression truncated in the doc, one wrong win rate,
  one wrong withdrawal rate, and one more wrong rate in a third document. What the laws cannot
  see is stated here: a rate written under proposal wording («proposed», «levers», «would») is
  exempt — §7.2's 0.5% scenario is a what-if, not a promise — and a document may still
  describe a fee qualitatively.
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
- **A count of mutations a document prints is the number of clauses it names.** Three paragraphs of
  this file proved gates by a number nothing backed: wave 31 claimed eight, and enumerated six
  clauses (the seventh and eighth hid inside «three wrong rates in three different documents»); wave
  34 claimed seventeen over fifteen, bundling three `-Check` keys into one clause and two tunnel
  protocols into another; wave 33 claimed twenty-five and named one.
  `test_every_documented_mutation_count_is_the_number_of_clauses_it_names` reads every such claim out
  of the tracked `*.md` through `_prose()`: `_MUTATION_CLAIM` takes the digits or the number word
  (zero..twenty, and a tens-hyphen like `twenty-five`) standing beside the sentence that says the
  mutations fired, `_enumeration_after()` takes the list that follows it in the same sentence — colon
  form or parenthesised — and `_clauses_of()` splits that list at **top-level** commas only, outside
  backtick spans and brackets, so a comma or a closing bracket inside a code span stays inside its
  own clause. Measured this wave: two claims, twenty-eight clauses, every count equal to its own
  enumeration. A claim with no enumeration fails too, and so a count the file can no longer
  reconstruct is stated as no count rather than as a padded one: wave 33's scratch tree is deleted,
  so its sentence now names what was mutated and leaves the number out — a number no clause list
  backs is exactly what this law refuses, and repeating it would be that same unbackable claim
  written twice. `test_the_mutation_clause_reader_reads_the_shapes_a_prose_list_takes` holds the
  reader to the shapes prose actually takes (both list forms, a comma and a closer inside a span, a
  hyphenated word, digits, «These mutations…» which states no count, a full stop before the list, an
  unclosed bracket), because a reader that quietly found no list would report a clean tree forever.
- **A byte-level claim in a document is a claim about this checkout, and the checkout is the witness.**
  `AGENTS.md` names the files that carry a UTF-8 BOM because a patch script reads that list and decides
  which encoding to name, and a BOM is a character no editor shows.
  `test_the_documented_bom_inventory_is_the_bom_inventory_of_the_tree` reads the parenthesised inventory
  out of the tracked markdown and holds it against `git ls-files` plus the first three bytes of every
  tracked file: the count word must equal the number of names, the names must equal the files that
  actually carry a BOM, a span in the list resolving to no tracked path is reported instead of dropped,
  and exactly one document may state the inventory — zero claims is the same stale sentence wearing a
  reworded shape. Measured this wave: twelve files, eleven of them Python modules, and every one of
  those eleven raises `SyntaxError: invalid non-printable character U+FEFF` when read as plain `utf-8`
  and parses after `utf-8-sig`, which is `test_a_bom_bearing_python_source_parses_only_after_the_sig`;
  its `.py` filter carries a floor of its own for the same reason the byte scan does.
  `test_the_bom_inventory_reader_reads_the_claim_a_document_writes` holds the reader to the shapes prose
  takes (a list wrapping a source line, a count written as digits, a `file.py::test_name` span beside a
  path, a sentence stating «Several», a colon list the reader cannot bound). Ten mutations were proven to
  fire: a thirteenth file gaining a BOM, a named file losing one, the count word off by one, one name
  leaving the list, the sentence reworded out of the shape the reader reads, a second document answering
  with its own inventory, a span that is no tracked path, the byte scan comparing two bytes instead of
  three, a BOM-less module entering the inventory, and the `.py` filter going blind. What neither law
  sees is a *reader*: only literal filenames resolve statically, so a gate that reaches a BOM'd file
  through a variable or a glob and names plain `utf-8` still dies at import on the message this bullet
  exists to prevent rather than here.
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
- **One product writes one grammar per kind of number, and the grammar is read off the formatter
  that states it.** A `maximumFractionDigits` option is not a grammar: a column of odds painted one row
  with a decimal and the next without, and the same USDC balance carried two decimals in the Mini App
  and one on the site — one product writing the same money two ways on the same day. `MECHANISMS` fixes
  the answer per kind (money two decimals, odds one, counts none, the fee rate in significant digits, a
  raw on-chain amount its six, a clock no fraction options at all) and names each kind by the words a
  formatter's own name has to carry (`usdc`/`balance`/`share`, `pct`/`chance`/`odds`, `count`/`num`,
  `fee`, `micro`, `time`/`date`/`day`).
  `test_every_painted_number_states_the_precision_its_mechanism_owes` takes every formatter out of
  `PAGES + SCRIPTS` (`every_formatter()` reads the function and arrow bodies that call `toLocaleString`),
  fails a formatter naming no mechanism, one naming two (the single allowed pair is fee-and-odds, judged
  as a fee), and one declaring digits its kind does not owe. Measured this wave as 22 formatters and
  the same 22 `toLocaleString` calls: money 8, odds 5, count 3, fee 1, raw 1, clock 4 — with a floor per kind
  and on the total, because a scan that quietly lost every formatter would report a clean tree. Its pair
  `test_no_number_is_formatted_outside_a_named_formatter` refuses a `toLocaleString` reached outside a
  function body — an inline format is a precision no law reads — and pins the call count so that reader
  stays awake. What it cannot see is a mechanism whose words no formatter name carries, which is why a
  new number face is named for what it counts, not for where it paints.
- **A rendered row is a set of parts, and every part owes its own type and one direction only.** The
  history row said its direction three times — the sign, the colour of the amount, and a green or red
  disc behind the arrow — and the disc is the one that breaks the contract: `style.css` keeps the state
  colours on figures (`.wallet-row b.pos`, `.lb-amt`, `.row-v`), so a tinted icon made the Mini App the
  only surface in the product where a state colour paints chrome, and a row whose money is a refund read
  as a withdrawal at a glance. `test_money_direction_is_painted_once_in_a_history_row` takes the row's
  own class family out of `app.html`'s stylesheet, allows a state colour only on a selector naming
  `amount`, pins the count at the two rules one fact looks like, and requires the amount cell's class
  and its sign to be keyed off the same field in the renderer — a cell coloured by one field and signed
  by another is a contradiction inside one template.
  `test_the_history_row_keeps_its_clock_in_the_money_face_and_its_note_out` separates two payloads the
  row used to paint in one span: the timestamp is a figure and keeps the money face with the tabular
  digits that come with it, the counterparty note is a sentence and does not, because a name drawn in
  the mono face reads as a checksum. Both classes are read out of the renderer
  (`_class_of_nearest_tag`), so a new element carrying either payload is inside the law the moment it
  exists. `test_every_part_of_a_rendered_row_declares_its_own_weight` is the third shape: `.tx-time` set
  a size and no weight, so the same class painted heavier inside `.tx-title` and lighter on the line
  below, decided by which branch of the cascade the markup happened to take — every class the renderer
  emits must weight the text it sizes, matched the way a browser resolves it (`.tx-amount.pos` answers
  for `tx-amount`). `test_the_row_reader_refuses_the_shapes_the_three_laws_exist_for` runs all three
  readers on markup that is not the shipped file.
- **One product speaks with one label voice, and a frame is only as good as the cells inside it.**
  The Mini App was rebuilt on the landing's measured recipes this wave, and the rebuild left two kinds
  of difference nothing could see: a label, and a row.
  `test_a_tracked_uppercase_voice_always_takes_the_mono_face` takes every rule in **both** stylesheets
  that sets `text-transform: uppercase` beside a `letter-spacing` — measured as 14 classes in
  `style.css` and 5 in `app.html`, 17 names over the two files — and refuses one that names no
  `font-family`, because `.gauge-cap` was exactly that: the only tracked caption in the product painted
  in the interface face while `.pal-kind`, `.ws-cur` and `.option-label::after` beside it used the money
  face. `test_a_label_voice_both_stylesheets_paint_is_one_voice` then compares the label classes the two
  surfaces share — `eyebrow` and `stat-label`, floor 2 — on family, weight, colour and tracking, and it
  found the second defect: the Mini App's `.stat-label` painted `400` against the landing's `500`, so
  the same caption came lighter on the phone than on the site. The size stays per surface, since the
  landing gradates a chip label against a micro annotation and the law governs the family, not the step.
  `test_the_tracked_voice_reader_reads_the_shapes_a_label_rule_takes` holds that reader to a grouped
  selector, a last declaration with no trailing `;`, `.1em`, a `::after`, caps without tracking,
  tracking without caps, and a comment that only looks like a rule.
  The row half: `web/static/app.html` paints a key/value cell (`.wd-row`) only inside a frame
  (`.list-group`) that owns the 1 px hairline ground between cells, so containment is a property of the
  app's **call graph**, not of its template text — the frame is opened by `rowFrame()` around rows an
  emitter handed back. `test_a_key_value_row_is_never_painted_outside_its_frame` takes the emitters (2
  measured, `availableLine()` and `infoRow()`) and requires an emitter that never opens a frame to have
  callers and **every** caller to reach one, which is why `infoRow()`'s bare cell is legal only while
  both sheets that call it wrap it.
  `test_the_frame_and_the_cell_it_holds_paint_two_different_grounds` keeps the divider real: the cell's
  background and the frame's must differ, and `.list-group` must keep `gap: 1px` **and** its own
  background, because losing either silently removes every hairline the group draws.
  `test_the_frame_reader_reads_the_shapes_a_sheet_builder_takes` runs both readers on synthetic builders,
  including `view.rowFrame(...)` and `rowFrames()` — a property and a longer name are not this call.
  What the pair cannot see is stated rather than hidden: it proves a builder reaches a frame, so a sheet
  that frames one list and paints a second row loose beside it answers the law and not the user, and
  only the rendered layer in `tests/test_production_design.py` measures that. Thirteen mutations were
  proven to fire: `.gauge-cap` back to no font family, a Mini App label invented in the interface face,
  the tracked-voice reader blinded, `.stat-label` painted 400 against the landing's 500, the landing
  re-tracked to `0.12em`, the app renaming its `.eyebrow`, `availableLine()` handing back a bare row,
  the withdraw sheet dropping `rowFrame(rows)`, `rowFrame()` opening a plain `div`, every
  `class="wd-row"` renamed, `.wd-row` painted on the hairline ground, `.list-group` trading `gap: 1px`
  for `gap: 8px`, and `.list-group` losing its background.
- **A language switch is a repaint, not a reload.** `setLang()` used to call `applyI18n()` and
  `renderMore()`: the first rewrites only the `data-i18n` labels, the second one tab, so switching on the
  Settings screen changed the tab bar and left every history, market and bet row in the language it was
  drawn in until the app was reopened — and a user who cannot read the language they are in is shown no
  control that fixes it. Two screens were in the same class and said nothing: the SmartAccount card,
  whose status line came from `t()` inside a fetch nobody re-ran, and the detail sheet's buy button,
  labelled at open time. The fix is at the primitive layer: `renderAll()` is the one repaint pass every
  screen is reachable from, the SmartAccount fetch and paint are split into `loadSmartWallet()` and
  `renderSmartWallet()` over `STATE.smart`, and `setLang()` calls `renderAll()` and nothing narrower.
  `test_a_language_switch_repaints_every_list_the_mini_app_ships` derives its subject set from the file —
  `localized_painters()` splits the app's functions into repainters, which write localized text into a
  document node, and builders, which create the node they write into — requires every repainter to be
  called by `renderAll`, pins the builder set by name because that set is exactly what the law cannot
  reach, and refuses a `setLang` that also calls a single screen beside `renderAll()`. Measured this wave
  as 7 repainters and 1 builder (`addCreateOpt`, which appends into the market the user is still
  writing). `test_the_repaint_reader_refuses_a_screen_it_cannot_reach` keeps that reader honest on
  synthetic markup, including the `setTimeout(` decoy that made the first version report every function
  holding a timer as a language leak. What the law cannot see is a painter that receives its localized
  text as a **parameter** instead of calling `t()`: the create-market option rows and the market detail
  still carry their add-time language for that reason, and are kept as open follow-ups rather than
  absorbed into an exception list.
- **A framed grid owns its last row, and a flex gap is already a separator.** The profile table was six
  markup tiles in a three-column hairline grid whose sixth tile shipped `hidden`, so what the browser
  drew was five tiles and an empty cell closing a frame whose own borders stopped where the missing tile
  began.
  `test_a_framed_tile_grid_paints_no_empty_cell` reads both of its premises out of the shipped files —
  the track count from the stylesheet that paints the grid, the cells from the markup that fills it —
  and applies only where the cells share a border, which is what keeps a ragged card grid out of scope;
  the cell count is the count of **painted** children, because a tag-counting reader saw `user.html` as
  six-in-three and certified the hole it exists to refuse (`_direct_children` drops a child carrying
  `hidden`, and the reader test hands it exactly that shape).
  `user.html` now states its figures in `.rows`, a list that declares no equal-track columns and so
  promises no count to be a multiple of anything, and
  `test_the_tile_grid_reader_owes_a_cell_to_five_tiles_in_three_columns` runs the reader on the defect,
  on its fix, on the media-query trap and on the framed premise. What that law cannot see is a tile
  hidden by `style="display:none"` rather than by `hidden`: it still counts as a cell, and the interface
  contract already refuses that form of show/hide, so the blind spot is the contract's own premise and
  not an exception list. The other half of the same class:
  `.page-meta` is `display:flex` with `gap: 14px`, and five documents joined its items with a
  punctuation span on top of that — a box of one glyph floating in the gap the layout already pays for,
  and a child of the accessibility tree that says nothing.
  `test_no_meta_row_is_joined_with_an_anonymous_punctuation_box` reads every document **and**
  script in the bundle, because three of those five joiners were built inside JS string literals and a
  tag-only reader would have certified them as clean, and
  `test_the_separator_reader_still_reads_the_joiner_it_exists_to_refuse` keeps that reader awake now
  that its shipped subject set is empty.
- **The alembic chain is the only writer of this database's schema, and that is measured
 `Ledger.__init__` runs `alembic upgrade head` and applies
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
- **The bound a route refuses with is the bound the field states.** A money field is the only place a
  user learns a limit before the refusal, and every bound this repository enforces was either copied
  into markup by hand or not copied at all: a withdrawal field whose own `min` let through every sum the
  route rejects under `MIN_WITHDRAW_USDC`, a create-market subsidy floor left at whatever the browser
  defaults to while the config sets another, a tip field with no ceiling named, and the whole Mini App,
  which read none of the published numbers and so let the ledger answer a tap the input could have
  refused. `test_the_bound_a_route_refuses_with_is_the_bound_the_field_states` measures both halves from
  the tree — the bound out of the handler's own `refuse(..., min=config.X)` through the assignments it
  resolves to, the field out of the HTML attribute or the script that binds a published key to it — and
  fails a bound no endpoint publishes, because a page facing one can only invent it. The rendered half is
  `tests/test_production_design.py::test_the_open_sheet_states_the_bounds_this_origin_publishes`
  (`production`): the sheet where the sum is chosen is judged against that origin's `/api/info`, because
  only the painted panel can show the attribute and the hint disagreeing, and the hint is what tells a
  visitor *why* a sum was refused rather than that it was.
- **An empty state is a payload, and the container that carries the heading is not its place.**
  `web/static/user.html`, `market.html` and `oc.html` each answered a failed read by replacing the
  article that also holds the page's `<h1>`: the heading, the controls under it and the outline a
  screen reader walks went away with the data, so a visitor following a link to a pool that had been
  closed reached a document with no title — and the rendered gate stayed green, because the load it
  opens is the one that succeeds.
  `tests/test_design_system.py::test_no_script_repaints_the_container_that_carries_the_heading` pairs
  the two sets a file can show: `element_subtrees()` walks each document's tags and keeps the inner
  markup of every identified element, and `HEADING_BURN_RE` takes the ids the page's own code hands a
  content assignment — `$('id')`, `getElementById('id')` and `querySelector('#id')` into `innerHTML`,
  `outerHTML`, `textContent` or `innerText`, with an append excluded because `+=` destroys nothing and
  a comparison excluded because it reads. Measured this wave as 4 heading-carrying containers (`#app`,
  `#market`, `#oc`, `#profile`) and 108 content writes over 8 pages, both floored, so a walk that lost
  its tags fails instead of certifying a clean tree; `#user-name` is an `<h1>` written through
  `textContent` and is allowed for the reason the law can state — the id's own subtree holds no
  heading. The rendered companion is
  `tests/test_production_design.py::test_an_empty_read_is_still_a_page` (`production`), parametrized
  over `id_bearing_page_routes()`, which reads the document and the path out of the arguments of every
  `_static_page` call in `web/server.py` — measured as 7 calls, 3 of them with a `{…}` in the path — so
  a fourth id-bearing surface joins by being written, and whose premise is checked rather than assumed:
  the probe id is fifteen nines, and a load that found its data is reported as the empty state never
  having been measured. What the pair cannot see: a container reached through a variable instead of an
  id literal, a note painted by a script the page loads from outside this bundle, a heading rebuilt
  rather than replaced, and the wording of the note itself, which no gate here can judge. The
  derivation was wrong on its first run in exactly the way a floor is for: without the brace test it
  parametrized `/tos`, `/agent` and `/`, three empty reads with nothing empty about them. Seven
  mutations were proven to fire: a wipe restored in the profile page, the same wipe in the pool page,
  the same wipe in the on-chain page, a fourth page that no list names reaching the gate by being
  written, the subtree walk made blind, the writer scan made blind, and the shipped `user.html` wipe
  restored in the tree and re-rendered against a live origin.
- **A crash is not a missing record, and a page that says otherwise has already lost the money.**
  `/m/{id}` wrapped the fetch *and* the paint in one `try`, and its one `catch` painted «Пул #N не
  найден»: measured 2026-11-18 on the live site, all four open pools answered that sentence while
  `/api/market/5` returned 200 with a full payload, because the published `chain.js` had no
  `barWidth` and the paint died on the first option row. The visitor was told the pool did not exist,
  the owner was told nothing at all — no `console.error`, no log, no gate. The shape was not unique:
  `oc.html` caught its own render errors into the same not-found wording, `user.html` swallowed a
  clipboard refusal, and the Mini App's AI catch erased the pending bubble without a trace.
  `tests/test_design_system.py::test_a_page_never_answers_a_crash_with_the_words_for_a_missing_record`
  now reads both halves out of the inline scripts of every page that fetches — measured this wave as
  four pages (`app.html`, `market.html`, `oc.html`, `user.html`), four catches that write into the
  document and all four of them logging, and two catches whose own wording names a missing record
  (`market.html`, guarded by a `read` flag because its `try` also paints, and `oc.html`, which owes
  no guard because its `try` only reads). A writing catch that logs nothing is a crash no human will
  ever see; a not-found card whose `try` also paints is a crash wearing the not-found wording; and
  the floors refuse a scan that went blind. `block_after_brace` matches braces rather than guessing
  from line offsets, and `test_the_catch_reader_reads_the_shapes_a_page_takes` holds the readers to
  the shapes the shipped pages take: a read-only `try` beside a painting one, a comparison against an
  assignment, `+=`, `insertAdjacentHTML`, an arrow `.catch(() => ({}))` that owes no log, and a
  `try { … try { … } catch { … } } catch { … }` nesting whose outer body must still end at its own
  brace. Seven mutations were proven to fire: `market.html` reverted to the defect as shipped, its
  crash card left with no `console.error`, its not-found branch left with no guard, `oc.html` left
  with no log, the document-write reader blinded, the missing-record reader blinded, and the page
  reader blinded. What the law cannot see: a catch whose card says something *else* wrong — it judges
  only the two wordings that hide a crash — and the wording of the crash card itself, which no gate
  here can read. The fix keeps the heading outside the failure both times, because the article that
  carries the `<h1>` is the same container the card would have replaced.
- **A clock is a bound too, and the money law could not see it.** `MARKET_MAX_DEADLINE_HOURS` is the
  ceiling `/api/mini/create` answers `api_deadline_long` under, and three surfaces typed a deadline
  against it blind: both Mini App create sheets carried no `max` at all, and the landing wrote
  `max="720"` into markup beside a hint restating the same cap as «До 30 дней». The money law reads
  `min=`/`cap=` kwargs, `_usdc`/`_micro` keys and decimal inputs only — a clock is in none of those
  sets — so `test_the_clock_a_field_offers_is_the_clock_the_route_honours` is its sibling: the cap
  comes out of the handler's own `refuse(..., max=config.X)` where the config name ends in `_HOURS`,
  the published key comes out of the `/api/info` dict rather than being paired by hand, and the fields
  come out of every function that reads a payload name containing the parameter — measured as
  `newHours`, `mkHours`, `btHours`, with the count floored so an id that reaches the reader through a
  variable fails the build instead of passing unseen. Each field must bind `max` to that key, and a
  `max=` literal left in markup as the no-JS fallback must equal the knob, because a number written
  into HTML is a second copy of the config. The rendered half is
  `tests/test_production_design.py::test_an_open_layer_states_the_bounds_this_origin_publishes`, which
  now opens the create layers too: the painted attribute is judged against this origin's `/api/info`
  **and** the figure must also be said in a hint, because a bound that blocks a tap without telling
  the user why is a refusal the field could have prevented.
- **A repaint hook is a claim about a dictionary, and the law had the wrong attribute names.**
  `applyI18n()` walks `[data-i18n]`, `[data-i18n-ph]` and `[data-i18n-label]`, while
  `test_every_mini_app_translation_key_exists_in_all_three_languages` typed the suffixes as one
  alternation — and it named `-title`, which no element carries and no line walks, and missed
  `-label`, whose four aria-labels were therefore checked against no dictionary by anything. The
  subject set is now `_mini_app_repainted_attributes()`, the app's own `querySelectorAll('[data-i18n…')`
  calls, and both directions of the rename are gated: markup carrying an attribute `applyI18n()` does
  not walk is a word that survives a language switch — the shape that put `Option 1` in a Russian
  placeholder — and an attribute it walks while no element carries it is a repaint pass with nothing
  to repaint. `_mini_app_dictionaries()` is the single reader of the inline dictionaries for both that
  law and `test_mini_app_dictionary_is_complete_across_languages`: the block is brace-scanned, so a
  dictionary written on one line and one spread over twenty read the same way, and the number of words
  parsed is held against the number of `key: "` pairs visible, because a rename that leaves one word
  written twice shadows the first and a set cannot show that it lost a declaration. `addCreateOpt`
  assembles its placeholder as `opt_<n>` and caps `n` with a literal in its own body, so
  `test_the_localization_key_the_mini_app_builds_covers_every_row_it_offers` takes the cap from that
  comparison and requires `opt_1`…`opt_4` in each language — raising it without the words paints
  `opt_5` as UI text in every language — and
  `test_the_mini_app_localization_readers_read_the_shapes_a_rename_leaves_behind` holds the three
  readers to a spread dictionary, a duplicated word, a `>`-written cap and a page whose hooks are
  walked by nothing.
- **A knob is only another module's default if the code reads it from that module, and a painted
  sentence owes every bound a typed one paid.** `agent/tools.py` imports `agent/config.py` as
  `config`, and the deadline ceiling this wave added there names a knob `bot/config.py` owns — so
  every market the agent tried to create died on `AttributeError`, and only the two `single_cycle()`
  e2e tests noticed.
  `tests/test_env_contract.py::test_every_knob_the_runtime_reads_exists_on_the_module_it_reads_it_from`
  resolves each binding through the AST (`from . import config` → `agent.config`, `from bot import
  config as runtime_config` → `bot.config`) and asks the imported module for the attribute: measured
  as 459 constant reads over 57 files and 7 in-repo modules, exactly those two sites missing, with the
  floor refusing a scan that goes blind. The same move on the landing left three loose ends nothing
  reached: the `__DEFAULT_DEADLINE_HOURS__` substitution in `_static_page` answering a document that no
  longer carries the token, an empty `<div class="hint">` the day its writer is renamed, and the
  figures themselves, because a painted sentence types a number as easily as markup.
  `test_every_placeholder_the_server_replaces_is_one_a_document_asks_for` scopes each `html.replace` to
  the documents that function serves — the literals passed to `_static_page`, the Mini App its own
  route; `test_every_hint_the_landing_ships_empty_is_filled_by_a_script_the_landing_loads` takes the
  slots from `index.html` and the writers from the scripts that same document names, through both quote
  styles and the `const foot = $("searchFoot")` hop; and the rewritten
  `test_landing_deadline_hint_quotes_the_configured_default` refuses **any digit** inside the painted
  expression while holding both keys against `monkeypatch`ed config. The class-parity reader was fooled
  by the same edit: `class="chip${v === value ? ' active' : ''}"` is JS standing in markup position, so
  `_literal_markup()` blanks every `${…}` span together with the token it interrupts and
  `test_the_class_reader_reads_a_class_attribute_the_way_a_template_writes_one` holds it to the shapes
  the shipped files take — a ternary, a state-suffixed name, a closed concatenation, a nested template
  and an interpolation the attribute never closes. What these readers cannot see is a class the
  interpolation decides (`.tag-open` arrives that way), the same blind spot as `bar-fill-over` inside
  `cls`. Twelve mutations were proven to fire across these laws: `bindBound('newHours', …)` removed
  from `site.js`, `max="720"` moved to 721 in `index.html`, `boundAttr` dropped from `#mkHours`,
  `boundAttr` dropped from `#btHours`, the landing's hours id reached through a variable, the ceiling
  enforced in the attribute but no longer said in the hint, `#mkHours` painted with no `max` at the
  rendered layer, markup carrying `data-i18n-title` that nothing walks, the option-row cap raised to
  six without the words, `theme_toggle` removed from the zh dictionary alone, a typed «30 дней» put
  back inside the painted hours sentence, and the `$('hoursLimits')` writer deleted.
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
- **The `awaiting` sentence is written once, in the file that derives the state.** A pool whose deadline
  passed while nobody resolved it is the fourth state, and the line beside it is the answer to «и что
  теперь?», so it is the one sentence on the site a visitor may not get in two versions: the landing
  promised the money could be returned while the market page promised the stakes could be, and nothing in
  the tree could notice, because two literals in two files are not a diff. The durable shape is the one
  every other answer to a derived state already uses — the sentence lives in `chain.js` as `REFUND_NOTE`,
  beside the `marketState()` and `deadlineLine()` that produce the state it describes.
  `test_the_awaiting_note_is_written_once_in_the_file_that_derives_the_state` measures that shape and not
  the wording: exactly one definition in the bundle, and every document that prints the sentence — or
  loads the script that does — loads the derivation before it, because a page that reads `REFUND_NOTE`
  without `chain.js` renders `undefined` where a refund should be promised.
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
- **The published bundle is a second answer to the same question, and Pages is published by hand,
  so the tree can be right and the site wrong at once.** Measured on the live host the day this was
  written: `style.css`, `app.js`, `chain.js` and `site.js` served bytes two weeks older than
  `main`, `fonts.css` and all eleven faces were absent — and an absent bundle file is not a 404, it
  is `200 text/html`: Pages answers a missing path with the landing page, so every `@font-face` src
  resolved to a whole website and the browser silently painted the operating system's font, exactly
  the shape `/robots.txt` had. Nothing in the tree could say so. The miss is now refused where it is
  produced: `functions/[[path]].js` treats an asset path whose bundle answer is `text/html` as a miss
  and lets it reach the origin, because a `200` from Pages is not evidence that a file was found.
  `tests/test_production_health.py::test_the_published_bundle_is_this_checkouts_bundle` now hashes
  every file the directory walk says the edge can answer and compares the bytes, the type and the
  `max-age` against this checkout and against `_headers`; the companion law asks the two paths that
  must never reach the bundle (`/robots.txt`, a missing `.css`) what the edge does with them. Both
  are `production` — an ordinary green run does not prove a deploy happened, and
  `--commit-dirty=true` is precisely why a deploy proves nothing about a commit either. Measured
  again on 2026-11-18: the published bundle is 21 assets and five of them — `agent.js`, `app.js`,
  `chain.js`, `site.js` and `style.css` — are still the bytes of a Pages deploy behind `main`, while
  every font, every image and both cache windows match. So this law is red on the live host by
  itself, and stays red until someone runs the publish: a green production run is evidence a deploy
  happened, never a substitute for one.
- **A page ships on two vehicles and is answered by a third, and all three are measured.** The
  markup is rendered from disk by the running origin at request time, the `*.js` and `*.css` reach
  the visitor from the hand-published Pages bundle, and the numbers a sheet binds come from the
  process started whenever the host last restarted it. One checkout can therefore be three
  versions, and on 2026-11-18 it was, in two directions at once. The published `chain.js` is 3,945 B
  against this tree's 4,748 and names `barWidth` nowhere, while four call sites in markup the origin
  serves fresh today — `app.html:1074`, `app.html:1494`, `market.html:150` and `oc.html:161` — reach
  for it, so every option row of the Mini App's market list, of its market detail, of `/m/{id}` and
  of `/oc/{id}` threw `ReferenceError: barWidth is not defined` while it was still building its own
  HTML. (The landing's bars are painted from `app.js`, stale in the same deploy, so the two old
  files agree with each other and that page broke nowhere: a page breaks where fresh markup meets a
  stale script, not wherever a wave happened to stop.) And the origin answering `/api/info` — this
  host's `deploy/run.py`, started at 13:16 against a `web/server.py` whose last commit landed at
  20:26 — published eight of the eleven bounds this checkout publishes, no `default_deadline_hours`,
  no `max_deadline_hours` and no `deposit_confirmations`, which is why the rendered drawer and sheet
  gates went red on a tree that was correct.
  `tests/test_design_system.py::test_every_bundle_function_a_page_reaches_is_one_the_page_loads`
  closes the first shape inside ordinary CI: a document's global scope is the concatenation of the
  bundle files its own `<script src="/…">` tags name plus its inline blocks — read with the
  `page_scripts` and `page_code` readers and no second map of page to scripts — and a function the
  bundle owns, which the scope calls and nowhere in it defines, is a helper reached from a file the
  page never loads. Measured this wave as 208 page/helper pairs over the eight documents
  (`index.html` 138, `app.html` 22, `agent.html` 18, `market.html`, `oc.html` and `user.html` 10
  each, `legal.html` and `not_found.html` 0) drawn from 146 bundle-owned names across five scripts,
  with a floor on the pairs, because a `CALL_RE` that quietly matched nothing would otherwise
  certify exactly the tree that broke the Mini App.
  `test_the_scope_reader_refuses_the_call_shapes_a_page_ships` holds the four regexes to the shapes
  the shipped files take: a `function` at column zero and one indented, an arrow on one line and one
  wrapped at its body, `const x = (v) =>` and `const x = v =>`, an `async` definition,
  `chain.barWidth(pct)` which is a property and not the global helper, `myBarWidth(pct)` which is a
  longer name and not this one, `<script defer src=…>` and `<script src=… defer>` in either attribute
  order, and an off-origin `https://cdn…/z.js` which is not a file of this bundle. Six mutations were
  proven to fire: `chain.js`'s script tag removed from `app.html`, a `shortAddr` call added to
  `legal.html`, `FUNC_DEF_RE` blinded, `ARROW_DEF_RE` blinded, `CALL_RE` blinded, and `SCRIPT_SRC_RE`
  blinded.
  `tests/test_production_health.py::test_the_running_origin_publishes_every_key_this_checkout_publishes`
  closes the second: `api_info_keys()` takes the key sets out of the AST of `api_info()`'s own dict
  literal — the 12 top-level keys, the 2 fees and the 11 bounds — and holds the live `/api/info` to
  them in both directions, so an origin answering fewer bounds is named key by key and an origin
  answering a key the tree no longer publishes is named too. Values are not compared: the deployed
  `.env` is not this machine's `.env`, and a knob is allowed to differ. Six more mutations were
  proven to fire: a bound added to the literal the deployment cannot answer, a bound removed from
  the literal while the origin still answers it, a bound renamed inside it, a top-level key renamed,
  the bound block published through a call instead of a literal, and the literal-key walk blinded —
  the last two tripping the reader's own floors rather than reading empty, which is what a vacuous
  publisher looks like. That reader is held in ordinary CI by
  `tests/test_web.py::test_the_info_key_reader_refuses_the_shapes_the_publisher_takes`, on a
  miniature publisher and on the two shapes that would make the law meaningless: a payload assembled
  at runtime and a module with no `api_info` in it.
  What none of this sees: a helper reached through a variable (`window[name]()`), a script a document
  loads from another origin, a bound whose *name* is published by a process that is otherwise the
  right one, and every difference between the deployed process and this checkout that the published
  key set does not show. The fix for both live findings is one republish of Pages and one restart of
  the origin — shared-state actions, so the maintainer's word, not a green gate's.
- **One file, one cache window, read in three places.** Before this wave the site had no promise
  about how long anything may be cached: the middleware's blanket `setdefault('Cache-Control',
  'no-store…')` covered the origin's own assets, and Pages answered the same files with
  `public, max-age=0, must-revalidate` — measured: one view of the landing re-fetched 61 KB of CSS,
  seven scripts and eleven faces. The table is the two pairs in `web/server.py` (`CACHE_CODE`,
  `CACHE_MEDIA`) and the `Cache-Control` lines in `web/static/_headers`; the subject set is
  `bundle_asset_suffixes()`, the extensions the directory actually carries.
  `test_one_file_one_caching_policy_in_both_answers` demands set equality in **both** directions and
  verbatim equality of the two directives, so a new file type fails until someone decides its window
  twice, and a `Cache-Control` on the `/*` block fails because `/*` reaches a document and a document
  is the session, the nonce and the balance. `test_the_window_a_response_carries_is_the_window_its_class_declares`
  reads the same policy off served bytes: the assets are the directory walk and the documents are the
  parameterless `GET` routes the app itself registers, so a new page or endpoint arrives answering
  `no-store` and has to be argued out of it. `/qr` is the one non-asset the origin publishes as
  cacheable and the law measures its premise rather than trusting it — the same URL answered cold
  and answered by a signed session must be the same bytes with the same window, because a long window
  on a URL is only safe when nothing but the URL decides the answer. The path-to-window reader is
  `window_for()`, held to the decoy this tree actually ships: `fonts.css` sits at the bundle root
  beside the `fonts/` directory, so a directory pattern sliced as a name prefix (`pattern[1:-2]`, the
  first version) claims that stylesheet twice with two different windows — which is how the live law
  went red on a bundle that was byte-for-byte correct.
- **Four more laws read the Function file, because it is the other program that answers this
  origin and no Python ever executes it.** `test_the_edge_routes_every_class_of_file_the_bundle_ships`
  takes the `ASSET` extension list out of the shipped `functions/[[path]].js` and requires every
  shipped suffix to match and `.html`/`.md`/`.txt`/`.json`/`.yaml`/`.yml` to **not** — a class the
  bundle carries but the routing does not name is proxied, and while the origin is down it becomes
  the maintenance card standing where a stylesheet should be.
  `test_every_asset_url_a_shipped_file_names_is_a_file_the_bundle_carries` scans every document,
  script and stylesheet plus the function for quoted absolute asset URLs and resolves each against
  the directory (the missing-file direction of the same defect, which is what a 404-ignoring edge
  turns into a silently wrong font). `test_the_edge_card_copies_the_tokens_the_stylesheets_ship`
  compares the card's own `:root` and dark blocks to `style.css` token by token — the copy had
  drifted two waves behind (`--muted` #6f6f6f, `--muted-2` #9a9a9a), and its `.hint` line measured
  **2.85:1**, the exact failure the contrast gates refuse, on the one page the origin does not
  answer; a token the card invents fails too, because no other gate would ever read it. The card is
  also the only document on this site that no middleware writes, so
  `test_the_edge_card_sends_the_headers_the_origin_sends` requires `nosniff`, `no-referrer`,
  `Retry-After: 300` and `no-store` in its own `Response` — a cacheable outage card outlives the
  outage. The fourth reads the **outage window itself**:
  `test_the_edge_answers_a_silent_origin_inside_the_window_the_doc_promises` takes
  `UPSTREAM_DEADLINE_MS` out of the Function, refuses a figure that is not whole seconds or not in
  5–25 s, then requires the four shapes that make it real — the timer armed on that constant, the
  `signal` reaching the upstream `fetch`, `if (expired) break;` inside the retry loop, and
  `clearTimeout(timer)` after the answer is chosen — and finally compares it with the one
  `docs/CLOUDFLARE.md` paragraph that names the constant and the **15 s** it bolds. The reason is a
  measurement: with the laptop's tunnel mid-reconnect a request into the black-holed origin sat
  **22.5 s** before the visitor saw the card, because `fetch` has no deadline of its own and the wait
  fell through to Cloudflare's subrequest limit — a 22-second spinner is indistinguishable from a dead
  tab, which is the one thing the card exists to avoid saying. 15 s rather than something snappier
  because the same origin has answered a plain health read in 7.8 s while the host ran its test suite,
  and a deadline that cuts off a live origin manufactures the outage it reports.
  The shipped file was then run, not read: in workerd against a scratch origin that answers nothing
  for 25 s the card arrived at **15.1 s** (one window, not two — the spent window is not retried), a
  second scratch origin that sends headers at once and trickles its body for 30 s delivered **all 12
  chunks and `END`** at 30.1 s, which is what `clearTimeout` buys and what `AbortSignal.timeout()`
  would have destroyed; a refused connection still retried and answered the card in 0.27 s, and the
  app's own `502 application/json` still passed through untouched. Mutations were proven to
  fire across these nine laws and the two live ones, in wave 33's scratch copies, the last of
  them against `window_for()` itself: the same slice that the first version carried makes
  `fonts.css` a file the font block owns. No count is stated for that wave: its scratch record
  is deleted, and a number no clause list backs is what the mutation-count law now refuses.
- **The card names the outage it measured, in words the runbook also has to know.** The edge card is the
  only answer this origin writes by itself, so it is the only place an outage can be named: the origin
  that could have explained is the thing that is missing.
  `test_the_edge_card_says_only_a_reason_the_measurement_can_produce` holds five things together —
  `OUTAGE_REASONS` and the branches of `outageReason()` are one set, because a word the chooser cannot
  return is a sentence nothing paints and a returned word the card does not know prints `undefined`
  where a visitor asked for a reason; the word reaches the page both in a `data-reason` attribute and in
  `x-tippy-outage-reason`, because an operator reading an outage with `curl -I` should not have to open a
  browser; every card call takes its word from the chooser, so no call site can hand-pick a flattering
  one; the chooser reads its three inputs and nothing else, since a reason taken from an exception object
  would be this edge quoting its own internals on a page anyone can request; and each word states the
  fact only its own branch can know.
  `test_the_reason_the_card_gives_is_the_reason_the_measured_shape_produced` then **runs** the shipped
  Function against a local origin answering each shape an outage actually takes — configured, serving,
  its own JSON 502, a proxy's HTML 502, a refused connection, a connection that says nothing — changing
  only `UPSTREAM_DEADLINE_MS`, so the silent case costs a second instead of the window the doc promises
  and the deadline itself stays measured against the unedited file. That is where the defect was:
  `outageReason({ upstream })` was handed no `expired` flag, so a spent window printed the *refused*
  sentence and the card blamed a connection that was never attempted for an origin that simply said
  nothing. `test_the_doc_gives_every_outage_reason_its_own_remedy` keeps `docs/CLOUDFLARE.md` at one row
  per word the Function can print, no row for a word it cannot, and a remedy in each row, because four
  reasons point at four different places to fix.
- **The program that decides whether the live site heals is PowerShell, and
 `deploy/watchdog.ps1` is the only
  writer of the tunnel's process table on this host (the `\TippyOriginWatchdog` task, every few
  minutes), and for thirty-three waves no test executed a line of it. What the text was doing,
  read out of 5,526 of its own logged passes and 19 probes through the live edge: **4,745 passes
  (86%) wrote `public=False`** while the site served, because the public health endpoint was
  judged by one request issued microseconds after `Start-Process cloudflared` and a connector
  needs seconds to reach its edge; a replica that lost every connection was kept for four more
  minutes answering 503 because the "old and disconnected" rule required five minutes of age
  while the median connection lifetime on this host is 20 s; and the flag that was *causing* the
  churn (`--edge-bind-address`: 22–173 s lifetimes, median 101 s, 49 deaths in 30 min, on an
  address 82 of 82 established `:443` sockets were already using) was the flag the script
  insisted every process carry, so it stopped the healthy ones. 25 laws now measure the supervisor:
  the two restart verdicts are executed as the PowerShell that produces them, against a nine-row
  truth table whose ages and probe counts are read back out of the same file (`$StartupGraceSeconds`,
  `$OriginGraceSeconds`, `$ConfirmProbes`), so a mutated window flips a verdict rather than an
  expectation; the replica pair is measured through `Get-MetricsPorts`/`Get-ReadyUrl`/
  `Get-ConnectorLog` for distinct ports, distinct logfiles and a URL naming its own port, because
  the availability comparison that justified the second connector is `6 of 19 (32%)` public with
  one replica against `10 of 19 (53%)` on that replica's own `/ready`, and the pair re-walked over
  87 samples answers ready on one replica for `73 of 87 (84%)` and on the other for
  `45 of 87 (52%)` — beside the single-replica walk the pair reads better, and beside itself the second
  connector bought this host nothing, because both die on one shared egress, so the page stands
  because the supervisor replaces the replica that went quiet and not because the count is two;
  every launcher of `cloudflared.exe` in `deploy/` — a directory glob, three files measured
  — must pin `http2` and name no source address; no function may stop a process it cannot name by
  command line, and `Get-Connector` must resolve to the script's own runtime directory; the pass
  must wait for the tunnel before it judges the public path; `-Check` must answer all eight keys,
  including `ReplicaPorts` and `ReadyPorts` together, because one boolean is what made three hours
  of outage read as a mystery (one replica answering 503 while its twin served the page).
  **The origin is healed only when nothing answers at all**: `Get-EndpointOutcome` is one reader
  with three values — `ok`, `answered` (a `/ready` 503, or a 200 whose `db` is not `ok`) and
  `silent` — and only a whole window of `silent` past `$OriginGraceSeconds` reboots `deploy/run.py`,
  because an `answered` origin is a fault the process is reporting and restarting it over a database
  outage kills Telegram polling while hiding the report. That is the wedge this host actually
  produces (`WinError 64` on a pipe, uvicorn alive, every request into a black hole, the tunnel
  carrying nothing). 1 of the 25 laws runs this host's live processes and carries the Windows
  guard with its reason printed; the rest are portable, and the executed predicate
  laws run in CI too because `ubuntu-24.04` carries PowerShell 7.6.10 — so the rules that decide
  whether the site heals are measured on the machine that has them and on the machine that does
  not. Twenty mutations were proven to fire, each one its own clause below — the last five
  measured in this wave's scratch runs, the first fifteen in the waves that wrote those laws —
  against copies in a scratch tree so the live supervisor never saw a mutated file: a shared
  logfile, `$ConnectorReplicas = 1`, a `Get-Connector` that stopped reading command lines,
  `Stop-Process -Name cloudflared`, `--edge-bind-address` back in the arguments,
  `deploy/tunnel.py` at `--protocol auto`, `deploy/tunnel.py` with no protocol at all, the
  public probe moved before the wait, one probe instead of three, `-Check` dropping
  `OriginOutcome`, `-Check` dropping `ReplicaPorts`, `-Check` dropping `ReadyPorts`, a doc
  probing `20243`, a doc counting three replicas, a doc printing `26%` for 6 of 19, a doc
  quoting the 20-sample pair walk the 87-sample walk superseded, that figure repeated across
  a hard line break so a reader that needed a literal space went blind, `AGENTS.md` stating a
  law count one off the file it describes, `AGENTS.md` stating a guard count the file's own
  decorators refuse, and `AGENTS.md` no longer stating either count at all. What the laws cannot
  see: the availability percentages are one host's path to one edge, and re-measuring them means
  editing the doc — which the fraction law requires to be arithmetically its own answer and the
  walk law requires every other document to repeat only figures the runbook measures, but neither
  can tell that the runbook's own walk went stale, because a superseded host is still a host that
  once answered, and a percentage written without its denominator is not a fraction any reader can
  check; the executed `-Check` law reads this machine's live processes and nothing else; and the
  supervisor's *timing* under load is measured by the log lines it writes, not by these tests.
  `docs/CLOUDFLARE.md` now
  carries the windows table the constants pair to, the three outcomes, the `-Check` command and
  its keys, and both causes of the connection churn; the prod workflow's job limit moved 15 → 25
  minutes the wave a hand-dispatched live check was cancelled at the limit mid-suite, because a
  probe that reports nothing is the one answer it exists to avoid.
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
- Money, addresses, odds and percentages use `--font-mono` with `font-variant-numeric: tabular-nums` —
  and a value being **typed** is still a figure, not a word: `test_every_money_and_address_field_is_drawn_in_the_mono_face`
  judges every `<input>` on every page, in both stylesheets, because in the interface face `1 000,00`
  and `11,00` start at different widths and an address the user checks before signing is proof-read
  letter by letter. Both directions are measured, so the money face on a field that holds neither money
  nor an address fails the same law.
- A history row states its direction **once**: the sign and the colour of the amount, keyed off the same
  field, and nothing else. No tinted disc behind the arrow — the state colours are reserved for figures,
  and an icon that takes one turns a refund into a withdrawal at a glance.
- A language switch repaints. `setLang()` calls `renderAll()` and no single-screen painter beside it, so
  any screen that writes localized text into a node gets its words from that pass and not from a fetch
  that happened to run earlier.
- An empty read is still a page. The note goes where the data would have gone, never into the article
  that carries the `<h1>`: replacing that container takes the heading, the controls under it and the
  outline a screen reader walks with it, and a visitor who cannot find the thing they were linked to is
  owed a title and a way back.
- A clock field states the cap its route honours: the `max` comes from `/api/info`'s
  `max_deadline_hours`, the hint under it says the same figure in words, and a `max=` literal left in
  markup as the no-JS fallback is a copy of `MARKET_MAX_DEADLINE_HOURS` that has to stay equal to it.
- A localization hook is a promise that `applyI18n()` walks it: `[data-i18n]`, `[data-i18n-ph]` and
  `[data-i18n-label]` are the three attributes the repaint reads, so a placeholder or aria-label hung
  on any other attribute keeps the language the screen was drawn in — and a hook whose key one of the
  three dictionaries lacks renders English, because `t()` falls back rather than failing.
- A limit the visitor has to trigger to learn is a limit the field failed to state. The number in the
  attribute and the number in the sentence under it are the same published key, and a sentence a
  script paints carries no digit of its own — a hint written `'До 30 дней'` is a second copy of a
  config knob in the one place nobody diffs.
- A `__TOKEN__` is a contract between a document and `web/server.py`, in both directions: a token no
  writer substitutes reaches the visitor literally, and a substitution no served document asks for is
  dead code that reads like a contract.
- A row with a `gap` needs no separator glyph, and a framed grid whose cells share a border needs a cell
  count that fills its tracks — a list of figures is `.rows`, one column, not a grid with a hole in it.
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
