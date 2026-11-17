"""Documentation links must point at files that exist.

A README link to a deleted chapter 404s for every reader, and a source comment
pointing at a documentation file that was never committed sends the next
maintainer hunting for nothing (one such pointer lived in bot/base.py until
this gate found it). Both are cheap to check from the tree.
"""

import ast
import re
import subprocess
from decimal import Decimal
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The vendored OpenZeppelin tree ships its own links and is not ours to fix.
SKIP_DIRS = {"contracts", ".git", ".qoder", "node_modules", "site-packages"}


def _tracked_files(*prefixes: str) -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files", *prefixes],
        capture_output=True,
        text=True,
        # Git writes UTF-8 bytes whatever the console says; decoding them with the
        # Windows locale codepage would turn a non-ASCII path into a reader-thread
        # UnicodeDecodeError, and stderr is the message an operator needs.
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        timeout=60,
    )
    if proc.returncode != 0 or proc.stderr.strip().startswith("fatal:"):
        pytest.fail(f"`git ls-files {' '.join(prefixes)}` did not answer: {proc.stderr.strip()!r}")
    if not proc.stdout.split():
        pytest.fail(f"`git ls-files {' '.join(prefixes)}` printed nothing — the tree is not measurable")
    return set(proc.stdout.split())


def _first_party_markdown() -> list[Path]:
    """Every markdown this repository tracks, minus the vendored trees.

    The list used to be `README.md` plus `docs/*.md`, hand-written — and the three policy
    files that live at the repository root were therefore read by no law. GitHub renders
    exactly those root files as the project's security, contribution and conduct policy,
    so a researcher never sees the careful version in `docs/`. What reading them found:
    `SECURITY.md` sends vulnerability reports to `security@ezoffer.ru`, a domain that
    appears in no other file in the tree, while `docs/SECURITY.md` names the channels the
    maintainer actually answers; `CODE_OF_CONDUCT.md` still carries the Contributor
    Covenant template's `[INSERT CONTACT METHOD]`.
    """
    files = sorted(
        ROOT / name
        for name in _tracked_files("*.md")
        if not any(part in SKIP_DIRS for part in PurePosixPath(name).parts)
    )
    assert len(files) >= 15, f"only {len(files)} first-party markdown files found — the scan is blind"
    return files


MARKDOWN = _first_party_markdown()


def _doc_mentions() -> list[tuple[Path, str]]:
    mentions = []
    for path in ROOT.rglob("*"):
        if path.suffix not in {".py", ".yml", ".yaml", ".toml", ".md"}:
            continue
        if any(part in SKIP_DIRS for part in path.parts) or path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in re.findall(r"docs/[A-Za-z0-9_.-]+\.md", text):
            mentions.append((path.relative_to(ROOT), match))
    return mentions


def test_markdown_local_links_resolve():
    broken = []
    for md in MARKDOWN:
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = target.split("#", 1)[0]
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue
            if not (md.parent / target).resolve().exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, "local links point at missing files:\n" + "\n".join(broken)


def test_doc_paths_mentioned_in_code_exist():
    missing = sorted({
        f"{rel} -> {mention}"
        for rel, mention in _doc_mentions()
        if not (ROOT / mention).exists()
    })
    assert not missing, "stale doc pointers:\n" + "\n".join(missing)


_QUOTED_SPAN = re.compile(r"“[^”]*”|\"[^\"]*\"|`[^`]*`")


def _uncited(line: str) -> str:
    """A banned word quoted as the thing being banned is a citation, not a promise.

    AGENTS.md has to write "non-custodial" to say it must not appear, and the root policy
    files it names are now inside the same scan. What the law therefore cannot see is a
    false claim written inside quotes — stated here rather than hidden behind a file
    exception list, which is the hole every earlier doc law in this file had."""
    return _QUOTED_SPAN.sub(" ", line)


def test_no_document_promises_a_custody_model_the_code_does_not_implement():
    """README and docs/ARCHITECTURE.md both printed “non-custodial” about the
    Smart Wallet while `SmartAccount.owner` is the relayer address derived from
    `HOT_WALLET_KEY` — the key that signs every UserOperation — and docs/SECURITY.md
    said the opposite in the same repository. A user picks a money product on the
    strength of that one word, so the word is measured against the code that
    decides who signs, not against how the feature is marketed."""
    source = (ROOT / "bot" / "smart_wallet.py").read_text(encoding="utf-8-sig")
    owner = re.search(r"def _owner\(\) -> str:.*?return (Web3\.to_checksum_address\(.*?\))\n",
                      source, re.S)
    assert owner, "smart_wallet._owner() moved somewhere this gate cannot read"
    operator_holds_the_keys = "HOT_WALLET_KEY" in owner.group(1)

    offenders = []
    for md in MARKDOWN:
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"non-?custodial", _uncited(line), re.I):
                offenders.append(f"{md.relative_to(ROOT)}:{number}")
    if operator_holds_the_keys:
        assert not offenders, f"{offenders} promise a non-custodial wallet the code does not give"
    else:  # the day a user key really signs, the docs have to say which model applies
        assert offenders, "the account is user-custodied now; say so where users read it"


def test_security_doc_still_states_the_custody_model_it_defends():
    """The other half: the honest sentence must not be deleted to make the
    contradiction disappear. docs/SECURITY.md is where the product says the hot
    wallet is custodial and why a multisig owner caps it."""
    security = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8-sig")
    assert "hot wallet is custodial by design" in security
    assert "multisig owner" in security


