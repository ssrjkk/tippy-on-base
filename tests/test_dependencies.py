"""The dependency surface: what the image installs against what the code imports.

Two vehicles run this repository's Python. The host runs a virtualenv built from
``requirements-dev.txt`` (which is what CI installs, and what the maintainer's machine
carries), and the container runs an image built from ``Dockerfile``, whose only install
line is ``pip install --no-cache-dir -r requirements.txt``. A module imported by the
runtime and supplied only by the first vehicle therefore works everywhere anybody tests
it and dies on boot in the one artifact the runbooks deploy.

That is not a hypothetical here: ``bot/paymaster.py`` names ``httpx`` at module scope,
``bot/handlers/_common.py`` imports ``paymaster``, and every handler imports ``_common``.
``httpx`` was declared in ``requirements-dev.txt`` and reached ``requirements.txt`` only
as an *extra* of ``fastapi`` and ``starlette`` — which ``pip install -r`` never resolves.
So ``docker compose up -d app`` produced a container that raised ``ModuleNotFoundError``
before it read ``.env``, while the whole suite stayed green.
"""

import ast
import importlib.metadata as md
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

#: The tree the container boots: the bot process, the origin, the agent, the supervisor
#: and the migrations. ``scripts/`` is deliberately outside this law — it is operator and
#: developer tooling, it imports things no runtime image should carry (``solcx`` compiles
#: contracts, ``playwright`` draws the docs pictures), and it reaches first-party modules
#: by bare name through its own ``sys.path`` hack.
RUNTIME_DIRS = ("bot", "web", "agent", "deploy", "alembic")

#: Packages of this repository, which are never installed by a requirements file.
FIRST_PARTY = {"bot", "web", "agent", "deploy", "scripts", "alembic", "tests", "functions", "contracts", "conftest"}

_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_MARKER_VERSION_RE = re.compile(r'python_version\s*(<=|>=|==|!=|<|>)\s*["\']([\d.]+)["\']')
_HOST_MARKER_RE = re.compile(r"os_name|platform_system|sys_platform|platform_machine|platform_release|platform_version")


