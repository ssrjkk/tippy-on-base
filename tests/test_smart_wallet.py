"""Tests for the P2 Smart Wallet (ERC-4337) module.

Covers: config wiring, address prediction (mocked factory), balance query,
and the fallback behavior when smart wallet is not configured.
"""

import types

import pytest
from eth_abi import decode as abi_decode
from web3 import Web3

from bot import config
from bot import smart_wallet as sw


class _Fn:
    def __init__(self, result):
        self._result = result

    def call(self, *a, **k):
        return self._result


def _make_contract(funcs):
    """Wrap a dict of function-name -> result into a fake contract.

    For each {name: result}, functions.name(*args).call() returns result.
    """
    namespace = {}
    for name, result in funcs.items():
        namespace[name] = (lambda res: (lambda *a, **k: _Fn(res)))(result)
    fake_functions = type("FakeFunctions", (), namespace)()
    return type("FakeContract", (), {"functions": fake_functions})()


def _monkeypatch_smart_wallet(monkeypatch, factory_returns=None, usdc_balance=0):
    """Point sw's config + base.w3 at controlled stubs."""
    factory_returns = factory_returns or {}
    monkeypatch.setattr(sw.config, "SMART_WALLET_ENTRYPOINT",
                        "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789")
    monkeypatch.setattr(sw.config, "SMART_WALLET_FACTORY_ADDRESS",
                        "0x" + "11" * 20)
    monkeypatch.setattr(sw.config, "SMART_WALLET_PAYMASTER_ADDRESS",
                        "0x" + "22" * 20)
    monkeypatch.setattr(sw.config, "USDC_ADDRESS", "0x" + "33" * 20)
    monkeypatch.setattr(sw.config, "HOT_WALLET_KEY", "0x" + "44" * 32)
    monkeypatch.setattr(sw.config, "SMART_WALLET_RELAYER_KEY", None)

    factory = _make_contract({
        "getAddress": factory_returns.get("address", "0x" + "11" * 20),
        "isDeployed": factory_returns.get("deployed", True),
    })
    usdc = _make_contract({
        "balanceOf": usdc_balance,
    })
    ep = _make_contract({
        "getUserOpHash": b"\x00" * 32,
        "getNonce": 0,
        "handleOps": None,
    })

    def _contract(address=None, abi=None):
        if abi is sw._ENTRYPOINT_ABI:
            return ep
        if abi is sw._SMART_ACCOUNT_FACTORY_ABI:
            return factory
        if abi is sw._ERC20_ABI:
            return usdc
        return factory

    fake_eth = types.SimpleNamespace(
        account=types.SimpleNamespace(
            from_key=lambda k: types.SimpleNamespace(address="0x" + "55" * 20)
        ),
        contract=_contract,
        get_transaction_count=lambda a, p="latest": 1,
        get_block=lambda x: {"baseFeePerGas": 1_000_000_000},
        gas_price=1_000_000_000,
        chain_id=8453,
        get_transaction_receipt=lambda h: {"status": 1},
        send_raw_transaction=lambda raw: b"\x66" * 32,
        wait_for_transaction_receipt=lambda h, timeout=60: {"status": 1},
        to_wei=lambda n, unit: n * 10**9,
    )
    fake_w3 = types.SimpleNamespace(
        eth=fake_eth,
        to_checksum_address=lambda a: a if a.startswith("0x") else "0x" + a,
        to_wei=lambda n, unit: n * 10**9,
        keccak=Web3.keccak,
        codec=Web3().codec,
    )
    monkeypatch.setattr(sw, "_get_w3", lambda: fake_w3)
    return fake_w3


def test_smart_wallet_config_defaults():
    import bot.config as cfg
    assert cfg.SMART_WALLET_ENTRYPOINT.startswith("0x")
    assert cfg.SMART_WALLET_ENABLED is False


def test_predict_address_returns_checksummed(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, {"address": "0x" + "aa" * 20})
    addr = sw.predict_address(12345)
    assert addr.lower() == "0x" + "aa" * 20