def test_the_audit_trail_keeps_the_properties_the_architecture_documents():
    """docs/ARCHITECTURE.md describes `tx_log` as a trail whose money is written
    once and whose only mutable fields are a withdrawal's stage and hash. That is
    a promise about SQL, so it is measured as SQL: no statement may rewrite
    amount/kind/counterparty, and no statement may delete a row. The wording and
    the code cannot now drift apart in either direction."""
    root = ROOT / "bot"
    updates, deletes = [], []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        for match in re.finditer(r"UPDATE tx_log\s+SET\s+(.*?)(?:WHERE|\Z)", text, re.S | re.I):
            columns = re.findall(r"\b([a-z_]+)\s*=", match.group(1))
            updates.append((path.relative_to(ROOT), columns))
        if re.search(r"DELETE\s+FROM\s+tx_log", text, re.I):
            deletes.append(path.relative_to(ROOT))
    assert not deletes, f"tx_log rows are deleted by {deletes}"
    forbidden = {"amount", "kind", "tg_id", "peer_tg_id", "created_at", "balance_after"}
    rewritten = [(where, cols) for where, cols in updates if forbidden & set(cols)]
    assert not rewritten, f"{rewritten} rewrite columns the audit trail promises to keep"
    assert updates, "no UPDATE tx_log at all — the gate above would pass on an empty scan"


def test_the_resolution_trust_model_is_described_as_the_code_implements_it():
    """The docs called on-chain resolution “trustless” while `bot/handlers/onchain.py`
    signs `owner_resolve` with `HOT_WALLET_KEY` whenever `ORACLE_PRIVATE_KEY` is
    unset — one key then proposes, vetoes and finalizes. The word is only allowed
    where the code makes it true, so the sentence is checked against the fallback
    it describes."""
    source = (ROOT / "bot" / "handlers" / "onchain.py").read_text(encoding="utf-8-sig")
    one_key_holds_both_roles = (
        "ORACLE_PRIVATE_KEY" in source and "owner_resolve" in source
        and "HOT_WALLET_KEY" in source
    )
    offenders = []
    for md in MARKDOWN:
        text = md.read_text(encoding="utf-8-sig", errors="replace")
        for match in re.finditer(r"trustless", text, re.I):
            # "Not trustless" is the honest sentence; the wrapped "Not\n  trustless"
            # is the same sentence, so the negation is read across the line break.
            if re.search(r"\bnot\s*$", text[max(0, match.start() - 24):match.start()], re.I):
                continue
            if any(span.start() <= match.start() < span.end()
                   for span in _QUOTED_SPAN.finditer(text)):
                continue  # the sentence names the ban, it does not make the claim
            offenders.append(f"{md.relative_to(ROOT)}:{text[:match.start()].count(chr(10)) + 1}")
    if one_key_holds_both_roles:
        assert not offenders, f"{offenders} call the resolution trustless; it is not"
    # whichever way the code moves, the oracle switch has to stay documented
    assert "ORACLE_PRIVATE_KEY" in (ROOT / "docs" / "ARCHITECTURE.md").read_text(
        encoding="utf-8-sig"), "the docs never say who the oracle key is"


def test_the_readme_module_tree_draws_every_branch():
    """The tree is the only map most readers get of the package layout, and one
    line there lost its elbow (`─ x402.py`), which no prose review sees. Every
    branch line has to start with the glyph that puts it in the tree."""
    tree = (ROOT / "README.md").read_text(encoding="utf-8-sig").splitlines()
    branches = [line for line in tree if line.startswith(("├", "└", "─", "│"))]
    assert branches, "no module tree found in README — the gate would prove nothing"
    malformed = [line for line in branches if not line.startswith(("├─ ", "└─ ", "│  "))]
    assert not malformed, f"{malformed[:3]} are drawn without their elbow"


