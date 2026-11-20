"""Prove each preview gate is measured by a row of its own: mutate one line of the
shipped Function at a time, run the law, restore. Never leaves the tree dirty."""
import pathlib
import subprocess
import sys

F = pathlib.Path("functions/[[path]].js")
orig = F.read_bytes().decode("utf-8")

MUTS = {
    "gate on the project (DEMO_HTML)": "if ((env.DEMO_HTML || '').trim() !== '1') return false;",
    "gate on the method (a read)": "if (request.method !== 'GET' && request.method !== 'HEAD') return false;",
    "gate on Accept": "if (!(request.headers.get('accept') || '').includes('text/html')) return false;",
    "gate on /api/ and /_": "if (pathname.startsWith('/api/') || pathname.startsWith('/_')) return false;",
    "gate on a format suffix": (
        "if (/\\.[a-z0-9]+$/i.test(pathname.split('/').pop() || '')) return false;"
    ),
    "probe against the single-page capture": "return captured ? null : previewDocument(body);",
}
REPLACES = {
    "probe against the single-page capture": ("return captured ? null : previewDocument(body);",
                                             "return previewDocument(body);"),
}

rows = []
for name, line in MUTS.items():
    assert line in orig, f"mutation site vanished: {name}"
    a, b = REPLACES.get(name, (line, ""))
    F.write_bytes(orig.replace(a, b, 1).encode("utf-8"))
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_design_system.py", "-q",
         "-k", "previews_the_bundle", "--timeout=180", "-p", "no:cacheprovider"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    tail = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
    fired = (name, r.returncode != 0, tail[-1] if tail else r.stdout[-200:])
    rows.append(fired)
    F.write_bytes(orig.encode("utf-8"))

for name, fired, tail in rows:
    print(f"{'ГОРИТ  ' if fired else 'ВЫЖИЛА(!)'} {name}\n            {tail}")
print("\nвсего мутаций:", len(rows), "| горит:", sum(1 for _, f, _ in rows if f))
