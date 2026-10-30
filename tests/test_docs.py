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
