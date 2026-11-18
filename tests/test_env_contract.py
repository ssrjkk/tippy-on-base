"""The configuration contract: what is a knob, where it is written down, and
what must never be filled in.

Three files describe the same surface — the code that reads `os.environ`,
`.env.example` (+ `deploy/prod.env.example`), and the docs. Two of them can say
anything: an operator who sets a knob that no code reads believes they changed
behaviour, and a knob that exists but is written down nowhere is set only by
accident. Measured both directions here, the same way every other claim in this
repository is measured.
"""

import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIRS = ("bot", "web", "agent", "scripts", "deploy", "functions", "alembic")
TEMPLATES = (".env.example", "deploy/prod.env.example")

# Read by the test suite and the rendered-design harness, never by the running
# service — they configure a laptop or a CI job, so they are not operator knobs
# and do not belong in a deployment template.
HARNESS_ONLY = {
    "TESTING": "disables the web rate limiter and the money cooldown inside tests",
    "TEST_DATABASE_URL": "tests/conftest.py: the disposable tipbot_test database",
    "TEST_ADMIN_DATABASE_URL": "tests/test_admin.py: the read-only panel probe",
    "DESIGN_GATE_URL": "tests/test_production_design.py: measure a local origin instead",
    "DESIGN_GATE_SESSION": "tests/test_production_design.py: a signed session for real lists",
}

