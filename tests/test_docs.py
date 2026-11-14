"""Documentation links must point at files that exist.

A README link to a deleted chapter 404s for every reader, and a source comment
pointing at a documentation file that was never committed sends the next
maintainer hunting for nothing (one such pointer lived in bot/base.py until
this gate found it). Both are cheap to check from the tree.
"""

import re
import subprocess
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

