"""Documentation links must point at files that exist.

A README link to a deleted chapter 404s for every reader, and a source comment
pointing at a documentation file that was never committed sends the next
maintainer hunting for nothing (one such pointer lived in bot/base.py until
this gate found it). Both are cheap to check from the tree.
"""

import ast
import importlib.util
import json
import re
import subprocess
from decimal import Decimal, InvalidOperation
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
# Both extensions: the law used to glob `*.sh` only, so every Python procedure under
# those folders — the deploys, the smoke tests, the tunnel — was outside it by suffix.
OPERATOR_SCRIPT_SUFFIXES = frozenset({".sh", ".py"})


def _operator_scripts() -> list[Path]:
    return sorted(
        p for folder in OPERATOR_SCRIPT_DIRS
        for p in (ROOT / folder).iterdir()
        if p.is_file() and p.suffix in OPERATOR_SCRIPT_SUFFIXES
    )


def _reached_by_a_sibling(script: Path, texts: dict[Path, str]) -> str | None:
    """Which sibling can bring this script up: a shell that names the file, or a
    Python module whose import path mentions it (`import tunnel` reaches
    `deploy/tunnel.py` the way `source pg_env.sh` reaches that library)."""
    for other, text in texts.items():
        if other == script:
            continue
        if script.name in text:
            return other.name
        if script.suffix == ".py":
            stem = script.stem
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and re.search(
                    rf"(^|[.\s]){re.escape(stem)}($|[.\s])", stripped
                ):
                    return other.name
    return None


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

    scripts = _operator_scripts()
    assert len(scripts) >= 20, f"only {len(scripts)} operator scripts found — the scan is blind"
    assert {p.suffix for p in scripts} == OPERATOR_SCRIPT_SUFFIXES, (
        f"{sorted({p.suffix for p in scripts})} — the scan lost one of the two extensions"
    )
    texts = {p: p.read_text(encoding="utf-8-sig", errors="replace") for p in scripts}

    unnamed = []
    for script in scripts:
        if script.name in named:
            continue
        # Reachable through a caller that is itself checked.
        reached = _reached_by_a_sibling(script, texts)
        if reached:
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
    on_disk = {p.relative_to(ROOT).as_posix() for p in _operator_scripts()}
    assert len(on_disk) >= 20, f"only {len(on_disk)} operator scripts found — the scan is blind"
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
    """The Postgres wire port read off a connection string, `WEB_PORT`, and every
    loopback port a tracked `deploy/` program binds.

    The third source is what makes `127.0.0.1:20241` a legal thing to tell an
    operator: cloudflared's metrics server is a port this host answers on because
    the script that starts it says so, and a document that reads it off the
    running machine is reading the same script. A deploy program that stops
    binding a port takes it out of the documents with it."""
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
    bind: set[str] = set()
    for name in sorted(_tracked_files("deploy")):
        path = ROOT / name
        if path.suffix.lower() not in {".py", ".ps1", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        bind |= set(re.findall(r"(?:127\.0\.0\.1|localhost):(\d{2,5})\b", text))
    assert bind, "no tracked deploy/ program binds a loopback port — the third source went blind"
    # A block of replicas derived from a base and a count is a set of ports this host
    # answers on too. `deploy/watchdog.ps1` formats `127.0.0.1:{0}` from
    # `$MetricsPortBase`, so the literal scan above sees only the base, and a runbook
    # that tells an operator to probe the second replica — which is the entire point of
    # the pair — would be refused for stating the truth.
    replica_ports: set[str] = set()
    for name in sorted(_tracked_files("deploy")):
        path = ROOT / name
        if path.suffix.lower() != ".ps1":
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        base = re.search(r"(?m)^\s*\$MetricsPortBase\s*=\s*(\d{2,5})\s*$", text)
        count = re.search(r"(?m)^\s*\$ConnectorReplicas\s*=\s*(\d{1,2})\s*$", text)
        if base and count:
            replica_ports |= {
                str(port) for port in range(int(base.group(1)), int(base.group(1)) + int(count.group(1)))
            }
    assert replica_ports, (
        "no deploy/ script derives a connector replica block — the reader that "
        "legitimises the metrics ports in the docs went blind"
    )
    return ports | web_port | bind | replica_ports


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


# --- the pictures the documents ship -------------------------------------------

SHOTS_DIR = ROOT / "docs" / "screenshots"
SHOT_SCRIPT = ROOT / "scripts" / "make_docs_screenshots.py"


def _shot_script():
    """`scripts/make_docs_screenshots.py` loaded as a module instead of reimplemented here.

    A law with its own parser would go on certifying pictures after someone changed what the
    generator reads — the gate would stay green while nothing in the tree could draw the image
    it just approved. So every check below calls the functions that wrote the chunk it reads."""
    if not SHOT_SCRIPT.is_file():
        pytest.fail(
            f"{SHOT_SCRIPT.relative_to(ROOT).as_posix()} is gone: the docs images carry a stamp "
            "this tree cannot compute, so they can neither be refreshed nor measured"
        )
    spec = importlib.util.spec_from_file_location("make_docs_screenshots", SHOT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pictures() -> dict[str, tuple[tuple[int, int], dict[str, str]]]:
    """Every PNG in `docs/screenshots`, as its pixel size and its text chunks.

    Pillow is a declared runtime dependency (`requirements.txt`), so a missing Pillow is a
    broken environment rather than a reason to skip: skipping is the one outcome that lets an
    unread picture through."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - the gate means nothing without the reader
        pytest.fail(f"Pillow is not importable, so no docs screenshot can be read: {exc}")
    found: dict[str, tuple[tuple[int, int], dict[str, str]]] = {}
    for path in sorted(SHOTS_DIR.glob("*.png")):
        image = Image.open(path)
        try:
            image.load()
            found[path.name] = (image.size, {k: v for k, v in image.info.items() if isinstance(v, str)})
        finally:
            image.close()
    assert len(found) >= 4, f"only {sorted(found)} read from docs/screenshots — the scan is blind"
    return found


def _markdown_image_targets() -> dict[str, list[str]]:
    """Every image a tracked markdown shows, keyed by the file under docs/screenshots it names."""
    refs: dict[str, list[str]] = {}
    for path in MARKDOWN:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for raw in re.findall(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)", text):
            resolved = (path.parent / raw.split("#", 1)[0].rstrip(")")).resolve()
            try:
                name = resolved.relative_to(SHOTS_DIR.resolve()).as_posix()
            except ValueError:
                continue  # an image the documents ship from somewhere else is not this directory's
            refs.setdefault(name, []).append(path.relative_to(ROOT).as_posix())
    return refs


def test_every_docs_screenshot_is_shown_by_a_document_and_carried_by_git():
    """README has four images and the directory has had files that no page shows and, once, a
    picture no commit carried. Both directions are the same defect seen from the two ends of the
    clone: an image git never tracked renders as nothing for every GitHub reader, and a picture
    on disk that no document names is a file nobody will ever notice is wrong.

    The bijection is over the directory, not over a list of file names in this test — a fifth
    screenshot joins the law by being added, not by being remembered here."""
    on_disk = set(_pictures())
    tracked = {
        PurePosixPath(name).name for name in _tracked_files("docs/screenshots") if name.endswith(".png")
    }
    refs = _markdown_image_targets()
    assert len(refs) >= 4, f"only {sorted(refs)} screenshots are shown by a document — the scan is blind"

    untracked = sorted(on_disk - tracked)
    unshown = sorted(on_disk - set(refs))
    phantom = sorted(set(refs) - on_disk)
    assert not untracked, (
        f"{untracked} exist on this machine and in no clone: the document that shows them "
        "renders a broken image for every reader on GitHub"
    )
    assert not unshown, f"{unshown} sit in docs/screenshots and are shown by no document"
    assert not phantom, f"{phantom} are shown by a document and are not in the directory"


def test_the_generator_owns_every_picture_the_documents_ship():
    """`SURFACE_OF` is the registry that says what each file pictures, `WEB_SHOTS` and
    `MENU_SHOT` are the files the script actually writes, and `docs/screenshots` is the
    directory README reads. Three lists that were kept by hand are three lists that drift:
    a registry entry for a file nothing draws, or a drawn file the registry never names,
    is an image whose provenance nobody can state."""
    module = _shot_script()
    registry, pictures = dict(module.SURFACE_OF), _pictures()
    drawn = {name for name, *_rest in module.WEB_SHOTS} | {module.MENU_SHOT}
    assert len(registry) >= 4 and len(drawn) >= 4, "the registry or the shot table emptied"

    assert set(registry) == drawn, (
        f"the registry names {sorted(set(registry) - drawn)} that this script draws no more, "
        f"and never names {sorted(drawn - set(registry))} that it writes"
    )
    assert set(registry) == set(pictures), (
        f"{sorted(set(registry) - set(pictures))} are registered and not on disk; "
        f"{sorted(set(pictures) - set(registry))} are on disk and register nothing"
    )


def test_every_docs_screenshot_carries_the_surface_it_pictures():
    """An unstamped PNG is an image whose source nobody can name — the four files were
    hand-saved from a laptop browser for a wave, with no record of which origin, viewport or
    build they came from. The stamp is what makes that claim checkable, and the gate requires
    both that every picture carries it and that every surface the generator can draw is
    carried by at least one picture, so a registry surface nobody shoots cannot pass."""
    module = _shot_script()
    registry, pictures = dict(module.SURFACE_OF), _pictures()

    unstamped = sorted(name for name, (_size, info) in pictures.items() if not info.get(module.STAMP_SURFACE))
    mismatched = sorted(
        f"{name} says {info.get(module.STAMP_SURFACE)!r}, the registry says {registry[name]!r}"
        for name, (_size, info) in pictures.items()
        if info.get(module.STAMP_SURFACE) and info.get(module.STAMP_SURFACE) != registry[name]
    )
    assert not unstamped, (
        f"{unstamped} carry no {module.STAMP_SURFACE} chunk — re-run "
        f"`python -X utf8 {SHOT_SCRIPT.relative_to(ROOT).as_posix()}`"
    )
    assert not mismatched, f"{mismatched} are stamped as a surface they do not picture"

    pictured = {info[module.STAMP_SURFACE] for (_size, info) in pictures.values()}
    assert pictured == set(registry.values()), (
        f"the generator draws {sorted(set(registry.values()) - pictured)} for no picture on disk"
    )


def test_no_docs_screenshot_outlives_the_source_it_pictures():
    """A README image is the design a buyer sees before they open the site, and it stayed
    current only as long as someone remembered to re-shoot it. The stamp is a fingerprint of
    what the picture can show — the palette tokens and headings of the landing and the Mini
    App, the label grid of the two keyboards the bot really builds — recomputed here from the
    tree, so the edit that changes the design is the edit that fails the build."""
    module = _shot_script()
    stale, missing_digest = [], []
    for name, (_size, info) in sorted(_pictures().items()):
        surface = info.get(module.STAMP_SURFACE)
        if not surface:
            continue  # the law above reports it
        wanted = module.fingerprint(surface)
        stamped = info.get(module.STAMP_FINGERPRINT)
        if not stamped:
            missing_digest.append(name)
        elif stamped != wanted:
            stale.append(f"{name} ({surface}): stamped {stamped}, the tree says {wanted}")

    how = "python -X utf8 scripts/make_docs_screenshots.py"
    assert not missing_digest, f"{missing_digest} carry a surface and no {module.STAMP_FINGERPRINT} chunk"
    assert not stale, (
        f"{stale} picture a source that has moved. Re-shoot with `{how}` (menu only: "
        f"`{how} --only menu`; web: `{how} --origin http://127.0.0.1:8000 --user-id <tg_id>`)."
    )


def test_the_pictures_are_the_size_the_generator_lays_out():
    """The menu mock is drawn to a canvas the script computes from its own row and button
    metrics, and every web shot to the viewport and scale factor its table names.

    The stamp carries that geometry too, so a constant moved in the table invalidates the
    picture; this law is the other half — the pixels themselves. A screenshot resized,
    cropped or padded in an image editor changes no source file and moves no token, and is
    caught here or nowhere."""
    module = _shot_script()
    pictures = _pictures()
    assert len(module.WEB_SHOTS) >= 3, f"only {len(module.WEB_SHOTS)} web shots — the table is blind"

    wrong = []
    for name, _path, (width, height), _signed in module.WEB_SHOTS:
        got = pictures[name][0]
        want = (width * module.DEVICE_SCALE, height * module.DEVICE_SCALE)
        if got != want:
            wrong.append(f"{name} is {got[0]}x{got[1]}, the table says {want[0]}x{want[1]}")
    assert not wrong, f"{wrong} are not the size the generator captures them at"

    menu = pictures[module.MENU_SHOT][0]
    canvas = module.menu_canvas(module.menu_panels())
    assert menu == canvas, f"{module.MENU_SHOT} is {menu[0]}x{menu[1]} pixels, the menu layout computes {canvas}"


def test_the_fingerprint_readers_read_the_structures_the_pictures_are_built_from():
    """Every law above trusts four small readers, so they are tested against markup that is
    not the file they usually get: a token block whose last declaration has no semicolon,
    a heading wrapping a nested tag, a decoy `data-i18n` outside the tab bar, a tab bar with
    one tab, and a payload where one character moves. A reader that silently returns nothing
    would make the fingerprint law green on an image of a design that no longer exists."""
    module = _shot_script()

    css = ':root { --blue: #0000ff; --gap: 1px   2px; }\n[data-theme="dark"] {\n  --blue: #224bff;\n}'
    assert module.palette_tokens(css, (":root", '[data-theme="dark"]')) == [
        "--blue:#0000ff",
        "--gap:1px 2px",
        "--blue:#224bff",
    ], "two blocks must keep their own value for one token, and whitespace must normalise"
    assert module.palette_tokens(":root { --a: 1; --b: 2 }", (":root",)) == ["--a:1", "--b:2"], (
        "a block's last declaration carries no semicolon, and losing it loses a token the picture is painted with"
    )
    with pytest.raises(ValueError, match=r"no '\.missing' block"):
        module.palette_tokens(css, (".missing",))
    with pytest.raises(ValueError, match="no custom properties"):
        module.palette_tokens(":root { color: red; }", (":root",))

    assert module.headings("<h1>A <em>b</em></h1><h4>nope</h4><h2>   </h2><h3>C</h3>") == ["A b", "C"]
    nav = '<nav class="tab-bar"><button data-i18n="wallet">A</button><button data-i18n="markets">B</button></nav>'
    assert module.tab_labels(f"{nav}<button data-i18n=\"decoy\">X</button>") == ["wallet", "markets"]
    with pytest.raises(ValueError, match=r"no \.tab-bar"):
        module.tab_labels("<main><button data-i18n=\"a\"></button><button data-i18n=\"b\"></button></main>")
    with pytest.raises(ValueError, match="1 labels"):
        module.tab_labels('<nav class="tab-bar"><button data-i18n="solo"></button></nav>')

    panels = module.menu_panels()
    assert len(panels) == 2, "the menu picture is laid out as two panels side by side"
    labels = [label for panel in panels for row in panel for label in row]
    assert len(labels) >= 8 and all(label.strip() for label in labels), f"{panels} is not a keyboard"

    base = {"tokens": ["--blue:#0000ff"], "panels": [["💰 Баланс"]]}
    assert len(module.fingerprint_of(base)) == 16
    assert module.fingerprint_of(base) == module.fingerprint_of(dict(reversed(list(base.items())))), (
        "key order must not change the digest: the same design may serialise in any order"
    )
    moved = json.loads(json.dumps(base))
    moved["panels"][0][0] = "💰 Баланс!"
    assert module.fingerprint_of(moved) != module.fingerprint_of(base), "a relabelled button must move the digest"
    recoloured = json.loads(json.dumps(base))
    recoloured["tokens"][0] = "--blue:#0052ff"
    assert module.fingerprint_of(recoloured) != module.fingerprint_of(base), "a token change must move the digest"

    payloads = {surface: module.surface_payload(surface) for surface in sorted(set(module.SURFACE_OF.values()))}
    assert set(payloads["landing"]) == {"tokens", "headings", "capture"}
    assert set(payloads["mini"]) == {"tokens", "tabs", "headings", "capture"}
    assert set(payloads["menu"]) == {"panels", "canvas"}
    assert len(payloads["landing"]["tokens"]) >= 40, "the landing palette read too few tokens to certify a design"
    assert len(payloads["mini"]["tokens"]) >= 40, "the Mini App palette read too few tokens to certify a design"
    assert len(payloads["landing"]["headings"]) >= 3 and len(payloads["mini"]["tabs"]) >= 2

    geometry = {surface: module.capture_geometry(surface) for surface in ("landing", "mini")}
    assert [len(rows) for rows in geometry.values()] == [2, 1], f"{geometry} is not the shot table"
    assert all(re.fullmatch(r"[\w.-]+\.png@\d+x\d+@\d+", row) for rows in geometry.values() for row in rows), (
        f"{geometry} does not read as `name@WxH@scale`"
    )
    assert sorted(row.split("@")[0] for rows in geometry.values() for row in rows) == sorted(
        name for name, surface in module.SURFACE_OF.items() if surface != "menu"
    ), "a web picture the camera geometry never accounts for"
    with pytest.raises(ValueError, match="no shot in WEB_SHOTS"):
        module.capture_geometry("menu")

    assert len({module.fingerprint_of(payload) for payload in payloads.values()}) == len(payloads), (
        "three different surfaces hashed to one digest — the payload is not what it claims"
    )


# --- the numbers a document prints about this tree ---------------------------
#
# Sections 4.1, 4.2 and 4.9 and Appendix A of the WHITEPAPER are inventories: they are
# the evidence, and until this wave nothing re-measured them. §4.9 still promised 21
# ledger domain mixins after the second schema source was deleted and 18 remained, §4.2
# counted 48 routes while `web/auth.py` had been serving five for waves, the line count
# was 4,700 lines short in `tests` and silent about `alembic` and `functions`, and the
# rendered design gate was described as "five surfaces" while it walks nine facets.
# Every claim below is read out of the files it counts, the same way Appendix A tells a
# reader to count them.

_CODE_SUFFIXES = frozenset({".py", ".sol", ".js", ".css", ".html", ".sh", ".ps1"})
_ROUTE_DECORATOR = re.compile(r"@(?:app|router)\.(?:get|post|put|delete)\(")
_NUMBERED_PACKAGE = re.compile(r"\b([a-z][a-z0-9_]*) ([\d,]{2,9})\b")
_WORD_NUMBER = {
    word: value
    for value, word in enumerate((
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen", "twenty",
    ))
}


def _whitepaper() -> str:
    return (ROOT / "docs" / "WHITEPAPER.md").read_text(encoding="utf-8-sig")


def _command_specs() -> list[tuple[str, str, str]]:
    """`BOT_COMMAND_SPECS` as the catalogue writes it — read from the literal, not by
    importing the bot, so the count does not need a database or a token."""
    tree = ast.parse((ROOT / "bot" / "commands_catalog.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "BOT_COMMAND_SPECS"
            for target in node.targets
        ):
            return [ast.literal_eval(element) for element in node.value.elts]
    pytest.fail("BOT_COMMAND_SPECS is no longer a literal list in bot/commands_catalog.py")


def _ledger_mixins() -> list[str]:
    tree = ast.parse((ROOT / "bot" / "ledger" / "__init__.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Ledger":
            return [ast.unparse(base) for base in node.bases]
    pytest.fail("class Ledger is no longer declared in bot/ledger/__init__.py")


def _routes_by_module() -> dict[str, int]:
    """Every tracked `web/*.py` that answers an HTTP route, and how many.

    The directory is the subject set, not the list the doc happened to name: three
    files were enumerated for waves and the fourth kept growing outside the count.
    """
    counts = {
        f"web/{path.name}": len(_ROUTE_DECORATOR.findall(path.read_text(encoding="utf-8-sig")))
        for path in sorted((ROOT / "web").glob("*.py"))
    }
    answered = {name: n for name, n in counts.items() if n}
    assert len(answered) >= 3, f"only {answered} — the walk found no route-bearing module"
    return answered


def _lines_of_code_by_package() -> dict[str, int]:
    lines: dict[str, int] = {}
    for rel in sorted(_tracked_files()):
        if PurePosixPath(rel).suffix not in _CODE_SUFFIXES:
            continue
        package = rel.split("/", 1)[0] if "/" in rel else "."
        text = (ROOT / rel).read_text(encoding="utf-8-sig", errors="replace")
        lines[package] = lines.get(package, 0) + len(text.splitlines())
    assert len(lines) >= 8, f"only {len(lines)} packages counted — the walk is blind"
    return lines


def test_the_whitepapers_lines_of_code_are_the_lines_the_tree_has():
    """`git ls-files`, the seven source suffixes, `len(splitlines())`, one number per
    top-level directory — and the directory set is the tree's answer, not the doc's."""
    sentence = re.search(r"the\s+tree\s+is\s+\*\*([\d,]+)\*\*\s+lines(?P<body>[^.]*\.)", _whitepaper())
    assert sentence, "the WHITEPAPER no longer states its lines of code where this law reads them"
    body = sentence.group("body")
    stated = {name: int(value.replace(",", "")) for name, value in _NUMBERED_PACKAGE.findall(body)}
    assert stated, f"no `package 12,345` pair in {body!r} — the sentence has nothing to check"
    measured = _lines_of_code_by_package()
    assert set(stated) == set(measured), (
        f"the sentence names {sorted(set(stated) - set(measured))} the tree does not have, "
        f"and hides {sorted(set(measured) - set(stated))} it does"
    )
    wrong = {name: (stated[name], measured[name]) for name in stated if stated[name] != measured[name]}
    assert not wrong, f"{wrong} read (doc, measured) — rerun the Appendix A command and restate them"
    total = int(sentence.group(1).replace(",", ""))
    assert total == sum(measured.values()), (
        f"the sentence totals {total:,} while its own packages sum to {sum(measured.values()):,}"
    )


def test_the_whitepaper_counts_the_routes_the_web_layer_serves():
    text = _whitepaper()
    measured = _routes_by_module()
    claimed = {
        f"web/{Path(module).name}": int(count)
        for count, module in re.findall(r"\*\*(\d+) routes? in `(web/[\w.-]+)`\*\*", text)
    }
    assert len(claimed) >= 3, f"{claimed} — the law stopped finding the route bullets in §4.2"
    assert set(claimed) == set(measured), (
        f"§4.2 describes {sorted(set(claimed) ^ set(measured))} — a module that serves routes "
        "and is not in the inventory, or one that is listed and serves none"
    )
    off = {name: (claimed[name], measured[name]) for name in claimed if claimed[name] != measured[name]}
    assert not off, f"{off} read (doc, measured) for route decorators under web/"

    total = sum(measured.values())
    heading = re.search(r"### 4\.2 Web and Mini App — (\d+) HTTP routes", text)
    assert heading, "§4.2 no longer states its route total in its heading"
    assert int(heading.group(1)) == total, f"§4.2 heads with {heading.group(1)}, the modules answer {total}"

    breakdown = re.search(r"# HTTP routes[^\n]*\((\d+(?: \+ \d+)+)\)", text)
    assert breakdown, "Appendix A no longer breaks the route total down per module"
    parts = sorted(int(part) for part in breakdown.group(1).split(" + "))
    assert parts == sorted(measured.values()), (
        f"Appendix A adds {parts} = {sum(parts)} while the tree answers {sorted(measured.values())}"
    )


def test_the_whitepapers_engineering_inventory_is_the_inventory_the_tree_has():
    """§4.1's command catalogue, §4.9's table and the rendered-gate facet count."""
    text = _whitepaper()
    specs = _command_specs()
    commands, groups = len(specs), len({row[2] for row in specs})
    assert commands >= 20 and groups >= 4, f"{commands} commands in {groups} groups — the catalogue was not read"

    inventory = re.findall(r"(\d+) commands, (\d+) groups", text)
    assert len(inventory) >= 2, (
        f"{inventory} — §4.1 and Appendix A are both supposed to state the catalogue"
    )
    for stated_commands, stated_groups in inventory:
        assert (int(stated_commands), int(stated_groups)) == (commands, groups), (
            f"a document says {stated_commands} commands in {stated_groups} groups; "
            f"bot/commands_catalog.py declares {commands} in {groups}"
        )

    mixins = _ledger_mixins()
    assert all(name.endswith("Mixin") for name in mixins) and len(mixins) >= 10, (
        f"{mixins} is not a list of ledger domain mixins"
    )
    row = re.search(r"\|\s*Ledger domain mixins\s*\|\s*(\d+)\b", text)
    assert row, "§4.9 no longer states the ledger domain mixin count"
    assert int(row.group(1)) == len(mixins), (
        f"§4.9 says {row.group(1)} mixins, `class Ledger` is built from {len(mixins)}"
    )

    agent_modules = sorted(rel for rel in _tracked_files("agent") if rel.endswith(".py"))
    assert len(agent_modules) >= 5, f"{agent_modules} — the agent package was not enumerated"
    agent_row = re.search(r"\|\s*Agent modules\s*\|\s*(\d+)\b", text)
    assert agent_row, "§4.9 no longer states the agent module count"
    assert int(agent_row.group(1)) == len(agent_modules), (
        f"§4.9 says {agent_row.group(1)} agent modules, `git ls-files agent/*.py` answers "
        f"{len(agent_modules)}"
    )

    contracts_row = re.search(r"\|\s*Contracts\s*\|\s*([^|\n]+)\|", text)
    assert contracts_row, "§4.9 no longer carries the contracts row"
    cell = contracts_row.group(1)
    sol_files = sorted(p.name for p in (ROOT / "contracts").glob("*.sol"))
    assert len(sol_files) >= 8, f"{sol_files} — the contracts folder was not read"
    stated_total = re.search(r"(\d+) in `contracts/`", cell)
    assert stated_total, f"{cell!r} no longer states how many contracts there are"
    assert int(stated_total.group(1)) == len(sol_files), (
        f"the row says {stated_total.group(1)} contracts, contracts/ holds {len(sol_files)}"
    )
    stand_ins = re.findall(r"`([\w.-]+\.sol)`", cell.split("production")[-1].split("),")[0])
    assert stand_ins and all(name in sol_files for name in stand_ins), (
        f"{stand_ins} — the row calls these the test-only stand-ins; contracts/ does not have them"
    )
    production = re.search(r"\((\d+) production", cell)
    assert production and int(production.group(1)) == len(sol_files) - len(stand_ins), (
        f"the row splits {production.group(1) if production else '?'} production + "
        f"{len(stand_ins)} stand-ins, contracts/ has {len(sol_files)}"
    )

    forge_suites = sorted(rel for rel in _tracked_files("contracts") if rel.endswith(".t.sol"))
    suites = re.search(r"(\d+) Forge suites", cell)
    assert suites, f"{cell!r} no longer states the Forge suite count"
    assert int(suites.group(1)) == len(forge_suites), (
        f"the row says {suites.group(1)} Forge suites, the tree tracks {forge_suites}"
    )

    deploy_scripts = sorted(
        rel for rel in _tracked_files("script", "contracts/script") if rel.endswith(".s.sol")
    )
    foundry = re.search(r"(\d+) Foundry deploy scripts \(([^)]*)\)", cell)
    assert foundry, f"{cell!r} no longer states the Foundry deploy scripts"
    assert int(foundry.group(1)) == len(deploy_scripts), (
        f"the row says {foundry.group(1)}, the tree tracks {deploy_scripts}"
    )
    named_scripts = [part.strip(" `") for part in foundry.group(2).split(",")]
    assert sorted(named_scripts) == deploy_scripts, (
        f"the row names {named_scripts} and the tree tracks {deploy_scripts}"
    )

    facets = _rendered_gate_facets()
    stated_facets = re.search(r"\b(\w+) facets named in `tests/test_production_design\.py`", text)
    assert stated_facets, "§4.9 no longer says how many rendered facets the design gate walks"
    assert _WORD_NUMBER.get(stated_facets.group(1), -1) == len(facets), (
        f"the text says {stated_facets.group(1)} facets, FACETS lists {len(facets)}: {facets}"
    )

    # The line the document opens with is the same inventory in Russian, and it kept saying
    # 48 routes and 21 mixins while §4.2 and §4.9 were being corrected around it. A number a
    # reader meets first is the number least allowed to be wrong.
    flat = re.sub(r"\s+", " ", text)
    summary = re.search(
        r"\*\*Что уже написано:\*\* (\d+) команд\S* бота, (\d+) HTTP-маршру\S*, "
        r"(\d+) контракт\S*, (\d+) доменн\S* миксин\S* леджера, (\d+) модулей агента",
        flat,
    )
    assert summary, (
        "the WHITEPAPER's opening inventory line no longer states its figures in the shape this "
        "law reads — if the sentence moved, move the pattern with it, and never only the numbers"
    )
    routes = sum(_routes_by_module().values())
    for label, stated, measured in (
        ("commands", int(summary.group(1)), commands),
        ("HTTP routes", int(summary.group(2)), routes),
        ("contracts", int(summary.group(3)), len(sol_files)),
        ("ledger mixins", int(summary.group(4)), len(mixins)),
        ("agent modules", int(summary.group(5)), len(agent_modules)),
    ):
        assert stated == measured, (
            f"the opening line states {stated} {label}, the tree answers {measured} "
            "(§4.1/§4.2/§4.9 state these the same way — one of the two is stale)"
        )


def _rendered_gate_facets() -> list[str]:
    tree = ast.parse((ROOT / "tests" / "test_production_design.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FACETS" for target in node.targets
        ):
            facets = [ast.literal_eval(element) for element in node.value.elts]
            assert len(facets) >= 5, f"{facets} — the rendered gate walks almost nothing"
            return facets
    pytest.fail("FACETS is no longer a literal list in tests/test_production_design.py")


# ── the README roadmap is a list of promises, and a promise rots ────────────
#
# Every bullet there either says the platform already does something or asks
# someone to build it. The first kind is a claim about the tree — `bot/create2.py`
# has to exist and `CREATE2_SAFE_DEPOSITS` has to be a name the runtime reads —
# and the second kind is a claim about the tree too: a live bullet that names a
# module the repository already tracks is finished work nobody crossed off. So
# this law reads the artifacts out of each bullet and resolves them against the
# tree in both directions: struck bullets must resolve, live bullets must not.

_SOURCE_PY_DIRS = ("bot", "web", "agent", "deploy", "scripts", "alembic", "functions")
_STRIKE = re.compile(r"~~(.+?)~~", re.S)
_URL = re.compile(r"https?://\S+")
_ARTIFACT_PATH = re.compile(
    r"\b[\w.-]+/[\w./-]*\.(?:py|js|css|html|md|sh|sol|sql|yml|yaml|json|toml)\b"
)
_ENV_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?:_\*)?(?![A-Za-z0-9_])")
_SLASH_COMMAND = re.compile(r"(?<![\w/.-])/([a-z][a-z0-9_]{1,15})\b")


def _is_environ(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "environ"
    )


def _env_names_the_runtime_reads() -> set[str]:
    """Every name `os.environ[...]`, `os.environ.get(...)` or `getenv(...)` reads."""
    found: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Subscript(self, node):
            if (
                _is_environ(node.value)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                found.add(node.slice.value)
            self.generic_visit(node)

        def visit_Call(self, node):
            func = node.func
            gets_env = isinstance(func, ast.Name) and func.id == "getenv"
            gets_env = gets_env or (isinstance(func, ast.Attribute) and func.attr in {"getenv", "get"})
            gets_env = gets_env or (isinstance(func, ast.Attribute) and _is_environ(func))
            if (
                gets_env
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
            self.generic_visit(node)

    for rel in sorted(_tracked_files()):
        path = PurePosixPath(rel)
        if path.suffix != ".py" or path.parts[0] not in _SOURCE_PY_DIRS:
            continue
        Visitor().visit(ast.parse((ROOT / rel).read_text(encoding="utf-8-sig")))
    assert len(found) >= 40, f"only {len(found)} env names — the scanner lost the config surface"
    return found


def _readme_roadmap_bullets() -> list[tuple[bool, int, str]]:
    """`(struck, first line, text)` for every bullet under the README's `## Roadmap`."""
    return _roadmap_bullets_from((ROOT / "README.md").read_text(encoding="utf-8-sig"))


def _roadmap_bullets_from(source: str) -> list[tuple[bool, int, str]]:
    lines = source.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == "## Roadmap")
    except StopIteration:
        pytest.fail("README.md has no `## Roadmap` section for this law to read")

    bullets: list[tuple[bool, int, str]] = []
    index = start + 1
    while index < len(lines) and not lines[index].startswith("## "):
        if lines[index].startswith(("- ", "* ")):
            first_line = index + 1
            body = [lines[index][2:]]
            index += 1
            while index < len(lines) and lines[index].startswith(("  ", "\t")):
                body.append(lines[index].strip())
                index += 1
            text = " ".join(body)
            bullets.append((bool(_STRIKE.search(text)), first_line, _STRIKE.sub(r"\1", text)))
        else:
            index += 1
    assert len(bullets) >= 4, f"{len(bullets)} bullets — that is not a roadmap this law can read"
    assert any(struck for struck, _, _ in bullets), "no struck bullet: nothing claims to ship"
    assert any(not struck for struck, _, _ in bullets), "no live bullet: the roadmap promises nothing"
    return bullets


def _artifacts_cited(text: str, env_names: set[str], commands: set[str], tracked: set[str]):
    """`[(token, resolves, why)]` for the paths, environment names and commands a bullet names.

    A token ending in `*` is a family and resolves when the runtime reads at least
    one name with that prefix; anything else has to appear exactly.
    """
    plain = _URL.sub(" ", text)
    cited = []
    for token in _ARTIFACT_PATH.findall(plain):
        cited.append((token, token in tracked, f"`{token}` is no file the repository tracks"))
    for token in _ENV_TOKEN.findall(plain):
        if "_" not in token:
            continue  # USDC, EIP, API — a word, not a configuration name
        if token.endswith("*"):
            prefix = token.rstrip("*").rstrip("_")
            hits = sorted(name for name in env_names if name.startswith(f"{prefix}_"))
            cited.append((token, bool(hits), f"nothing reads a `{prefix}_*` environment name"))
        else:
            cited.append((token, token in env_names, f"`{token}` is no name the runtime reads"))
    for token in _SLASH_COMMAND.findall(plain):
        cited.append(
            (f"/{token}", f"/{token}" in commands, f"`/{token}` is no command the bot registers")
        )
    return cited


def test_the_readme_roadmap_promises_only_what_the_tree_does_not_have_yet():
    """Struck bullets cite artifacts the tree has; live bullets cite none; nothing rots."""
    env_names = _env_names_the_runtime_reads()
    commands = {f"/{name}" for name, _, _ in _command_specs()}
    tracked = _tracked_files()
    assert len(commands) >= 20, f"only {len(commands)} commands — the catalogue read is blind"

    shipped_lies: list[str] = []
    finished_but_live: list[str] = []
    untraceable: list[str] = []
    resolved_total = 0

    for struck, line, text in _readme_roadmap_bullets():
        cited = _artifacts_cited(text, env_names, commands, tracked)
        if not struck:
            asking = sorted(token for token, resolves, _ in cited if resolves)
            if asking:
                finished_but_live.append(f"README.md:{line} still asks for {asking} the tree has")
            continue
        broken = sorted(token for token, resolves, _ in cited if not resolves)
        if broken:
            shipped_lies.append(f"README.md:{line} claims shipped and cites {broken}")
        if not cited:
            untraceable.append(f"README.md:{line}")
        resolved_total += sum(1 for _, resolves, _ in cited if resolves)

    assert not shipped_lies, (
        "a struck roadmap bullet cites what the tree does not have — the feature is "
        "not shipped or the artifact moved and the doc never followed:\n" + "\n".join(shipped_lies)
    )
    assert not untraceable, (
        "a struck roadmap bullet must name at least one artifact that ships it, or nothing "
        "can ever tell it was true and nothing can tell it stopped being true:\n"
        + "\n".join(untraceable)
    )
    assert not finished_but_live, (
        "a live roadmap bullet may not cite an artifact the tree already has "
        "(cross it off, or say what you meant):\n" + "\n".join(finished_but_live)
    )
    assert resolved_total >= 5, (
        f"only {resolved_total} struck-bullet artifacts resolved — the law read almost nothing"
    )


def test_the_roadmap_reader_reads_the_artifacts_the_text_names():
    """The resolvers, on text written to break them: a URL is not a repository path, a
    doc path is not a slash command, a spec number is not an environment name."""
    env = {"CREATE2_SAFE_DEPOSITS", "WITHDRAW_BATCH_FLUSH_COUNT", "ORACLE_PRIVATE_KEY"}
    commands = {"/deposit", "/balance"}
    tracked = {"bot/create2.py", "docs/SECURITY.md"}

    def cited(markdown: str) -> dict[str, bool]:
        return {token: resolves for token, resolves, _ in _artifacts_cited(markdown, env, commands, tracked)}

    assert cited("`bot/create2.py:212` derives one address") == {"bot/create2.py": True}
    assert cited("`bot/moved.py` shipped") == {"bot/moved.py": False}
    assert cited("see `docs/SECURITY.md`") == {"docs/SECURITY.md": True}
    assert cited("chat at https://t.me/ssrjkk or https://x.com/ludych1") == {}
    assert cited("upstream: https://github.com/org/repo/blob/main/web/mini.py") == {}
    assert cited("queued under `WITHDRAW_BATCH_*` caps") == {"WITHDRAW_BATCH_*": True}
    assert cited("gated by `REEF_4589_*`") == {"REEF_4589_*": False}
    assert cited("`CREATE2_SAFE_DEPOSITS=1` with a deployed `CREATE2_FACTORY_ADDRESS`") == {
        "CREATE2_SAFE_DEPOSITS": True,
        "CREATE2_FACTORY_ADDRESS": False,
    }
    assert cited("EIP-1167 proxies, ERC-1155 shares, USDC") == {}
    assert cited("USDC on Base through the API, per PROTOCOL") == {}
    assert cited("printed by `/deposit`, never by `/melt`") == {"/deposit": True, "/melt": False}
    assert cited("set create2_safe_deposits in .env") == {}


def test_the_roadmap_reader_keeps_one_wrapped_bullet_one_bullet():
    """A bullet that wraps is one promise, struck text is scanned without its markup, and
    the line number points where a reader has to look."""
    source = "\n".join([
        "## Roadmap",
        "",
        "- ~~Per-user deposits~~ shipped: `bot/create2.py` derives one",
        "  proxy per user and `/deposit` prints it.",
        "- ~~Old work~~ shipped, names nothing",
        "- Live `bot/live.py` idea",
        "- Another live idea",
        "",
        "## Contributing",
    ])
    bullets = _roadmap_bullets_from(source)
    assert len(bullets) == 4, bullets
    struck, line, text = bullets[0]
    assert struck and line == 3, (struck, line)
    assert "`bot/create2.py`" in text and "`/deposit`" in text
    assert "~~" not in text, "the strike markup must not hide inside the text the law scans"
    assert [item[0] for item in bullets] == [True, True, False, False]


# ── a money figure written beside a named knob is a claim about that knob ───
#
# `docs/OPERATIONS.md` told an operator to review transactions "> $1000 USDC" while the
# ledger has always flagged a payout at `WITHDRAW_LARGE_USDC_THRESHOLD` — 500. The
# checklist was a sentence away from the knob it meant and two times away from the number
# it promised, and a reviewer who trusts the doc misses half the flagged payouts. This law
# reads the default out of the `int(os.environ.get(NAME, DEFAULT))` pair in the runtime and
# holds every USDC figure a document puts beside that name to it.

_MONEY_FIGURE = re.compile(r"(?<![\d.])\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:USDC|usdc)\b")
_NUMERIC_PARSERS = frozenset({"int", "float", "Decimal"})
_THRESHOLD_WINDOW = 60  # characters on each side that still count as "about this knob"


def _numeric_env_knobs() -> tuple[dict[str, Decimal], list[str]]:
    """Every knob the runtime parses as a number from its own default, and conflicts found.

    The pair is read out of `int(os.environ.get("NAME", "500"))` and its `Decimal` sibling —
    `Decimal` is what this configuration uses for every money-sized knob, so leaving it out
    would read the bounds of the site and miss the amounts — because that default is the number
    a document quotes, and a name two modules default to two different numbers is the same
    defect in code that this law refuses to accept in prose.
    """
    defaults: dict[str, Decimal] = {}
    conflicts: list[str] = []
    for rel in sorted(_tracked_files()):
        path = PurePosixPath(rel)
        if path.suffix != ".py" or path.parts[0] not in _SOURCE_PY_DIRS:
            continue
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _NUMERIC_PARSERS
                and len(node.args) == 1
            ):
                continue
            inner = node.args[0]
            if not (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "get"
                and _is_environ(inner.func.value)
                and len(inner.args) == 2
                and all(isinstance(arg, ast.Constant) for arg in inner.args)
            ):
                continue
            name, raw = inner.args[0].value, inner.args[1].value
            if not isinstance(name, str) or not isinstance(raw, str):
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue
            if name in defaults and defaults[name] != value:
                conflicts.append(f"{name} defaults to {defaults[name]} and also to {value} ({rel})")
            defaults[name] = value
    assert len(defaults) >= 50, f"only {len(defaults)} numeric knobs (74 read this wave) — the scan lost the config"
    return defaults, conflicts


