"""The event loop is one thread, and a blocking call inside a coroutine spends it for everybody.

aiogram serves every user of the bot from one loop and uvicorn serves every request of the origin
from one loop, so a raw ``time.sleep``, an ``urllib.request.urlopen`` or a direct call into a
``*_sync`` helper inside a handler does not slow that one user down — it freezes the process for
the whole timeout. ``bot/base.py`` states the convention (*"separate thread via asyncio.to_thread,
so the bot's event loop stays free"*) and the tree honours it: 368 coroutines, 61 ``*_sync``
helpers and 100 offload sites were measured the day these laws were written. The day before it the
repo carried a real violation — ``agent/signals.py``'s ``buy_own_signal()`` was an ``async def``
wrapped around a blocking ``requests.post(url, timeout=30)`` that would have held the loop for
half a minute per call. It had no callers and was deleted; this file is the law for the version of
that function that had them.

What the scan cannot see, stated rather than hidden behind an exception list: a blocking call
reached through a callable the offload never names (``asyncio.to_thread(make_work())``, where
``make_work`` returns a closure that blocks), a web3 **property** read on the loop
(``w3.eth.block_number`` is an attribute, not a call — the tree already knows this trap, and
``bot/base.py`` comments on exactly it while wrapping the read in a lambda), and a helper that
waits but is neither named ``*_sync`` nor built from a primitive in ``_PRIMITIVE_PATTERNS``. That
last is why a new sync I/O boundary is expected to take the ``_sync`` suffix: it is what makes the
second law able to read it.
"""

import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The tree that runs inside a loop. ``alembic/`` is sync-only migrations and ``tests/`` is the
#: harness, which may block on a database as long as it likes.
LOOP_DIRS = ("bot", "web", "agent", "deploy")

#: Calls whose arguments run on another thread, so blocking work inside them is the fix, not the bug.
OFFLOAD_NAMES = frozenset({"to_thread", "run_in_executor", "submit"})

#: The mark this repository uses for "this waits for Postgres, the chain or the disk".
SYNC_SUFFIX = "_sync"

_PRIMITIVE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?:^|\.)urlopen$"), "urllib.request.urlopen"),
    (re.compile(r"^requests\.[a-z]+$"), "requests"),
    (re.compile(r"(?:^|\.)sleep$"), "time.sleep"),
    (re.compile(r"^subprocess\.(?:run|call|check_call|check_output|Popen)$"), "subprocess"),
    (re.compile(r"^httpx\.(?:Client|get|post|put|patch|delete|head|request|stream)$"), "httpx (sync client)"),
)


