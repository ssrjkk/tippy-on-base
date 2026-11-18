"""The supervisor of the live tunnel is PowerShell, and until this file nothing ran it.

`deploy/watchdog.ps1` is the only program that decides whether the site's connector
is replaced, and for thirty-three waves the decision was reviewed as text. What the
text was doing, measured out of its own log (5,526 passes) and the endpoints it
judges (19 probes through the live edge):

- It killed a connector for a flag: `--edge-bind-address` was compared against an
  address derived from the routing table, and any process that did not carry it was
  stopped — while the flag itself was the churn driver. A replica started without it
  registered four edge connections and terminated none in ten minutes; the pinned
  ones died every 22-173 s (median 101 s, 49 deaths in 30 min), and all 82
  established `:443` sockets on this host already egressed the address being pinned.
- It preserved a connector that had lost every connection: the "old and disconnected"
  rule required five minutes of age, so a replica that died at 90 s — the median —
  was kept for four more minutes answering 503.
- It judged the public path with one HTTP request issued microseconds after starting
  a connector that cannot have connections yet. **4,745 of 5,526 passes logged
  `public=False` (86%)** while the origin answered in 0.24 s on loopback.

So these tests run the shipped script. The restart verdicts are executed as the
PowerShell that produces them (`Test-ConnectorShouldRestart`,
`Test-OriginShouldRestart`) against a truth table, and the constants the table uses
are the file's own — a mutation of `$StartupGraceSeconds` in a copy flips the
verdict, which is the proof that the rule under test is the rule that ships.

What runs where: the executed laws need PowerShell, which this host has and the CI
image carries (PowerShell 7.6.10 on ubuntu-24.04), so the predicates are measured in
CI too. One law runs the shipped probe against this host's own running processes, and
that one carries the platform guard with its reason printed; every other law in the
file reads the source or a scratch copy, so CI still refuses a report that drops the
per-replica fields. `test_the_documented_law_count_is_the_number_of_laws_in_this_file`
counts this file's own functions, which is what makes the count in `AGENTS.md` a
measurement rather than a memory.
"""

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
WATCHDOG = DEPLOY / "watchdog.ps1"
DOC = ROOT / "docs" / "CLOUDFLARE.md"
EDGE = ROOT / "functions" / "[[path]].js"
BUNDLE = ROOT / "web" / "static"
# What the live edge answers on this host, and what the doc tells an operator to
# curl when the page is down: the metrics server of each connector replica.
CLOUDFLARED_IMAGE = re.compile(r"cloudflared\.exe")
PROTOCOL = re.compile(r"--protocol['\", ]+['\"]?([a-z0-9]+)")
SOURCE_PIN = "--edge-bind-address"
# An availability as a document states it: `6 of 19 probes (32%)`. One reader for the
# runbook and for every document that quotes the runbook, so a figure cannot be read
# strictly in one place and loosely in another. The whitespace is `\s+` because these
# files are hard-wrapped: `45 of 87` and `(52%)` landing on different lines is the same
# claim, and a reader that needed a literal space would let a wrapped figure go unread.
WALK_FRACTION = re.compile(r"(\d{1,3})\s+of\s+(?:the\s+)?(\d{1,3})(?:\s+[a-z]+)?\s+\((\d{1,3})%\)")
# The two counts `AGENTS.md` publishes about this file. The wrap is hard, so the
# whitespace between the words is whatever the editor chose.
TOTAL_LAW_CLAIM = re.compile(r"(\d+)\s+laws?\s+now\s+measure\s+the\s+supervisor", re.S)
GUARDED_LAW_CLAIM = re.compile(r"(\d+)\s+of\s+(?:the\s+)?(\d+)\s+laws?", re.S)


def _pwsh() -> list[str]:
    """A PowerShell that can dot-source the shipped script, or a loud failure.

    `pwsh` first: the CI image carries PowerShell 7.6.10, this host has both. A
    machine with neither does not measure the supervisor's rules, and that is said
    here rather than reported as a green board — the same law `tests/test_db_scripts.py`
    applies to `bash`.
    """
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            argv = [found, "-NoProfile", "-NonInteractive"]
            if os.name == "nt":
                argv += ["-ExecutionPolicy", "Bypass"]
            return argv
    pytest.fail(
        "no PowerShell on PATH. The tunnel supervisor is a .ps1, and the rules that "
        "decide whether a dead connector is replaced are measured by running them — "
        "a machine that cannot run them proves nothing about them."
    )