def _usdc_figures_a_document_attributes_to_a_knob(
    defaults: dict[str, Decimal], paths: list[Path] | None = None
):
    """`(attributed, wrong, ambiguous)` for every USDC figure written beside a money knob.

    A knob counts as money-sized only when its own name says the unit (`…_USDC…`): the
    default of `AGENT_DAILY_CAP` is 50 and of `MAX_WITHDRAWS_PER_DAY` is 5, and nothing in
    the name tells a reader — or this scan — which of them a "$50 USDC" in a runbook means.
    Guessing a unit would invent claims, so the law checks only the knobs whose own name
    declares it (12 read this wave) and AGENTS.md states that the rest are outside its reach.
    """
    money = {name: value for name, value in defaults.items() if "USDC" in name}
    # How many money-sized knobs the real configuration has is floored by the law that calls
    # this with it; the reader's own unit test calls it with a three-entry fixture.
    attributed: list[tuple[str, int, str, str, str]] = []
    wrong: list[str] = []
    ambiguous: list[str] = []
    for path in MARKDOWN if paths is None else paths:
        # A probe document written by the reader's own unit test has no place under ROOT.
        rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        text = _prose(path.read_text(encoding="utf-8-sig", errors="replace"))
        for match in _MONEY_FIGURE.finditer(text):
            start = max(0, match.start() - _THRESHOLD_WINDOW)
            near = text[start : match.end() + _THRESHOLD_WINDOW]
            if _PROPOSAL_WORDS.search(near):
                continue  # a figure in a proposal is not a bound this build keeps
            offset = match.start() - start
            named = sorted(
                (min((abs(found.start() - offset) for found in re.finditer(rf"\b{name}\b", near)),
                     default=10**6), name)
                for name in money
            )
            named = [entry for entry in named if entry[0] < 10**6]
            if not named:
                continue
            line = text[: match.start()].count("\n") + 1
            figure = Decimal(match.group(1).replace(",", ""))
            owners = [name for _, name in named]
            row = (rel, line, match.group(0).strip(), owners[0], str(defaults[owners[0]]))
            attributed.append(row)
            if len(owners) > 1 and defaults[owners[0]] != defaults[owners[1]]:
                ambiguous.append(
                    f"{rel}:{line} {row[2]} sits between {owners[0]} ({defaults[owners[0]]}) "
                    f"and {owners[1]} ({defaults[owners[1]]})"
                )
                continue
            if figure != defaults[owners[0]]:
                wrong.append(
                    f"{rel}:{line} states {row[2]} beside `{owners[0]}`, "
                    f"which the runtime defaults to {defaults[owners[0]]}"
                )
    return attributed, wrong, ambiguous