def _chain(node: ast.expr) -> tuple[str, ...]:
    """``asyncio.to_thread`` -> ``("asyncio", "to_thread")``; ``om._w3().eth`` keeps the tail."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def blocking_primitive(chain: tuple[str, ...]) -> str | None:
    """The blocking family a dotted call name belongs to, or ``None``.

    ``asyncio.sleep`` answers ``None`` before any pattern runs: it yields to the loop, which is the
    whole point, and it ends in ``sleep`` like ``time.sleep`` does. The tail anchors mean
    ``counter.sleep_if_needed()`` names nothing while a bare imported ``sleep()`` names the timer.
    """
    text = ".".join(chain)
    if not text:
        return None
    if text == "asyncio.sleep" or text.endswith(".asyncio.sleep"):
        return None
    for pattern, label in _PRIMITIVE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _ancestors(node: ast.AST, parents: dict[int, ast.AST]) -> list[ast.AST]:
    chain: list[ast.AST] = []
    current = parents.get(id(node))
    while current is not None:
        chain.append(current)
        current = parents.get(id(current))
    return chain


def _is_offload(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and bool(_chain(node.func)[-1:]) and _chain(node.func)[-1:][0] in OFFLOAD_NAMES


def _offload_scopes(tree: ast.AST) -> tuple[set[int], set[str]]:
    """Node ids lexically inside an offload call, plus the names of the callables handed to one.

    The first set answers ``await asyncio.to_thread(lambda: time.sleep(1))``; the second answers
    the ``_bals()`` shape in ``web/mini.py`` — a nested ``def`` whose body waits and whose *name* is
    what reaches the thread. ``run_in_executor`` is read from its second argument (the first is the
    executor), every other offload from its first.
    """
    nodes: set[int] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not _is_offload(node):
            continue
        for child in ast.walk(node):
            nodes.add(id(child))
        verb = _chain(node.func)[-1:][0]
        callables = node.args[1:] if verb == "run_in_executor" else node.args[:1]
        for arg in callables:
            if isinstance(arg, ast.Name):
                names.add(arg.id)
            for sub in ast.walk(arg):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(sub.name)
    return nodes, names


def _calls_with_functions(tree: ast.AST) -> list[tuple[ast.Call, list[ast.AST]]]:
    parents = _parents(tree)
    found: list[tuple[ast.Call, list[ast.AST]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        funcs = [a for a in _ancestors(node, parents) if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))]
        found.append((node, funcs))
    return found


def _called_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _scan(tree: ast.AST, where: str, source: str) -> tuple[list[str], list[str], int, int]:
    """``(blocking primitives, unoffloaded *_sync calls, offload count, call count)`` for one module."""
    parents = _parents(tree)
    offload_nodes, offload_names = _offload_scopes(tree)
    raw: list[str] = []
    sync: list[str] = []
    offloads = sum(1 for node in ast.walk(tree) if _is_offload(node))
    calls = 0
    for node, funcs in _calls_with_functions(tree):
        calls += 1
        if id(node) in offload_nodes:
            continue
        if any(isinstance(f, ast.FunctionDef) and f.name in offload_names for f in funcs):
            continue
        if not any(isinstance(f, ast.AsyncFunctionDef) for f in funcs):
            continue
        label = blocking_primitive(_chain(node.func))
        if label:
            raw.append(f"{where}:{node.lineno}: {label} — {ast.get_source_segment(source, node)[:70]}")
            continue
        name = _called_name(node.func)
        if name.endswith(SYNC_SUFFIX):
            sync.append(f"{where}:{node.lineno}: {name}() — {ast.get_source_segment(source, node)[:70]}")
    return raw, sync, offloads, calls


def loop_findings() -> tuple[list[str], list[str], dict[str, int]]:
    """Everything the two laws judge, plus the census that proves the walk saw a tree at all."""
    raw: list[str] = []
    sync: list[str] = []
    census = {"files": 0, "coros": 0, "sync_defs": 0, "offloads": 0, "calls": 0}
    for pkg in LOOP_DIRS:
        for path in sorted((REPO / pkg).rglob("*.py")):
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=str(path))
            parents = _parents(tree)
            rel = path.relative_to(REPO).as_posix()
            found, waited, offloads, calls = _scan(tree, rel, source)
            raw += found
            sync += waited
            census["files"] += 1
            census["coros"] += sum(1 for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef))
            census["sync_defs"] += sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name.endswith(SYNC_SUFFIX)
                and not any(isinstance(a, ast.AsyncFunctionDef) for a in _ancestors(node, parents))
            )
            census["offloads"] += offloads
            census["calls"] += calls
    return raw, sync, census


def test_no_blocking_primitive_runs_inside_a_coroutine():
    """``time.sleep``/``urlopen``/``requests``/sync-httpx/``subprocess`` belong to a thread."""
    raw, _waited, census = loop_findings()
    assert census["files"] >= 80, f"{census['files']} loop-bearing files read — the walk is blind"
    assert census["coros"] >= 300, f"{census['coros']} coroutines read — the walk is blind"
    assert census["offloads"] >= 80, f"{census['offloads']} offload sites — the offload reader is blind"
    assert not raw, (
        "a blocking call running on the event loop holds every user of this process for its whole "
        "timeout:\n" + "\n".join(raw)
    )


def test_every_sync_helper_a_coroutine_reaches_is_reached_from_a_thread():
    """The ``*_sync`` suffix is this tree's own mark for "this waits", and a coroutine may only
    reach it through ``asyncio.to_thread``/``run_in_executor``/``submit``."""
    _raw, waited, census = loop_findings()
    assert census["sync_defs"] >= 50, f"{census['sync_defs']} *_sync helpers — the walk is blind"
    assert census["calls"] >= 5_000, f"{census['calls']} calls read — the call walk is blind"
    assert not waited, "a *_sync helper called straight from a coroutine:\n" + "\n".join(waited)


def test_the_thread_reader_reads_the_shapes_a_coroutine_takes(tmp_path):
    """The reader on markup that is not the shipped tree, because both laws above are an
    ``assert not`` and a scan that quietly found nothing would report a clean loop forever."""
    samples = {
        "offloaded_nested_def": (
            "async def h():\n"
            "    def _bals():\n"
            "        time.sleep(1)\n"
            "        return 1\n"
            "    return await asyncio.to_thread(_bals)\n",
            0,
        ),
        "unoffloaded_nested_def": (
            "async def h():\n"
            "    def _bals():\n"
            "        time.sleep(1)\n"
            "    return _bals()\n",
            1,
        ),
        "direct_sleep": ("async def h():\n    time.sleep(30)\n", 1),
        "asyncio_sleep": ("async def h():\n    await asyncio.sleep(30)\n", 0),
        "bare_imported_sleep": ("async def h():\n    sleep(30)\n", 1),
        "lambda_offload": ("async def h():\n    return await asyncio.to_thread(lambda: time.sleep(1))\n", 0),
        "sync_httpx": ("async def h():\n    return httpx.get(url)\n", 1),
        "async_httpx": (
            "async def h():\n    async with httpx.AsyncClient() as c:\n        return await c.get(url)\n",
            0,
        ),
        "urlopen": ("async def h():\n    return urllib.request.urlopen(req).read()\n", 1),
        "sync_helper_direct": ("async def h():\n    return wait_for_tx_sync(h)\n", 1),
        "sync_helper_offloaded": ("async def h():\n    return await asyncio.to_thread(wait_for_tx_sync, h)\n", 0),
        "sync_helper_attribute": ("async def h():\n    return await base.poll_deposits_sync(h)\n", 1),
        "executor_offload": ("async def h():\n    return loop.run_in_executor(None, _work)\n", 0),
        "nested_sync_def_offloaded_by_name": (
            "async def h():\n"
            "    def _work():\n"
            "        subprocess.run(cmd)\n"
            "    return await asyncio.to_thread(_work)\n",
            0,
        ),
        "module_level": ("import time\ntime.sleep(1)\n", 0),
        "sync_function_at_top": ("def _work():\n    time.sleep(1)\n", 0),
        "longer_name": ("async def h():\n    counter.sleep_if_needed()\n", 0),
        "not_a_call": ("async def h():\n    return w3.eth.block_number\n", 0),
    }
    for name, (code, expected) in samples.items():
        path = tmp_path / f"_loop_reader_{name}.py"
        path.write_bytes(code.encode("utf-8"))
        raw, sync, _offloads, calls = _scan(ast.parse(code), path.name, code)
        got = raw + sync
        assert len(got) == expected, f"{name}: expected {expected} finding, got {got}"
        if expected:
            assert calls >= expected

    assert blocking_primitive(("time", "sleep")) == "time.sleep"
    assert blocking_primitive(("asyncio", "sleep")) is None
    assert blocking_primitive(("sleep",)) == "time.sleep"
    assert blocking_primitive(("urllib", "request", "urlopen")) == "urllib.request.urlopen"
    assert blocking_primitive(("httpx", "AsyncClient")) is None
    assert blocking_primitive(("httpx", "Client")) == "httpx (sync client)"
    assert blocking_primitive(("requests", "post")) == "requests"
    assert blocking_primitive(("w3", "eth", "get_balance")) is None
