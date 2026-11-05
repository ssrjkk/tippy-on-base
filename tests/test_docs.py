"""Documentation links must point at files that exist.

A README link to a deleted chapter 404s for every reader, and a source comment
pointing at a documentation file that was never committed sends the next
maintainer hunting for nothing (one such pointer lived in bot/base.py until
this gate found it). Both are cheap to check from the tree.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
# The vendored OpenZeppelin tree ships its own links and is not ours to fix.
SKIP_DIRS = {"contracts", ".git", ".qoder", "node_modules", "site-packages"}


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
            if re.search(r"non-?custodial", line, re.I):
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
    assert not invented, f"{invented} are addresses no file in the repo defines"


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
