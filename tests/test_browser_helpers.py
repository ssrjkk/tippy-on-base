import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    result = subprocess.run(
        ["node", "-"], input=script, text=True, encoding="utf-8",
        cwd=ROOT, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_pending_payment_keeps_its_original_payload_and_key():
    filename = json.dumps(str(ROOT / "web" / "static" / "site.js"))
    run_node("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(FILENAME, 'utf8');
const code = source.slice(source.indexOf('function reason('), source.indexOf('function usd('));
const calls = [];
let loseReply = true;
const ctx = {
  crypto, window: { crypto }, Response,
  signedOut() {},
  async fetch(path, options) {
    calls.push({ path, key: options.headers['Idempotency-Key'], payload: JSON.parse(options.body) });
    if (loseReply) { loseReply = false; throw new Error('reply lost'); }
    return Response.json({ new_balance: 100 });
  }
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
(async () => {
  await assert.rejects(() => ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip'));
  const original = calls[0].key;
  await assert.rejects(async () => ctx.post('/api/mini/tip', { to: 'bob', amount: 2 }, 'tip'),
    { code: 'IDEMPOTENCY_PAYLOAD_CHANGED' });
  assert.equal(calls.length, 1);
  await ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip');
  assert.equal(calls[1].key, original);
  ctx.resetIdem('tip');
  await ctx.post('/api/mini/tip', { to: 'bob', amount: 2 }, 'tip');
  assert.notEqual(calls[2].key, original);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("FILENAME", filename))


def test_validation_refusal_does_not_lock_the_payment_form():
    filename = json.dumps(str(ROOT / "web" / "static" / "site.js"))
    run_node("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(FILENAME, 'utf8');
const code = source.slice(source.indexOf('function reason('), source.indexOf('function usd('));
const calls = [];
const ctx = {
  crypto, window: { crypto }, Response,
  signedOut() {},
  async fetch(path, options) {
    calls.push(options.headers['Idempotency-Key']);
    return calls.length === 1 ? Response.json({ detail: 'invalid' }, { status: 400 })
      : Response.json({ new_balance: 100 });
  }
};
vm.createContext(ctx); vm.runInContext(code, ctx);
(async () => {
  await assert.rejects(() => ctx.post('/api/mini/tip', { to: 'alice', amount: -1 }, 'tip'));
  await ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip');
  assert.notEqual(calls[1], calls[0]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("FILENAME", filename))


def test_invalid_success_body_does_not_confirm_or_reset_a_payment():
    filename = json.dumps(str(ROOT / "web" / "static" / "site.js"))
    run_node("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(FILENAME, 'utf8');
const code = source.slice(source.indexOf('function reason('), source.indexOf('function usd('));
const calls = [];
const ctx = {
  crypto, window: { crypto }, Response, signedOut() {},
  async fetch(path, options) {
    calls.push(options.headers['Idempotency-Key']);
    return calls.length === 1 ? new Response('incomplete', { status: 200 })
      : Response.json({ new_balance: 100 });
  }
};
vm.createContext(ctx); vm.runInContext(code, ctx);
(async () => {
  await assert.rejects(() => ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip'),
    { code: 'INCOMPLETE_API_RESPONSE' });
  await ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip');
  assert.equal(calls[1], calls[0]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("FILENAME", filename))


def test_retry_refusal_after_lost_reply_preserves_original_key():
    filename = json.dumps(str(ROOT / "web" / "static" / "site.js"))
    run_node("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(FILENAME, 'utf8');
const code = source.slice(source.indexOf('function reason('), source.indexOf('function usd('));
const calls = [];
const ctx = {
  crypto, window: { crypto }, Response, signedOut() {},
  async fetch(path, options) {
    calls.push(options.headers['Idempotency-Key']);
    if (calls.length === 1) throw new Error('reply lost');
    if (calls.length === 2) return Response.json({ detail: 'throttled' }, { status: 429 });
    return Response.json({ new_balance: 100 });
  }
};
vm.createContext(ctx); vm.runInContext(code, ctx);
(async () => {
  const payload = { to: 'alice', amount: 1 };
  await assert.rejects(() => ctx.post('/api/mini/tip', payload, 'tip'));
  await assert.rejects(() => ctx.post('/api/mini/tip', payload, 'tip'));
  await ctx.post('/api/mini/tip', payload, 'tip');
  assert.equal(calls[1], calls[0]);
  assert.equal(calls[2], calls[0]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("FILENAME", filename))


def test_identical_inflight_payments_share_one_request():
    filename = json.dumps(str(ROOT / "web" / "static" / "site.js"))
    run_node("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(FILENAME, 'utf8');
const code = source.slice(source.indexOf('function reason('), source.indexOf('function usd('));
let finish, calls = 0;
const ctx = {
  crypto, window: { crypto }, Response, signedOut() {},
  fetch() { calls += 1; return new Promise(resolve => { finish = resolve; }); }
};
vm.createContext(ctx); vm.runInContext(code, ctx);
(async () => {
  const first = ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip');
  const second = ctx.post('/api/mini/tip', { to: 'alice', amount: 1 }, 'tip');
  assert.equal(calls, 1);
  assert.equal(first, second);
  finish(Response.json({ new_balance: 100 }));
  await Promise.all([first, second]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("FILENAME", filename))


def test_configured_fee_precision_survives_terms_rendering():
    filename = json.dumps(str(ROOT / "web" / "static" / "app.js"))
    run_node("""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(FILENAME, 'utf8');
const formatting = source.slice(source.indexOf('const fmtUSDC'), source.indexOf('/* ---------- reveal-on-scroll'));
const rendering = source.slice(source.indexOf('function renderTerms('), source.indexOf('const STAT_IDS'));
const body = { innerHTML: '' };
const ctx = { $: () => body, escapeHtml: value => String(value) };
vm.createContext(ctx); vm.runInContext(formatting + '\\n' + rendering, ctx);
ctx.renderTerms({
  limits: { max_tip_usdc: 100, max_bet_usdc: 100, max_trade_usdc: 100, min_withdraw_usdc: 1,
    max_withdraws_per_day: 3, min_subsidy_usdc: 1, max_subsidy_usdc: 100 },
  fees: { withdraw_pct: 1.25, win_pct: 2.125 },
  chain_name: 'Test', chain_id: 8453, usdc_address: '0x' + '11'.repeat(20),
  onchain_markets_enabled: false, smart_wallet_enabled: false
});
assert.match(body.innerHTML, /<td>1,25%<\\/td>/);
assert.match(body.innerHTML, /<td>2,125%<\\/td>/);
""".replace("FILENAME", filename))