def test_no_document_states_a_usdc_figure_that_is_not_the_named_knobs_default():
    defaults, conflicts = _numeric_env_knobs()
    assert not conflicts, "an environment knob has two defaults, so no document can quote one:\n" + \
        "\n".join(conflicts)

    # The helper takes a fixture dict from its own unit test, so the count of the real
    # configuration is floored here, where the scan has read the tree.
    money = sorted(name for name in defaults if "USDC" in name)
    assert len(money) >= 10, (
        f"only {money} — {len(money)} knobs declare a USDC unit (12 read this wave), so the "
        "scan lost the money half of the configuration and this law would measure prose "
        "against nothing"
    )

    attributed, wrong, ambiguous = _usdc_figures_a_document_attributes_to_a_knob(defaults)
    assert len(attributed) >= 2, (
        f"only {len(attributed)} USDC figures in the documents sit beside a money knob — the "
        "prose stopped telling an operator the bound, or the reader stopped finding it"
    )
    assert not ambiguous, (
        "a USDC figure written between two knobs that default to two different numbers states "
        "neither of them; name the knob the figure belongs to:\n" + "\n".join(ambiguous)
    )
    assert not wrong, (
        "a document states a USDC figure beside the knob whose default it is not — an operator "
        "reading the prose applies a bound the code never enforces:\n" + "\n".join(wrong)
    )


