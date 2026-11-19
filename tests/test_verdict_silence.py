"""A money verdict nobody records is a verdict that never happened.

Two shapes of silence were measured in this tree, and both were real:

**A delivery that silently failed.** ``await cb.message.edit_text(receipt)``
wrapped in ``except Exception: pass``. When the money had already moved, the
card under the user's finger stayed on "processing" and the buttons under it
still invited the same tap — the receipt existed only in the handler's memory.
`bot/handlers/onchain.py` bought and sold like that, and `_edit_withdraw_prompt`
answered a withdrawal by editing a message that might not exist while leaving
the confirm button painted.

**A decode that silently failed.** ``process_log()`` of a USDC Transfer wrapped
in ``except Exception: continue``. The deposit scan then returned one event
short, ``_usdc_received`` reported a payout short, ``settle_eip3009`` raised
"no USDC moved" about a receipt that may have moved some, and ``_scan_deposits``
dropped a payment into the hot wallet that nobody was ever going to credit.

Neither shape needed a *raise*: several failures in this product are
deliberately invisible to the user (a blocked recipient must not break a
resolve). What they needed was a record, so these laws refuse a handler whose
body is only ``pass``/``continue`` over a delivery call, over an event-log
decode, or over the parse of a record that feeds a published count. Anything
else the handler does — a log call at any level, a raise, an assignment —
counts as a record.

The subject set is ``git ls-files`` over the runtime packages, not a list of
files this wave remembered: a new handler that swallows a receipt joins the law
the moment it is written.

What these laws cannot see, and nobody should pretend otherwise:

* a handler that "records" by assigning a variable, returning, or ``break``ing
  instead of logging — anything but a ``pass``/``continue``-only body reads as a
  record, and only a human can tell a log line from a second shrug;
* a delivery reached through a helper that swallows inside itself (the helper
  is what the law reads, so the helper has to be the honest one);
* a log call at a level nobody reads at the default verbosity;
* a swallow outside a function — module level has no receiver to answer;
* ``tests/``, ``alembic/`` and ``web/static/``: the first is the harness, the
  second runs under a command that prints its own failures, the third is not
  Python.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# The runtime tree the laws walk. A file in none of these directories is a test,
# a migration or a static asset, and none of them paints a user's receipt.
RUNTIME_TOP_DIRS = ("bot", "web", "agent", "deploy", "scripts")

# Methods that put something in front of a user. `answer` is in the set for both
# of its meanings (a message answer and a callback toast): a tap that answers
# nothing is the same silence either way.
DELIVERY_METHODS = frozenset({
    "edit_text", "edit_caption", "edit_reply_markup", "delete_message",
    "send_message", "send_invoice", "send_photo", "send_document", "send_video",
    "reply", "forward", "copy_message", "answer",
})

# A decoder whose failure loses an on-chain money fact.
LOG_DECODE_METHODS = frozenset({"process_log"})
# A parser of one record from a trail that a published counter is built from.
RECORD_PARSE_METHODS = frozenset({"loads"})

# Floors: a scan that quietly stopped reading would report a clean tree, so each
# law also holds the number of sites it looked at away from zero. Measured this
# wave over the runtime tree: 32 delivery tries, 6 process_log decodes, and
# exactly 1 counted record parse — which is why the last floor is 1 and not a
# number that would make the third law read like a survey it never ran.
MIN_DELIVERY_TRIES = 25
MIN_LOG_DECODE_TRIES = 5
MIN_RECORD_PARSE_TRIES = 1


def runtime_python_files() -> list[str]:
    """Every tracked runtime `.py`, from `git ls-files` rather than a list."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", check=True,
    ).stdout.splitlines()
    return sorted(p for p in listed
                  if p.endswith(".py") and p.split("/")[0] in RUNTIME_TOP_DIRS)