def test_is_deployed_true(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, {"deployed": True})
    assert sw.is_deployed(12345) is True


def test_is_deployed_false(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, {"deployed": False})
    assert sw.is_deployed(12345) is False


def test_smart_balance_returns_micro(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch, usdc_balance=5_000_000)
    assert sw.smart_balance(12345) == 5_000_000


# ---------------------------------------------------------------------------
# Pure encoding / signing (no network)
# ---------------------------------------------------------------------------

def test_execute_selector_is_correct():
    # keccak("execute(address,uint256,bytes)")[:4] == 0xb61d27f6
    assert sw._ENCODE_EXECUTE_SELECTOR == "b61d27f6"


def test_encode_execute_computes_function_selector():
    dest = Web3.to_checksum_address("0x" + "ab" * 20)
    data = Web3.keccak(text="buy()")[:4]
    encoded = sw._encode_execute(dest, 0, data)
    assert encoded[:4].hex() == "b61d27f6", "execute selector mismatch"
    decoded = abi_decode(["address", "uint256", "bytes"], encoded[4:])
    assert decoded[0].lower() == dest.lower()
    assert decoded[1] == 0
    assert decoded[2] == data


def test_encode_execute_batch_selector_is_correct():
    assert sw._ENCODE_EXECUTE_BATCH_SELECTOR == "7c7652c8"


def test_encode_execute_batch_decodes_destinations():
    usdc = Web3.to_checksum_address("0x" + "cd" * 20)
    market = Web3.to_checksum_address("0x" + "ef" * 20)
    approve = Web3.keccak(text="approve()")[:4]
    trade = Web3.keccak(text="buy()")[:4]
    encoded = sw._encode_execute_batch(usdc, approve, market, trade)
    assert encoded[:4].hex() == "7c7652c8", "executeBatch selector mismatch"
    args = abi_decode(
        ["address", "bytes", "address", "bytes"], encoded[4:]
    )
    assert args[0].lower() == usdc.lower()
    assert args[1] == approve
    assert args[2].lower() == market.lower()
    assert args[3] == trade


def test_pack_user_op_roundtrip():
    op = {
        "sender": "0x" + "11" * 20,
        "nonce": 7,
        "initCode": b"",
        "callData": b"\xab" * 4,
        "callGasLimit": 100,
        "verificationGasLimit": 200,
        "preVerificationGas": 300,
        "maxFeePerGas": 4,
        "maxPriorityFeePerGas": 5,
        "paymasterAndData": b"\x00" * 20,
        "signature": b"\x99" * 65,
    }
    packed = sw._pack_user_op(op)
    assert packed == (
        op["sender"], op["nonce"], op["initCode"], op["callData"],
        op["callGasLimit"], op["verificationGasLimit"],
        op["preVerificationGas"], op["maxFeePerGas"],
        op["maxPriorityFeePerGas"], op["paymasterAndData"],
        op["signature"],
    )


def test_build_paymaster_data_layout():
    pm = "0x" + "22" * 20
    tg_id = 123456789
    sig = b"\x01" * 65
    sw.config.SMART_WALLET_PAYMASTER_ADDRESS = pm
    data = sw._build_paymaster_data(tg_id, sig)
    # 20 (paymaster) + 32 (tg_id) + 65 (sig) = 117 bytes
    assert len(data) == 20 + 32 + 65
    assert data[:20] == bytes.fromhex(pm[2:])
    assert int.from_bytes(data[20:52], "big") == tg_id
    assert data[52:] == sig


def test_eth_account_signing_primitive():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    key = "0x" + "ab" * 32
    acct = Account.from_key(key)
    msg = Web3.keccak(text="chainid bullshark") + b"!" * 4
    # Same signing/verification the UserOp + paymaster flow relies on:
    # Account.sign_message(encode_defunct(hash)) uses the EIP-191 personal-message
    # prefix, matching SmartAccount.validateUserOp's ecrecover over the ring
    # keccak256("\x19Ethereum Signed Message:\n32" || hash).
    signed = Account.sign_message(encode_defunct(primitive=msg), key)
    recovered = Account.recover_message(encode_defunct(primitive=msg), signature=signed.signature)
    assert recovered.lower() == acct.address.lower()