def test_the_threshold_reader_attributes_a_figure_to_the_knob_beside_it(tmp_path):
    """The instrument the threshold law measures with, on prose written to break it: only a
    name that states the USDC unit owns a figure, only within the window, never inside a fence,
    and a figure between two different defaults is reported instead of guessed."""
    defaults = {
        "LARGE_USDC": Decimal("500"),
        "DAILY_USDC": Decimal("5000"),
        "AGENT_DAILY_CAP": Decimal("50"),
    }

    def probe(markdown: str):
        doc = tmp_path / "probe.md"
        doc.write_text(markdown, encoding="utf-8", newline="\n")
        return _usdc_figures_a_document_attributes_to_a_knob(defaults, [doc])

    attributed, wrong, ambiguous = probe("`LARGE_USDC` is 500 USDC by default.\n")
    assert attributed and not wrong and not ambiguous, (attributed, wrong, ambiguous)
    assert attributed[0][3] == "LARGE_USDC"

    assert probe("`DAILY_USDC` is 5,000 USDC by default.\n")[1] == []
    _, one_wrong, _ = probe("`LARGE_USDC` flags anything over $1000 USDC.\n")
    assert len(one_wrong) == 1 and "500" in one_wrong[0], one_wrong

    assert probe("`AGENT_DAILY_CAP` spends 50 USDC a day.\n")[0] == []
    far = "`LARGE_USDC` is the bound, and the ledger compares every payout it is "
    far += "offered against the stored threshold before it flags anything. A payout of 500 USDC.\n"
    assert probe(far)[0] == [], "a figure outside the window belongs to no name"
    assert probe("```python\n# `LARGE_USDC` 500 USDC\n```\n")[0] == []
    assert probe("proposed: raise `LARGE_USDC` to 1000 USDC\n")[0] == []

    *_, between = probe("`LARGE_USDC`/`DAILY_USDC`: 700 USDC\n")
    assert len(between) == 1 and "between" in between[0], between

    attributed, _, _ = probe("first line\nsecond line\n`LARGE_USDC` is 500 USDC here\n")
    assert attributed[0][1] == 3, attributed