def _source_of(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """A handler that says nothing: only `pass` and `continue` in its body."""
    return bool(handler.body) and all(
        isinstance(stmt, (ast.Pass, ast.Continue)) for stmt in handler.body
    )


def _functions(tree: ast.Module) -> list[ast.AST]:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _callees(node: ast.AST) -> set[str]:
    """Attribute-call names under `node`, without descending into a nested try —
    a delivery guarded by an inner handler belongs to the inner try, not this one.
    """
    found: set[str] = set()

    def walk(sub: ast.AST) -> None:
        for child in ast.iter_child_nodes(sub):
            if isinstance(child, ast.Try):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                found.add(child.func.attr)
            walk(child)

    walk(node)
    return found


def _names_returned(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            names |= {leaf.id for leaf in ast.walk(node.value)
                      if isinstance(leaf, ast.Name)}
    return names


def _feeds_a_returned_counter(fn: ast.AST, try_node: ast.Try) -> bool:
    """Is this try the parse of one record inside a loop that bumps a name the
    function returns? Then the number a page prints is the number of lines this
    loop managed to read, and an unparseable line is a smaller figure — which is
    a published wrong answer, not a skipped line.
    """
    returned = _names_returned(fn)
    for loop in [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.AsyncFor))]:
        if any(node is try_node for node in ast.walk(loop)):
            bumped = {n.target.id for n in ast.walk(loop)
                      if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)}
            if bumped & returned:
                return True
    return False


def kinds_of_try(fn: ast.AST, try_node: ast.Try) -> set[str]:
    callees: set[str] = set()
    for stmt in try_node.body:
        if isinstance(stmt, ast.Try):
            continue  # a nested try carries its own handlers — the reader test hands it that shape
        callees |= _callees(stmt)
    kinds: set[str] = set()
    if callees & DELIVERY_METHODS:
        kinds.add("delivery")
    if callees & LOG_DECODE_METHODS:
        kinds.add("log-decode")
    if (callees & RECORD_PARSE_METHODS) and _feeds_a_returned_counter(fn, try_node):
        kinds.add("audit-count")
    return kinds


def analyse(rel: str, source: str) -> tuple[list[dict], dict[str, int]]:
    """Every silent handler over a delivery, a log decode or a counted record.

    Returns `(offenders, inspected)`, where `inspected` counts the try blocks
    looked at per kind so a reader that went blind is caught by the floor instead
    of passing as a clean tree.
    """
    offenders: list[dict] = []
    inspected = {"delivery": 0, "log-decode": 0, "audit-count": 0}
    tree = ast.parse(source)
    for fn in _functions(tree):
        for try_node in [t for t in ast.walk(fn) if isinstance(t, ast.Try)]:
            kinds = kinds_of_try(fn, try_node)
            for kind in kinds:
                inspected[kind] += 1
            for handler in try_node.handlers:
                if not _is_silent(handler):
                    continue
                for kind in sorted(kinds):
                    offenders.append({"file": rel, "line": handler.lineno,
                                      "function": getattr(fn, "name", "?"),
                                      "kind": kind})
    return offenders, inspected


def _all_offenders() -> tuple[list[dict], dict[str, int]]:
    offenders: list[dict] = []
    inspected = {"delivery": 0, "log-decode": 0, "audit-count": 0}
    for rel in runtime_python_files():
        found, seen = analyse(rel, _source_of(rel))
        offenders += found
        for key, value in seen.items():
            inspected[key] += value
    return offenders, inspected


# ---------------------------------------------------------------------------
# the laws
# ---------------------------------------------------------------------------

def test_no_delivery_that_may_fail_is_wrapped_in_a_handler_that_says_nothing():
    offenders, inspected = _all_offenders()
    silent = [o for o in offenders if o["kind"] == "delivery"]
    assert inspected["delivery"] >= MIN_DELIVERY_TRIES, (
        f"only {inspected['delivery']} delivery tries were read — the walker went blind")
    assert not silent, "a receipt that can vanish unrecorded: " + repr(silent[:12])


