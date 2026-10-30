"""CI workflow contract: pinned actions, least privilege, and gates that can fail.

A workflow is the one file in the repo nobody runs locally, so a weakened gate
there is invisible until it matters. These tests read the YAML as text on
purpose: adding a YAML dependency to the suite to validate three files would
make the check heavier than the thing it checks.
"""

import re
from pathlib import Path

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))

# A step may only invoke a local action or a commit SHA. Tags and branch names
# (`@v4`, `@main`) are mutable: the owner of the upstream repo can repoint them
# at any commit, and the next workflow run executes that code with the repo's
# token.
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.M)
_PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.\-/]+@[0-9a-f]{40}$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflows_exist():
    # An empty glob would make every assertion below pass vacuously.
    assert {p.name for p in WORKFLOWS} == {"ci.yml", "codeql.yml", "prod-health-check.yml"}


def test_every_action_is_pinned_to_a_commit_sha():
    offenders = []
    for path in WORKFLOWS:
        for ref in _USES.findall(_read(path)):
            if ref.startswith("./"):
                continue
            if not _PINNED.match(ref):
                offenders.append(f"{path.name}: {ref}")
    assert not offenders, (
        "third-party actions must be pinned to a 40-character commit SHA "
        "(a tag or branch is mutable and can be repointed upstream):\n"
        + "\n".join(offenders)
    )


def test_every_workflow_declares_read_only_permissions():
    for path in WORKFLOWS:
        text = _read(path)
        assert re.search(r"^permissions:", text, re.M), f"{path.name} has no top-level permissions"
        assert "write-all" not in text, path.name
        assert re.search(r"^  contents: read$", text, re.M), f"{path.name} lost `contents: read`"


def test_no_workflow_targets_pull_request_target():
    # `pull_request_target` runs with the base repo's secrets and token while
    # checking out attacker-controlled code; nothing here needs it.
    for path in WORKFLOWS:
        assert "pull_request_target" not in _read(path), path.name


def test_no_gate_is_masked():
    """`continue-on-error`, `|| true` and `--no-verify` turn a red gate green
    without anyone deciding to stop checking. None of the three has a
    legitimate use in these workflows: a step that is allowed to fail is a step
    that does not gate anything."""
    for path in WORKFLOWS:
        text = _read(path)
        assert "continue-on-error" not in text, path.name
        assert "|| true" not in text, path.name
        assert "--no-verify" not in text, path.name
        assert re.search(r"pytest[^\n]*\|\|", text) is None, path.name


def test_service_images_are_not_floating():
    # `:latest` makes the job's environment change without a commit.
    for path in WORKFLOWS:
        for image in re.findall(r"^\s+image:\s*(\S+)", _read(path), re.M):
            assert not image.endswith(":latest"), f"{path.name}: {image}"
            assert ":" in image.split("/")[-1], f"{path.name}: {image} has no tag"


def test_ci_ruff_pin_matches_the_local_pin():
    """The lint job installed ruff 0.16.4 while requirements-dev.txt pinned
    0.17.2, so `ruff check` could accept locally what CI rejects (or the other
    way round) on a rule that changed between the two releases. One pin, one
    version, in both places."""
    root = Path(__file__).resolve().parents[1]
    ci = _read(root / ".github" / "workflows" / "ci.yml")
    ci_pin = re.search(r"pip install ruff==(\S+)", ci)
    assert ci_pin, "the lint job no longer installs ruff by exact version"
    dev_pin = re.search(r"^ruff==(\S+)$", _read(root / "requirements-dev.txt"), re.M)
    assert dev_pin, "requirements-dev.txt no longer pins ruff"
    assert ci_pin.group(1) == dev_pin.group(1), (
        f"CI lints with ruff {ci_pin.group(1)}, the local gate with {dev_pin.group(1)}"
    )


def test_ci_runs_on_the_interpreter_the_image_ships():
    """The image moved to a newer Python than the suite runs on, so the
    interpreter that executes in production was the one nobody tested. The
    runtime job has to name the Dockerfile's version, or a base-image bump
    reintroduces the blind spot."""
    root = Path(__file__).resolve().parents[1]
    image = re.search(
        r"^FROM python:(\d+\.\d+)-", (root / "Dockerfile").read_text(encoding="utf-8"), re.M
    )
    assert image, "the Dockerfile no longer pins a python:X.Y base image"
    ci = _read(root / ".github" / "workflows" / "ci.yml")
    tested = set(re.findall(r'python-version: "(\d+\.\d+)"', ci))
    assert image.group(1) in tested, (
        f"the image runs Python {image.group(1)} but CI only tests {sorted(tested)}"
    )
    assert "compileall -q bot web deploy agent" in ci
    assert "import aiogram, web3, fastapi" in ci


def test_ci_test_and_lint_commands_are_intact():
    """A workflow that quietly stops running the suite, or narrows it to a
    directory, still reports green. Pin the two commands the gate depends on."""
    ci = _read(Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml")
    assert "run: ruff check bot web tests deploy agent scripts" in ci
    assert 'run: python -m pytest -q -m "not production"' in ci
    # `--continue-on-collection-errors` turns an unimportable test module into a
    # green run with a warning, which is exactly the silent gap this bans.
    assert "--continue-on-collection-errors" not in ci