def test_sign_paymaster_is_eip191(monkeypatch):
    # _sign_paymaster must produce a recoverable signature without any network.
    from eth_account import Account
    key = "0x" + "cd" * 32
    acct = Account.from_key(key)
    _monkeypatch_smart_wallet(monkeypatch)  # chain_id 8453, paymaster 0x22*20
    user_op = {
        "sender": "0x" + "11" * 20,
        "nonce": 0,
        "initCode": b"",
        "callData": b"\xab" * 4,
        "callGasLimit": 200_000,
        "verificationGasLimit": 150_000,
        "preVerificationGas": 50_000,
        "maxFeePerGas": 10_000_000,
        "maxPriorityFeePerGas": 1_000_000,
    }
    sig = sw._sign_paymaster(user_op, key)
    assert len(sig) == 65
    # Recover using the rawHash that _sign_paymaster computes internally:
    # the 9 op fields PLUS chainid and the paymaster address (replay binding).
    from eth_abi import encode as abi_encode
    from eth_account.messages import encode_defunct
    raw_hash = Web3.keccak(abi_encode(
        ["address", "uint256", "bytes", "bytes", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "address"],
        [
            user_op["sender"], user_op["nonce"], user_op["initCode"], user_op["callData"],
            user_op["callGasLimit"], user_op["verificationGasLimit"], user_op["preVerificationGas"],
            user_op["maxFeePerGas"], user_op["maxPriorityFeePerGas"],
            8453, Web3.to_checksum_address(config.SMART_WALLET_PAYMASTER_ADDRESS),
        ],
    ))
    recovered = Account.recover_message(
        encode_defunct(primitive=raw_hash), signature=sig
    )
    assert recovered.lower() == acct.address.lower()


def test_sign_user_op_uses_mocked_entrypoint_hash(monkeypatch):
    # The entrypoint stub returns b"\x00"*32 for getUserOpHash; _sign_user_op
    # must sign that hash and return a 65-byte EIP-191 signature.
    from eth_account import Account
    from eth_account.messages import encode_defunct
    _monkeypatch_smart_wallet(monkeypatch)
    key = "0x" + "ef" * 32
    acct = Account.from_key(key)
    op = {
        "sender": "0x" + "11" * 20,
        "nonce": 0,
        "initCode": b"",
        "callData": b"\xab" * 4,
        "callGasLimit": 1,
        "verificationGasLimit": 2,
        "preVerificationGas": 3,
        "maxFeePerGas": 4,
        "maxPriorityFeePerGas": 5,
        "paymasterAndData": b"",
        "signature": b"",
    }
    sig = sw._sign_user_op(op, key)
    assert len(sig) == 65
    # _sign_user_op now asks the EntryPoint itself for the hash (RPC call),
    # never recomputes it locally — so the signed digest is exactly whatever
    # getUserOpHash returns. The stub returns b"\x00"*32.
    recovered = Account.recover_message(
        encode_defunct(primitive=b"\x00" * 32), signature=sig
    )
    assert recovered.lower() == acct.address.lower()


# ---------------------------------------------------------------------------
# On-chain paymaster hash domain (solcx + eth-tester)
# ---------------------------------------------------------------------------

_ENTRYPOINT_SINGLETON = "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789"