# ---------------------------------------------------------------------------
# A count of proven mutations is a claim about a list, and the list is counted.
# ---------------------------------------------------------------------------

_MUTATION_CLAIM = re.compile(
    r"\b(\d{1,3}|[A-Za-z]+(?:-[A-Za-z]+)?)\s+mutations?\s+were\s+proven\s+to\s+fire", re.I
)
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}
_CLAIM_WINDOW = 3000


def _count_word(token: str) -> int | None:
    """The number a prose claim states, as a word or as digits, or None when it is not a number.

    «Seventeen laws», «25 laws» and «twenty-five mutations» are one kind of claim; «these
    mutations» and «last mutations» state no count, and reading the word beside them as a
    number would fail a sentence that promises nothing.
    """
    token = token.lower()
    if token.isdigit():
        return int(token)
    if token in _WORD_NUMBER:
        return _WORD_NUMBER[token]
    head, dash, tail = token.partition("-")
    if dash and head in _TENS and tail in _WORD_NUMBER:
        return _TENS[head] + _WORD_NUMBER[tail]
    return None


def _clauses_of(span: str) -> list[str]:
    """Split an enumeration at its top-level commas, outside backtick spans and brackets.

    A comma inside `` `credit(…, 'fee')` `` belongs to the span, and a bracket a span carries
    opens no level: a reader that counted either would read one mutation as two.
    """
    clauses, buf, depth, in_span = [], "", 0, False
    for ch in span:
        if ch == "`":
            in_span = not in_span
        elif not in_span:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                clauses.append(buf)
                buf = ""
                continue
        buf += ch
    clauses.append(buf)
    out = []
    for clause in clauses:
        clause = " ".join(clause.split()).strip().rstrip(".!")
        if clause.lower().startswith("and "):
            clause = clause[4:].strip()
        if clause:
            out.append(clause)
    return out


