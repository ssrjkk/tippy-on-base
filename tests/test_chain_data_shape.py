"""A digest that reaches the node, or a ledger row, in a shape the node is not
allowed to read.

The premise this family rests on is a library fact, measured here rather than
remembered: this environment pins `hexbytes==2.0`, whose `.hex()` writes a
digest **without** the `0x`. Two shapes of defect followed, and both were real
in this tree before this wave:

* a topic built by `Web3.keccak(...).hex()` reached `eth_getLogs` as a bare
  64-digit string. JSON-RPC DATA is `0x`-prefixed by definition, so the node
  answers an *error*, not an empty list — the deposit scan failed instead of
  finding nothing, and the x402 reconciliation scan answered `None` about a
  payment that had already settled;
* `get_code(addr).hex()[2:]` in the deploy verifiers chopped two real nibbles
  off the runtime bytecode, so a correct deployment failed verification forever.

The tolerant forms are `Web3.to_hex(x)` (prefixed, lowercase), `to_checksum_address`,
`removeprefix("0x")`, and — for an event topic — the contract's own
`events.<Name>.topic`, which is ABI-derived and prefixed. These laws refuse the
shapes that break and nothing else:

1. each `eth_getLogs` filter this runtime builds is **executed** against a
   recording fake, and every DATA field it sends is checked for the prefix and
   length the node requires;
2. the first topic of each executed filter equals `keccak` of the signature
   **built from the ABI entry the module ships** — a prefixed digest can still
   be the wrong one, and a signature typed into a test is a second copy;
3. no `.hex()` result is cut with a `[2:]` slice: the prefix-strip idiom applied
   to a value that no longer carries a prefix;
4. a digest written under a wire-named field (`topics`, `address`, `tx`, `hash`,
   `data`, `nonce`, `reasoning_hash`, …) states where its `0x` comes from in the
   same expression, so a bare `x.hex()` in that position fails the build;
5. the executed filters are every filter the runtime builds, so a third one
   cannot join the tree unread.

What these laws cannot see, stated rather than parked in an exception list:

* a digest that reaches a wire field as a bare `Name` or a subscript: the reader
  has no value-flow analysis, and `bot/base.py`'s module-level `_transfer_topic`
  is exactly that shape — which is why law 1 executes the filters, and why the
  mutation proof for that topic is executed too;
* a display string: `f"tx {sent.hex()[:20]}…"` is a line for a human, and a
  truncated digest in it costs nothing, so law 4 judges dict entries under wire
  names rather than every `.hex()` in the tree;
* a node response read back with the wrong shape — the node always answers
  prefixed, so an unprefixed comparison against a node value is a different bug;
* an event whose inputs include a tuple or an array type: law 2 refuses to build
  a canonical signature for one and says so, rather than hashing the wrong string;
* a spelling such as `lstrip("0x")` or a hand-written `x[2:] if x.startswith("0x")`,
  which reads as neither tolerant nor broken and would need a human.
"""

import ast
import copy
import subprocess
import sys
from pathlib import Path

import pytest
from hexbytes import HexBytes
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TOP_DIRS = ("bot", "web", "agent", "deploy", "scripts")

# Field names whose value is JSON-RPC DATA, or a hex digest stored in a ledger
# row and later sent back to the node or compared against a node value.
WIRE_FIELD_NAMES = frozenset({
    "topics", "address", "to", "from", "data", "tx", "tx_hash", "transactionHash",
    "hash", "blockHash", "nonce", "reasoning_hash", "signature", "digest",
    "sighash", "contractAddress",
})

# Calls that state the prefix (or remove it) without depending on a library
# version that no longer writes it.
PREFIX_NORMALIZERS = frozenset({
    "to_hex", "to_checksum_address", "removeprefix", "encode_hex", "to_0x_hex",
})

# Floors, measured by this run over the runtime tree: 37 `.hex()` calls, and 4
# digests written straight into a wire-named field. The second floor is small
# because the tree is small there, and a walk that reported three of four is a
# walk that lost one — which is what a floor is for.
MIN_HEX_CALLS = 30
MIN_WIRE_DIGESTS = 3

PAYER = Web3.to_checksum_address("0x" + "ab" * 20)
RECEIVER = Web3.to_checksum_address("0x" + "cd" * 20)
NONCE = b"\x11" * 32


