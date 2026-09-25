"""Deploy the CREATE2 factory + USDC forwarder on Base (mainnet or Sepolia).

Usage:
    python scripts/deploy_create2_factory.py --dry-run
    python scripts/deploy_create2_factory.py
    python scripts/deploy_create2_factory.py --wire-env

Environment:
    BASE_RPC_URL      RPC (defaults to sepolia.base.org)
    EXPECTED_CHAIN_ID chain guard (default 84532 for Sepolia)
    HOT_WALLET_KEY    deployer key (the hot wallet becomes forwarder target)
    USDC_ADDRESS      network USDC (defaults to Base Sepolia)

Steps:
  1. Compile USDCForwarder + Create2Factory (uses host solcx when available,
     otherwise expects prebuilt artifacts/ dir — see --artifacts).
  2. Deploy USDCForwarder(hotWallet, usdc).
  3. Deploy Create2Factory(forwarder).
  4. Emit the EIP-1167 proxy init code (create2.py MINIMAL_PROXY_BYTECODE) so
     the bot's offline address derivation matches on-chain proxies exactly.
  5. Optional --wire-env writes CREATE2_FACTORY_ADDRESS,
     CREATE2_FACTORY_FORWARDER, CREATE2_PROXY_BYTECODE, CREATE2_SAFE_DEPOSITS=1
     into .env.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# Base Sepolia default (test-first workflow as documented in docs/DEPLOY.md)
RPC_DEFAULT = "https://sepolia.base.org"
CHAIN_DEFAULT = 84532
USDC_DEFAULT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia USDC

EIP1167_PREFIX = "3d602d80600a3d3981f3363d3d373d3d3d363d73"
EIP1167_SUFFIX = "5af43d82803e903d91602b57fd5bf3"

ARTIFACTS_DIR = ROOT / "artifacts"


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for ln in ENV_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                env[k.strip()] = v.strip()
    return env


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or load_env().get(key, "")


def compile_forwarder():
    src = (ROOT / "contracts" / "USDCForwarder.sol").read_text(encoding="utf-8")
    return _compile("USDCForwarder.sol", src, "USDCForwarder")


def compile_factory():
    src = (ROOT / "contracts" / "Create2Factory.sol").read_text(encoding="utf-8")
    return _compile("Create2Factory.sol", src, "Create2Factory")


def _compile(fname: str, src: str, contract_name: str):
    solc_binary = None
    try:
        import solcx
    except ImportError:
        solcx = None

    if solcx is not None:
        # install_solc downloads the pinned compiler to solcx's per-user cache
        # if it is missing, so no machine-specific path is needed; only the env
        # var SOLC_BINARY_PATH pinpoints an exotic custom build.
        try:
            solcx.install_solc("0.8.24")
            solcx.set_solc_version("0.8.24")
        except Exception:
            raise SystemExit("solcx: no usable solc; run with solc on PATH or set SOLC_BINARY_PATH")

    if args_solc_binary():
        solc_binary = args_solc_binary()

    out = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {fname: {"content": src}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"]}},
            },
        },
        solc_binary=solc_binary,
    )
    art = out["contracts"][fname][contract_name]
    return {
        "abi": art["abi"],
        "bin": art["evm"]["bytecode"]["object"],
        "runtime": art["evm"]["deployedBytecode"]["object"],
    }


def args_solc_binary():
    # Env-var override only: no hardcoded machine-specific path. solcx picks
    # the pinned 0.8.24 from its own cache when this is None.
    return os.environ.get("SOLC_BINARY_PATH") or os.environ.get("SOLC_BINARY") or None


def save_artifact(name: str, art: dict) -> None:
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    (ARTIFACTS_DIR / f"{name}.json").write_text(json.dumps(art, indent=2), encoding="utf-8")
    print(f"[artifacts] wrote {ARTIFACTS_DIR / (name + '.json')}")


def load_artifact(name: str) -> dict:
    p = ARTIFACTS_DIR / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"Missing artifact {p}. Run with --compile to build it first.")
    art = json.loads(p.read_text(encoding="utf-8"))
    if "evm" in art:  # raw solc output -> normalized {abi, bin, runtime}
        return {
            "abi": art["abi"],
            "bin": art["evm"]["bytecode"]["object"],
            "runtime": art["evm"]["deployedBytecode"]["object"],
        }
    return art


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--chain", type=int, default=None)
    parser.add_argument("--usdc", default=None)
    parser.add_argument("--dry-run", action="store_true", help="compile+sign only, do not broadcast")
    parser.add_argument("--wire-env", action="store_true", help="write CREATE2_* keys into .env")
    parser.add_argument("--compile", action="store_true",
                        help="recompile contracts and rewrite artifacts/ (host solcx needed)")
    args = parser.parse_args()

    # ---- compile or load artifacts -------------------------------------
    if args.compile:
        fwd_art = compile_forwarder()
        fac_art = compile_factory()
        save_artifact("USDCForwarder", fwd_art)
        save_artifact("Create2Factory", fac_art)
    fwd_art = load_artifact("USDCForwarder")
    fac_art = load_artifact("Create2Factory")

    # ---- read config -----------------------------------------------------
    rpc = args.rpc or cfg("BASE_RPC_URL") or RPC_DEFAULT
    key = cfg("HOT_WALLET_KEY") or os.environ.get("DEPLOYER_KEY")
    if not key:
        print("ERROR: HOT_WALLET_KEY not set", file=sys.stderr)
        return 1
    usdc = (args.usdc or cfg("USDC_ADDRESS") or USDC_DEFAULT).strip()
    expected_chain = args.chain or int(cfg("EXPECTED_CHAIN_ID", str(CHAIN_DEFAULT)) or CHAIN_DEFAULT)

    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print("ERROR: RPC unreachable:", rpc)
        return 1
    chain_id = w3.eth.chain_id
    if chain_id != expected_chain:
        print(f"ERROR: RPC on chain {chain_id}, expected {expected_chain}. Refusing to deploy.")
        return 1

    acct = w3.eth.account.from_key(key)
    deployer = Web3.to_checksum_address(acct.address)
    usdc = Web3.to_checksum_address(usdc)

    print(f"[deploy] chain={chain_id} deployer={deployer} usdc={usdc}")
    if w3.eth.get_code(usdc) == b"":
        print(f"ERROR: no code at USDC {usdc} on chain {chain_id} (wrong network?)")
        return 1

    bal = w3.eth.get_balance(deployer)
    print(f"[deploy] balance={bal / 1e18:.6f} ETH nonce={w3.eth.get_transaction_count(deployer)}")


    def _send_contract(abi, bin, *ctor_args, label: str):
        c = w3.eth.contract(abi=abi, bytecode=bin)
        ctor = c.constructor(*ctor_args)
        gas_est = ctor.estimate_gas({"from": deployer})
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        priority = max(w3.eth.gas_price - base_fee, 10**6)
        max_fee = base_fee * 2 + priority
        tx = ctor.build_transaction({
            "chainId": chain_id,
            "nonce": w3.eth.get_transaction_count(deployer),
            "gas": int(gas_est * 1.2),
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
        })
        signed = acct.sign_transaction(tx)
        cost = tx["gas"] * max_fee
        print(f"[deploy] {label}: gas={tx['gas']:,} max_fee={max_fee / 1e9:.4f} gwei cost~{cost / 1e18:.8f} ETH")
        if args.dry_run:
            print(f"[dry-run] would broadcast {label} (signed, not sent)")
            return None
        if bal < cost:
            print(f"INSUFFICIENT GAS: need ~{cost / 1e18:.8f} ETH on {deployer}")
            sys.exit(2)
        sent = w3.eth.send_raw_transaction(signed.raw_transaction)
        rc = w3.eth.wait_for_transaction_receipt(sent, timeout=300, poll_latency=2)
        if rc.status != 1:
            raise SystemExit(f"TX REVERTED: {sent.hex()}")
        print(f"[deploy] {label} broadcast {sent.hex()[:20]}… block={rc.blockNumber} gas={rc.gasUsed}")
        return rc.contractAddress

    fwd_addr = _send_contract(fwd_art["abi"], fwd_art["bin"], deployer, usdc, label="USDCForwarder")
    if args.dry_run:
        print("DRY-RUN OK")
        return 0
    assert fwd_addr, "forwarder deploy failed"

    fac_addr = _send_contract(fac_art["abi"], fac_art["bin"], fwd_addr, label="Create2Factory")
    assert fac_addr, "factory deploy failed"

    # ---- runtime code check ---------------------------------------------
    onchain = w3.eth.get_code(fac_addr).hex()[2:].lower()
    if onchain != fac_art["runtime"].lstrip("0x").lower():
        print("ERROR: Create2Factory runtime bytecode MISMATCH vs local compile!")
        return 1

    fac = w3.eth.contract(address=fac_addr, abi=fac_art["abi"])
    checks = {
        "forwarder()==deployed": fac.functions.forwarder().call().lower() == fwd_addr.lower(),
        "proxyInitCode().len==55": len(fac.functions.proxyInitCode().call()) == 55,
    }
    for name, ok in checks.items():
        print(f"[check] {name}: {'OK' if ok else 'FAIL'}")
        if not ok:
            return 1

    proxy_bytecode = "0x" + EIP1167_PREFIX + fwd_addr[2:].lower() + EIP1167_SUFFIX
    print()
    print(f"USDCForwarder : {fwd_addr}")
    print(f"Create2Factory: {fac_addr}")
    print(f"proxy init    : {proxy_bytecode}")

    if args.wire_env:
        _wire_env(fac_addr, fwd_addr, proxy_bytecode)

    return 0


def _wire_env(factory: str, forwarder: str, proxy_bytecode: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    writes = {
        "CREATE2_FACTORY_ADDRESS": factory,
        "CREATE2_FACTORY_FORWARDER": forwarder,
        "CREATE2_PROXY_BYTECODE": proxy_bytecode,
        "CREATE2_SAFE_DEPOSITS": "1",
    }
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip()
        if key in writes:
            out.append(f"{key}={writes.pop(key)}")
        else:
            out.append(ln)
    for k, v in writes.items():
        out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[wire] wrote CREATE2_* keys into {ENV_FILE}")


if __name__ == "__main__":
    sys.exit(main())
