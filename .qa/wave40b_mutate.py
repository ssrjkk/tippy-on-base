"""Mutation proofs for the wave-40b laws (dependency surface + address escaping).

Each mutation is applied to the live tree, the named test is run, and the file is
restored byte-for-byte. The script asserts the restore matched, so a mutation can never
be left behind in the tree the suite measures.
"""
import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def _read(rel: str) -> bytes:
    return (REPO / rel).read_bytes()


def _write(rel: str, data: bytes) -> None:
    (REPO / rel).write_bytes(data)


MUTATIONS = [
    (
        "httpx removed from requirements.txt",
        ("requirements.txt",),
        lambda r: (r, r.replace(b"httpx==0.28.1\n", b"")),
        "tests/test_dependencies.py::test_every_module_the_runtime_imports_is_one_the_image_installs",
    ),
    (
        "the paymaster imports a package nothing declares",
        ("bot/paymaster.py",),
        lambda r: (r, r.replace(b"import httpx", b"import zzz_not_declared_anywhere", 1)),
        "tests/test_dependencies.py::test_every_module_the_runtime_imports_is_one_the_image_installs",
    ),
    (
        "requirements-dev.txt re-pins httpx to a second version",
        ("requirements-dev.txt",),
        lambda r: (r, r.replace(b"httpx==0.28.1", b"httpx==0.27.0")),
        "tests/test_dependencies.py::test_a_package_the_image_and_ci_both_install_is_pinned_to_one_version",
    ),
    (
        "a runtime requirement loses its exact pin",
        ("requirements.txt",),
        lambda r: (r, r.replace(b"qrcode==8.2", b"qrcode>=8")),
        "tests/test_dependencies.py::test_a_package_the_image_and_ci_both_install_is_pinned_to_one_version",
    ),
    (
        "the runtime import walk is blinded",
        ("tests/test_dependencies.py",),
        lambda r: (r, r.replace(b'RUNTIME_DIRS = ("bot", "web", "agent", "deploy", "alembic")',
                                b'RUNTIME_DIRS = ()')),
        "tests/test_dependencies.py::test_every_module_the_runtime_imports_is_one_the_image_installs",
    ),
    (
        "every environment marker is refused, so the closure shrinks",
        ("tests/test_dependencies.py",),
        lambda r: (r, r.replace(b'    if "extra" in text:\n        return False',
                                b'    if text:\n        return False')),
        "tests/test_dependencies.py::test_every_module_the_runtime_imports_is_one_the_image_installs",
    ),
    (
        "one on-chain addr= argument back to raw",
        ("bot/handlers/onchain.py",),
        lambda r: (r, r.replace(b"addr=esc(addr)) + registry_note", b"addr=addr) + registry_note")),
        "tests/test_handlers.py::test_user_text_reaching_an_html_reply_is_escaped",
    ),
    (
        "one wallet addr= argument back to raw",
        ("bot/handlers/wallet.py",),
        lambda r: (r, r.replace(b"i18n.t(lang, 'wallet_active_switched', slot=active['slot'], addr=common._h(active['address']))",
                                b"i18n.t(lang, 'wallet_active_switched', slot=active['slot'], addr=active['address'])")),
        "tests/test_handlers.py::test_user_text_reaching_an_html_reply_is_escaped",
    ),
    (
        "the escaper reader stops following the assignment hop",
        ("tests/test_handlers.py",),
        lambda r: (r, r.replace(b"    writes = hops.get(name)\n    if not writes:\n        return False",
                                b"    return False\n    writes = hops.get(name)\n    if not writes:\n        return False")),
        "tests/test_handlers.py::test_user_text_reaching_an_html_reply_is_escaped",
    ),
    (
        "the user-text field set is emptied",
        ("tests/test_handlers.py",),
        lambda r: (r, r.replace(b'ESCAPERS = ("_h("', b'USER_TEXT_FIELDS = set()' + bytes([10]) + b'ESCAPERS = ("_h("', 1)),
        "tests/test_handlers.py::test_user_text_reaching_an_html_reply_is_escaped",
    ),
]


def run(nodeid: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", nodeid, "-q", "--no-header",
         "-p", "no:cacheprovider", "--timeout=120", "--timeout-method=thread"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:] or ["<no output>"]
    return proc.returncode != 0, tail[0]


def main() -> int:
    results = []
    for name, files, mutate, nodeid in MUTATIONS:
        originals = {rel: _read(rel) for rel in files}
        try:
            for rel in files:
                data, mutated = mutate(originals[rel])
                assert mutated != data, f"mutation {name!r} changed nothing in {rel}"
                _write(rel, mutated)
            fired, summary = run(nodeid)
        finally:
            for rel, blob in originals.items():
                _write(rel, blob)
            restored = all(_read(rel) == blob for rel, blob in originals.items())
        results.append((name, fired, restored, summary))
        print(f"[{'FIRED ' if fired else 'SURVIVED'}] restore_ok={restored} {name}\n           {summary[:150]}")
    bad = [r for r in results if not (r[1] and r[2])]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations fired and every file was restored")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