def runtime_python_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", check=True,
    ).stdout.splitlines()
    return sorted(p for p in listed
                  if p.endswith(".py") and p.split("/")[0] in RUNTIME_TOP_DIRS)


def _source_of(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _hex_calls_under(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and not n.args
            and isinstance(n.func, ast.Attribute) and n.func.attr == "hex"]


def _path_to(root: ast.AST, target: ast.AST) -> list[ast.AST]:
    """The nodes from `root` down to `target`, or an empty list."""
    if root is target:
        return [root]
    for child in ast.iter_child_nodes(root):
        sub = _path_to(child, target)
        if sub:
            return [root, *sub]
    return []


def _states_its_prefix(call: ast.Call, root: ast.AST) -> bool:
    """Does the expression this `.hex()` sits inside say where the `0x` comes from?

    Only the chain from the field's own value down to the call is read, so the
    concat that *is* the field counts and a `0x` elsewhere in the file does not.
    """
    for node in _path_to(root, call):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                and any(isinstance(side, ast.Constant) and isinstance(side.value, str)
                        and side.value.startswith("0x")
                        for side in (node.left, node.right))):
            return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in PREFIX_NORMALIZERS):
            return True
    return False


def unprefixable_fields(rel: str, source: str) -> tuple[list[dict], int]:
    """Wire-named dict entries whose digest is a `.hex()` that states no prefix.

    Returns `(offenders, inspected)`: `inspected` counts the digests read, so the
    law's floor measures the walk and not the defect.
    """
    tree = ast.parse(source)
    offenders: list[dict] = []
    inspected = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if not (isinstance(key, ast.Constant) and key.value in WIRE_FIELD_NAMES):
                continue
            for call in _hex_calls_under(value):
                inspected += 1
                if not _states_its_prefix(call, value):
                    offenders.append({"file": rel, "line": call.lineno,
                                      "field": str(key.value)})
    return offenders, inspected


def prefix_slice_sites(rel: str, source: str) -> list[dict]:
    """`.hex()` results cut with a `[2:]` slice — the idiom that removes a prefix.

    A slice with no lower bound (`sent.hex()[:20]`, a display truncation) and a
    slice applied to `Web3.to_hex(...)` (prefixed by definition) both pass.
    """
    offenders: list[dict] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "hex"):
            continue
        sl = node.slice
        if (isinstance(sl, ast.Slice) and isinstance(sl.lower, ast.Constant)
                and sl.lower.value == 2):
            offenders.append({"file": rel, "line": node.lineno})
    return offenders


def get_logs_files() -> list[str]:
    """Every tracked runtime file that builds an `eth_getLogs` filter."""
    return [rel for rel in runtime_python_files() if ".get_logs(" in _source_of(rel)]


def _event_signature(entries: list[dict], name: str) -> str:
    """The canonical event signature, built from the ABI entry the module ships."""
    entry = next((e for e in entries
                  if isinstance(e, dict) and e.get("type") == "event"
                  and e.get("name") == name), None)
    assert entry is not None, f"no {name} event in the ABI this law reads"
    types = [i["type"] for i in entry["inputs"]]
    for one in types:
        assert "tuple" not in one and "[" not in one, (
            f"{name} carries {one}: a tuple or array needs canonical encoding, "
            "and this reader would hash the wrong string")
    return f"{name}({','.join(types)})"


# ---------------------------------------------------------------------------
# the recording fake
# ---------------------------------------------------------------------------

class _RecordingEth:
    def __init__(self, real: Web3):
        self.real = real
        self.filters: list[dict] = []

    def get_logs(self, filter_params):
        self.filters.append(copy.deepcopy(dict(filter_params)))
        return []

    def get_block(self, tag):
        return {"number": 20_000_000}

    def contract(self, **kwargs):
        # Delegated to the real web3, so `.events.X.topic` is the ABI's own
        # answer and not a digest this test typed.
        return self.real.eth.contract(**kwargs)


class _FakeW3:
    def __init__(self, real: Web3):
        self.eth = _RecordingEth(real)


def _is_data(value, nibbles: int) -> bool:
    return (isinstance(value, str) and value.startswith("0x")
            and len(value) == 2 + nibbles * 2
            and all(c in "0123456789abcdefABCDEF" for c in value[2:]))