def _compile_test_variant(file_name: str, contract_name: str, fake_ep: str):
    """Compile contracts/<file_name> with the EntryPoint singleton constant
    replaced by a controllable eth-tester account.

    eth-tester cannot spoof msg.sender, so the test build redirects the
    `require(msg.sender == entryPoint())` gate; the signature domain
    (chainid + contract address, or the EntryPoint-provided userOpHash)
    is untouched. Each source must contain the singleton exactly once.
    """
    import os

    import solcx

    path = os.path.join(os.path.dirname(__file__), "..", "contracts", file_name)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert src.count(_ENTRYPOINT_SINGLETON) == 1
    solcx.set_solc_version("0.8.24")
    # optimize=True: the 11-field abi.encode in relayerMessageHash overflows
    # the EVM stack without the optimizer's stack-height reduction.
    compiled = solcx.compile_source(
        src.replace(_ENTRYPOINT_SINGLETON, fake_ep),
        output_values=["abi", "bin"], optimize=True,
    )
    return next(
        a for k, a in compiled.items()
        if k.endswith(":" + contract_name) and a["bin"]
    )


def _tester_chain():
    import solcx
    from web3 import Web3
    from web3.providers.eth_tester import EthereumTesterProvider

    solcx.set_solc_version("0.8.24")
    w3 = Web3(EthereumTesterProvider())
    fake_ep = Web3.to_checksum_address(w3.eth.accounts[5])
    return w3, fake_ep


def _deploy_paymaster_with_test_entrypoint():
    """Deploy a VerifyingPaymaster whose entryPoint() returns a tester account.

    Returns (w3, pm_contract, relayer_account, entry_point_address).
    """
    import os

    import solcx
    from eth_account import Account

    solcx.set_solc_version("0.8.24")
    # Also build-check the real sources (incl. SmartAccount's validateUserOp
    # change) before compiling the test variants.
    base = os.path.join(os.path.dirname(__file__), "..", "contracts")
    solcx.compile_files(
        [os.path.join(base, "VerifyingPaymaster.sol"), os.path.join(base, "SmartAccount.sol")],
        output_values=["abi"], optimize=True,
    )

    w3, fake_ep = _tester_chain()
    art = _compile_test_variant("VerifyingPaymaster.sol", "VerifyingPaymaster", fake_ep)
    relayer = Account.create()
    pm = w3.eth.contract(abi=art["abi"], bytecode=art["bin"])
    receipt = w3.eth.wait_for_transaction_receipt(
        pm.constructor(relayer.address, "0x" + "33" * 20, 1_000).transact({"from": w3.eth.accounts[0]})
    )
    return w3, w3.eth.contract(address=receipt.contractAddress, abi=art["abi"]), relayer, fake_ep


def _deploy_smart_account_with_test_entrypoint():
    """Deploy and initialize a SmartAccount with a tester-account EntryPoint.

    Returns (w3, sa_contract, owner_account, entry_point_address).
    """
    from eth_account import Account

    w3, fake_ep = _tester_chain()
    art = _compile_test_variant("SmartAccount.sol", "SmartAccount", fake_ep)
    owner = Account.create()
    sa = w3.eth.contract(abi=art["abi"], bytecode=art["bin"])
    receipt = w3.eth.wait_for_transaction_receipt(
        sa.constructor().transact({"from": w3.eth.accounts[0]})
    )
    sa = w3.eth.contract(address=receipt.contractAddress, abi=art["abi"])
    w3.eth.wait_for_transaction_receipt(
        sa.functions.initialize(owner.address).transact({"from": w3.eth.accounts[0]})
    )
    return w3, sa, owner, fake_ep


_TEST_OP = {
    "sender": "0x" + "11" * 20,
    "nonce": 7,
    "initCode": b"",
    "callData": b"\xab" * 4,
    "callGasLimit": 200_000,
    "verificationGasLimit": 150_000,
    "preVerificationGas": 50_000,
    "maxFeePerGas": 10_000_000,
    "maxPriorityFeePerGas": 1_000_000,
}


def _py_relayer_raw_hash(op, chain_id, paymaster_addr):
    """The rawHash exactly as bot _sign_paymaster computes it."""
    from eth_abi import encode as abi_encode
    return Web3.keccak(abi_encode(
        ["address", "uint256", "bytes", "bytes", "uint256", "uint256", "uint256", "uint256", "uint256", "uint256", "address"],
        [op["sender"], op["nonce"], op["initCode"], op["callData"],
         op["callGasLimit"], op["verificationGasLimit"], op["preVerificationGas"],
         op["maxFeePerGas"], op["maxPriorityFeePerGas"], chain_id, paymaster_addr],
    ))