def _run_ps(body: str, *, timeout: int = 300) -> str:
    """Run PowerShell and hand back its stdout.

    The console codepage on this host is cp1251 while PowerShell writes UTF-8, so
    the child is told to answer in UTF-8 and this parent names the same encoding —
    the rule `test_every_child_process_this_repo_reads_as_text_names_its_encoding`
    walks every spawn site for.
    """
    preamble = "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }\n"
    out = subprocess.run(
        [*_pwsh(), "-Command", preamble + body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    assert out.returncode == 0, f"PowerShell exited {out.returncode}\n{out.stdout}\n{out.stderr}"
    return out.stdout


def _dot_source(path: Path) -> str:
    return f'. "{path.as_posix()}" -AsLibrary\n'


def _as_list(value: object) -> list:
    """A one-element PowerShell pipeline renders as a scalar, not an array.

    `$ports` with a single replica is `20241`, and iterating that answers
    `'int' object is not iterable` instead of the law's own verdict.
    """
    return value if isinstance(value, list) else [value]


def _public_urls(path: Path | None = None) -> dict[str, str]:
    """The three public addresses the supervisor probes, read off its own variables.

    Taken from the file rather than restated, so a mutation of `$PublicOrigin` moves
    the law with it — which is the point: the witness is only a witness while it
    shares the route it witnesses.
    """
    script = _dot_source(path or WATCHDOG) + (
        "[ordered]@{ origin = $PublicOrigin; health = $PublicHealth; control = $PublicControl }"
        " | ConvertTo-Json -Compress\n"
    )
    return json.loads(_run_ps(script).strip())


def _edge_asset_pattern(path: Path | None = None) -> re.Pattern[str]:
    """The edge's own «this is a file» pattern, read out of the shipped Function.

    The same extraction `tests/test_design_system.py` uses: a copy of the extension
    list here would go stale the day the routing changes, and this law is about the
    routing.
    """
    source = (path or EDGE).read_text(encoding="utf-8-sig")
    declared = re.search(r"const ASSET = /\\.\((\?:[^)]*)\)\$/i;", source)
    assert declared, "the edge asset pattern moved somewhere this gate cannot read"
    pattern = re.compile(f"\\.({declared.group(1)})$", re.I)
    assert pattern.search("/style.css"), "the extracted pattern matches nothing — a broken gate"
    return pattern


def _edge_asks_the_bundle_first(path: Path | None = None) -> bool:
    """True while an asset path is answered from the bundle before the origin is named.

    Anchored on the `if (reads && ASSET.test(pathname))` statement itself, not on a
    substring: a branch rewritten as `if (false && …)` still contains the text of the
    original and would answer «the bundle is still asked first» while nothing reaches
    it any more. A missing branch is reported, not read as a pass.
    """
    source = (path or EDGE).read_text(encoding="utf-8-sig")
    bundle = re.search(r"if \(reads && ASSET\.test\(pathname\)\) \{", source)
    assert bundle, (
        "the Function no longer carries a live branch that answers an asset out of the "
        "bundle, so every public probe now depends on the origin it measures"
    )
    origin = source.index("const backend = (env.BACKEND_URL")
    return bundle.start() < origin


def _outcomes() -> tuple[list[str], list[str], list[str]]:
    """Both vocabularies and every word `Get-PublicOutcome` can actually return.

    The first two are the script's own arrays (so a predicate that refuses a value
    the producer can return cannot pass unread); the third is read off the shipped
    returns, so the list is checked against the function rather than against itself.
    """
    script = _dot_source(WATCHDOG) + (
        "$body = (Get-Command Get-PublicOutcome).ScriptBlock.ToString()\n"
        "$returned = [regex]::Matches($body, \"return\\s+'(\\w+)'\") | ForEach-Object { $_.Groups[1].Value }\n"
        "[ordered]@{ endpoint = $EndpointOutcomes; public = $PublicOutcomes;"
        " returned = @($returned) } | ConvertTo-Json -Compress\n"
    )
    read = json.loads(_run_ps(script).strip())
    return (
        [str(x) for x in _as_list(read["endpoint"])],
        [str(x) for x in _as_list(read["public"])],
        [str(x) for x in _as_list(read["returned"])],
    )


def _check_fields() -> set[str]:
    """The keys `-Check` emits, read off the ordered hashtable it converts to JSON.

    A hand-written tuple of field names is what let the report lose a field in
    silence: the law checked the list, not the script. The subject set is the file.
    """
    code = WATCHDOG.read_text(encoding="utf-8")
    block = re.search(r"\$state = \[ordered\]@\{(.*?)\n\s*\}", code, re.S)
    assert block, "`-Check` no longer builds one [ordered] hashtable this gate can read"
    keys = set(re.findall(r"(?m)^\s+([A-Z][A-Za-z0-9]+) =", block.group(1)))
    assert len(keys) >= 8, f"`-Check` emits {sorted(keys)} — fewer than eight fields is a broken reader"
    return keys


# One row per verdict the supervisor can be asked for: a name, the shipped function,
# and the arguments as the script's own expressions. `_verdict_script` renders these
# into PowerShell and `_verdicts` refuses any name that came back missing, so adding a
# case here adds the row to the run — a rule that ships without a row is the only way
# a verdict can go unread.
_VERDICT_ROWS = [
    (
        "c_just_under_grace",
        "Test-ConnectorShouldRestart",
        {"AgeSeconds": "$grace - 1", "ProbeOutcomes": "@('silent') * $n"},
    ),
    (
        "c_at_grace_all_silent",
        "Test-ConnectorShouldRestart",
        {"AgeSeconds": "$grace", "ProbeOutcomes": "@('silent') * $n"},
    ),
    (
        "c_at_grace_never_ready",
        "Test-ConnectorShouldRestart",
        {"AgeSeconds": "$grace", "ProbeOutcomes": "@('answered') * $n"},
    ),
    (
        "c_old_one_ready",
        "Test-ConnectorShouldRestart",
        {"AgeSeconds": "100000", "ProbeOutcomes": "@('silent', 'ok', 'answered')"},
    ),
    ("c_one_probe", "Test-ConnectorShouldRestart", {"AgeSeconds": "100000", "ProbeOutcomes": "@('silent')"}),
    (
        "o_just_under_grace",
        "Test-OriginShouldRestart",
        {"AgeSeconds": "$ograce - 1", "ProbeOutcomes": "@('silent') * $n"},
    ),
    (
        "o_at_grace_all_silent",
        "Test-OriginShouldRestart",
        {"AgeSeconds": "$ograce", "ProbeOutcomes": "@('silent') * $n"},
    ),
    (
        "o_at_grace_db_down",
        "Test-OriginShouldRestart",
        {"AgeSeconds": "$ograce", "ProbeOutcomes": "@('silent', 'answered', 'silent')"},
    ),
    (
        "o_short_window",
        "Test-OriginShouldRestart",
        {"AgeSeconds": "$ograce", "ProbeOutcomes": "@('silent') * ($n - 1)"},
    ),
    # The public leg: three answers, and the witness decides between «the site is
    # down» and «this host could not look».
    (
        "p_health_ok",
        "Get-PublicOutcome",
        {"ControlOutcome": "'ok'", "HealthOutcomes": "@('ok')"},
    ),
    (
        "p_answered_with_route",
        "Get-PublicOutcome",
        {"ControlOutcome": "'ok'", "HealthOutcomes": "@('answered', 'silent')"},
    ),
    (
        "p_silent_with_route",
        "Get-PublicOutcome",
        {"ControlOutcome": "'ok'", "HealthOutcomes": "@('silent') * $n"},
    ),
    (
        "p_silent_control_answered",
        "Get-PublicOutcome",
        {"ControlOutcome": "'answered'", "HealthOutcomes": "@('silent') * $n"},
    ),
    (
        "p_no_route_no_answer",
        "Get-PublicOutcome",
        {"ControlOutcome": "'silent'", "HealthOutcomes": "@('silent') * $n"},
    ),
    ("p_no_probe", "Get-PublicOutcome", {"ControlOutcome": "'ok'", "HealthOutcomes": "@()"}),
    ("p_control_typo", "Get-PublicOutcome", {"ControlOutcome": "'awnsered'", "HealthOutcomes": "@('silent')"}),
    # A pass is green when the parts this host owns answer, whatever a leg that
    # could not be measured says.
    (
        "r_everything_up",
        "Test-Readiness",
        {"OriginReady": "$true", "ConnectorReady": "$true", "PublicOutcome": "'ok'"},
    ),
    (
        "r_public_down",
        "Test-Readiness",
        {"OriginReady": "$true", "ConnectorReady": "$true", "PublicOutcome": "'down'"},
    ),
    (
        "r_public_unmeasured",
        "Test-Readiness",
        {"OriginReady": "$true", "ConnectorReady": "$true", "PublicOutcome": "'unreachable'"},
    ),
    (
        "r_connector_down",
        "Test-Readiness",
        {"OriginReady": "$true", "ConnectorReady": "$false", "PublicOutcome": "'ok'"},
    ),
    ("r_public_typo", "Test-Readiness", {"OriginReady": "$true", "ConnectorReady": "$true", "PublicOutcome": "'sid'"}),
]


def _verdict_script(path: Path) -> str:
    """The whole truth table, in one PowerShell pass, as `name=verdict|throws`."""
    rows = []
    for name, fn, args in _VERDICT_ROWS:
        inner = "; ".join(f"{key} = {value}" for key, value in args.items())
        rows.append(f"  {name} = @{{ f = '{fn}'; a = @{{{inner}}} }}\n")
    return (
        _dot_source(path)
        + "$grace = $StartupGraceSeconds\n"
        "$ograce = $OriginGraceSeconds\n"
        "$n = $ConfirmProbes\n"
        "$rows = [ordered]@{\n" + "".join(rows) + "}\n"
        "foreach ($name in $rows.Keys) {\n"
        "  $row = $rows[$name]\n"
        "  $sp = $row.a\n"
        "  try { $v = & $row.f @sp }\n"
        "  catch { $v = 'throws' }\n"
        '  Write-Output ("{0}={1}" -f $name, $v)\n'
        "}\n"
        'Write-Output ("constants={0},{1},{2},{3},{4}" -f $grace, $ograce, $n, $ConnectorReplicas, $MetricsPortBase)\n'
    )


def _verdicts(path: Path | None = None) -> dict[str, str]:
    """The truth table as the shipped script answers it, or as a mutated copy does."""
    path = path or WATCHDOG
    table: dict[str, str] = {}
    constants = ""
    for line in _run_ps(_verdict_script(path)).splitlines():
        name, _, value = line.partition("=")
        if not value:
            continue
        if name == "constants":
            constants = value
        else:
            table[name] = value.strip()
    assert constants, "the script printed no constants line — the truth table never ran"
    expected = {name for name, _, _ in _VERDICT_ROWS}
    assert set(table) == expected, (
        f"the table answered {sorted(set(table) ^ expected)} differently from the rows declared"
    )
    table["constants"] = constants
    return table


@pytest.fixture(scope="module")
def shipped_verdicts() -> dict[str, str]:
    return _verdicts()


def _constants(path: Path | None = None) -> tuple[int, int, int, int, int]:
    return tuple(int(v) for v in _verdicts(path)["constants"].split(","))  # type: ignore[return-value]


def test_the_shipped_connector_verdict_is_the_verdict_the_measuring_justified(shipped_verdicts):
    """A replica is replaced when it never said ready and is past its grace — and
    only then.

    `answered` (a `/ready` that says 503) condemns: that is the state this host sat
    in for hours, four connections registered and every one of them dead. A single
    `ok` in the window clears it, because the connection set turns over even while
    the page serves.
    """
    grace, _, _, _, _ = _constants()
    assert shipped_verdicts["c_at_grace_all_silent"] == "True"
    assert shipped_verdicts["c_at_grace_never_ready"] == "True"
    assert shipped_verdicts["c_old_one_ready"] == "False"
    assert shipped_verdicts["c_just_under_grace"] == "False", (
        f"a connector {grace - 1}s old is inside the startup window; killing one for "
        "having no connections yet is the watchdog manufacturing the outage it heals"
    )
    assert shipped_verdicts["c_one_probe"] == "throws", (
        "one sample of one instant is how 4,745 of 5,526 passes reported the public "
        "site down while the origin answered in 0.24 s"
    )


def test_the_origin_is_replaced_only_when_nothing_answers_at_all(shipped_verdicts):
    """/api/health saying 200 with `db != ok` is a report, not a fault to hide.

    Restarting the app over a database that is down would kill the bot, rerun the
    alembic chain against the same outage and cost the operator the one message that
    says what broke — and the process is the combined runner, so it takes Telegram
    polling with it. Only `silent` (connection refused, timeout: no HTTP layer
    behind the pid) condemns, and only after the boot window.
    """
    _, o_grace, _, _, _ = _constants()
    assert shipped_verdicts["o_at_grace_all_silent"] == "True"
    assert shipped_verdicts["o_at_grace_db_down"] == "False"
    assert shipped_verdicts["o_just_under_grace"] == "False", (
        f"run.py migrates before it binds, so {o_grace - 1}s of silence is boot, not a wedge"
    )
    assert shipped_verdicts["o_short_window"] == "throws"


def test_the_public_leg_answers_three_ways_and_only_a_measured_failure_condemns(shipped_verdicts):
    """`unreachable` is the third answer, and it is the one this host needs.

    Measured while the origin answered every loopback request: TCP to both
    `tippy-egi.pages.dev` addresses opened in 9-33 ms and every HTTPS request to
    that hostname then died inside the handshake, while `www.cloudflare.com` answered
    in 0.05 s from a different address block. A leg that cannot complete a handshake
    has no evidence about the site either way, so it must not fail the scheduled task
    — but a control that answered *anything*, including a 404, is proof this host
    reached Pages, and a silent health path beside it is the fault the card stands in
    for. `p_silent_control_answered` is that case, and the shipped rule read it as
    «not measured» until this row said otherwise.
    """
    assert shipped_verdicts["p_health_ok"] == "ok"
    assert shipped_verdicts["p_answered_with_route"] == "down", (
        "an HTTP answer from the public hostname is a measurement whatever the route looks like"
    )
    assert shipped_verdicts["p_silent_with_route"] == "down", (
        "the witness reached Pages while the health path answered nothing — that is the outage"
    )
    assert shipped_verdicts["p_silent_control_answered"] == "down", (
        "a control that answered at all proves the route, so a silent health path is a fault, "
        "not an unmeasurable leg"
    )
    assert shipped_verdicts["p_no_route_no_answer"] == "unreachable", (
        "this host could not look; that says nothing about a visitor's page and must not "
        "fail the pass forever"
    )
    assert shipped_verdicts["p_no_probe"] == "throws", "a public verdict from zero probes is the 86% bug"
    assert shipped_verdicts["p_control_typo"] == "throws", (
        "a misspelled outcome reads as a silent probe and turns a fault into 'not measured'"
    )


def test_a_pass_is_green_when_the_parts_this_host_owns_answer(shipped_verdicts):
    """The exit code the scheduled task carries is the origin, the connector pair,
    and a public leg that actually reported a failure.

    Three hours of this host exiting 1 every few minutes for a site a visitor could
    read says nothing on the occasion it exists to speak, so `unreachable` keeps the
    pass green while the report still names it.
    """
    assert shipped_verdicts["r_everything_up"] == "True"
    assert shipped_verdicts["r_public_down"] == "False", (
        "the page is measurably broken and the supervisor reported a green pass"
    )
    assert shipped_verdicts["r_public_unmeasured"] == "True", (
        "a leg this host could not measure failed a pass over the two parts that answered"
    )
    assert shipped_verdicts["r_connector_down"] == "False"
    assert shipped_verdicts["r_public_typo"] == "throws"


def _function_body(name: str) -> str:
    """One shipped function, braces included, as the file writes it."""
    code = WATCHDOG.read_text(encoding="utf-8")
    found = re.search(rf"(?ms)^function {re.escape(name)}\b.*?\n\}}", code)
    assert found, f"deploy/watchdog.ps1 defines no {name} this gate can read"
    return found.group(0)


def test_the_public_witness_shares_the_route_the_measurement_takes():
    """`unreachable` is only honest if the witness could fail for a different reason
    than the measurement — which means it must *not* be able to succeed for one.

    The first version of this leg probed `https://www.cloudflare.com/cdn-cgi/trace`
    beside `https://tippy-egi.pages.dev/api/health` and reported the site `down`
    during hours when the real cause was that this host cannot finish a TLS
    handshake with `*.pages.dev` (TCP opened in 9-33 ms, then reset — measured while
    the origin answered every loopback request, with `www.cloudflare.com` answering
    200 from a different address block). That control witnessed egress to Cloudflare,
    not egress to Pages, so it could say «reachable» about a route the measurement
    was not on. A witness worth its name shares the authority *and* the edge's own
    behaviour: the asset branch of `functions/[[path]].js` answers it out of the
    bundle, so the tunnel, the app and the database decide nothing about it, while a
    missing bundle file — which is exactly what the `text/html` refusal in that
    branch was written for — makes the control reach the origin and therefore says
    something about the tunnel. The path is checked against the shipped routing and
    the directory rather than against a comment claiming it.
    """
    urls = _public_urls()
    origin, health, control = (urlsplit(urls[key]) for key in ("origin", "health", "control"))
    assert (health.scheme, health.netloc) == (origin.scheme, origin.netloc), (
        f"`$PublicHealth` is on {health.netloc}, not on the origin the leg measures ({origin.netloc})"
    )
    assert (control.scheme, control.netloc) == (origin.scheme, origin.netloc), (
        f"`$PublicControl` is on {control.netloc}, which the site's own route does not cross: "
        "it would witness egress to some other address block and call the site down"
    )
    asset = _edge_asset_pattern()
    assert asset.search(control.path), (
        f"`{control.path}` is not an asset path, so the edge proxies the witness to the origin "
        "and the leg can no longer separate a dead tunnel from a dead app"
    )
    assert control.path != health.path, "the witness probes the path it is witnessing"
    on_disk = BUNDLE / control.path.lstrip("/")
    assert on_disk.is_file(), f"the bundle does not carry {control.path}: Pages would answer it with the landing page"
    assert not (BUNDLE / health.path.lstrip("/")).exists(), (
        f"{health.path} is a file in the bundle, so the health probe never asks the origin anything"
    )
    assert _edge_asks_the_bundle_first(), (
        "the Function names the backend before it asks the bundle, so every public probe — "
        "witness included — now depends on the very origin whose absence it measures"
    )


def test_each_answer_is_one_list_read_by_the_producer_and_the_judge():
    """`Get-PublicOutcome` returns members of `$PublicOutcomes`, and `Test-Readiness`
    validates against the same array instead of a second copy of its literals.

    Two spellings of one vocabulary is how a rename turns into a supervisor that
    throws on its own verdict: the function that answers and the rule that reads the
    answer must be looking at the same list. `$EndpointOutcomes` is guarded the same
    way for the probes, because a control outcome that is not in it used to be read
    as a silent probe — the difference between «the site is down» and «this host
    could not look».
    """
    endpoint, public, returned = _outcomes()
    assert len(endpoint) == 3 and len(public) == 3, f"{endpoint} / {public}"
    assert set(returned) == set(public), (
        f"Get-PublicOutcome answers {sorted(set(returned) - set(public))} that the vocabulary "
        f"denies, and refuses {sorted(set(public) - set(returned))} it can never reach"
    )
    assert "-notin $EndpointOutcomes" in _function_body("Get-PublicOutcome")
    judge = _function_body("Test-Readiness")
    assert "-notin $PublicOutcomes" in judge, (
        "Test-Readiness carries its own copy of the public answers again"
    )
    condemns = set(re.findall(r"\$PublicOutcome -ne '(\w+)'", judge))
    assert condemns and condemns <= set(public), (
        f"the pass fails on {sorted(condemns)}, which is not an answer the public leg gives"
    )
    assert "down" in condemns and "unreachable" not in condemns, (
        "an unmeasurable leg condemns the pass, or a measured outage does not"
    )


def test_the_verdicts_read_the_grace_from_the_file_not_from_the_test(tmp_path):
    """Mutate the shipped predicates in a copy and watch the verdicts move.

    The ages in the truth table are taken from the same file as the constants, so
    editing a *value* proves nothing — the table would follow it. What is mutated is
    a predicate's use of the value: a rule that hard-codes its own grace, or its own
    window, is a rule the doc's table does not describe and this test would not
    notice. Without these three, the assertions above pass for any predicate that
    happens to answer the nine cases.
    """
    text = WATCHDOG.read_text(encoding="utf-8")

    def copy_named(name: str, old: str, new: str) -> Path:
        assert text.count(old) == 1, f"{old!r} is not a unique line in the shipped script"
        target = tmp_path / name
        target.write_bytes(text.replace(old, new).encode("utf-8"))
        return target

    young = copy_named(
        "young.ps1",
        "if ($AgeSeconds -lt $StartupGraceSeconds) { return $false }",
        "if ($AgeSeconds -lt 0) { return $false }",
    )
    assert _verdicts(young)["c_just_under_grace"] == "True", (
        "the connector rule ignores $StartupGraceSeconds"
    )

    origin = copy_named(
        "origin.ps1",
        "if ($AgeSeconds -lt $OriginGraceSeconds) { return $false }",
        "if ($AgeSeconds -lt 0) { return $false }",
    )
    assert _verdicts(origin)["o_just_under_grace"] == "True", (
        "the origin rule ignores $OriginGraceSeconds: it would reboot a process that "
        "is still running the alembic chain"
    )

    window = copy_named(
        "window.ps1",
        "if ($ProbeOutcomes.Count -lt $ConfirmProbes) {",
        "if ($ProbeOutcomes.Count -lt 1) {",
    )
    assert _verdicts(window)["o_short_window"] != "throws", (
        "the origin window is not $ConfirmProbes"
    )


def test_the_replicas_cannot_share_a_port_a_logfile_or_a_lie(tmp_path):
    """Two connectors on one tunnel is the fix for a 32% public path, and it only
    works if they are actually separate: distinct metrics ports, distinct logfiles,
    and a readiness URL naming its own port.

    The replica count is not style. Measured on this host with one connector,
    `https://tippy-egi.pages.dev/api/health` answered 200 for 6 of 19 probes while
    `/ready` answered 200 for 10 — the pair is what turned the same walk into 9 of 20
    with three samples where one replica had no connection at all and the page still
    served.
    """
    script = (
        _dot_source(WATCHDOG)
        + "$ports = Get-MetricsPorts\n"
        "$urls = $ports | ForEach-Object { Get-ReadyUrl $_ }\n"
        "$logs = $ports | ForEach-Object { Get-ConnectorLog $_ }\n"
        "[ordered]@{ replicas = $ConnectorReplicas; base = $MetricsPortBase;"
        " ports = $ports; urls = $urls; logs = $logs } | ConvertTo-Json -Compress\n"
    )
    layout = json.loads(_run_ps(script).strip())
    ports = [int(p) for p in _as_list(layout["ports"])]
    logs = [str(x) for x in _as_list(layout["logs"])]
    urls = [str(x) for x in _as_list(layout["urls"])]
    assert layout["replicas"] >= 2, (
        "one replica answered the public health endpoint for 32% of probes on this host"
    )
    assert len(ports) == layout["replicas"], f"{ports} for {layout['replicas']} replicas"
    assert len(set(ports)) == len(ports), f"two replicas cannot bind {ports}"
    assert ports == list(range(layout["base"], layout["base"] + layout["replicas"])), (
        f"replica ports {ports} are not the contiguous block from {layout['base']} "
        "that the doc tells an operator to probe"
    )
    assert len(set(logs)) == len(ports), f"replicas share a logfile: {logs}"
    for log in logs:
        assert "cloudflared" in log
    assert urls == [f"http://127.0.0.1:{port}/ready" for port in ports]


def test_no_launcher_of_this_hosts_tunnel_pins_a_source_address():
    """Every launcher that execs `cloudflared.exe` pins http2 and names no source address.

    The set is the directory's answer, not a list kept here: a fourth launcher joins
    by carrying the binary path. `docker-compose.yml`'s `cloudflared` service is not
    in it — `command: tunnel run` runs in a container with no VPN/TUN interface, where
    QUIC is not the thing that kills the connection, so the http2 pin is a remedy for
    this host and the law is scoped to this host.

    What the pin did here: every connector that carried `--edge-bind-address` lost its
    edge connections every 22-173 s (median 101 s), while the same binary without it
    registered four and terminated none for the ten minutes the comparison ran — and
    the address it selected was the one all 82 established `:443` sockets were already
    using.
    """
    launchers = []
    for path in sorted(DEPLOY.iterdir()):
        if path.suffix.lower() not in {".py", ".ps1"}:
            continue
        lines = [
            line
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            # The watchdog's own comment is the explanation of the defect; only the
            # commands it can run are evidence.
            if not line.strip().startswith("#")
        ]
        if not any(CLOUDFLARED_IMAGE.search(line) for line in lines):
            continue
        launchers.append((path, "\n".join(lines)))
    assert len(launchers) >= 3, f"only {[p.name for p, _ in launchers]} exec cloudflared.exe"
    for path, code in launchers:
        protocols = PROTOCOL.findall(code)
        assert protocols, f"{path.name} execs cloudflared with no --protocol"
        assert set(protocols) == {"http2"}, f"{path.name} starts the tunnel as {sorted(set(protocols))}"
        assert SOURCE_PIN not in code, f"{path.name} still binds the tunnel to a source address"


def test_the_supervisor_only_touches_processes_it_can_name(tmp_path):
    """Every stop targets a pid found by command line, inside a function that read
    the command line to get it.

    `Stop-Process -Name cloudflared` would kill the tunnel of anyone else sharing this
    machine, and the watchdog is scheduled every few minutes. This is not hypothetical:
    during the measuring, two replica probes started outside `data\\runtime` ran
    alongside the watchdog's own and were correctly left alone — because
    `Get-Connector` selects on the runtime directory the replicas write their logfiles
    into. That filter is the whole blast-radius control, so a function that queries
    processes without it is reported.
    """
    code = WATCHDOG.read_text(encoding="utf-8")
    bodies = {
        match.group(1): match.group(2)
        for match in re.finditer(r"(?ms)^function ([\w-]+)[^\n]*\n(.*?)\n\}", code)
    }
    assert len(bodies) >= 10, f"only {sorted(bodies)} functions parsed — the reader is blind"
    readers = {name for name, body in bodies.items() if "Win32_Process" in body}
    assert readers, "nothing enumerates processes any more"
    for name in sorted(readers):
        assert "CommandLine" in bodies[name], f"{name} selects processes without reading their command line"
    for stop in re.finditer(r"(?m)^\s*Stop-Process\b(.*)$", code):
        assert "-Id" in stop.group(1), f"`Stop-Process{stop.group(1)}` targets a process by name"
    assert re.search(r"function Get-Connector\b(.*?)\n\}", code, re.S).group(1).count("$Runtime") >= 1


def test_the_pass_waits_for_the_tunnel_before_it_judges_the_public_path():
    """Ordering, not prose: the public probe runs after the pair has been given
    $ReadyWaitSeconds to register connections, and it is allowed more than one try.

    The pass this replaces judged the public site on the request immediately after
    `Start-Process cloudflared`, which cannot succeed: a connector needs seconds to
    reach its edge. The log line every 2-4 minutes said `public=False` for 86% of all
    passes while the site was up.
    """
    code = WATCHDOG.read_text(encoding="utf-8")
    body = re.search(r"(?ms)^function Ensure-Services\b.*?\n\}", code).group(0)
    heal = re.search(r"(?ms)^function Repair-ConnectorsUntilServing\b.*?\n\}", code).group(0)
    assert "Wait-ForConnector" in heal, (
        "nothing waits for the pair any more, so the public path is judged against "
        "a connector that has not reached its edge"
    )
    assert "Repair-ConnectorsUntilServing" in body and "Get-PublicProbeOutcome" in body
    assert body.index("Repair-ConnectorsUntilServing") < body.index("Get-PublicProbeOutcome"), (
        "the public verdict is taken before the connector has been given its window"
    )
    assert "Test-Readiness" in body, (
        "the pass composes its own exit code again instead of asking the measured rule"
    )
    script = (
        _dot_source(WATCHDOG)
        + "[ordered]@{ wait = $ReadyWaitSeconds; public = $PublicProbes;"
        " confirm = $ConfirmProbes; startup = $StartupGraceSeconds } | ConvertTo-Json -Compress\n"
    )
    windows = json.loads(_run_ps(script).strip())
    assert windows["public"] >= 2, "the public path is still judged by one request"
    assert windows["confirm"] >= 2, "a replica is still condemned by one readiness sample"
    assert windows["wait"] > 0 and windows["startup"] > 0


_REPAIR_CASES = r"""
$ConfirmProbes = 2
$ConfirmDelaySeconds = 0
$StartupGraceSeconds = 60
$ReadyWaitSeconds = 2
$Ports = @(Get-MetricsPorts)
$script:Actions = New-Object System.Collections.ArrayList
$script:Ready = @{}
$script:Heal = $false

# A shape says what one replica looks like to the pass; `heal` says whether the
# replacement the pass starts ever comes up. Both are needed: a burst on the way
# to the edge takes a replica out and then takes out its replacement too, and a
# pass cannot tell the two situations apart from the first probe.
function Invoke-Case([string]$Rule, [string]$Name, [hashtable]$Shape, [bool]$Heal = $false) {
    $script:Actions = New-Object System.Collections.ArrayList
    $script:Heal = $Heal
    $script:Ready = @{}
    foreach ($port in $Ports) { $script:Ready["$port"] = $Shape["$port"] }
    function Get-ConnectorForPort([int]$Port) {
        if ($script:Ready["$Port"] -eq 'missing') { return @() }
        $age = 600
        if ($script:Ready["$Port"] -eq 'young') { $age = 5 }
        return @([pscustomobject]@{ ProcessId = 90000 + $Port; CreationDate = (Get-Date).AddSeconds(-$age) })
    }
    function Get-EndpointOutcome([string]$Url, [switch]$Application) {
        $port = [int](([regex]::Matches($Url, '(\d+)/ready'))[0].Groups[1].Value)
        if ($script:Ready["$port"] -eq 'ready') { return 'ok' }
        return 'silent'
    }
    function Stop-Connector($Connector, [string]$Reason) {
        [void]$script:Actions.Add([ordered]@{ verb = 'stop'; port = ($Connector.ProcessId - 90000); reason = $Reason })
    }
    function Start-Connector([int]$Port) {
        [void]$script:Actions.Add([ordered]@{ verb = 'start'; port = $Port; reason = '' })
        if ($script:Heal) { $script:Ready["$Port"] = 'ready' }
    }
    function Write-Log([string]$Message) {
        [void]$script:Actions.Add([ordered]@{ verb = 'log'; port = 0; reason = $Message })
    }
    $replaced = @()
    $serving = $null
    if ($Rule -eq 'serving') { $serving = [bool](Repair-ConnectorsUntilServing $Ports) }
    else { $replaced = @(Repair-Connectors $Ports) }
    [ordered]@{
        rule = $Rule
        case = $Name
        replaced = $replaced
        serving = $serving
        stops = @($script:Actions | Where-Object { $_.verb -eq 'stop' } | ForEach-Object { $_.port })
        starts = @($script:Actions | Where-Object { $_.verb -eq 'start' } | ForEach-Object { $_.port })
        logs = @($script:Actions | Where-Object { $_.verb -eq 'log' } | ForEach-Object { $_.reason })
    } | ConvertTo-Json -Compress -Depth 5
}

$bothQuiet = @{ }
$all = @{}; $Ports | ForEach-Object { $all["$_"] = 'ready' }
$none = @{}; $Ports | ForEach-Object { $none["$_"] = 'missing' }
$young = @{}; $Ports | ForEach-Object { $young["$_"] = 'young' }
$quiet = @{}; $Ports | ForEach-Object { $quiet["$_"] = 'unready' }

Invoke-Case 'repair' 'all-unready' $bothQuiet
$Ports | ForEach-Object {
    $shape = @{ "$_" = 'unready' }
    foreach ($other in $Ports) { if ($other -ne $_) { $shape["$other"] = 'ready' } }
    Invoke-Case 'repair' "only-$_-unready" $shape
}
Invoke-Case 'repair' 'all-ready' $all
Invoke-Case 'repair' 'all-missing' $none
Invoke-Case 'repair' 'all-in-grace' $young
$mixed = @{}; $Ports | ForEach-Object { $mixed["$_"] = 'unready' }
$mixed["$($Ports[0])"] = 'young'
Invoke-Case 'repair' 'first-in-grace-rest-unready' $mixed

Invoke-Case 'serving' 'pair-quiet-nothing-heals' $quiet
Invoke-Case 'serving' 'pair-quiet-replacement-heals' $quiet $true
Invoke-Case 'serving' 'all-ready' $all
Invoke-Case 'serving' 'all-missing' $none
Invoke-Case 'serving' 'all-in-grace' $young
$halfGrace = @{}; $Ports | ForEach-Object { $halfGrace["$_"] = 'unready' }
$halfGrace["$($Ports[0])"] = 'young'
Invoke-Case 'serving' 'first-in-grace-rest-quiet' $halfGrace
$Ports | ForEach-Object {
    $shape = @{ "$_" = 'unready' }
    foreach ($other in $Ports) { if ($other -ne $_) { $shape["$other"] = 'ready' } }
    Invoke-Case 'serving' "only-$_-quiet" $shape
}
"""


def _repair_cases() -> tuple[list[dict], list[dict], int, list[int]]:
    """Run the two replacement rules over each replica state this host can be in.

    The supervisor is dot-sourced and the five verbs it would use — find the
    process, read `/ready`, stop it, start it, log — are replaced, so the pass is
    measured without a second watchdog touching the live tunnel (the same
    isolation `test_library_mode_supervises_nothing` demands of every law here).
    """
    raw = _run_ps(_dot_source(WATCHDOG) + _REPAIR_CASES, timeout=420)
    budget = int(_run_ps(_dot_source(WATCHDOG) + "Write-Output $MaxConnectorReplacementsPerPass\n").strip())
    waits = int(_run_ps(_dot_source(WATCHDOG) + "Write-Output $ReadyWaitSeconds\n").strip())
    ports = [int(p) for p in _as_list(json.loads(
        _run_ps(_dot_source(WATCHDOG) + "(Get-MetricsPorts | ConvertTo-Json -Compress)\n").strip()))]
    records = [json.loads(line) for line in raw.splitlines() if line.strip().startswith("{")]
    cases = [r for r in records if r["rule"] == "repair"]
    serving = [r for r in records if r["rule"] == "serving"]
    assert len(cases) >= 3 + len(ports), (
        f"the harness answered {len(cases)} repair cases for {len(ports)} replicas — the reader is blind"
    )
    assert len(serving) >= 4 + len(ports), (
        f"the harness answered {len(serving)} serving cases for {len(ports)} replicas — the reader is blind"
    )
    assert waits > 0, "the harness waited no time, so every wait in the rule is unmeasured"
    return cases, serving, budget, ports


def _escalation_logs(case: dict) -> list[str]:
    return [line for line in case["logs"] if "replaces it now" in line]



def test_a_pass_replaces_one_replica_and_never_the_whole_pair():
    """The pair is the fix for a 32% public path, and a pass that stops both
    replicas throws the fix away: both connectors are then mid-registration at the
    same moment, over the one egress that had just refused them.

    Measured in this host's own `data/runtime/watchdog.log`: in the 00:08-00:22
    window of 2026-11-18 four passes each wrote `Stopped connector …: 3 readiness
    probes in a row answered no edge connection` for one replica and then the same
    line for the other ~40 s later, and `public=down` followed every one of them.
    The one pass that replaced a single replica (00:25:29) had the public health
    endpoint answering 200 five seconds later. So the budget is the rule, and what
    it must not do is disarm recovery: a replica with no process at all is still
    started, and a deferred one is still named in the log.
    """
    cases, _serving, budget, ports = _repair_cases()
    by_name = {case["case"]: case for case in cases}
    assert budget >= 1, (
        "the budget forbids every replacement, so the supervisor watches a dead "
        "connector forever instead of healing the tunnel it exists for"
    )
    assert len(ports) >= 2, (
        f"{ports} — one replica is configured, so nothing here measures the pair"
    )

    unready = by_name["all-unready"]
    assert len(unready["stops"]) < len(ports), (
        f"a pass stopped {unready['stops']} of {ports}: the whole pair was taken out "
        "at once, which is the outage the second replica exists to prevent"
    )
    assert 0 < len(unready["stops"]) <= budget, f"{unready['stops']}"
    assert unready["stops"] == unready["starts"], (
        f"stopped {unready['stops']} and started {unready['starts']} — a replica killed "
        "without its replacement is a hole, not a repair"
    )
    deferred = [port for port in ports if port not in unready["stops"]]
    named = " ".join(unready["logs"])
    for port in deferred:
        assert str(port) in named, (
            f"replica {port} was left unready with nothing in the log — an operator "
            "reading `public=down` cannot tell healing from a supervisor that gave up"
        )
    assert sorted(unready["replaced"]) == sorted(unready["stops"]), (
        f"the function reported {unready['replaced']} and performed {unready['stops']} — "
        "the pass decides whether to escalate on what this list says"
    )

    for port in ports:
        single = by_name[f"only-{port}-unready"]
        assert single["stops"] == [port], (
            f"a pass where only {port} was quiet stopped {single['stops']} — the healthy "
            "replica is the one that serves while the other re-registers"
        )
        assert single["starts"] == [port] and not single["logs"]

    assert by_name["all-ready"]["stops"] == [] and by_name["all-ready"]["starts"] == []
    missing = by_name["all-missing"]
    assert missing["stops"] == [], f"nothing was running and the pass stopped {missing['stops']}"
    assert sorted(missing["starts"]) == sorted(ports), (
        f"a host with no connector at all started {missing['starts']} — the replacement "
        "budget must not throttle recovery from a crash"
    )
    grace = by_name["all-in-grace"]
    assert grace["stops"] == [] and grace["starts"] == [], (
        f"a replica {grace['starts']} seconds into its own startup grace was touched"
    )
    mixed = by_name["first-in-grace-rest-unready"]
    assert ports[0] not in mixed["stops"], f"the process inside its grace was stopped: {mixed['stops']}"
    assert len(mixed["stops"]) <= budget


def test_the_replacement_budget_is_the_only_way_a_replica_is_stopped():
    """`Repair-Connectors` is the single place a connector is stopped and started,
    so the budget cannot be bypassed by a second copy of the loop.

    The pass used to carry the decision inline; an inline copy is exactly how a
    rule that reads `one per pass` ends up taking the pair down anyway. And the
    pass may not call that rule directly: the wrapper is the half that waits for
    the pair and escalates when nothing serves, and a pass that reaches past it
    judges the public path against a connector it just started.
    """
    code = WATCHDOG.read_text(encoding="utf-8")
    bodies = {
        match.group(1): match.group(0)
        for match in re.finditer(r"(?ms)^function ([\w-]+)[^\n]*\n.*?\n\}", code)
    }
    assert "Repair-Connectors" in bodies, (
        "the replacement decision is inline in the pass again, where no case can be "
        "run against it without starting a live connector"
    )
    repair = bodies["Repair-Connectors"]
    assert "Stop-Connector $connector" in repair and "Start-Connector $port" in repair
    assert repair.count("Stop-Connector $connector") == 1, (
        f"{repair.count('Stop-Connector $connector')} replacement stops inside one "
        "function — a replacement that does not go through the budget is a "
        "replacement that is not counted"
    )
    assert "$MaxConnectorReplacementsPerPass" in repair
    assert repair.index("$MaxConnectorReplacementsPerPass") < repair.index("Stop-Connector $connector"), (
        "the budget is read after the replica is already stopped"
    )
    assert "$AlreadyReplaced" in repair, (
        "the second call has no way to say which replica this pass already replaced, "
        "so escalation kills the one it just started"
    )
    pass_body = bodies["Ensure-Services"]
    assert "Repair-ConnectorsUntilServing" in pass_body, (
        "the shipped pass no longer calls the rule the gate measures"
    )
    assert re.search(r"Repair-Connectors(?!UntilServing)", pass_body) is None, (
        "the pass calls the budgeted rule directly, skipping the wait that decides "
        "whether the rest of the pair has to be replaced in this pass"
    )
    for verb in ("Stop-Connector", "Start-Connector"):
        assert verb not in pass_body, f"{verb} is back in the pass, beside the function that budgets it"
    heal = bodies["Repair-ConnectorsUntilServing"]
    assert "Wait-ForConnector" in heal, (
        "the wrapper no longer waits, so it escalates against a replica that is "
        "still registering and takes the pair down"
    )
    assert heal.index("Wait-ForConnector") < heal.index("Repair-Connectors -Ports $Ports -AlreadyReplaced"), (
        "the pair is escalated before the wait has proved nothing serves"
    )
    assert pass_body.index("Repair-ConnectorsUntilServing") < pass_body.index("Get-PublicProbeOutcome"), (
        "the public path is judged before the tunnel has been healed and waited for"
    )


def test_a_pass_that_heals_one_replica_finishes_the_pair_only_when_nothing_serves():
    """The budget holds while some replica serves. When the wait expires with
    nothing ready, the deferred replica is not half a pair — it is a second dead
    process, and leaving it costs the page another whole pass interval.

    Measured in this host's `data/runtime/watchdog.log` in the minutes after the
    budget was introduced: passes wrote `connector=False public=down`, the next
    repair came 3-4 minutes later, and the replica that had just been replaced was
    still registering when it came. So the rule escalates, but only on evidence
    that nothing serves: an escalation fired on the first probe is the same
    whole-pair outage the budget was written to prevent, and an escalation that
    re-kills the replica it started this pass leaves both halves mid-registration.
    """
    _cases, serving, budget, ports = _repair_cases()
    by_name = {case["case"]: case for case in serving}
    assert budget == 1, (
        f"the budget is {budget}: with more than one replica per pass the first call "
        "already empties the pair and there is nothing left for the wait to measure"
    )

    burst = by_name["pair-quiet-nothing-heals"]
    assert burst["serving"] is False, (
        f"the pass reported {burst['serving']} while every replica answered nothing"
    )
    assert sorted(burst["stops"]) == sorted(ports), (
        f"nothing served after the wait and the pass stopped {burst['stops']} of {ports}: "
        "the remaining replica is left dead for another pass interval"
    )
    assert burst["stops"] == burst["starts"], (
        f"stopped {burst['stops']} and started {burst['starts']}"
    )
    escalation = _escalation_logs(burst)
    assert escalation, (
        "the pass replaced the rest of the pair without saying why — an operator "
        "reading the log cannot tell a burst from a supervisor that stopped budgeting"
    )
    assert str(burst["stops"][0]) in escalation[0], (
        f"the escalation line {escalation[0]!r} does not name the replica whose wait "
        "expired, which is the fact that made this pass escalate"
    )
    every = " ".join(burst["logs"])
    for port in ports:
        assert str(port) in every, (
            f"replica {port} was stopped with no line naming it — `public=down` alone "
            "cannot tell a burst from a supervisor that gave up"
        )

    heals = by_name["pair-quiet-replacement-heals"]
    assert heals["serving"] is True
    assert len(heals["stops"]) == 1, (
        f"the replica the pass replaced came up and it still stopped {heals['stops']} — "
        "the healthy half of the pair is the measurement that says to stop here"
    )
    assert not _escalation_logs(heals), (
        "the pass escalated although something was already serving"
    )

    ready = by_name["all-ready"]
    assert ready["serving"] is True and ready["stops"] == [] and ready["starts"] == []
    for port in ports:
        single = by_name[f"only-{port}-quiet"]
        assert single["serving"] is True, f"the healthy replica was ready and the pass read {single['serving']}"
        assert single["stops"] == [port] and not _escalation_logs(single)

    grace = by_name["all-in-grace"]
    assert grace["stops"] == [] and grace["starts"] == [], (
        f"a pair inside its startup grace was touched: {grace['stops']}/{grace['starts']}"
    )
    assert grace["serving"] is False
    assert not _escalation_logs(grace), (
        "the pass escalated a pair that had not been given its grace, which is the "
        "outage the grace exists to prevent"
    )

    half = by_name["first-in-grace-rest-quiet"]
    assert ports[0] not in half["stops"], (
        f"escalation reached the replica inside its own grace: {half['stops']}"
    )

    missing = by_name["all-missing"]
    assert missing["stops"] == [], f"nothing was running and the pass stopped {missing['stops']}"
    assert sorted(missing["starts"]) == sorted(ports), (
        f"a host with no connector started {missing['starts']} — every replica has to "
        "be brought up, and none of them twice"
    )


def test_library_mode_supervises_nothing(tmp_path):
    """Dot-sourcing must not start, stop or log — or the gate that reads the rules
    would be a second watchdog writing to the live tunnel.

    The copy resolves `$Repo` into `tmp_path`, so a pass that reached `Ensure-Services`
    or `Write-Log` would leave files in the scratch directory. The script itself is
    never dot-sourced with the supervisor switched on.
    """
    copy = tmp_path / "watchdog.ps1"
    copy.write_bytes(WATCHDOG.read_bytes())
    before = sorted(p.name for p in tmp_path.iterdir())
    out = _run_ps(_dot_source(copy) + "Write-Output 'loaded'\n")
    assert "loaded" in out
    created = [
        p.name
        for p in tmp_path.iterdir()
        if p.name not in before and "runtime" in p.name.lower()
    ]
    assert not created, f"library mode wrote {created} — a gate must not run the supervisor"


def test_the_script_refuses_to_supervise_without_a_switch(tmp_path):
    """A stray `watchdog.ps1` on a prompt must not take the tunnel over.

    `-Once` is the switch Task Scheduler passes; everything else is an error that
    names both switches, so an operator reading the message learns the read-only
    probe exists instead of assuming the script did nothing.
    """
    copy = tmp_path / "watchdog.ps1"
    copy.write_bytes(WATCHDOG.read_bytes())
    out = subprocess.run(
        [*_pwsh(), "-File", str(copy)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert out.returncode != 0, "the script supervised on a bare invocation"
    message = out.stdout + out.stderr
    assert "-Once" in message and "-Check" in message, message


# The fields whose existence is the point of `-Check`: which replica is ready, and
# *how* the origin failed. A report that drops one of these collapses back to the
# single boolean that made a three-hour outage read as a mystery, so the whole set is
# not restated here — `_check_fields()` reads it off the script.
DIAGNOSTIC_KEYS = ("ReplicaPorts", "ReadyPorts", "OriginOutcome", "PublicOutcome")


def test_the_read_only_probe_reports_each_replica_separately():
    """The report shape, read off the source so CI refuses a collapse back to one
    boolean.

    `ConnectorReady` alone is what made three hours of outage read as a mystery: one
    replica answered 503 while its twin served the page, and the line said
    `connector=False` about both. The executed version of this law, below, runs the
    probe on the host that owns the processes.
    """
    check_block = WATCHDOG.read_text(encoding="utf-8")
    check_block = check_block[check_block.index("if ($Check)") :]
    fields = _check_fields()
    assert set(DIAGNOSTIC_KEYS) <= fields, f"`-Check` collapsed {sorted(set(DIAGNOSTIC_KEYS) - fields)} away"
    for key in fields:
        assert re.search(rf"\b{key} =", check_block), f"`-Check` no longer answers {key}"


@pytest.mark.skipif(os.name != "nt", reason="Get-CimInstance enumerates Windows processes")
def test_the_read_only_probe_names_the_replica_that_died():
    """`-Check` answers which replica is ready, not merely that one is, and every
    outcome it reports is a word the measured rules can actually answer.

    The single-port report is why the outage read as "connector=False" for hours: the
    page was serving through a second connector while the first had nothing. Both the
    replica set and the ready subset must be in the answer, and the origin and the
    public leg must say *how* they failed — `silent` and `answered` decide whether the
    watchdog will reboot the process, and `down` and `unreachable` decide whether the
    pass exits 1. Only `Get-EndpointOutcome` and `Get-PublicOutcome` know the
    difference, so the live probe is read against the vocabularies they answer from.
    """
    out = subprocess.run(
        [*_pwsh(), "-File", str(WATCHDOG), "-Check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert out.returncode == 0, out.stderr
    state = json.loads(out.stdout[out.stdout.index("{") : out.stdout.rindex("}") + 1])
    assert set(_check_fields()) == set(state), (
        f"-Check answered {sorted(set(state) ^ set(_check_fields()))} away from the fields the block builds"
    )
    assert set(_as_list(state["ReadyPorts"])) <= set(_as_list(state["ReplicaPorts"]))
    endpoint, public, _ = _outcomes()
    assert state["OriginOutcome"] in endpoint, f"-Check answered the origin as {state['OriginOutcome']!r}"
    assert state["PublicOutcome"] in public, f"-Check answered the public leg as {state['PublicOutcome']!r}"


def test_the_doc_names_the_replica_block_the_script_binds():
    """The operator reading `docs/CLOUDFLARE.md` during an incident is told to probe
    exactly the ports the replicas bind, and the doc says how many replicas there are.

    Both directions. A doc naming a third `/ready` port sends a human to a socket
    nothing owns; a script that grows a replica leaves the runbook describing a
    machine that no longer exists. The replica count is paired with the word it
    belongs to rather than searched for anywhere, so an unrelated digit in the file
    cannot answer for it, and only the plural counts: a paragraph explaining that
    one replica had no edge connection is a measurement, not a block size.
    """
    _, _, _, replicas, base = _constants()
    ports = set(range(base, base + replicas))
    prose = DOC.read_text(encoding="utf-8")
    named = {int(p) for p in re.findall(r"127\.0\.0\.1:(\d{2,5})/ready", prose)}
    assert named, "docs/CLOUDFLARE.md never tells an operator to probe a replica"
    assert named == ports, f"the doc probes {sorted(named)}, the watchdog binds {sorted(ports)}"
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    stated = set()
    for line in prose.splitlines():
        if "replica" not in line.lower():
            continue
        stated |= {int(d) for d in re.findall(r"(\d+)(?= replicas\b)", line)}
        stated |= {words[w.lower()] for w in re.findall(r"(?i)\b(one|two|three|four|five) replicas\b", line)}
    assert stated == {replicas}, f"the doc counts {sorted(stated)} replicas, the script starts {replicas}"


def test_the_windows_table_states_the_delays_the_script_waits_by():
    """The runbook's table of windows and the script's constants are one set of numbers.

    An operator deciding whether to wait for the next pass reads "3, 15 s apart" and
    "600 s"; the same figures live as `$ConfirmProbes`, `$ConfirmDelaySeconds` and
    `$OriginGraceSeconds`, and nothing compared them. The table is found by its shape —
    the three-column table whose value column states waits in seconds — because a doc
    also carries a routing table and a troubleshooting table that state no window at
    all. The script's side is the variables it defines whose own names end in `Seconds`
    or `Probes`, so a new window has to be written down in the runbook to pass, and a
    figure the script does not wait by fails.
    """
    prose = DOC.read_text(encoding="utf-8")
    doc_seconds: set[int] = set()
    doc_counts: set[int] = set()
    tables = 0
    for block in re.findall(r"(?m)^(?:\|.*\|\n)+", prose):
        rows = [line for line in block.strip().splitlines() if line.count("|") >= 4]
        values = [row.split("|")[2] for row in rows if not set(row) <= set("|-: ") and len(row.split("|")) >= 4][1:]
        if len([v for v in values if re.search(r"\d+\s+s\b", v)]) < 2:
            continue
        tables += 1
        for value in values:
            for number, unit in re.findall(r"(\d+)(?:\s*([A-Za-z]+))?", value):
                if unit and unit.lower().rstrip(",") == "s":
                    doc_seconds.add(int(number))
                else:
                    doc_counts.add(int(number))
    assert tables, "docs/CLOUDFLARE.md states no table of windows in seconds"
    script = _dot_source(WATCHDOG) + (
        "$found = [ordered]@{};"
        " Get-Variable | Where-Object { $_.Name -match '(Seconds|Probes)$' -and $_.Value -is [int] }"
        " | ForEach-Object { $found[$_.Name] = $_.Value };"
        " $found | ConvertTo-Json -Compress\n"
    )
    variables = json.loads(_run_ps(script).strip())
    seconds = {v for name, v in variables.items() if name.endswith("Seconds")}
    counts = {v for name, v in variables.items() if name.endswith("Probes")}
    assert len(seconds) >= 4, f"the script waits by {sorted(seconds)} — fewer than four second-windows is a broken reader"
    assert counts, "the script defines no *_Probes constant"
    assert doc_seconds == seconds, f"the runbook waits by {sorted(doc_seconds)}s, the script by {sorted(seconds)}s"
    assert doc_counts == counts, f"the runbook probes {sorted(doc_counts)} times, the script {sorted(counts)}"


def test_the_doc_names_the_variables_the_public_leg_is_built_from():
    """The runbook and the script share one name per knob, in both directions.

    The subject set is the script's own `Public*` variables, enumerated by PowerShell
    rather than typed here, so a new knob in the supervisor cannot ship undocumented
    and a runbook cannot point an operator at a variable that was renamed. During the
    hours this host reported the site `down` for a fault in its own TLS path, the two
    names that would have explained it to whoever looked — the witness and the origin
    it witnesses — were in no document.
    """
    listed = _run_ps(
        _dot_source(WATCHDOG)
        + "Get-Variable -Name 'Public*' | ForEach-Object { '$' + $_.Name } | Sort-Object | ConvertTo-Json -Compress\n"
    )
    names = [str(x) for x in _as_list(json.loads(listed.strip()))]
    assert len(names) >= 5, f"the supervisor's public leg reads as {names} — the variable walk is blind"
    prose = DOC.read_text(encoding="utf-8")
    stated = set(re.findall(r"\$Public\w+", prose))
    assert set(names) <= stated, f"docs/CLOUDFLARE.md never names {sorted(set(names) - stated)}"
    assert stated <= set(names), f"docs/CLOUDFLARE.md points an operator at {sorted(stated - set(names))}, which the script has not defined"


def test_the_doc_states_the_answers_and_the_fields_the_supervisor_reports():
    """Every word the supervisor can answer is in the runbook, and the runbook's
    field list is the report's.

    The outcome vocabulary is what an operator branches on, and `-Check` is the only
    command that says which half broke; both were prose the script never had to agree
    with. The field list is paired with the hashtable `-Check` builds, so a report
    that grows a field fails until the sentence grows it too, and a sentence that
    promises one the script dropped fails as well.
    """
    endpoint, public, _ = _outcomes()
    fields = _check_fields()
    prose = DOC.read_text(encoding="utf-8")
    backticked = set(re.findall(r"`([^`\n]+)`", prose))
    answers = set(endpoint) | set(public)
    missing = answers - backticked
    assert not missing, f"docs/CLOUDFLARE.md never states the answer(s) {sorted(missing)} the supervisor gives"
    lists = [
        paragraph
        for paragraph in prose.split("\n\n")
        if len({*re.findall(r"`([A-Z][A-Za-z0-9]+)`", paragraph)} & fields) >= 3
    ]
    assert len(lists) == 1, f"{len(lists)} paragraphs list the `-Check` fields; exactly one runbook sentence should"
    named = set(re.findall(r"`([A-Z][A-Za-z0-9]+)`", lists[0]))
    assert named == fields, (
        f"the runbook reports {sorted(named ^ fields)} away from what `-Check` actually answers"
    )
    assert {"ok", "down", "unreachable"} <= set(public), f"the public leg answers {sorted(public)}"
    assert set(endpoint) & {"answered", "silent"}, f"the probe vocabulary is {sorted(endpoint)}"


def test_the_doc_keeps_both_availability_measurements_side_by_side():
    """The replica pair exists because of three fractions, and the doc has to keep them.

    Asserted as a relationship rather than as literals: the paragraph about the
    replicas states each availability as `n of d (p%)`, every one of those
    percentages has to be the percentage its own fraction yields, and the last is
    higher than the first — that is the comparison that justified starting a second
    connector. The middle figure is the one that localises the fault: the public
    endpoint and the connector's own `/ready` measured over the same walk say the
    origin was answering while the page was not, which is why the remedy was a
    replica and not a restart of the app. Re-measure this host's path to the edge
    and the doc is only required to keep all three sides of it.
    """
    prose = DOC.read_text(encoding="utf-8")
    blocks = []
    for paragraph in prose.split("\n\n"):
        if "replica" not in paragraph.lower():
            continue
        figures = [(int(n), int(d), int(p)) for n, d, p in WALK_FRACTION.findall(paragraph)]
        if figures:
            blocks.append(figures)
    assert blocks, "no replica paragraph measures an availability as a fraction"
    for figures in blocks:
        for hits, total, pct in figures:
            assert abs(pct - 100 * hits / total) <= 0.6, (
                f"the doc prints {pct}% for {hits} of {total}, which is {100 * hits / total:.1f}%"
            )
    wide = [figures for figures in blocks if len(figures) >= 3]
    assert wide, (
        f"a replica paragraph states {max(len(f) for f in blocks)} availability fractions; "
        "the comparison that justified the pair needs the public figure with one connector, "
        "the connector's own /ready over the same walk, and the public figure with the pair"
    )
    for figures in wide:
        assert figures[-1][2] > figures[0][2], (
            f"{[f[2] for f in figures]} — the doc claims the pair, but never that it reads better"
        )
    for needle in ("edge-bind-address", "http2"):
        assert needle in prose, f"docs/CLOUDFLARE.md never mentions {needle!r}"


def _replica_walks() -> dict[str, set[tuple[int, int]]]:
    """Every availability walk a human-readable markdown file states about a replica.

    The subject set is the directory the operator reads — the repository root and
    `docs/` — not a hand-written pair of filenames, so a third document joins by
    saying the word `replica` beside a fraction. A paragraph is the unit because a
    fraction only means something inside the sentence that names its denominator, and
    the vendored trees are outside both globs by construction.
    """
    paths = sorted(list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")))
    assert len(paths) >= 5, f"the readable markdown is {len(paths)} files: the glob went blind"
    walks: dict[str, set[tuple[int, int]]] = {}
    for path in paths:
        figures: set[tuple[int, int]] = set()
        for paragraph in path.read_text(encoding="utf-8").split("\n\n"):
            if "replica" in paragraph.lower():
                figures |= {(int(hits), int(total)) for hits, total, _ in WALK_FRACTION.findall(paragraph)}
        if figures:
            walks[path.name] = figures
    return walks


def test_no_document_quotes_a_replica_walk_this_host_never_measured():
    """One walk, published once, quoted identically everywhere else.

    `docs/CLOUDFLARE.md` measures the tunnel; `AGENTS.md` repeats its figures to explain
    why the supervisor exists. When this wave re-walked the pair over 87 samples, the
    runbook was corrected and the short file kept the earlier 20-sample pass — `9 of 20
    (45%)`, arithmetically its own answer, so the fraction law that reads only the
    runbook certified a number the host no longer produces, and a maintainer comparing
    the two documents found two measurements of one question. Containment, not set
    equality: the runbook keeps walks a summary has no reason to repeat.
    """
    walks = _replica_walks()
    runbook = walks[DOC.name]
    assert len(runbook) >= 3, f"the runbook measures {sorted(runbook)} — too few fractions to be the source"
    assert len(walks) >= 2, f"only {sorted(walks)} states a replica availability, so nothing is compared"
    stale = {name: sorted(figures - runbook) for name, figures in walks.items() if figures - runbook}
    assert not stale, (
        f"{stale} — a document quotes an `n of d` walk the runbook does not measure; "
        "re-measure this host's path to the edge and every file repeating the figure changes with it"
    )


def test_the_documented_law_count_is_the_number_of_laws_in_this_file():
    """The sentence that tells a maintainer how much of the supervisor is measured is
    measured by this run.

    `AGENTS.md` stated fourteen laws while this file held twenty-three, and stated that
    two of them enumerate processes while exactly one carries the Windows guard — a
    count nobody re-counted is a count that drifts every time a law lands, and the drift
    reads as coverage. Both numbers are read out of this file's own AST: a law is a
    `test_*` function, and a guarded law is one decorated to skip off Windows.
    """
    code = Path(__file__).resolve().read_text(encoding="utf-8")
    body = ast.parse(code).body
    laws = [node for node in body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    guarded = [
        node.name
        for node in laws
        if any("skipif" in ast.get_source_segment(code, decorator) for decorator in node.decorator_list)
    ]
    prose = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    totals = [int(n) for n in TOTAL_LAW_CLAIM.findall(prose)]
    pairs = [(int(a), int(b)) for a, b in GUARDED_LAW_CLAIM.findall(prose)]
    assert totals, "AGENTS.md no longer states how many laws measure the supervisor"
    assert pairs, "AGENTS.md no longer states which of them runs this host's live processes"
    assert len(totals) == len(pairs) == 1, f"the claims are {totals} and {pairs}: one sentence each, or nothing pins them"
    for stated in totals:
        assert stated == len(laws), (
            f"AGENTS.md says {stated} laws measure the supervisor and this file has {len(laws)}: "
            "count them by running the file, not by remembering"
        )
    for guarded_stated, stated_total in pairs:
        assert stated_total == len(laws), f"the guarded pair names {stated_total} laws; this file has {len(laws)}"
        assert guarded_stated == len(guarded), (
            f"AGENTS.md says {guarded_stated} of {stated_total} laws is guarded by the platform "
            f"and this file guards {len(guarded)}: {guarded}"
        )