def test_no_event_log_decode_that_may_fail_is_wrapped_in_a_handler_that_says_nothing():
    offenders, inspected = _all_offenders()
    silent = [o for o in offenders if o["kind"] == "log-decode"]
    assert inspected["log-decode"] >= MIN_LOG_DECODE_TRIES, (
        f"only {inspected['log-decode']} process_log tries were read")
    assert not silent, "an on-chain money log that can vanish unrecorded: " + repr(silent[:12])


def test_no_counted_record_parse_that_may_fail_is_wrapped_in_a_handler_that_says_nothing():
    offenders, inspected = _all_offenders()
    silent = [o for o in offenders if o["kind"] == "audit-count"]
    assert inspected["audit-count"] >= MIN_RECORD_PARSE_TRIES, (
        f"only {inspected['audit-count']} counted record parses were read")
    assert not silent, "a published count reduced by a line nobody reported: " + repr(silent[:12])


def test_the_subject_set_is_the_runtime_tree_not_a_remembered_list():
    files = runtime_python_files()
    for needed in ("bot/base.py", "bot/handlers/onchain.py", "web/x402.py",
                   "web/x402_spec.py", "web/server.py", "bot/handlers/wallet.py"):
        assert needed in files, needed
    assert len(files) > 100, len(files)
    assert not [f for f in files if f.startswith(("tests/", "alembic/", "web/static/"))]


# ---------------------------------------------------------------------------
# the readers, held to the shapes source code actually takes
# ---------------------------------------------------------------------------

SHAPES = [
    pytest.param(
        'async def h(cb):\n'
        '    try:\n'
        '        await cb.message.edit_text("receipt")\n'
        '    except Exception:\n'
        '        pass\n',
        {"delivery"}, id="edit-text-swallowed"),
    pytest.param(
        'async def h(cb):\n'
        '    try:\n'
        '        await cb.message.edit_text("receipt")\n'
        '    except Exception as e:\n'
        '        log.warning("refused: %s", e)\n',
        set(), id="edit-text-logged"),
    pytest.param(
        'async def h(cb):\n'
        '    try:\n'
        '        await cb.message.edit_text("receipt")\n'
        '    except Exception:\n'
        '        raise\n',
        set(), id="edit-text-re-raised"),
    pytest.param(
        'async def h(cb):\n'
        '    try:\n'
        '        await cb.message.edit_text("receipt")\n'
        '    except Exception:\n'
        '        # the user may have deleted the message\n'
        '        pass\n',
        {"delivery"}, id="a-comment-is-not-a-record"),
    pytest.param(
        'async def h(bot, rows):\n'
        '    for row in rows:\n'
        '        try:\n'
        '            await bot.send_message(row.id, "notice")\n'
        '        except Exception:\n'
        '            continue\n',
        {"delivery"}, id="dm-loop-continue"),
    pytest.param(
        'async def h(cb):\n'
        '    try:\n'
        '        await cb.answer("toast")\n'
        '    except Exception:\n'
        '        pass\n',
        {"delivery"}, id="callback-toast-swallowed"),
    pytest.param(
        'def h(receipt):\n'
        '    total = 0\n'
        '    for entry in receipt["logs"]:\n'
        '        try:\n'
        '            ev = usdc.events.Transfer().process_log(entry)\n'
        '        except Exception:\n'
        '            continue\n'
        '        total += ev["args"]["value"]\n'
        '    return total\n',
        {"log-decode"}, id="process-log-swallowed"),
    pytest.param(
        'def h(receipt):\n'
        '    for entry in receipt["logs"]:\n'
        '        try:\n'
        '            ev = usdc.events.Transfer().process_log(entry)\n'
        '        except Exception as e:\n'
        '            log.error("will not decode: %s", e)\n'
        '            continue\n',
        set(), id="process-log-logged"),
    pytest.param(
        'def h(trail):\n'
        '    count = 0\n'
        '    for line in trail:\n'
        '        try:\n'
        '            entry = json.loads(line)\n'
        '        except json.JSONDecodeError:\n'
        '            continue\n'
        '        count += 1\n'
        '    return {"count": count}\n',
        {"audit-count"}, id="counted-parse-swallowed"),
    pytest.param(
        'def h(trail):\n'
        '    out = []\n'
        '    for line in trail:\n'
        '        try:\n'
        '            entry = json.loads(line)\n'
        '        except json.JSONDecodeError:\n'
        '            continue\n'
        '        out.append(entry)\n'
        '    return out\n',
        set(), id="a-parse-whose-failure-changes-no-count"),
    pytest.param(
        'def h(raw):\n'
        '    try:\n'
        '        return json.loads(raw)\n'
        '    except json.JSONDecodeError:\n'
        '        return None\n',
        set(), id="parse-with-a-second-answer"),
    pytest.param(
        'def h(rows):\n'
        '    total = 0\n'
        '    for row in rows:\n'
        '        try:\n'
        '            price = compute(row)\n'
        '        except Exception:\n'
        '            continue\n'
        '    return len(rows)\n',
        set(), id="a-loop-accumulator-the-try-never-touches"),
    pytest.param(
        'async def h(cb):\n'
        '    try:\n'
        '        try:\n'
        '            await cb.message.edit_text("receipt")\n'
        '        except Exception as e:\n'
        '            log.warning("inner refused: %s", e)\n'
        '    except Exception:\n'
        '        pass\n',
        set(), id="an-inner-handler-answers-the-delivery"),
]