def _enumeration_after(tail: str) -> list[str] | None:
    """The list a claim introduces, colon form or bracketed, or None when it names no list."""
    colon, paren = tail.find(": "), tail.find(" (")
    spots = [p for p in (colon, paren) if p >= 0]
    if not spots:
        return None
    intro = min(spots)
    if ". " in tail[:intro]:
        return None
    # `find(" (")` reports the space before the bracket, so the introducer has to be read from
    # the character after it. A slice taken at that index starts with a space, matches neither
    # "(" nor ": ", and the bracketed list is then returned whole — both brackets attached and
    # the closing one counted as part of the last clause. The reader test below hands it both.
    head = tail[intro:].lstrip()
    if head.startswith("("):
        depth, in_span = 0, False
        for k, ch in enumerate(head):
            if ch == "`":
                in_span = not in_span
            elif not in_span:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return _clauses_of(head[1:k])
        return None
    if head.startswith(": "):
        cut = head.find(". ")
        return _clauses_of(head[2:cut] if cut >= 0 else head[2:])
    return None


def _documented_mutation_counts() -> list[tuple[str, int, list[str]]]:
    """Every count of proven mutations a first-party document states, with the clauses it names.

    The subject set is `MARKDOWN` — the tracked `*.md` these laws already read — so a count
    added to a new document is inside the law the moment the file is, and no wave has to add
    its own paragraph to a list here.
    """
    found: list[tuple[str, int, list[str]]] = []
    for path in MARKDOWN:
        flat = " ".join(_prose(path.read_text(encoding="utf-8")).split())
        for match in _MUTATION_CLAIM.finditer(flat):
            stated = _count_word(match.group(1))
            if stated is None:
                continue
            tail = flat[match.end(): match.end() + _CLAIM_WINDOW]
            clauses = _enumeration_after(tail)
            found.append((path.name, stated, clauses if clauses is not None else []))
    return found