def _op_struct(op, pmd, sig=b""):
    return (op["sender"], op["nonce"], op["initCode"], op["callData"],
            op["callGasLimit"], op["verificationGasLimit"], op["preVerificationGas"],
            op["maxFeePerGas"], op["maxPriorityFeePerGas"], pmd, sig)


def _pmd_for(pm_address, tg_id, sig):
    return bytes.fromhex(Web3.to_checksum_address(pm_address)[2:]) + tg_id.to_bytes(32, "big") + sig


def _validate(pm, op_full, hash_for_call, from_addr):
    return pm.functions.validatePaymasterUserOp(op_full, hash_for_call, 10**24).call({"from": from_addr})


def test_paymaster_relayer_hash_matches_python():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    w3, pm, relayer, fake_ep = _deploy_paymaster_with_test_entrypoint()
    raw = _py_relayer_raw_hash(_TEST_OP, w3.eth.chain_id, pm.address)

    # The contract view returns the EIP-191 digest over the same rawHash the
    # bot signs with Account.sign_message(encode_defunct(primitive=raw)).
    on_chain = pm.functions.relayerMessageHash(_op_struct(_TEST_OP, b"")).call()
    assert on_chain == Web3.keccak(b"\x19Ethereum Signed Message:\n32" + raw)

    # The hash excludes paymasterAndData, so it is stable across the bot's
    # sign-then-attach sequence.
    sig = Account.sign_message(encode_defunct(primitive=raw), relayer.key).signature
    pmd = _pmd_for(pm.address, 4242, sig)
    assert pm.functions.relayerMessageHash(_op_struct(_TEST_OP, pmd)).call() == on_chain

    # validatePaymasterUserOp accepts the off-chain bot signature as EntryPoint.
    _validate(pm, _op_struct(_TEST_OP, pmd), raw, fake_ep)


def test_paymaster_rejects_cross_chain_replay():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    w3, pm, relayer, fake_ep = _deploy_paymaster_with_test_entrypoint()

    # Same relayer, same op — but the signature was minted for chain 1 (a
    # relayer key leaked from another chain cannot sponsor ops here).
    raw_other_chain = _py_relayer_raw_hash(_TEST_OP, 1, pm.address)
    sig = Account.sign_message(encode_defunct(primitive=raw_other_chain), relayer.key).signature
    pmd = _pmd_for(pm.address, 4242, sig)
    with pytest.raises(Exception, match="bad relayer sig"):
        _validate(pm, _op_struct(_TEST_OP, pmd), raw_other_chain, fake_ep)


def test_paymaster_rejects_foreign_signer():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    w3, pm, relayer, fake_ep = _deploy_paymaster_with_test_entrypoint()

    attacker = Account.create()
    raw = _py_relayer_raw_hash(_TEST_OP, w3.eth.chain_id, pm.address)
    sig = Account.sign_message(encode_defunct(primitive=raw), attacker.key).signature
    pmd = _pmd_for(pm.address, 4242, sig)
    with pytest.raises(Exception, match="bad relayer sig"):
        _validate(pm, _op_struct(_TEST_OP, pmd), raw, fake_ep)


def test_smart_account_owner_sig_over_entrypoint_hash():
    # validateUserOp verifies EIP-191 over the hash the EntryPoint passes it:
    # 0 (valid) for the owner's signature, 1 (sig validation failed) otherwise.
    from eth_account import Account
    from eth_account.messages import encode_defunct
    w3, sa, owner, fake_ep = _deploy_smart_account_with_test_entrypoint()
    h = Web3.keccak(text="ep-computed userOpHash")
    sig = Account.sign_message(encode_defunct(primitive=h), owner.key).signature
    assert sa.functions.validateUserOp(
        _op_struct(_TEST_OP, b"", sig), h, 0
    ).call({"from": fake_ep}) == 0
    attacker = Account.create()
    bad_sig = Account.sign_message(encode_defunct(primitive=h), attacker.key).signature
    assert sa.functions.validateUserOp(
        _op_struct(_TEST_OP, b"", bad_sig), h, 0
    ).call({"from": fake_ep}) == 1