def _norm(name: str) -> str:
    """PEP 503-ish: ``Pillow``, ``pillow`` and ``Pillow-1.0`` name one project."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _version_tuple(text: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3])


def marker_applies(marker: str) -> bool:
    """Whether a PEP 508 marker can be true for ``pip install -r`` on this interpreter.

    ``extra == "standard"`` never is: an install line that names no extra resolves no
    extras, and that single fact is the difference between the CI venv and the image.
    A ``python_version`` comparison is evaluated against this interpreter, because a
    dependency held back to an old Python (``exceptiongroup``) is not a gap in the
    image. Every other marker — host, platform, implementation — is treated as
    applicable: the image is Linux and this gate may run anywhere, so refusing a
    platform-conditional package would report a missing dependency that the deployed
    container really does have.
    """
    text = (marker or "").strip().lower()
    if not text:
        return True
    if "extra" in text:
        return False
    here = _version_tuple(sys.version.split()[0])
    for op, want in _MARKER_VERSION_RE.findall(text):
        there = _version_tuple(want)
        if op == "<" and not here < there:
            return False
        if op == "<=" and not here <= there:
            return False
        if op == ">" and not here > there:
            return False
        if op == ">=" and not here >= there:
            return False
        if op == "==" and here[: len(there)] != there:
            return False
        if op == "!=" and here[: len(there)] == there:
            return False
    return True


def import_names_of(dist_name: str) -> set[str]:
    """The top-level module names one installed distribution puts on ``sys.path``.

    ``top_level.txt`` is the declared answer; a wheel that ships none says so through
    its ``RECORD``, where a package is a directory holding ``__init__.py``. The bare
    project name is always included — the common case is ``psycopg`` importing as
    ``psycopg`` — and a distribution that is not installed contributes nothing, which
    the caller reports rather than treating as satisfied.
    """
    names = {_norm(dist_name)}
    try:
        dist = md.distribution(dist_name)
    except md.PackageNotFoundError:
        return names
    try:
        top = dist.read_text("top_level.txt")
    except Exception:
        top = None
    if top:
        names.update(_norm(line) for line in top.split() if line.strip())
        return names
    try:
        record = dist.read_text("RECORD") or ""
    except Exception:
        record = ""
    for line in record.splitlines():
        head = line.split(",", 1)[0].strip().replace("\\", "/")
        parts = head.split("/")
        if len(parts) >= 2 and parts[-1] == "__init__.py" and not parts[0].endswith(".dist-info"):
            names.add(_norm(parts[0]))
    return names


def _project_of(requirement: str) -> str | None:
    """The project name a PEP 508 line asks for, or ``None`` for a line that asks for none."""
    text = requirement.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("-"):
        return None  # an option line (`-r other.txt`, `--hash=…`): not a package
    if "://" in text.split("#")[0]:
        return None  # a URL or VCS pin: no registry name to walk
    match = _REQ_NAME_RE.match(text)
    return match.group(1) if match else None


def requirements_lines(path: pathlib.Path, _seen: frozenset = frozenset()) -> list[str]:
    """Every requirement line of a file, with ``-r``/``-c`` includes followed.

    ``requirements-dev.txt`` is ``-r requirements.txt`` plus tooling, so a pin read from
    one file without following the include would compare an empty set against itself.
    """
    if not path.is_file() or path.resolve() in _seen:
        return []
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        include = re.match(r"^-\s*(?:r|c)\s+(\S+)", text) or re.match(r"^-(?:r|c)(\S+)", text)
        if include:
            lines.extend(requirements_lines(path.parent / include.group(1), _seen | {path.resolve()}))
            continue
        lines.append(text)
    return lines


def pins_of(path: pathlib.Path) -> dict[str, str]:
    """``project -> pinned version`` for each ``name==version`` line it resolves to."""
    found: dict[str, str] = {}
    for line in requirements_lines(path):
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^;\s]+)", line)
        if match:
            found[_norm(match.group(1))] = match.group(2)
    return found


def image_closure(requirements_path: pathlib.Path) -> tuple[set[str], set[str], set[str]]:
    """What ``pip install -r requirements.txt`` puts on ``sys.path``.

    Returns the import roots available, the distributions walked, and the names that are
    not installed *here* so their own requires could not be read. The third value exists
    because a law that silently walked a smaller tree would certify a broken image; the
    caller prints it and floors the first two.
    """
    available: set[str] = set()
    walked: set[str] = set()
    unresolved: set[str] = set()
    queue = [name for name in map(_project_of, requirements_lines(requirements_path)) if name]
    while queue:
        name = queue.pop()
        key = _norm(name)
        if key in walked:
            continue
        walked.add(key)
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            unresolved.add(key)
            continue
        available |= import_names_of(name)
        for req in dist.requires or []:
            head, _, marker = req.partition(";")
            if not marker_applies(marker):
                continue
            child = _project_of(head)
            if child:
                queue.append(child)
    return available, walked, unresolved


def runtime_import_roots() -> dict[str, list[str]]:
    """``import root -> the runtime files that name it``, read from the AST.

    Module scope and function scope are the same to this scan on purpose: an import
    inside a handler still raises the moment that handler runs inside the container.
    What it cannot see is a name built at runtime — ``importlib.import_module(var)`` or
    ``__import__(f"{pkg}")`` — which is stated here rather than hidden behind an
    exception list; nothing in this tree imports money or HTTP that way today.
    """
    roots: dict[str, list[str]] = {}

    def add(root: str, path: pathlib.Path, lineno: int) -> None:
        key = _norm(root)
        entry = f"{path.relative_to(REPO).as_posix()}:{lineno}"
        if entry not in roots.setdefault(key, []):
            roots[key].append(entry)

    for pkg in RUNTIME_DIRS:
        for path in sorted((REPO / pkg).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        add(alias.name.split(".")[0], path, node.lineno)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    add(node.module.split(".")[0], path, node.lineno)
    return roots


def third_party_runtime_roots() -> dict[str, list[str]]:
    """The runtime imports that are neither the standard library nor this repository."""
    std = set(sys.stdlib_module_names)
    local = {_norm(p) for p in FIRST_PARTY} | {_norm("__future__")}
    return {
        root: files
        for root, files in runtime_import_roots().items()
        if root not in std and root not in local
    }


def test_every_module_the_runtime_imports_is_one_the_image_installs():
    """A dependency that only the dev vehicle carries is a container that never boots.

    The subject set is the runtime tree itself, so a new import at module scope joins
    this law the commit it lands, and no list in this file has to be edited for that to
    happen. Measured in Linux CI: 13 declared projects, a 69-distribution closure,
    77 import names and 18 third-party roots the code reaches. The original 100-root
    floor was host-dependent: Windows metadata counted pywin32 modules the Linux image
    never installs. The closure is checked against its declared projects instead, and
    an unresolved distribution fails explicitly.

    The floors refuse a scan that went blind on either side: an empty closure passes
    against an empty import set, and both empties look exactly like a clean tree.
    """
    available, walked, unresolved = image_closure(REPO / "requirements.txt")
    used = third_party_runtime_roots()
    declared = {
        _norm(name)
        for name in map(_project_of, requirements_lines(REPO / "requirements.txt"))
        if name
    }
    assert len(declared) >= 10, (
        f"only {len(declared)} declared runtime projects read — the reader is blind"
    )
    assert declared <= walked, (
        f"declared projects missing from the image closure: {sorted(declared - walked)}"
    )
    assert len(walked) >= 50, (
        f"the image closure walked only {len(walked)} distributions — the walk is blind"
    )
    assert not unresolved, (
        f"distributions whose own requirements could not be read: {sorted(unresolved)}"
    )
    assert len(available) >= len(declared), (
        f"the image closure names {len(available)} import roots — the walk is blind"
    )
    assert len(used) >= 8, f"the runtime scan found {sorted(used)} — the import reader is blind"
    missing = {root: files for root, files in used.items() if root not in available}
    assert not missing, (
        "the runtime imports what `pip install -r requirements.txt` does not install: "
        + "; ".join(f"`{root}` ({', '.join(files[:3])})" for root, files in sorted(missing.items()))
        + (f" — distributions whose own requires could not be read here: {sorted(unresolved)}" if unresolved else "")
    )


def test_a_package_the_image_and_ci_both_install_is_pinned_to_one_version():
    """Two vehicles, two pins of one package, and CI tests a library production does not run.

    ``requirements-dev.txt`` starts with ``-r requirements.txt``, so an unqualified pin of
    a runtime package in the dev file silently overrides it during every CI run. Every
    project the two files share has to answer the same version, and every runtime line has
    to be an exact pin: the dev file's own header says an unpinned pytest 9 broke CI, and
    the image has the same exposure to an unpinned major.
    """
    runtime = pins_of(REPO / "requirements.txt")
    dev = pins_of(REPO / "requirements-dev.txt")
    assert len(runtime) >= 10, f"only {len(runtime)} pinned runtime requirements read — the reader is blind"
    unpinned = [
        line for line in requirements_lines(REPO / "requirements.txt")
        if _project_of(line) and "==" not in line
    ]
    assert not unpinned, f"a runtime requirement is not an exact pin: {unpinned}"
    drift = {name: (runtime[name], dev[name]) for name in runtime if name in dev and runtime[name] != dev[name]}
    assert not drift, "pinned twice with two versions: " + "; ".join(
        f"`{name}` image {want} vs CI {other}" for name, (want, other) in sorted(drift.items())
    )


def test_the_dependency_reader_refuses_the_shapes_a_requirement_line_takes(tmp_path):
    """The reader is held to the markup a requirements file actually carries.

    Each shape below decides a real outcome: a line that is read as a package when it is
    an option or a URL invents a dependency, and — worse — a requirement held by an extra
    read as installed is the exact false pass this file exists to refuse.
    """
    assert marker_applies("") is True
    assert marker_applies('extra == "standard"') is False
    assert marker_applies("extra == \"all\"") is False
    assert marker_applies(f'python_version < "{sys.version_info[0] - 1}"') is False
    assert marker_applies('python_version >= "3.8"') is True
    assert marker_applies('platform_system == "Linux"') is True, "a host marker must not shrink the image's closure"

    assert _project_of("psycopg[binary]==3.3.8") == "psycopg"
    assert _project_of("-r requirements.txt") is None, "an include line names no package"
    assert _project_of("# a comment") is None
    assert _project_of("--hash=sha256:aaaa") is None
    assert _project_of("pkg @ https://example.invalid/pkg.tar.gz") is None

    scratch = tmp_path / "_dep_reader_requirements.txt"
    other = tmp_path / "_dep_reader_base.txt"
    other.write_text("alpha==1.2.3\n", encoding="utf-8")
    scratch.write_text(
        "-r _dep_reader_base.txt\n# comment\nBETA-GAMMA == 4.5.6\nnot-a-pin\n", encoding="utf-8"
    )
    assert pins_of(scratch) == {"alpha": "1.2.3", "beta-gamma": "4.5.6"}, "an include or a spaced pin was lost"
    assert requirements_lines(scratch) == ["alpha==1.2.3", "BETA-GAMMA == 4.5.6", "not-a-pin"]

    assert import_names_of("definitely-not-installed-anywhere") == {"definitely-not-installed-anywhere"}
    names = import_names_of("Pillow")
    assert "pillow" in names, "the project name itself was not read"
    assert "pil" in names, "a module named unlike its distribution was not read"