ENV_READ = re.compile(r"""os\.(?:environ\.get|getenv)\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]""")
ENV_INDEX = re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]""")
# A name handed to a validating wrapper is still a name the code reads: the
# alternative would be a knob this scanner cannot see, which is how `alembic/env.py`
# looked like it invented two of them the moment its timeouts gained a guard. The
# wrapper is pinned on the other side by
# test_every_number_alembic_writes_into_a_set_statement_comes_from_the_guard.
ENV_GUARDED_READ = re.compile(r"""_milliseconds\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]""")
TEMPLATE_LINE = re.compile(r"^#?([A-Z][A-Z0-9_]{2,})=(.*)$", re.M)
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSPHRASE)$")
PLACEHOLDER = re.compile(r"^(0x0+|123456:ABC-|change[-_]me|CHANGE_ME|<.*>|\.\.\.|xxx+$)|"
                         r"(Your|your|example|placeholder|REPLACE)", re.I)


def _sources():
    for name in APP_DIRS:
        folder = ROOT / name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".sh"} and "node_modules" not in path.parts:
                yield path


def _read_by_code() -> set[str]:
    names: set[str] = set()
    for path in _sources():
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        names |= set(ENV_READ.findall(text)) | set(ENV_INDEX.findall(text)) | set(ENV_GUARDED_READ.findall(text))
    return names


def _read_by_deployment() -> set[str]:
    """Names consumed by compose, the images and CI rather than by Python."""
    names: set[str] = set()
    for path in list(ROOT.glob("docker-compose*.yml")) + list((ROOT / ".github/workflows").glob("*.yml")) \
            + list(ROOT.glob("Dockerfile*")):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        names |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]{2,})", text))
        names |= set(re.findall(r"^\s+([A-Z][A-Z0-9_]{2,}):", text, re.M))
    return names


def _template_names(pattern: str | None = None):
    """[(file, name, value, commented)] for both templates."""
    rows = []
    for rel in TEMPLATES:
        text = (ROOT / rel).read_text(encoding="utf-8-sig")
        for line in text.splitlines():
            m = re.match(r"^(#?)([A-Z][A-Z0-9_]{2,})=(.*)$", line)
            if m and (pattern is None or re.match(pattern, m.group(2))):
                rows.append((rel, m.group(2), m.group(3), m.group(1) == "#"))
    return rows


def _numeric_knobs() -> dict[str, str]:
    """Knobs the code wraps in int()/float() WITHOUT a fallback for the empty
    string: `int(os.environ.get("PORT", 8000))` crashes on `PORT=` — the default
    is only used when the name is absent, not when it is blank — while
    `int(os.environ.get("OWNER_ID", "") or "0")` survives it. The distinction is
    the whole point of the gate, so an `or` anywhere in the parsed expression
    marks the knob guarded and it is left out.
    """
    found: dict[str, str] = {}
    for path in _sources():
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("int", "float") and node.args):
                continue
            guarded = any(isinstance(sub, ast.BoolOp) and isinstance(sub.op, ast.Or)
                          for sub in ast.walk(node))
            if guarded:
                continue
            for name in re.findall(r"""os\.(?:environ\.get|getenv)\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]""",
                                   ast.unparse(node.args[0])):
                found.setdefault(name, f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    return found


def test_every_knob_the_code_reads_is_written_down_somewhere():
    """A knob nobody documents is a knob that gets set by reading the source —
    and the ones added here are money and safety settings (the staged-withdrawal
    window, the paymaster's USD ceiling, the login rate limit)."""
    read = _read_by_code()
    assert len(read) > 100, f"only {len(read)} env names found — the scan is blind"

    written = {name for _f, name, _v, _c in _template_names()}
    for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        written |= set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", text))

    undocumented = sorted(n for n in read - written if n not in HARNESS_ONLY)
    assert not undocumented, (
        f"{undocumented} are read by the code and named in no template, no doc: an operator "
        f"cannot discover them without reading the source")


def test_no_template_offers_a_knob_that_nothing_reads():
    """The lie in the other direction is worse, because it is believed: an
    operator sets `WETH_ADDRESS=` (a per-chain constant in `bot/config.py`, never
    read from the environment), and believes a DEX route moved."""
    read = _read_by_code() | _read_by_deployment() | set(HARNESS_ONLY)
    offered = _template_names()
    assert offered, "no templates parsed — the scan is blind"
    phantom = sorted({f"{rel}:{name}" for rel, name, _v, commented in offered
                      if not commented and name not in read})
    assert not phantom, f"{phantom} are offered as configuration and read by nothing"


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_numeric_knob_is_never_offered_empty(template):
    """`int(os.environ.get('PORT', …))` answers an empty `PORT=` with
    `ValueError`, so a template line can stop the boot of a service that was
    configured correctly in every other way. Commented examples are allowed."""
    numeric = _numeric_knobs()
    assert numeric, "no numeric knobs detected — the scan is blind"
    empty = [f"{name}={value!r} (read at {numeric[name]})"
             for _rel, name, value, commented in _template_names()
             if _rel == template and not commented and name in numeric and not value.strip()]
    assert not empty, f"{template} offers {empty} empty; int()/float() will raise"


def test_the_templates_carry_no_plausible_secret():
    """`.env.example` is the file people copy secrets INTO, and it is committed.
    Every secret-shaped line is either empty or an unmistakable placeholder —
    this is what keeps `git add .env.example` from publishing a real key."""
    suspects = [f"{rel}:{name}={value[:12]}…"
                for rel, name, value, commented in _template_names()
                if not commented and SECRET_NAME.search(name) and value.strip()
                and not PLACEHOLDER.search(value.strip())]
    assert not suspects, f"{suspects} look like real secrets in a committed template"


# ---------- the knobs a migration writes into SQL as text ----------

ENV_PY = ROOT / "alembic" / "env.py"


def _milliseconds_guard():
    """The guard is read out of `alembic/env.py` and executed here. Importing that
    file runs a migration, and no test should migrate a database to check that a
    string is a number."""
    source = ENV_PY.read_text(encoding="utf-8")
    block = re.search(r"^def _milliseconds\(.*?(?=\n\S)", source, re.M | re.S)
    assert block, "alembic/env.py has no _milliseconds() to measure — the scan is blind"
    namespace: dict[str, object] = {"os": os}
    exec(compile(block.group(0), "alembic/env.py:_milliseconds", "exec"), namespace)
    return namespace["_milliseconds"]


def test_a_knob_written_into_a_set_statement_refuses_a_value_that_is_not_a_number(monkeypatch):
    """`SET lock_timeout = {LOCK_TIMEOUT_MS}` is text, not a bind parameter: without
    the guard, an operator's typo or a poisoned environment writes SQL."""
    guard = _milliseconds_guard()

    monkeypatch.setenv("ALEMBIC_LOCK_TIMEOUT_MS", "15000; DROP TABLE tx_log")
    with pytest.raises(SystemExit):
        guard("ALEMBIC_LOCK_TIMEOUT_MS", "15000")

    monkeypatch.setenv("ALEMBIC_LOCK_TIMEOUT_MS", "1٥00")  # isdigit(), not a SQL number
    with pytest.raises(SystemExit):
        guard("ALEMBIC_LOCK_TIMEOUT_MS", "15000")

    monkeypatch.setenv("ALEMBIC_LOCK_TIMEOUT_MS", "25000")
    assert guard("ALEMBIC_LOCK_TIMEOUT_MS", "15000") == "25000"

    monkeypatch.setenv("ALEMBIC_LOCK_TIMEOUT_MS", "")
    assert guard("ALEMBIC_LOCK_TIMEOUT_MS", "15000") == "15000", "a blank template line must not reach the server"

    monkeypatch.delenv("ALEMBIC_LOCK_TIMEOUT_MS")
    assert guard("ALEMBIC_LOCK_TIMEOUT_MS", "15000") == "15000"


def test_every_number_alembic_writes_into_a_set_statement_comes_from_the_guard():
    source = ENV_PY.read_text(encoding="utf-8")
    interpolated = set(re.findall(r"SET\s+\w+\s*=\s*\{([A-Z_]+)\}", source))
    assert interpolated, "no interpolated SET in alembic/env.py — the scan is blind"
    unguarded = sorted(
        name for name in interpolated
        if not re.search(rf"^{name} = _milliseconds\(", source, re.M)
    )
    assert not unguarded, f"{unguarded} reach a SQL statement without being read as a number first"


# ---------------------------------------------------------------------------
# The other half of the contract: a knob is only one file's default if the code
# reads it from that file. `agent/tools.py` imports `agent.config` as `config`
# and `bot/config.py` was never in scope there, so the deadline ceiling added in
# wave 36 raised AttributeError on every market the agent tried to create — a
# check the author verified by reading the name, which is spelled right.


CONST_ATTR = re.compile(r"[A-Z][A-Z0-9_]{3,}$")
LOCAL_PACKAGES = ("bot", "web", "agent")


def _python_sources():
    for name in APP_DIRS:
        folder = ROOT / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.py")):
            if "node_modules" not in path.parts:
                yield path


def _module_bindings(tree: ast.AST, package: str) -> dict[str, str]:
    """Every name the file binds, mapped to the dotted path it was imported from."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package if node.level == 1 else package.rsplit(".", node.level - 1)[0]
                prefix = f"{base}.{node.module}" if node.module else base
            else:
                prefix = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                out[name] = f"{prefix}.{alias.name}" if prefix else alias.name
    return out


def _is_importable_module(dotted: str) -> bool:
    import importlib

    if dotted.split(".")[0] not in LOCAL_PACKAGES:
        return False
    try:
        import inspect

        return inspect.ismodule(importlib.import_module(dotted))
    except Exception:  # noqa: BLE001 — a module this harness cannot load is not evidence of a missing knob
        return False


def _knobs_read_off_a_module() -> list[tuple[str, int, str, str]]:
    """`(file, line, module, NAME)` for every constant read through an import."""
    found: list[tuple[str, int, str, str]] = []
    for path in _python_sources():
        rel = path.relative_to(ROOT)
        package = ".".join(rel.parts[:-1])
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:  # a template or a script the harness cannot parse
            continue
        bindings = {
            name: dotted for name, dotted in _module_bindings(tree, package).items()
            if _is_importable_module(dotted)
        }
        if not bindings:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id in bindings
                and CONST_ATTR.fullmatch(node.attr)
            ):
                found.append((rel.as_posix(), node.lineno, bindings[node.value.id], node.attr))
    return found


def test_every_knob_the_runtime_reads_exists_on_the_module_it_reads_it_from():
    read = _knobs_read_off_a_module()
    assert len(read) >= 40, f"only {len(read)} module-read constants found — this scan needs re-reading"
    missing = sorted(
        f"{path}:{line}: {module}.{name}"
        for path, line, module, name in read
        if not hasattr(import_module(module), name)
    )
    assert not missing, (
        "read from a module that does not define them (AttributeError at runtime): "
        + "; ".join(missing)
    )


def import_module(dotted: str):  # noqa: E402 — defined after use, resolved at call time
    import importlib

    return importlib.import_module(dotted)


def test_the_module_binding_reader_resolves_the_imports_this_tree_actually_writes():
    def bound(source: str, package: str) -> dict[str, str]:
        return _module_bindings(ast.parse(source), package)

    assert bound("from . import config", "agent")["config"] == "agent.config"
    assert bound("from bot import config", "agent")["config"] == "bot.config"
    assert bound("from .. import config as runtime", "bot.ledger")["runtime"] == "bot.config"
    assert bound("import bot.config", "agent")["bot"] == "bot.config"
    assert bound("import bot.config as cfg", "agent")["cfg"] == "bot.config"
    assert bound("from bot.ledger import Ledger", "web")["Ledger"] == "bot.ledger.Ledger"
    # `from bot import config` binds a module; `from bot.i18n import t` does not.
    assert _is_importable_module("bot.config") and not _is_importable_module("bot.i18n.t")