def test_smart_account_rejects_non_entrypoint_caller():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    w3, sa, owner, fake_ep = _deploy_smart_account_with_test_entrypoint()
    h = Web3.keccak(text="ep-computed userOpHash")
    sig = Account.sign_message(encode_defunct(primitive=h), owner.key).signature
    with pytest.raises(Exception, match="not EntryPoint"):
        sa.functions.validateUserOp(
            _op_struct(_TEST_OP, b"", sig), h, 0
        ).call({"from": w3.eth.accounts[1]})


# ---------------------------------------------------------------------------
# OutcomeMarket buy (gasless approve + buy UserOp)
# ---------------------------------------------------------------------------

def _pytest_market_contract(w3, market_address):
    """A contract stub whose buy() build_transaction returns real ABI bytes
    (built offline via eth_abi — no provider/network needed)."""
    from eth_abi import encode as abi_encode

    selector = Web3.keccak(text="buy(uint256,uint256,uint256,uint256)")[:4]
    body = abi_encode(
        ["uint256", "uint256", "uint256", "uint256"],
        [100, 1, 2, 3],
    )

    class _Fn:
        def build_transaction(self, *a, **k):
            return {"data": selector + body}
        def call(self, *a, **k):
            return 0

    class _Market:
        functions = type("F", (), {"buy": (lambda *a, **k: _Fn())})()

    return _Market()


def test_smart_buy_sync_passes_approve_and_buy_data(monkeypatch):
    _monkeypatch_smart_wallet(monkeypatch)
    monkeypatch.setattr(sw.config, "OUTCOME_MARKET_ADDRESS", "0x" + "99" * 20)
    monkeypatch.setattr(sw, "_market_contract", _pytest_market_contract)

    captured = {}
    def fake_att(tg_id, market_address, approve_amount, trade_data):
        captured.update(
            tg_id=tg_id, market_address=market_address,
            approve_amount=approve_amount, trade_data=trade_data,
        )
        return "0x" + "aa" * 32

    monkeypatch.setattr(sw, "approve_and_trade_sync", fake_att)

    tx = sw.smart_buy_sync(12345, 42, 1, 1000, 5_000_000)
    assert tx == "0x" + "aa" * 32
    assert captured["tg_id"] == 12345
    assert captured["market_address"].lower() == "0x" + "99" * 20
    # Approve amount == the slippage cap (not the shares count or cost).
    assert captured["approve_amount"] == 5_000_000
    # trade_data is the buy(uint256,uint256,uint256,uint256) selector.
    assert captured["trade_data"][:4] == Web3.keccak(
        text="buy(uint256,uint256,uint256,uint256)"
    )[:4]


def test_smart_nonce_uses_entrypoint_not_storage(monkeypatch):
    """smart_nonce() must read EntryPoint.getNonce(sender,0), never the
    SmartAccount's own storage nonce (never incremented -> would break the
    EntryPoint sequential-nonce check on a real chain)."""
    _monkeypatch_smart_wallet(monkeypatch)  # config must not depend on .env
    # Any contract lookup that is NOT the factory returns a getNonce result;
    # this proves smart_nonce reads EntryPoint.getNonce (not account storage).
    def fake_contract(address=None, abi=None):
        if abi is sw._SMART_ACCOUNT_FACTORY_ABI:
            return _make_contract({"getAddress": "0x" + "11" * 20})
        return _make_contract({"getNonce": 1234})

    fake_w3 = types.SimpleNamespace(
        eth=types.SimpleNamespace(
            contract=fake_contract,
            to_checksum_address=lambda a: a,
        ),
    )
    monkeypatch.setattr(sw, "_get_w3", lambda: fake_w3)
    assert sw.smart_nonce(12345) == 1234