def _data_violations(filter_params: dict) -> list[str]:
    bad: list[str] = []
    address = filter_params.get("address")
    if address is not None and not _is_data(address, 20):
        bad.append(f"address {address!r} is not 20-byte DATA")
    for field in ("fromBlock", "toBlock"):
        value = filter_params.get(field)
        if value is None or isinstance(value, (bool, int)):
            continue
        if not (isinstance(value, str) and value.startswith("0x")
                and all(c in "0123456789abcdefABCDEF" for c in value[2:])):
            bad.append(f"{field} {value!r} is not a block quantity")
    topics = filter_params.get("topics")
    if not isinstance(topics, list):
        return [*bad, f"topics is {topics!r}, not a list"]
    for index, topic in enumerate(topics):
        if topic is None:
            continue
        if not _data_shape_ok(topic):
            bad.append(f"topics[{index}] {topic!r} is not 32-byte DATA")
    return bad


def _data_shape_ok(topic) -> bool:
    """A topic slot may also be an operator-written list of alternatives."""
    if isinstance(topic, list):
        return bool(topic) and all(_is_data(one, 32) for one in topic)
    return _is_data(topic, 32)


# (module, builder, args) — the filter each site actually sends. Law 5 pins this
# set to the scanned set, so a third filter in the runtime fails the build and
# names the table it must join.
FILTER_BUILDERS = [
    pytest.param("bot.base", "_scan_deposits", (100, 110), id="deposit-scan"),
    pytest.param("web.x402_spec", "find_settlement_by_nonce", (PAYER, NONCE, RECEIVER),
                 id="x402-reconciliation"),
]


def _build_filter(module_name: str, builder: str, args: tuple,
                  monkeypatch: pytest.MonkeyPatch) -> dict:
    import importlib

    from bot import base

    module = importlib.import_module(module_name)
    real = getattr(module, "w3", base.w3)
    fake = _FakeW3(real)
    # `_scan_deposits` reads its own module's `w3`; the reconciliation scan reads
    # `base.w3` through a function-local import. Patch both and pytest restores both.
    monkeypatch.setattr(base, "w3", fake)
    monkeypatch.setattr(module, "w3", fake, raising=False)
    getattr(module, builder)(*args)
    assert fake.eth.filters, f"{module_name}.{builder} sent no filter — nothing was measured"
    return fake.eth.filters[-1]


# ---------------------------------------------------------------------------
# the laws
# ---------------------------------------------------------------------------

def test_the_pinned_hex_library_writes_a_digest_without_the_prefix_the_node_reads():
    digest = Web3.keccak(text="Transfer(address,address,uint256)")
    assert digest.hex()[:2] != "0x", (
        "hexbytes writes the prefix again — the shape this family refuses has "
        "changed; the tolerant forms still hold, so re-read the laws")
    assert Web3.to_hex(digest) == "0x" + bytes(digest).hex()
    assert len(Web3.to_hex(digest)) == 66
    assert HexBytes(digest).hex() == bytes(digest).hex()


@pytest.mark.parametrize("module_name,builder,args", FILTER_BUILDERS)
def test_the_filter_this_runtime_sends_to_the_node_is_valid_json_rpc_data(
        module_name, builder, args, monkeypatch):
    violations = _data_violations(_build_filter(module_name, builder, args, monkeypatch))
    assert not violations, f"{module_name}.{builder}: " + "; ".join(violations)


@pytest.mark.parametrize("module_name,builder,args,event", [
    pytest.param("bot.base", "_scan_deposits", (100, 110), "Transfer", id="deposit-topic"),
    pytest.param("web.x402_spec", "find_settlement_by_nonce", (PAYER, NONCE, RECEIVER),
                 "AuthorizationUsed", id="x402-topic"),
])
def test_the_first_topic_is_the_digest_of_the_event_the_abi_declares(
        module_name, builder, args, event, monkeypatch):
    import importlib

    module = importlib.import_module(module_name)
    if module_name == "bot.base":
        from bot import config
        entries = list(config.ERC20_ABI)
    else:
        entries = list(module.AUTHORIZATION_USED_ABI)
    signature = _event_signature(entries, event)
    sent = _build_filter(module_name, builder, args, monkeypatch)["topics"][0]
    assert sent == Web3.to_hex(Web3.keccak(text=signature)), (module_name, sent, signature)