@pytest.mark.parametrize("source,expected", SHAPES)
def test_the_silence_reader_reads_the_shape_a_handler_leaves_behind(source, expected):
    offenders, inspected = analyse("scratch.py", source)
    assert {o["kind"] for o in offenders} == expected, offenders
    assert set(inspected) == {"delivery", "log-decode", "audit-count"}


def test_the_silence_reader_counts_only_the_try_it_classes():
    _offenders, inspected = analyse(
        "scratch.py",
        'async def h(cb):\n'
        '    await cb.message.edit_text("first")\n'
        '    await cb.message.edit_text("second")\n')
    assert inspected == {"delivery": 0, "log-decode": 0, "audit-count": 0}


def _mutate(rel: str, fixed: str, broken: str) -> tuple[set[str], set[str]]:
    """Run the reader over the real file and over a copy of it with the fix undone."""
    source = _source_of(rel)
    assert fixed in source, (rel, "the shape this mutation undoes is gone from the file")
    before, _seen = analyse(rel, source)
    after, _seen = analyse(rel, source.replace(fixed, broken, 1))
    clean = {o["kind"] for o in before}
    fired = {o["kind"] for o in after}
    assert not clean, (rel, "the shipped file already reads as silent")
    return clean, fired


MUTATIONS = [
    pytest.param("bot/handlers/wallet.py",
                 '    await common.paint_or_send(cb, text, what="withdraw prompt")\n',
                 '    try:\n'
                 '        await cb.message.edit_text(text)\n'
                 '    except Exception:\n'
                 '        pass\n',
                 "delivery", id="withdraw-prompt-shrug-returns"),
    pytest.param("bot/handlers/bets.py",
                 '            log.info("bet #%s result notice never reached %s: %s", '
                 'bet_id, tg_id, e)\n',
                 "            pass\n", "delivery", id="bet-result-notice-shrug-returns"),
    pytest.param("bot/base.py",
                 '            log.info("subscription-expiry notice never reached %s: %s", '
                 'tg_id, e)\n',
                 "            pass\n", "delivery", id="expiry-notice-shrug-returns"),
    pytest.param("bot/handlers/onchain.py",
                 '    except Exception as e:\n'
                 '        log.warning("onchain #%s: the amount chooser would not paint: %s", '
                 'mid, e)\n',
                 '    except Exception:\n'
                 '        pass\n',
                 "delivery", id="amount-chooser-shrug-returns"),
    pytest.param("web/server.py",
                 "                    log.warning('agent audit line will not parse and is left "
                 "out of the published market and bet counts: %s', e)\n",
                 "", "audit-count", id="audit-line-shrug-returns"),
    pytest.param("web/x402_spec.py",
                 '            log.error("USDC Transfer log of settlement tx %s will not decode: %s",\n'
                 '                      receipt.get("transactionHash", "?"), e)\n',
                 "", "log-decode", id="settlement-decode-shrug-returns"),
]