def test_every_documented_mutation_count_is_the_number_of_clauses_it_names():
    claims = _documented_mutation_counts()
    assert len(claims) >= 2, (
        f"only {len(claims)} documents state a mutation count — the reader found "
        "«N mutations were proven to fire» in fewer places than the tree writes it, so this "
        "law would be certifying a scan that went blind"
    )

    unnamed = [(name, stated) for name, stated, clauses in claims if not clauses]
    assert not unnamed, (
        f"{unnamed} — a document states how many mutations were proven to fire and names no "
        "list beside it. The number then belongs to a scratch run that no longer exists: nobody "
        "reading the file can re-count it, and the next wave has no choice but to trust it."
    )

    wrong = [(name, stated, len(clauses)) for name, stated, clauses in claims if stated != len(clauses)]
    assert not wrong, (
        "a stated count is not the number of clauses it enumerates — either one clause bundled "
        "several mutations (which a reader cannot re-count) or the list lost an item:\n"
        + "\n".join(f"  {name}: says {stated}, names {count}" for name, stated, count in wrong)
    )

    total = sum(len(clauses) for _name, _stated, clauses in claims)
    assert total >= 12, (
        f"the documents name {total} mutations between them, far under the 28 read this wave — "
        "the clause reader is losing items rather than the prose shrinking"
    )