def test_the_padded_third_topic_is_the_wallet_left_padded_not_shifted(monkeypatch):
    """The deposit filter's third topic is an address in a 32-byte slot: `0x` +
    24 zeros + 40 digits. A pad that chops the wrong end scans somebody else's
    wallet and finds nothing, which reads as an idle chain.
    """
    from bot import base
    sent = _build_filter("bot.base", "_scan_deposits", (100, 110), monkeypatch)["topics"][2]
    assert sent == "0x" + "0" * 24 + base.HOT_WALLET[2:].lower()
    assert len(sent) == 66 and int(sent, 16) == int(base.HOT_WALLET, 16)


def test_a_topic_written_without_its_prefix_is_caught_by_running_the_scan(monkeypatch):
    """The defect this wave found, put back the way it was written. A module
    global in a `topics` list is a blind spot of the static reader, so the proof
    runs the scan."""
    from bot import base
    monkeypatch.setattr(base, "_transfer_topic",
                        Web3.keccak(text="Transfer(address,address,uint256)").hex())
    violations = _data_violations(
        _build_filter("bot.base", "_scan_deposits", (100, 110), monkeypatch))
    assert violations, "the unprefixed topic was accepted by the reader"


def test_a_nonce_topic_written_without_its_prefix_is_caught_by_running_the_scan(
        monkeypatch):
    """The same defect in a function-local expression. The mutated source runs in
    its own namespace, so neither the tree nor the live module ever sees it."""
    sent = _run_mutated_reconciliation_scan(
        '        nonce_topic = "0x" + nonce.hex()\n',
        '        nonce_topic = nonce.hex()\n', monkeypatch)
    assert _data_violations(sent), sent


def test_no_digest_is_cut_with_a_slice_off_a_hex_that_carries_no_prefix():
    offenders = [o for rel in runtime_python_files()
                 for o in prefix_slice_sites(rel, _source_of(rel))]
    total_hex = sum(len(_hex_calls_under(ast.parse(_source_of(rel))))
                    for rel in runtime_python_files())
    assert total_hex >= MIN_HEX_CALLS, (
        f"only {total_hex} `.hex()` calls were read — the walk went blind")
    assert not offenders, "a prefix strip applied to a value with no prefix: " + repr(offenders)


def test_a_digest_written_to_a_wire_named_field_states_where_its_prefix_comes_from():
    offenders: list[dict] = []
    inspected = 0
    for rel in runtime_python_files():
        found, seen = unprefixable_fields(rel, _source_of(rel))
        offenders += found
        inspected += seen
    assert inspected >= MIN_WIRE_DIGESTS, (
        f"only {inspected} wire-field digests were read — the walk went blind")
    assert not offenders, "a digest written unprefixed into a wire field: " + repr(offenders)


def _builder_file(module_name: str) -> str:
    return module_name.replace(".", "/") + ".py"


def test_the_executed_filters_are_every_filter_the_runtime_builds():
    scanned = sorted(get_logs_files())
    executed = sorted({_builder_file(case.values[0]) for case in FILTER_BUILDERS})
    assert len(scanned) >= 2, scanned
    assert scanned == executed, (
        "an eth_getLogs filter exists that is executed by nothing: "
        + repr([f for f in scanned if f not in executed]))


# ---------------------------------------------------------------------------
# the readers, held to the shapes source code actually takes
# ---------------------------------------------------------------------------

def test_the_prefix_reader_reads_two_digraphs_written_on_one_line():
    source = ('def h(nonce):\n'
              '    return {"topics": ["0x" + nonce.hex(), nonce.hex()]}\n')
    offenders, inspected = unprefixable_fields("scratch.py", source)
    assert inspected == 2, inspected
    assert [o["field"] for o in offenders] == ["topics"], offenders


def test_the_prefix_reader_accepts_each_tolerant_form_it_owes():
    source = (
        'def h(d, a, n, r):\n'
        '    return {\n'
        '        "tx": Web3.to_hex(d),\n'
        '        "hash": "0x" + d.hex(),\n'
        '        "address": Web3.to_checksum_address(a.hex()),\n'
        '        "data": n.hex().removeprefix("0x"),\n'
        '        "nonce": ("0x" + r.hex()) if isinstance(r, bytes) else r,\n'
        '    }\n')
    offenders, inspected = unprefixable_fields("scratch.py", source)
    assert inspected == 4, inspected
    assert not offenders, offenders


def test_the_prefix_reader_refuses_a_digest_explained_by_a_far_away_literal():
    """A `0x` that belongs to another expression must not excuse this one."""
    source = ('def h(d):\n'
              '    return {"hash": ("0x" if d else "") + d.hex()}\n')
    offenders, _seen = unprefixable_fields("scratch.py", source)
    assert [o["field"] for o in offenders] == ["hash"], offenders


