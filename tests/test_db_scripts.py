"""The recovery procedure is shell scripts, and until this file nothing read them.

What measuring them found, in the order the defects mattered:

`pg_dump … | gzip > file` under `set -o pipefail` aborts the script but leaves the
archive `gzip` had already opened. A gzip of an empty payload is a valid file of a
couple dozen bytes, so the size check called it a backup, the retention step
counted it among the keepers, and piping it into psql answers "Restore completed
successfully" after restoring no rows. Both backup scripts verified with
`[ ! -s ]`; the one that also claims to verify integrity did it with a `gzip -t`
whose failure branch `set -e` never let run, and printed a row count taken from a
`pg_dump` option that does not exist, with the error sent to `/dev/null`.

The PATH probe that finds PostgreSQL when it is not on PATH lived in the backup
scripts and not in the restore one, so the documented recovery command failed on
the host it was written for. It is one function now, and these tests run the real
scripts against a stubbed `pg_dump` — no database is opened, because the stub
never reads its arguments.
"""

import gzip
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PG_ENV = SCRIPTS / "pg_env.sh"
RELEASE_GATE = "final_release_check.sh"
DUMP_TEXT = (
    "--\n"
    "-- PostgreSQL database dump\n"
    "--\n"
    "CREATE TABLE public.users (tg_id bigint);\n"
    "COPY public.users (tg_id) FROM stdin;\n"
    "123\n"
    "\\.\n"
)
# What a stubbed pg_dump prints so the archive it produces is a real dump.
STUB_DUMP_BODY = "cat <<'EOF'\n" + DUMP_TEXT + "EOF"
# The scripts the restore procedure is allowed to be run through: two that write
# an archive and the one that reads it back.
DATABASE_SCRIPTS = ("backup_db.sh", "backup_and_monitor.sh", "restore_db.sh")


def _bash() -> str:
    found = shutil.which("bash")
    if not found:
        pytest.fail(
            "bash is not on PATH. The backup and restore procedure is shell scripts, "
            "and a machine that cannot run them measures nothing here — say so loudly "
            "instead of reporting a green board."
        )
    return found


def _posix(path: Path) -> str:
    return path.as_posix()