def test_the_mutation_clause_reader_reads_the_shapes_a_prose_list_takes():
    """The reader on markup that is not the file it usually gets.

    The shipped prose uses one colon list and one bracketed list, both written by hand; either
    can gain a bracketed span, a hyphenated number or a stray sentence break tomorrow, and a
    reader that quietly returns nothing reports a clean tree over a claim it never read.
    """

    def read(text: str) -> list[tuple[int, list[str]]]:
        flat = " ".join(text.split())
        out = []
        for match in _MUTATION_CLAIM.finditer(flat):
            stated = _count_word(match.group(1))
            if stated is None:
                continue
            clauses = _enumeration_after(flat[match.end(): match.end() + _CLAIM_WINDOW])
            out.append((stated, clauses if clauses is not None else []))
        return out

    colon = read("Six mutations were proven to fire: a, b, and c. What the laws cannot see: x.")
    assert colon == [(6, ["a", "b", "c"])], f"{colon} — a colon list ends at its own sentence"

    bracketed = read("Six mutations were proven to fire (a, b, and c). Done.")
    assert bracketed == [(6, ["a", "b", "c"])], (
        f"{bracketed} — the bracketed introducer is found at the space before it, so a reader "
        "that slices there keeps the space, matches neither form, and returns the whole list "
        "with both brackets attached"
    )

    def one(text: str) -> tuple[int, list[str]]:
        """One claim read out of one sentence — a reader that found none, or two, is blind."""
        claims = read(text)
        assert len(claims) == 1, f"{claims} — exactly one count was written in {text!r}"
        return claims[0]

    _stated, spans = one(
        "Four mutations were proven to fire: a `fee` row, a `credit(…, 'fee')` call, "
        "a `x, y` span that is one clause, and a `z)` closer inside a span. End."
    )
    assert len(spans) == 4, spans
    assert any("x, y" in c for c in spans), spans
    assert not any(c.endswith(")") for c in spans), spans

    assert one("Twenty-five mutations were proven to fire: a, b.")[0] == 25, "a hyphenated word"
    assert one("25 mutations were proven to fire: a, b.")[0] == 25, "digits"
    assert read("These mutations were proven to fire, and the record is gone.") == []
    assert one("Nine mutations were proven to fire. The list is late: a, b.")[1] == [], (
        "a full stop ends the claim, and a list arriving in the next sentence enumerates "
        "nothing this number can be checked against"
    )
    assert one("Nine mutations were proven to fire (a, b, c")[1] == [], (
        "a bracket that never closes is not a list"
    )


# ---------------------------------------------------------------------------
# The byte-level inventory: which tracked sources carry a UTF-8 BOM.
# ---------------------------------------------------------------------------

_BOM_BYTES = b"\xef\xbb\xbf"
_BOM_CLAIM = re.compile(r"\b([\w-]+) tracked files carry a UTF-8 BOM\s*\(([^)]*)\)", re.I)
_SPAN = re.compile(r"`([^`]*)`")


def _bom_bearing_tracked_files() -> list[str]:
    """Every tracked path whose first three bytes are a UTF-8 BOM.

    No editor shows the character, so it arrives by accident and leaves by accident. AGENTS.md
    lists the files a patch script must read with `utf-8-sig` and put the BOM back into, which
    makes that list a claim about bytes in this checkout — exactly the kind of number the rest of
    this file refuses to leave unmeasured.
    """
    names = []
    for name in sorted(_tracked_files()):
        path = ROOT / PurePosixPath(name)
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(3) == _BOM_BYTES:
                names.append(name)
    assert len(names) >= 8, f"only {len(names)} tracked files carry a BOM — the byte scan is blind"
    return names


def _documented_bom_claims(text: str, tracked: set[str]) -> list[tuple[int, set[str], list[str]]]:
    """`(count, named paths, spans the reader could not resolve)` for each BOM inventory in a doc.

    The window is the parenthesised list the sentence itself opens, so a law name mentioned
    beside it is not swallowed into the inventory as a missing file — it is reported instead.
    """
    claims = []
    for match in _BOM_CLAIM.finditer(" ".join(text.split())):
        stated = _count_word(match.group(1))
        if stated is None:
            continue
        named, unknown = set(), []
        for span in _SPAN.findall(match.group(2)):
            if span in tracked:
                named.add(span)
            else:
                unknown.append(span)
        claims.append((stated, named, unknown))
    return claims


def test_the_documented_bom_inventory_is_the_bom_inventory_of_the_tree():
    """A patch script trusts this document about which files keep a BOM; the tree is the witness."""
    tracked = set(_tracked_files())
    measured = set(_bom_bearing_tracked_files())
    claims = [
        (path.relative_to(ROOT).as_posix(), *claim)
        for path in MARKDOWN
        for claim in _documented_bom_claims(
            path.read_text(encoding="utf-8-sig", errors="replace"), tracked
        )
    ]
    assert len(claims) == 1, (
        f"{len(claims)} documents state a BOM inventory ({[c[0] for c in claims]}) — zero means the "
        "sentence was reworded out of the shape the reader reads, which is a stale document "
        "reporting a clean tree, and two means the tree has two answers"
    )
    where, stated, named, unknown = claims[0]
    assert named, f"{where} states a BOM inventory that names no tracked file"
    assert not unknown, (
        f"{where} puts {sorted(unknown)} in the inventory and none of them is a tracked path"
    )
    assert stated == len(named), f"{where} says {stated} files carry a BOM and then names {len(named)}"
    assert named == measured, (
        f"{where} is stale — it names {sorted(named - measured)} that carry no BOM, while "
        f"{sorted(measured - named)} carry one the document never mentions"
    )


def test_a_bom_bearing_python_source_parses_only_after_the_sig():
    """Why the inventory is worth a law: the same bytes are a module to one reader and a crash to another."""
    proven = 0
    for name in _bom_bearing_tracked_files():
        if not name.endswith(".py"):
            continue
        path = ROOT / PurePosixPath(name)
        with pytest.raises(SyntaxError):
            ast.parse(path.read_text(encoding="utf-8"), filename=name)
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
        proven += 1
    assert proven >= 8, f"only {proven} BOM'd modules proved the rule — the .py filter is blind"


def test_the_bom_inventory_reader_reads_the_claim_a_document_writes():
    tracked = {"bot/main.py", "web/static/style.css", "bot/handlers/ai.py"}
    plain = _documented_bom_claims(
        "Twelve tracked files carry a UTF-8 BOM (`bot/main.py`, `web/static/style.css`) — read them.",
        tracked,
    )
    assert plain == [(12, {"bot/main.py", "web/static/style.css"}, [])], plain

    wrapped = _documented_bom_claims(
        "Ten tracked files carry a UTF-8 BOM (`bot/main.py`,\n  `web/static/style.css`) done.",
        tracked,
    )
    assert wrapped == [(10, {"bot/main.py", "web/static/style.css"}, [])], (
        "a list wrapping across source lines is one list"
    )

    digits = _documented_bom_claims("3 tracked files carry a UTF-8 BOM (`bot/main.py`)", tracked)
    assert digits == [(3, {"bot/main.py"}, [])], "a count written as digits is the same claim"

    law_name = _documented_bom_claims(
        "Twelve tracked files carry a UTF-8 BOM (`bot/main.py`, `tests/test_docs.py::a_law`)",
        tracked,
    )
    assert law_name == [(12, {"bot/main.py"}, ["tests/test_docs.py::a_law"])], (
        "a span that is not a tracked path is reported, not dropped: a reader that quietly lost "
        "one would certify an inventory missing a file"
    )

    assert _documented_bom_claims("Ten files carry a UTF-8 BOM (`bot/main.py`)", tracked) == [], (
        "the sentence has to state a count of tracked files for the reader to check it"
    )
    assert _documented_bom_claims("Several tracked files carry a UTF-8 BOM (`bot/main.py`)", tracked) == [], (
        "«Several» states no number, so there is nothing to measure against the tree"
    )
    assert _documented_bom_claims("Twelve tracked files carry a UTF-8 BOM: `bot/main.py`.", tracked) == [], (
        "a list the reader cannot bound is not read — which the inventory law reports as zero claims"
    )
