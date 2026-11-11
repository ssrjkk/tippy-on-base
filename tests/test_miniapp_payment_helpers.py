import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "static" / "app.html"


def helper_source() -> str:
    text = APP.read_text(encoding="utf-8")
    start = text.index("function errMsg(")
    end = text.index("// ─── Tip ───")
    return text[start:end]


def run_node(body: str) -> None:
    result = subprocess.run(
        ["node", "-"], input=body, text=True, encoding="utf-8",
        cwd=ROOT, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def script(test_body: str) -> str:
    helpers = json.dumps(helper_source())
    return f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const ctx = {{ crypto, t: key => key, errMsg: data => data?.detail || 'error', Response }};
vm.createContext(ctx); vm.runInContext({helpers}, ctx);
(async () => {{ {test_body} }})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""


def test_miniapp_payment_helpers_preserve_key_and_payload():
    run_node(script("""
const calls = [];
ctx.fetch = async (path, options) => {
  calls.push(options.headers['Idempotency-Key']);
  if (calls.length === 1) throw new Error('reply lost');
  if (calls.length === 2) return Response.json({ detail: 'throttled' }, { status: 429 });
  return Response.json({ ok: true });
};
const payload = { to: 'alice', amount: 1 };
await assert.rejects(() => ctx.moneyPost('/api/mini/tip', payload, 'tip'));
await assert.rejects(() => ctx.moneyPost('/api/mini/tip', { to: 'bob', amount: 2 }, 'tip'),
  { code: 'IDEMPOTENCY_PAYLOAD_CHANGED' });
await assert.rejects(() => ctx.moneyPost('/api/mini/tip', payload, 'tip'));
await ctx.moneyPost('/api/mini/tip', payload, 'tip');
assert.equal(calls.length, 3);
assert.equal(calls[1], calls[0]);
assert.equal(calls[2], calls[0]);
"""))


def test_miniapp_payment_helpers_deduplicate_inflight_request():
    run_node(script("""
let finish, calls = 0;
ctx.fetch = () => { calls += 1; return new Promise(resolve => { finish = resolve; }); };
const payload = { to: 'alice', amount: 1 };
const first = ctx.moneyPost('/api/mini/tip', payload, 'tip');
const second = ctx.moneyPost('/api/mini/tip', payload, 'tip');
assert.equal(calls, 1);
assert.equal(first, second);
finish(Response.json({ ok: true }));
await Promise.all([first, second]);
"""))


def test_miniapp_money_routes_all_use_the_shared_helper():
    text = APP.read_text(encoding="utf-8")
    block = text[text.index("function errMsg("):text.index("// ─── Language ───")]
    routes = re.findall(r"moneyPost\('(/api/mini/[^']+)'", block)
    assert routes == ["/api/mini/tip", "/api/mini/create", "/api/mini/create"]
    assert "moneyPost(isMarket ? '/api/mini/trade' : '/api/mini/betplace', body, action)" in block
    assert "moneyHeaders(" not in block