def _shell(statement: str) -> subprocess.CompletedProcess[str]:
    """Run one statement with the shared script library already sourced."""
    return subprocess.run(
        [_bash(), "-c", f'set -euo pipefail; . "{_posix(PG_ENV)}"; {statement}'],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _stub_dump(bin_dir: Path, body: str) -> None:
    """A `pg_dump` the scripts find first on PATH: no connection, fixed output."""
    stub = bin_dir / "pg_dump"
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    # Git Bash derives the exec bit from the Windows ACL, so ask bash itself to set it.
    subprocess.run([_bash(), "-c", f'chmod 755 "{_posix(stub)}"'], check=True, timeout=60)


def _run_script(script: str, args: list[str], tmp_path: Path, stub_body: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_dump(bin_dir, stub_body)
    env = {
        **os.environ,
        "PATH": os.pathsep.join([_posix(bin_dir), os.environ["PATH"]]),
        "DATABASE_URL": "postgresql://stub:stub@127.0.0.1:1/stub",
        # Never let an operator's own alert webhook or rsync target turn a test run
        # into a message or a `--delete` against a real share.
        "ALERT_WEBHOOK": "",
        "BACKUP_REMOTE": "",
    }
    return subprocess.run(
        [_bash(), _posix(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
        cwd=_posix(ROOT),
        input="",
    )


def _archives_in(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.sql.gz"))


def _empty_archive(tmp_path: Path) -> Path:
    path = tmp_path / "empty.sql.gz"
    path.write_bytes(gzip.compress(b""))
    return path


def _real_archive(tmp_path: Path) -> Path:
    path = tmp_path / "real.sql.gz"
    path.write_bytes(gzip.compress(DUMP_TEXT.encode()))
    return path


# ---------- the validator, measured on files it is handed ----------


def test_an_archive_is_judged_by_its_payload_and_not_by_its_size(tmp_path):
    """The exact shape the old `[ ! -s ]` check accepted: a gzip that exists, opens,
    and holds nothing. Everything a size check can see about it is true."""
    empty = _empty_archive(tmp_path)
    assert empty.stat().st_size > 0, "a gzip of nothing is a non-empty file: that is the defect"
    assert gzip.decompress(empty.read_bytes()) == b""

    refused = _shell(f'pg_archive_is_real "{_posix(empty)}"')
    assert refused.returncode != 0, refused.stdout + refused.stderr


def test_the_validator_refuses_what_is_not_an_archive_at_all(tmp_path):
    missing = tmp_path / "never_written.sql.gz"
    not_a_gzip = tmp_path / "text.sql.gz"
    not_a_gzip.write_bytes(b"just some text, never compressed")

    for path in (missing, not_a_gzip):
        refused = _shell(f'pg_archive_is_real "{_posix(path)}"')
        assert refused.returncode != 0, f"{path.name} was accepted as a dump"


def test_the_validator_accepts_a_dump_the_way_pg_dump_writes_one(tmp_path):
    real = _real_archive(tmp_path)
    accepted = _shell(f'pg_archive_is_real "{_posix(real)}"')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


# ---------- the scripts, run for real against the stub ----------


@pytest.mark.parametrize("script", ["backup_db.sh", "backup_and_monitor.sh"])
def test_a_dump_that_produces_nothing_is_not_left_behind_as_a_backup(script, tmp_path):
    out_dir = tmp_path / "backups"
    result = _run_script(script, [_posix(out_dir)], tmp_path, "exit 0")

    assert result.returncode != 0, f"{script} called an empty dump a backup:\n{result.stdout}"
    assert _archives_in(out_dir) == [], f"{script} kept a file with no dump in it"
    assert "no PostgreSQL dump" in result.stdout + result.stderr, result.stdout + result.stderr


@pytest.mark.parametrize("script", ["backup_db.sh", "backup_and_monitor.sh"])
def test_a_pg_dump_that_fails_leaves_no_archive_behind(script, tmp_path):
    out_dir = tmp_path / "backups"
    result = _run_script(script, [_posix(out_dir)], tmp_path, 'echo "pg_dump: error: connection failed" >&2; exit 1')

    assert result.returncode != 0, f"{script} reported success over a failed pg_dump:\n{result.stdout}"
    assert _archives_in(out_dir) == [], f"{script} left the partial archive on disk"


@pytest.mark.parametrize("script", ["backup_db.sh", "backup_and_monitor.sh"])
def test_a_real_dump_is_kept_and_named(script, tmp_path):
    out_dir = tmp_path / "backups"
    result = _run_script(script, [_posix(out_dir)], tmp_path, STUB_DUMP_BODY)
    assert result.returncode == 0, result.stdout + result.stderr

    kept = _archives_in(out_dir)
    assert len(kept) == 1, f"{script} wrote {len(kept)} archives for one run"
    assert gzip.decompress(kept[0].read_bytes()).decode() == DUMP_TEXT


def test_the_monitor_reports_rows_read_off_the_archive_it_wrote(tmp_path):
    """The count used to come from a second `pg_dump` call given an option that does
    not exist, with `2>/dev/null` deciding what the operator saw: it always read 0."""
    out_dir = tmp_path / "backups"
    result = _run_script("backup_and_monitor.sh", [_posix(out_dir)], tmp_path, STUB_DUMP_BODY)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "users rows in the dump: 1" in result.stdout, result.stdout


def test_restore_refuses_an_archive_with_no_dump_before_it_asks_to_overwrite(tmp_path):
    empty = _empty_archive(tmp_path)
    result = _run_script("restore_db.sh", [_posix(empty)], tmp_path, "exit 0")

    assert result.returncode != 0, f"restore accepted an archive with no dump:\n{result.stdout}"
    assert "nothing would be restored" in result.stdout + result.stderr, result.stdout + result.stderr
    assert "This will overwrite" not in result.stdout, "the operator was asked to confirm before the refusal"


# ---------- the laws that keep the three scripts from drifting apart ----------


def test_the_postgres_path_probe_is_written_once_and_used_by_all_three():
    glob = "/c/Program Files/PostgreSQL"
    carriers = [p for p in sorted(SCRIPTS.glob("*.sh")) if glob in p.read_text(encoding="utf-8")]
    assert [p.name for p in carriers] == ["pg_env.sh"], (
        f"the Windows/POSIX PostgreSQL install glob is hand-written in {carriers}: "
        "one of them will grow the Linux path and the others will keep missing it"
    )

    for name in DATABASE_SCRIPTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert 'source "$SCRIPT_DIR/pg_env.sh"' in text, f"{name} does not source the shared probe"
        assert "pg_path_probe" in text, f"{name} runs a client binary without finding it first"


def test_the_restore_probes_for_psql_after_it_knows_the_archive_is_worth_it():
    """`backup_db.sh` knew where the PostgreSQL binaries live and `restore_db.sh`
    did not, so the documented recovery command died on `command not found` only
    when an operator actually needed it. The refusal that needs no binary at all —
    this archive holds no dump — comes first."""
    text = (SCRIPTS / "restore_db.sh").read_text(encoding="utf-8")
    assert "pg_path_probe psql" in text, "restore never looks for psql"
    assert text.index("pg_archive_is_real") < text.index("pg_path_probe psql"), (
        "restore hunts for a client binary before it knows there is anything to restore"
    )
    assert text.index("pg_path_probe psql") < text.index("read -p"), (
        "restore asks the operator to approve a database it has no binary to write to"
    )


def test_no_backup_script_trusts_a_byte_count_anymore():
    offenders = [
        p.name
        for p in sorted(SCRIPTS.glob("*.sh"))
        if '[ ! -s "$BACKUP_FILE" ]' in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"{offenders} still call a file a backup because it has bytes in it"


def _logical_lines(text: str) -> list[str]:
    """Shell statements the way the parser sees them: a `pg_dump` call that ends in
    a backslash continues on the next line, and measuring line by line reads the
    first line of it and misses everything after."""
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        joined.append((pending + line).strip())
        pending = ""
    if pending:
        joined.append(pending.strip())
    return joined


def test_no_database_script_swallows_the_error_it_is_supposed_to_report():
    """`2>/dev/null` on a client binary is how a permanently failing verification
    kept printing a number nobody could tell was invented."""
    offenders = []
    for name in DATABASE_SCRIPTS:
        for statement in _logical_lines((SCRIPTS / name).read_text(encoding="utf-8")):
            if ("pg_dump" in statement or "psql" in statement) and "2>/dev/null" in statement:
                offenders.append(f"{name}: {statement}")
    assert offenders == [], "\n".join(offenders)


def test_the_backup_failure_branches_are_reachable_code():
    """`gzip -t …` then `if [ $? -ne 0 ]` cannot both run under `set -e`: the first
    exits the script, so the alert it guarded had never fired. The checks that
    report a bad archive are the ones whose failure the script handles."""
    for name in ("backup_db.sh", "backup_and_monitor.sh"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert 'if ! pg_archive_is_real "$BACKUP_FILE"; then' in text, (
            f"{name} no longer handles a bad archive — it would abort before saying so"
        )


def test_the_shared_library_is_shell_that_parses():
    assert PG_ENV.is_file(), "scripts/pg_env.sh is what the three database scripts share"
    for name in (PG_ENV.name, *DATABASE_SCRIPTS):
        path = SCRIPTS / name
        assert _parses(path), f"{name} does not parse as shell"
        # `.gitattributes` promises LF and these four run on the Linux host, where a
        # CRLF shebang is "bad interpreter" and the backup simply never happens.
        assert "\r" not in path.read_text(encoding="utf-8"), f"{name} carries CRLF line endings"


def _parses(path: Path) -> bool:
    checked = subprocess.run(
        [_bash(), "-n", _posix(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert checked.returncode == 0, f"{path.name} does not parse: {checked.stderr}"
    return True


def test_every_operator_script_is_shell_that_parses():
    """The parse-and-line-ending law above named four files by hand; the release gate
    and the Oracle bootstrap are the same kind of artifact and were not among them,
    so a CRLF shebang in either — the exact defect the database scripts were fixed
    for — was measured by nothing. Which files are read is the directory's answer."""
    shells = sorted((ROOT / "scripts").glob("*.sh")) + sorted((ROOT / "deploy").glob("*.sh"))
    assert len(shells) >= 8, f"only {len(shells)} operator scripts found — the scan is blind"
    for path in shells:
        assert _parses(path)
        assert "\r" not in path.read_bytes().decode("utf-8", errors="replace"), (
            f"{path.relative_to(ROOT).as_posix()} carries CRLF line endings: on the Linux "
            "host a CRLF shebang is 'bad interpreter' and the procedure does not run"
        )


# The reporter half of the release gate: the counters it reports through, the temp
# file, and the two functions — stopped before the first check runs.
GATE_HELPERS = re.compile(r"(PASS=0.*?)(?=\necho \"=== Tippy-on-Base)", re.DOTALL)


def _gate_helpers() -> str:
    block = GATE_HELPERS.search((SCRIPTS / RELEASE_GATE).read_text(encoding="utf-8"))
    assert block, (
        f"{RELEASE_GATE} no longer carries the reporter this file measures — a check that "
        "prints a label without the command's output cannot be acted on"
    )
    return block.group(1)


def _run_bash(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_bash(), "-c", body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_the_release_gate_prints_what_the_failing_command_said():
    """`check` used to run every command with `>/dev/null 2>&1`, so a red
    `[FAIL] pytest suite passes` arrived with no reason attached and the operator had
    to rediscover by hand what the gate exists to report."""
    out = _run_bash(
        "set -euo pipefail\n" + _gate_helpers()
        + "\ncheck boom bash -c 'echo the-command-said; echo to-stderr >&2; exit 3'\n"
        + 'echo "FAILS=$FAIL"\n'
    )
    assert out.returncode == 0, out.stderr
    assert "[FAIL] boom" in out.stdout, out.stdout
    assert "the-command-said" in out.stdout and "to-stderr" in out.stdout, (
        "the failing command's own output is still thrown away:\n" + out.stdout
    )
    assert "FAILS=1" in out.stdout, out.stdout


def test_a_green_release_check_stays_quiet():
    out = _run_bash(
        "set -euo pipefail\n" + _gate_helpers()
        + "\ncheck calm bash -c 'echo nothing-to-see'\necho \"PASSES=$PASS\"\n"
    )
    assert out.returncode == 0, out.stderr
    assert "[PASS] calm" in out.stdout, out.stdout
    assert "nothing-to-see" not in out.stdout, "a passing check spams the report"
    assert "PASSES=1" in out.stdout, out.stdout


def test_the_release_gate_warn_branch_reports_too():
    out = _run_bash(
        "set -euo pipefail\n" + _gate_helpers()
        + "\nwarn_check soft bash -c 'echo the-reason; exit 1'\necho \"WARNS=$WARN\"\n"
    )
    assert "[WARN] soft" in out.stdout, out.stdout
    assert "the-reason" in out.stdout, out.stdout
    assert "WARNS=1" in out.stdout, out.stdout


def test_the_release_gate_leaves_its_capture_file_behind_nothing():
    """The trap lives in the same block as the functions, and it can only be
    observed from a second process: a check run **inside** the script that installed
    it sees the file, because EXIT has not happened yet."""
    first = _run_bash("set -euo pipefail\n" + _gate_helpers() + '\ncheck x true\necho "$CHECK_LOG"\n')
    assert first.returncode == 0, first.stderr
    written = first.stdout.strip().splitlines()[-1]
    assert written.endswith(".log"), f"the gate printed no capture path: {written!r}"
    second = _run_bash(f'test ! -e "{written}"')
    assert second.returncode == 0, (
        f"the capture file {written} survives a gate run — from cron that is one file per call"
    )



def test_the_release_gate_does_not_silence_its_own_checks():
    helpers = _gate_helpers()
    assert "/dev/null" not in helpers, "the reporter redirects the output it should replay"
    offenders = [
        line
        for line in (SCRIPTS / RELEASE_GATE).read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*(warn_)?check\s", line) and "2>/dev/null" in line
    ]
    assert offenders == [], "\n".join(offenders)