@pytest.mark.parametrize("rel,fixed,broken,kind", MUTATIONS)
def test_undoing_a_shipped_fix_fires_the_law(rel, fixed, broken, kind):
    _before, fired = _mutate(rel, fixed, broken)
    assert kind in fired, (rel, kind, fired)


# ---------------------------------------------------------------------------
# the primitive that replaced the shrug
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, *, edit_error=None, answer_error=None):
        self.edited: list[tuple[str, object]] = []
        self.sent: list[tuple[str, object]] = []
        self._edit_error = edit_error
        self._answer_error = answer_error

    async def edit_text(self, text, reply_markup=None):
        if self._edit_error is not None:
            raise RuntimeError(self._edit_error)
        self.edited.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        if self._answer_error is not None:
            raise RuntimeError(self._answer_error)
        self.sent.append((text, reply_markup))


class _Cb:
    def __init__(self, message):
        self.message = message

        class _User:
            id = 4242

        self.from_user = _User()


@pytest.fixture()
def common():
    from bot.handlers import _common
    return _common


async def test_a_verdict_the_edit_reaches_sends_nothing_else(common):
    msg = _Msg()
    await common.paint_or_send(_Cb(msg), "receipt", what="test")
    assert msg.edited == [("receipt", None)]
    assert msg.sent == []


async def test_a_message_that_already_says_this_sends_no_duplicate(common):
    msg = _Msg(edit_error="Bad Request: message is not modified")
    await common.paint_or_send(_Cb(msg), "receipt", what="test")
    assert msg.edited == []
    assert msg.sent == []


async def test_a_refused_edit_sends_the_verdict_instead_of_losing_it(common):
    msg = _Msg(edit_error="Bad Request: chat not found")
    await common.paint_or_send(_Cb(msg), "receipt", reply_markup="KB", what="test")
    assert msg.edited == []
    assert msg.sent == [("receipt", "KB")]


async def test_a_verdict_nobody_can_receive_is_written_down(common, caplog):
    msg = _Msg(edit_error="edit refused", answer_error="send refused")
    with caplog.at_level("ERROR"):
        await common.paint_or_send(_Cb(msg), "receipt", what="withdraw prompt")
    assert "undeliverable" in caplog.text
    assert "withdraw prompt" in caplog.text


async def test_a_callback_carrying_no_message_is_written_down(common, caplog):
    with caplog.at_level("ERROR"):
        await common.paint_or_send(_Cb(None), "receipt", what="oc buy #7")
    assert "carried no message" in caplog.text


def test_the_verdict_primitive_is_reachable_from_every_handler(common):
    assert "paint_or_send" in common.__all__


def test_the_money_paths_that_painted_a_receipt_and_shrug_now_use_the_primitive():
    """A settled on-chain trade is the case the primitive exists for: the money
    moved, the screen still says "processing", and the buttons invite the tap
    again. Each label below is the `what=` a refusal is logged under, so a path
    that went back to a bare edit is named here and caught by the silence law.
    """
    settled = [
        ("bot/handlers/onchain.py", 'what=f"onchain buy #'),
        ("bot/handlers/onchain.py", 'what=f"onchain sell #'),
        ("bot/handlers/onchain.py", 'what=f"onchain resolve #'),
        ("bot/handlers/wallet.py", 'what="withdraw prompt"'),
    ]
    for rel, needle in settled:
        assert needle in _source_of(rel), (rel, needle)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