def test_the_prefix_reader_ignores_a_display_string():
    offenders, inspected = unprefixable_fields(
        "scratch.py", 'def h(sent):\n    print(f"broadcast {sent.hex()[:20]}")\n')
    assert inspected == 0 and offenders == []


def test_the_prefix_reader_ignores_a_field_that_is_not_a_wire_field():
    offenders, inspected = unprefixable_fields(
        "scratch.py", 'def h(sent):\n    return {"note": sent.hex()}\n')
    assert inspected == 0 and offenders == []


def test_the_data_reader_accepts_a_slot_of_alternatives_and_refuses_a_short_one():
    assert _data_violations({"topics": [["0x" + "a1" * 32, "0x" + "b2" * 32]]}) == []
    assert _data_violations({"topics": [[("0x" + "a1" * 32)[:64]]]})
    assert _data_violations({"topics": "0x" + "a1" * 32})


def test_the_slice_reader_reads_both_ends_of_a_slice():
    source = ('def h(sent):\n'
              '    a = sent.hex()[2:]\n'
              '    b = sent.hex()[2:66]\n'
              '    c = sent.hex()[:20]\n'
              '    d = sent.hex().removeprefix("0x")\n'
              '    e = Web3.to_hex(sent)[2:]\n'
              '    return a, b, c, d, e\n')
    found = prefix_slice_sites("scratch.py", source)
    assert [f["line"] for f in found] == [2, 3], found


def test_the_signature_reader_builds_the_string_the_abi_declares():
    abi = [{"type": "event", "name": "Transfer", "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"}]}]
    assert _event_signature(abi, "Transfer") == "Transfer(address,address,uint256)"
    with pytest.raises(AssertionError, match="tuple or array"):
        _event_signature(
            [{"type": "event", "name": "Weird",
              "inputs": [{"type": "tuple", "components": []}]}], "Weird")


def test_the_subject_set_is_the_runtime_tree():
    files = runtime_python_files()
    assert "bot/base.py" in files and "web/x402_spec.py" in files
    assert len(files) > 100, len(files)


# ---------------------------------------------------------------------------
# mutations, proven against copies of the shipped files
# ---------------------------------------------------------------------------

def _mutated_source(rel: str, fixed: str, broken: str) -> str:
    source = _source_of(rel)
    assert fixed in source, (rel, "the shape this mutation undoes is gone from the file")
    return source.replace(fixed, broken, 1)


def test_restoring_a_prefix_strip_on_the_runtime_bytecode_fires_the_law():
    rel = "scripts/deploy_outcome_market.py"
    assert prefix_slice_sites(rel, _source_of(rel)) == []
    found = prefix_slice_sites(rel, _mutated_source(
        rel,
        "    onchain_runtime = Web3.to_hex(w3.eth.get_code(address))[2:].lower()\n",
        "    onchain_runtime = w3.eth.get_code(address).hex()[2:].lower()\n"))
    assert len(found) == 1, found


def test_restoring_an_unprefixed_digest_in_a_wire_field_fires_the_law():
    rel = "web/x402_spec.py"
    before, inspected = unprefixable_fields(rel, _source_of(rel))
    assert not before and inspected > 0, (before, inspected)
    after, _seen = unprefixable_fields(rel, _mutated_source(
        rel,
        '                "nonce": "0x" + nonce.hex(),\n',
        '                "nonce": nonce.hex(),\n'))
    assert [(o["field"], o["line"]) for o in after] == [("nonce", 194)], after


def _run_mutated_reconciliation_scan(fixed: str, broken: str,
                                     monkeypatch: pytest.MonkeyPatch) -> dict:
    """Execute `find_settlement_by_nonce` from a mutated copy of the shipped file."""
    from bot import base
    from web import x402_spec

    rel = "web/x402_spec.py"
    code = compile(_mutated_source(rel, fixed, broken), str(ROOT / rel), "exec")
    namespace = dict(vars(x402_spec))
    exec(code, namespace)  # noqa: S102 - the copy runs in its own namespace only
    fake = _FakeW3(base.w3)
    monkeypatch.setattr(base, "w3", fake)
    namespace["find_settlement_by_nonce"](PAYER, NONCE, RECEIVER)
    assert fake.eth.filters, "the mutated scan sent no filter — nothing was measured"
    return fake.eth.filters[-1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