def test_no_document_prints_an_address_the_tree_does_not_define():
    """A 40-hex in a document reads as a payment instruction, and this repo
    shipped two kinds of them: a hot-wallet address derived from the maintainer's
    own `.env` (true of no clone, and it stops being true the moment the key
    rotates), and CREATE2 factory addresses from one testnet deployment.

    Every address a document states has to exist in the tracked tree — code,
    contract, migration, manifest or template — so the docs can only repeat what
    the project actually defines, and a number nobody can re-derive is written as
    `<from the deploy output>` instead.
    """
    defined = ""
    for folder in ("bot", "web", "agent", "scripts", "deploy", "contracts", "alembic", "functions"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sol", ".json", ".js", ".sql", ".toml", ".yml", ".sh"}                     and "lib" not in path.parts:
                defined += path.read_text(encoding="utf-8-sig", errors="ignore")
    defined += (ROOT / ".env.example").read_text(encoding="utf-8-sig")
    known = {a.lower() for a in re.findall(r"0x[a-fA-F0-9]{40}", defined)}
    assert known, "no address found in the tree — the scan is blind"

    invented = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            for address in re.findall(r"0x[a-fA-F0-9]{40}", line):
                if address.lower() not in known:
                    invented.append(f"{path.relative_to(ROOT)}:{number} {address}")
            # A prefix is the same claim with less evidence: `0x862b...` read as a
            # payment instruction in three places while section 0.3 of the same file
            # explains why no address belongs in the doc, and no 40-hex in the tree
            # starts with it.
            for prefix in re.findall(r"0x([a-fA-F0-9]{3,})(?:\.\.\.|…)", line):
                if not any(a.startswith(prefix.lower()) for a in known):
                    invented.append(f"{path.relative_to(ROOT)}:{number} 0x{prefix}…")
    assert not invented, f"{invented} are addresses no file in the repo defines"


def test_the_readme_counts_the_suites_it_advertises():
    """The README advertises "14 end-to-end scenarios" and "13 TipBotVault
    tests" as proof of what is tested for real, and the fifteenth scenario landed
    while the sentence stayed. Both numbers are countable from the files they
    name, so there is no reason for either to be remembered."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    claims = {
        "end-to-end scenarios": ROOT / "tests" / "test_e2e.py",
        "TipBotVault tests": ROOT / "tests" / "test_vault.py",
    }
    for phrase, path in claims.items():
        measured = len(re.findall(r"^def test_", path.read_text(encoding="utf-8-sig"), re.M))
        stated = re.search(rf"(\d+) {re.escape(phrase)}\b", readme)
        assert stated, f"the README no longer advertises its {phrase}"
        assert int(stated.group(1)) == measured, (phrase, stated.group(1), measured)


ENUMERATED = ("bot", "bot/ledger", "bot/handlers", "bot/chain", "web", "contracts")
# These folders are described by their purpose in one line, not file by file —
# that is a choice, and the gate only requires the line to stay.
SUMMARISED = {"agent": "the autonomous market-maker",
              "scripts": "the operator and smoke scripts",
              "deploy": "the container entrypoints and the tunnel",
              "functions": "the Cloudflare Pages function"}


def test_the_readme_tree_names_every_module_of_what_it_enumerates():
    """The Architecture block lists some folders file by file and summarizes
    others. Silence inside an enumerated folder is the failure: five modules were
    missing when this was measured — `bot/fees.py` (the file that decides how
    every amount in the product is printed), `bot/tx_view.py`, `ledger/_ai.py`,
    `ledger/_audit.py`, `handlers/admin.py` — and a reader looking for the money
    notation would never find it.

    The reverse direction is checked too, because a tree entry for a deleted file
    sends a reader to a module that does not exist.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    block = text.split("## Architecture", 1)[1].split("```", 2)[1]
    named = set(re.findall(r"([A-Za-z0-9_]+\.(?:py|sol))", block))
    assert len(named) > 40, f"only {len(named)} names parsed — the block moved"

    missing = []
    for rel in ENUMERATED:
        folder = ROOT / rel
        files = sorted({p.name for p in folder.glob("*.py")} | {p.name for p in folder.glob("*.sol")})
        assert files, f"{rel} has no modules — the scan is blind"
        missing += [f"{rel}/{n}" for n in files if n not in named]
    assert not missing, f"{missing} exist, the tree does not name them"

    on_disk = {p.name for p in ROOT.rglob("*")
               if p.suffix in {".py", ".sol"} and p.is_file()
               and not any(x in p.parts for x in {".git", ".venv", "node_modules", "lib", "versions"})}
    phantom = sorted(n for n in named if n not in on_disk)
    assert not phantom, f"{phantom} are in the tree and nowhere else"

    for rel, why in SUMMARISED.items():
        assert f"{rel}/" in block, f"{rel} ({why}) vanished from the tree"


# Where an operator script may be named: prose a human reads, the CI jobs, the
# container image and the compose files it is invoked from.
SCRIPT_NAMING_SOURCES = [ROOT / "AGENTS.md", ROOT / "Dockerfile",
                        ROOT / "docker-compose.yml", ROOT / "docker-compose.ci.yml",
                        *sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
OPERATOR_SCRIPT_DIRS = ("scripts", "deploy")


def test_every_operator_script_is_named_by_something_a_human_reads():
    """A procedure no doc, CI job, image or calling script names is a procedure that
    exists only for whoever wrote it.

    Two scripts in this repo wrote the same `tipbot_*.sql.gz` archives: one was
    described in the operations guide and the other was the one the cron line in the
    header comment actually pointed at. Which one a recovery follows depends on who
    is reading, and the undocumented one had the weaker verification.
    """
    sources = [path for path in [*MARKDOWN, *SCRIPT_NAMING_SOURCES] if path.exists()]
    assert len(sources) >= 6, f"only {len(sources)} naming sources found — the scan is blind"
    named = "\n".join(p.read_text(encoding="utf-8-sig", errors="replace") for p in sources)

    shells = sorted(p for d in OPERATOR_SCRIPT_DIRS for p in (ROOT / d).glob("*.sh"))
    assert len(shells) >= 6, f"only {len(shells)} operator scripts found — the scan is blind"
    sibling_text = {p: p.read_text(encoding="utf-8-sig", errors="replace") for p in shells}

    unnamed = []
    for script in shells:
        if script.name in named:
            continue
        # Sourced by another script: reachable through that caller, whose name is checked.
        if any(script.name in text for other, text in sibling_text.items() if other != script):
            continue
        unnamed.append(f"{script.parent.name}/{script.name}")
    assert not unnamed, (
        f"{unnamed} are executable procedures named by no doc, CI job, image or script"
    )


def test_every_operator_script_reaches_the_server_that_runs_it():
    """`scripts/*` is gitignored with a hand-maintained whitelist of the files that
    count as production, so a new library under that folder is invisible to git
    until someone remembers to name it.

    `scripts/pg_env.sh` — the PATH probe and the archive validator that all three
    database scripts `source` — was not on that whitelist. A clone that follows the
    documented backup procedure would die on the `source` line, which under
    `set -euo pipefail` is the first executable statement in every one of them.
    """
    tracked = _tracked_files("scripts", "deploy")
    on_disk = {
        p.relative_to(ROOT).as_posix()
        for folder in OPERATOR_SCRIPT_DIRS
        for p in (ROOT / folder).glob("*.sh")
    }
    assert len(on_disk) >= 6, f"only {len(on_disk)} operator scripts found — the scan is blind"
    missing = sorted(on_disk - tracked)
    assert not missing, (
        f"{missing} exist on this machine and in no clone: the scripts that source them "
        "fail on a server that was set up from git"
    )


# --- the compose contract the operator docs have to obey -------------------

_COMPOSE_MENTION = re.compile(r"docker[- ]compose", re.IGNORECASE)
_COMPOSE_INLINE = re.compile(r"`([^`]*docker[- ]compose [^`]*)`", re.IGNORECASE)
# Subcommands whose positional arguments are compose service names, and how many
# of them a service occupies (`exec`/`run` take one, then a command of their own).
_COMPOSE_SERVICE_ARGS = {"up": "many", "logs": "many", "restart": "many", "stop": "many",
                         "start": "many", "ps": "many", "build": "many", "scale": "many",
                         "exec": "one", "run": "one"}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*")


def _compose_stack() -> tuple[set[str], dict[str, str]]:
    """Services and the credentials the stack itself creates, read off the file
    `docker compose up` consumes."""
    services: set[str] = set()
    env: dict[str, str] = {}
    in_services = False
    for line in (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
        elif in_services and re.match(r"^\S", line):
            in_services = False
        elif in_services:
            named = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if named:
                services.add(named.group(1))
        knob = re.match(r"^\s+(POSTGRES_USER|POSTGRES_DB):\s*(\S+)\s*$", line)
        if knob:
            env[knob.group(1)] = knob.group(2)
    assert services, "docker-compose.yml defines no services — the scan is blind"
    assert {"POSTGRES_USER", "POSTGRES_DB"} <= set(env), (
        f"docker-compose.yml no longer states the role and database it creates, only read {env}"
    )
    return services, env


def _compose_commands(path: Path) -> list[tuple[int, str]]:
    """Command lines only: fenced blocks, and inline spans bounded by backticks.

    Prose is deliberately excluded — the sentence "run `docker compose up -d` on the
    VPS" has the words "on" and "the" after the closing backtick, and a scanner that
    reads them as arguments would report the doc wrong and the law would be tuned
    until it stopped meaning anything.
    """
    text = path.read_text(encoding="utf-8-sig")
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            continue
        for span in _COMPOSE_INLINE.findall(line):
            found.append((number, span))
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and _COMPOSE_MENTION.search(line):
            found.append((number, line))
    return found


def _command_tail(command: str) -> str:
    """What follows the words `docker compose`, up to the first pipe, redirect or
    comment — the arguments of this command and nothing that runs after it.

    The cut happens **after** the mention, not on the whole line:
    `ssh <vps> "docker compose ps"` would otherwise be trimmed to `ssh <vps`.
    """
    mention = _COMPOSE_MENTION.search(command)
    if not mention:
        return ""
    return re.split(r"[#|<>]", command[mention.end():], maxsplit=1)[0]


def _service_args(command: str) -> list[str]:
    positional = [t.strip("`\"',;:(){}[]") for t in _command_tail(command).split()]
    subcommand = next((t for t in positional if t and not t.startswith("-")), "")
    shape = _COMPOSE_SERVICE_ARGS.get(subcommand)
    if not shape:
        return []
    services: list[str] = []
    seen_subcommand = False
    for token in positional:
        if not token or token.startswith("-"):
            continue
        if not seen_subcommand:
            seen_subcommand = True
            continue
        if not _IDENTIFIER.fullmatch(token):
            continue  # a path, a number, a flag value — not a service name
        services.append(token)
        if shape == "one":
            break
    return services


def test_every_documented_compose_command_names_a_service_the_stack_defines():
    """`docs/DEPLOY.md` told the operator to run `docker compose logs -f backup` and
    `docker compose exec backup ls /backups` for a service named `backup`, and to
    expect a volume `backups_data` — neither exists in `docker-compose.yml`, whose
    whole stack is `db`, `app` and `cloudflared` with one volume, `pgdata`.

    The commands in a doc are the interface: an operator in an incident types them,
    and a compose command that names a phantom service reads as the stack being
    broken rather than the doc.
    """
    services, _ = _compose_stack()
    offenders = []
    for path in MARKDOWN:
        for number, command in _compose_commands(path):
            for service in _service_args(command):
                if service not in services:
                    offenders.append(f"{path.relative_to(ROOT)}:{number} → {service!r}")
    assert not offenders, (
        f"{offenders} name a compose service the stack does not define "
        f"(defined: {sorted(services)})"
    )


def test_every_documented_psql_command_uses_the_role_the_stack_creates():
    """`POSTGRES_USER`/`POSTGRES_DB` in `docker-compose.yml` are `tippy`/`tippy`, and
    `docs/INCIDENT_RESPONSE.md` says so — while `docs/DEPLOY.md` told the operator to
    dump and restore `-U tipbot -d tipbot`, which is the **test** database's role
    (`tests/conftest.py`). The documented recovery command would have answered
    `database "tipbot" does not exist` in the middle of an incident."""
    _, env = _compose_stack()
    offenders = []
    for path in MARKDOWN:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not re.search(r"\b(psql|pg_dump|pg_isready)\b", line):
                continue
            user = re.search(r"\s-U\s+([A-Za-z0-9_]+)", line)
            database = re.search(r"\s-d\s+([A-Za-z0-9_]+)", line)
            if user and user.group(1) != env["POSTGRES_USER"]:
                offenders.append(f"{path.relative_to(ROOT)}:{number} -U {user.group(1)}")
            if database and database.group(1) != env["POSTGRES_DB"]:
                offenders.append(f"{path.relative_to(ROOT)}:{number} -d {database.group(1)}")
    assert not offenders, (
        f"{offenders} contradict the stack, which creates "
        f"-U {env['POSTGRES_USER']} -d {env['POSTGRES_DB']}"
    )


_RAW_DOCKER = re.compile(r"\bdocker\s+(logs|stop|start|restart|exec|run|ps|rm|inspect|build)\b")


def test_no_document_addresses_a_container_beyond_docker_compose():
    """`docker stop tipbot-bot` and `docker logs tipbot` name containers the stack
    never creates: `docker-compose.yml` sets no `container_name`, so the real names
    are `<project>-<service>-<N>` and depend on the directory the stack was started
    in. Those two lines were step 2 of the hot-key rotation and step 3 of the
    deposit-lag incident runbook — the operator would have met `No such container`
    exactly when following a procedure matters."""
    offenders = []
    for path in MARKDOWN:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            match = _RAW_DOCKER.search(line)
            if match:
                offenders.append(f"{path.relative_to(ROOT)}:{number} docker {match.group(1)}")
    assert not offenders, (
        f"{offenders} reach for a container name; write the command as "
        "`docker compose <verb> <service>` so it works from any project directory"
    )


# ---------- the root policy files, which GitHub renders and no law used to read ----------


_TEMPLATE_FILL = re.compile(r"\[(?:[A-Z ]*\b(?:INSERT|YOUR|TBD|FIXME|REPLACE_WITH)[A-Z ]*)\]")
_CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:t\.me|x\.com|twitter\.com)/[A-Za-z0-9_]+")
POLICY_PAIRS = ("SECURITY.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md")


def test_no_policy_document_still_carries_the_template_blank_it_was_copied_from():
    """GitHub shows the root `CODE_OF_CONDUCT.md` to every contributor, and that file is
    the Contributor Covenant text with its fill-in-the-blank marker still in the
    Enforcement section: `[INSERT CONTACT METHOD]`. A reader who wants to report a
    harasser is handed a bracket, while `docs/CODE_OF_CONDUCT.md` names the channels the
    maintainer answers.

    `[FEATURE]` in an issue template and `[ERROR]/[WARN]` in a command comment are real
    output labels, not blanks — only the template's own vocabulary counts."""
    offenders = []
    for path in MARKDOWN:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if _TEMPLATE_FILL.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number} {line.strip()}")
    assert not offenders, f"{offenders} still ask the reader to fill in a template blank"


def test_the_policy_at_the_repo_root_offers_the_channel_the_longer_one_does():
    """Two files, one policy, two contacts — and the root file is the one GitHub puts on
    the Security page, so it is what a researcher reads first. Root `SECURITY.md` sent
    vulnerability reports to `security@ezoffer.ru`, a domain that appears nowhere else in
    this repository, while `docs/SECURITY.md` named the Telegram and X accounts the
    maintainer actually answers. A USDC custody bug mailed there is a bug nobody received,
    and root `CODE_OF_CONDUCT.md` named no channel a reader could use at all.

    The rule is deliberately asymmetric: a short root summary of a long guide is fine, a
    root summary that sends a reader somewhere the real policy does not know is not. Where
    reporting *is* the document's purpose — security and conduct — the root copy has to
    give the route, not just avoid a wrong one."""
    offenders = []
    for name in POLICY_PAIRS:
        root_copy, docs_copy = ROOT / name, ROOT / "docs" / name
        if not (root_copy.is_file() and docs_copy.is_file()):
            continue
        root_channels = set(_CONTACT.findall(root_copy.read_text(encoding="utf-8-sig")))
        docs_channels = set(_CONTACT.findall(docs_copy.read_text(encoding="utf-8-sig")))
        if root_channels - docs_channels:
            offenders.append(
                f"{name}: the root copy sends readers to {sorted(root_channels - docs_channels)}, "
                "which the policy in docs/ never mentions")
        if name != "CONTRIBUTING.md" and not (root_channels & docs_channels):
            offenders.append(f"{name}: neither copy names a channel a reader can use")
    assert not offenders, "\n".join(offenders)


def _ci_gate_commands() -> tuple[frozenset[str], str]:
    """The ruff directory list and the pytest invocation CI actually runs."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ruff = re.findall(r"^\s*run:\s*(?:python -m )?ruff check (\S.*?)\s*$", text, re.M)
    pytest_runs = re.findall(r'^\s*run:\s*(python -m pytest .*?)\s*$', text, re.M)
    assert len(ruff) == 1, f"the lint step CI runs is not the single line this law reads: {ruff}"
    assert len(pytest_runs) == 1, f"CI does not answer with exactly one pytest run: {pytest_runs}"
    return frozenset(ruff[0].split()), pytest_runs[0]


def test_every_documented_gate_command_is_the_command_ci_runs():
    """A contributor runs what the contributing guide says. `docs/CONTRIBUTING.md` told
    them `python -m ruff check bot web tests scripts` — three of the seven directories CI
    lints were missing, the same blind spot that let `alembic/` rot — and
    `python -m pytest tests -q`, which selects the 171 `production` tests that probe the
    deployed site and belong to another workflow. Following that guide produces a red run
    nobody caused and a green lint over unlinted code.

    The lint directories are compared to CI's exactly. A pytest command has to state which
    half of the suite it means with an explicit `-m`, which lets a documented targeted-file
    run and the `production` collection line live while a blind whole-suite run cannot:
    prose that names no directories at all (`python -m pytest`) is a mention, not a
    command, and is not read as one."""
    directories, pytest_run = _ci_gate_commands()
    marker = re.search(r'-m\s+"([^"]+)"', pytest_run)
    assert marker, f"CI's pytest run names no marker expression: {pytest_run}"
    offenders = []
    for path in MARKDOWN:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            lint = re.search(r"ruff check ([A-Za-z0-9_ .]+)", line)
            if lint and frozenset(lint.group(1).split()) != directories:
                offenders.append(f"{path.relative_to(ROOT)}:{number} ruff check {lint.group(1).strip()}")
            test = re.search(r"python -m pytest (\S.*)", line)
            if test and not re.search(r"-m\s+[\"']?[A-Za-z0-9 \"'()–-]+", test.group(1)):
                offenders.append(f"{path.relative_to(ROOT)}:{number} python -m pytest {test.group(1).strip()}")
    assert not offenders, (
        f"{offenders} differ from what CI runs — the directories are {sorted(directories)} "
        f'and the suite selection is {pytest_run}'
    )


def _ports_the_stack_answers_on() -> set[str]:
    """The Postgres wire port read off a connection string, plus `WEB_PORT`."""
    authority = [
        ROOT / ".env.example",
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.ci.yml",
        ROOT / "tests" / "conftest.py",
        ROOT / "bot" / "config.py",
    ]
    ports: set[str] = set()
    web_port: set[str] = set()
    for path in authority:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        ports |= set(re.findall(r"postgresql://[^\s\"']*?:(\d{2,5})/", text))
        web_port |= set(re.findall(r'WEB_PORT\D{0,20}?(\d{2,5})', text))
    assert ports and web_port, f"no Postgres port ({sorted(ports)}) or WEB_PORT ({sorted(web_port)}) derived"
    return ports | web_port


def test_no_document_tells_a_human_to_reach_a_port_the_stack_does_not_answer_on():
    """`docs/CONTRIBUTING.md` promised `docker compose up -d db` puts PostgreSQL on
    `localhost:5433`. Neither compose file publishes a single port — the database lives on
    the compose network and the tests connect to a host server on 5432 — so the port is
    invented twice over: wrong number, wrong mechanism. `docs/OPERATIONS.md` likewise had
    Prometheus scrape `localhost:8080` while `/metrics` is a route on `WEB_PORT` (8000).
    """
    live = _ports_the_stack_answers_on()
    offenders = []
    for path in MARKDOWN:
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            for port in re.findall(r"(?:localhost|127\.0\.0\.1):(\d{2,5})", line):
                if port not in live:
                    offenders.append(f"{path.relative_to(ROOT)}:{number} :{port} (live {sorted(live)})")
    assert not offenders, f"{offenders} name a port nothing in this tree listens on"


RUNTIME_PACKAGES = ("bot", "web", "agent", "deploy", "scripts")


def _split_on_commas(text: str) -> list[str]:
    """Split a SQL column/value list on its top-level commas only.

    `COALESCE(status, 'done')` and `substring(note from … for …)` are one value each;
    splitting them by `text.split(",")` moves every later column one slot right and
    reads a status string as the kind.
    """
    parts: list[str] = []
    buf, depth, quote = "", 0, None
    for char in text:
        if quote:
            buf += char
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            buf += char
        elif char == "(":
            depth += 1
            buf += char
        elif char == ")":
            depth -= 1
            buf += char
        elif char == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += char
    parts.append(buf)
    return [part.strip() for part in parts]


def _sql_argument(node: ast.Call) -> str | None:
    """The SQL of a call, when its first argument is one string (implicit concatenation
    folds into a single Constant) or an f-string with no interpolated SQL."""
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.JoinedStr):
        return "".join(
            value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else "?"
            for value in first.values
        )
    return None


def _kinds_written_by(sql: str) -> list[str]:
    """The `kind` values one `INSERT INTO tx_log` writes, found by column position.

    A `'fee'` in the note slot, inside `COALESCE`, or in a `WHERE kind = 'fee'` guard is
    not a kind being written; only the value the column list puts in the `kind` slot is.
    A parameterised kind comes back as `<expression>` so a caller naming it is still
    measured, and a column/value count mismatch comes back as `<arity mismatch>` rather
    than silently reading the wrong slot."""
    flat = " ".join(sql.split())
    head = re.match(r"INSERT\s+INTO\s+tx_log\s*\(([^)]*)\)", flat, re.I)
    if head is None:
        return []
    columns = _split_on_commas(head.group(1))
    rest = flat[head.end():].lstrip()
    verb = re.match(r"(VALUES|SELECT)\b", rest, re.I)
    if verb is None:
        return []
    body = rest[verb.end():].strip()
    if verb.group(1).lower() == "values":
        if not body.startswith("("):
            return []
        depth, quote, end = 1, None, len(body)
        for index, char in enumerate(body[1:], 1):
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        values = _split_on_commas(body[1:end])
    else:
        stop = re.search(r"\bWHERE\b", body, re.I)
        values = _split_on_commas(body[: stop.start()] if stop else body)
    if len(values) != len(columns):
        return ["<arity mismatch>"]
    kinds = []
    for index, column in enumerate(columns):
        if column.lower() != "kind":
            continue
        literal = re.fullmatch(r"'([a-z_0-9]+)'", values[index])
        kinds.append(literal.group(1) if literal else f"<{values[index]}>")
    return kinds


def _tx_log_writers() -> list[tuple[str, int, int, list[str]]]:
    """Every runtime statement that writes a `tx_log` row, with the kinds its SQL names.

    `UPDATE tx_log` is deliberately absent: `test_the_audit_trail_keeps_the_properties_
    the_architecture_documents` already forbids any UPDATE from touching `kind`, so an
    INSERT scan sees every kind this database ever holds. `credit(...)` calls join the
    list because that one method takes the kind as a parameter and writes it through the
    `%s` slot the INSERT above can only call `<kind>`.
    """
    writers = []
    for package in RUNTIME_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8-sig")
            rel = path.relative_to(ROOT).as_posix()
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                sql = _sql_argument(node)
                if sql and "tx_log" in sql and "INSERT" in sql.upper():
                    kinds = _kinds_written_by(sql)
                    if kinds:
                        writers.append((rel, node.lineno, node.end_lineno or node.lineno, kinds))
                function = node.func
                name = getattr(function, "attr", None) or getattr(function, "id", None)
                if name != "credit":
                    continue
                literal = None
                if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
                    literal = node.args[2].value
                for keyword in node.keywords:
                    if keyword.arg == "kind" and isinstance(keyword.value, ast.Constant):
                        literal = keyword.value.value
                if isinstance(literal, str):
                    writers.append((rel, node.lineno, node.end_lineno or node.lineno, [literal]))
    return writers


def test_the_whitepaper_cites_the_statements_that_earn_the_platform_money():
    """docs/WHITEPAPER.md §7.1 promises three things a change can break in silence:
    that exactly one revenue line exists (the withdrawal commission), that the only
    `kind='fee'` rows are the two statements it names by file and line, and that the
    win commission is a creator's income rather than the platform's. Every clause was
    true when the section was written and is measured by nothing — the platform could
    book a cut on a paywall sale tomorrow and §7.1 would keep printing its table of
    «no platform cut at all»."""
    paper = (ROOT / "docs" / "WHITEPAPER.md").read_text(encoding="utf-8-sig")
    start = paper.find("### 7.1")
    assert start != -1, "docs/WHITEPAPER.md lost the §7.1 heading this gate reads"
    tail = paper[start:]
    following = re.search(r"\n#{2,3} ", tail[8:])
    section = tail[: 8 + following.start()] if following else tail

    writers = _tx_log_writers()
    assert len(writers) >= 30, f"the tree writes {len(writers)} tx_log rows — the reader is blind"
    broken = [f"{rel}:{first}" for rel, first, _last, kinds in writers if "<arity mismatch>" in kinds]
    assert not broken, f"{broken} write tx_log with a column/value count that does not line up"

    fee_sites = [site for site in writers if "fee" in site[3]]
    assert fee_sites, "no statement writes kind='fee': §7.1 cites revenue that does not exist"

    cited = re.findall(r"`([A-Za-z0-9_.\-/]+\.py):(\d+)`", section)
    assert cited, "§7.1 names no file:line, so the claim it was made against is no longer written down"
    matched, dangling = set(), []
    for rel, line in cited:
        winner = next(
            (
                index
                for index, (file, first, last, kinds) in enumerate(fee_sites)
                if file == rel and first <= int(line) <= last and "fee" in kinds
            ),
            None,
        )
        if winner is None:
            dangling.append(f"{rel}:{line}")
        else:
            matched.add(winner)
    assert not dangling, (
        f"{dangling} is not a line of a statement writing kind='fee' "
        f"(the statements start at {[(file, first) for file, first, _l, _k in fee_sites]})"
    )
    uncited = [f"{file}:{first}" for index, (file, first, _last, _kinds) in enumerate(fee_sites) if index not in matched]
    assert not uncited, f"{uncited} take platform revenue that §7.1 never lists"

    modules = {file for file, _first, _last, _kinds in fee_sites}
    assert modules == {"bot/ledger/_withdraw.py"}, (
        f"the platform keeps money in {sorted(modules)}; §7.1 promises the withdrawal path and nothing else"
    )

    creator_sites = [site for site in writers if "creator_income" in site[3]]
    assert {file for file, *_rest in creator_sites} == {"bot/ledger/_bets.py", "bot/ledger/_markets.py"}, (
        "the win commission is supposed to land on the creator in both engines, and these are the writers: "
        f"{sorted({file for file, *_rest in creator_sites})}"
    )

    quoted = re.findall(r"`([A-Za-z0-9_.\-/]+\.py)`\s*\(`([^`]+)`\)", section)
    assert quoted, "§7.1 quotes no expression, so the creator-income half of the claim is unmeasured"
    for rel, expression in quoted:
        source = (ROOT / rel).read_text(encoding="utf-8-sig")
        assignments = {
            " ".join(ast.get_source_segment(source, node).split())
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assign) and ast.get_source_segment(source, node)
        }
        # Compared as a whole statement: `creator_income = fee_sum` is a substring of the
        # real line and would satisfy a containment check while describing a ledger that
        # no longer hands the creator the rounding dust.
        wanted = " ".join(expression.split())
        if wanted in assignments:
            continue
        name = wanted.split("=")[0].strip()
        now = sorted(item for item in assignments if item.split("=")[0].strip() == name)
        raise AssertionError(f"§7.1 quotes {expression!r} from {rel}; that file assigns {now or 'nothing'} to {name}")


_FEE_NOUN = re.compile(r"\bfee\b|\bfees\b|commission|комисси", re.I)
_PERCENT = re.compile(r"(?<![\d.])(\d+(?:[.,]\d+)?)\s?%")
_WITHDRAWAL_WORDS = re.compile(r"withdraw\w*|вывод", re.I)
_WIN_WORDS = re.compile(r"\bwins?\b|\bwinn?ers?\b|winning|profit|выигрыш", re.I)
_PROPOSAL_WORDS = re.compile(r"proposed|предлож|lever|рычаг|\bwould\b|not yet|не реализ", re.I)
_FILE_SPAN = re.compile(r"`[^`\n]*\.(?:py|js|html|css|md|sql|sh|yml|yaml|toml|json)[^`\n]*`")
_FEE_WINDOW = 60  # characters on each side that still count as "says the fee out loud"


def _prose(text: str) -> str:
    """The sentences a human reads, with file paths and fenced blocks blanked out.

    A span naming a file is not a sentence about that file: `bot/ledger/_withdraw.py`
    three lines above a 2% made the withdrawal fee look like the fee being stated. A
    fenced block is code an operator copies into `.env`, and `tests/test_env_contract.py`
    already reads those numbers as configuration; treating it as prose both invented
    claims and — because a backtick inside a fence has no partner on its own line —
    swallowed whole paragraphs and moved every line number after it.
    """
    kept, inside = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            kept.append("")
            continue
        kept.append("" if inside else _FILE_SPAN.sub(" ", line))
    return "\n".join(kept)


def _fee_rate_claims() -> tuple[list[tuple[str, int, str, str]], list[str]]:
    """Every percentage a document states next to a fee word, and which fee it names.

    Command spans (``/withdraw <address> <amount>``) survive the blanking: they are the
    mechanism the reader is being told about, and they are how README's table row names
    the withdrawal fee at all."""
    claims, loose = [], []
    for path in MARKDOWN:
        text = _prose(path.read_text(encoding="utf-8-sig", errors="replace"))
        rel = path.relative_to(ROOT).as_posix()
        for match in _PERCENT.finditer(text):
            start = max(0, match.start() - _FEE_WINDOW)
            near = text[start: match.end() + _FEE_WINDOW]
            if not _FEE_NOUN.search(near):
                continue
            line = text[: match.start()].count("\n") + 1
            if _PROPOSAL_WORDS.search(near):
                continue  # a rate in a proposal is not a promise this build keeps
            offset = match.start() - start
            families = []
            for pattern, name in ((_WITHDRAWAL_WORDS, "withdrawal"), (_WIN_WORDS, "win")):
                best = min((abs(found.start() - offset) for found in pattern.finditer(near)), default=None)
                if best is not None:
                    families.append((best, name))
            families.sort()
            if not families:
                loose.append(f"{rel}:{line} {match.group(0)} in {near.strip()[:120]!r}")
                continue
            if len(families) > 1 and families[1][0] - families[0][0] <= 8:
                loose.append(f"{rel}:{line} {match.group(0)} names both fees in {near.strip()[:120]!r}")
                continue
            claims.append((rel, line, match.group(1), families[0][1]))
    return claims, loose


def test_the_positional_reader_reads_the_kind_from_the_column_that_holds_it():
    """`_kinds_written_by` is the instrument the revenue law above measures with, so it is
    tested against SQL that does not look like the two statements in `bot/ledger/_withdraw.py`:
    a `fee` sitting in a note slot is not a kind, a reordered column list still has to
    resolve, a `SELECT … WHERE` insert is read from the projection and not from the guard,
    and a count that does not line up is reported rather than guessed at."""
    assert _kinds_written_by(
        "INSERT INTO tx_log (kind, tg_id, amount, note) VALUES ('fee', %s, %s, 'withdraw_id=12')"
    ) == ["fee"]
    assert _kinds_written_by(
        "INSERT INTO tx_log (tg_id, note, kind) VALUES (%s, 'fee', 'tip')"
    ) == ["tip"], "a note that says fee is not a platform fee"
    assert _kinds_written_by(
        "INSERT INTO tx_log (kind, tg_id) SELECT 'withdraw', %s "
        "WHERE (SELECT COUNT(*) FROM tx_log WHERE kind = 'fee') < 5"
    ) == ["withdraw"], "the kind comes from the projection, not from the guard"
    assert _kinds_written_by("INSERT INTO tx_log (kind, amount) VALUES (%s, %s)") == ["<%s>"]
    assert _kinds_written_by("INSERT INTO tx_log (kind, amount) VALUES (%s)") == ["<arity mismatch>"]
    assert _kinds_written_by("INSERT INTO audit_logs (kind) VALUES ('fee')") == []


def test_no_document_states_a_fee_rate_that_is_not_the_rate_that_fee_carries():
    """`bot/fees.py` is one formula and `bot/config.py` one pair of rates, but a page can
    still print a number: README promises «2% fee on net profit», docs/USER_GUIDE.md «a 1%
    fee» and docs/DEPLOY.md tells an operator which knobs the defaults are. Until now only
    four hand-picked files were read, and only for whether the number was one of the two —
    a document could call the withdrawal commission 2% and pass. Here every tracked
    markdown is read, and each percentage is judged against the fee its own sentence names."""
    from bot import fees

    rates = {"withdrawal": fees.withdraw_fee_pct_label(), "win": fees.win_fee_pct_label()}
    claims, loose = _fee_rate_claims()
    assert len(claims) >= 10, f"only {len(claims)} documented fee rates were read — the scan is blind"

    wrong = []
    for rel, line, value, family in claims:
        stated = Decimal(value.replace(",", "."))
        if stated != Decimal(rates[family]):
            wrong.append(f"{rel}:{line} states the {family} fee as {value}%, the code charges {rates[family]}%")
    assert not wrong, "\n".join(wrong)

    families = {family for _rel, _line, _value, family in claims}
    assert families == {"withdrawal", "win"}, (
        f"the documents name only {sorted(families)} of the two rates the code charges"
    )
    assert not loose, f"{loose} state a fee percentage without naming which fee it is"

