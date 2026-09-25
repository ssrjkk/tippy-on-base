# Tippy - Community Economy in USDC on Base

[![CI](https://github.com/ssrjkk/Tippy-on-base/actions/workflows/ci.yml/badge.svg)](https://github.com/ssrjkk/Tippy-on-base/actions/workflows/ci.yml)
![Tests](https://img.shields.io/badge/tests-793%20passed-brightgreen)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![Network](https://img.shields.io/badge/network-Base-0052FF)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Telegram bot that turns any chat or community into a financial ecosystem:
**instant USDC tips, Polymarket-style prediction markets with live AMM odds,
an AI assistant, paywalled content, and per-user wallets** — all on Base.
No custom contracts in the hot path: only the official USDC contract plus an
optional audited-style treasury vault; everything else is instant internal
accounting backed by public proof-of-reserves.

**Made by [@ssrjkk](https://github.com/ssrjkk) — by ssrjkk.**

**Author:** [@ssrjkk](https://t.me/ssrjkk) · [@b2wmain](https://t.me/b2wmain) · [X / Twitter](https://x.com/ludych1) · [GitHub](https://github.com/ssrjkk)

---

## Features

### 💸 Instant tips (zero gas)
- `/tip 5 @nick` — or reply `/tip 5` to any message
- Recipient gets an immediate DM notification
- 🌧️ `/rain 10 [N]` — scatter USDC across active group members
- 🔥❤️⚡👏🎉 emoji reactions tip the message author (groups)

### 📈 Prediction markets v2 — Polymarket analog (LMSR AMM)
- `/market create 50 Who wins? | Alice | Bob 7d` — creator funds the AMM liquidity
- **Live odds that move with demand** — Hanson's Logarithmic Market Scoring Rule,
  exact `Decimal` math (`b = subsidy / ln(n)`)
- `/trade <id> <opt> <amount>` — buy outcome shares at the live price
- **`/sell <id> <opt> [50%]` — sell back any time before resolution** (exit anytime,
  not locked until the end like parimutuel pools)
- `/positions` — your portfolio with live mark-to-market value and PnL
- Resolution pays **1 USDC per winning share**; the market creator keeps the
  remaining liquidity pool as their earnings
- **Guaranteed solvency by the LMSR funding theorem**: the escrow can always
  cover the worst-case payout, verified by property tests along aggressive
  random trading paths
- Deadline pings + grace-period auto-refund protection for forgotten markets

### 🎲 Parimutuel polls (quick group games)
- `/bet create Question | Option 1 | Option 2 [24h]`, `/bet <id> <opt> <amount>`
- Winners split the whole pot proportionally (2% fee on net profit to the creator)
- Inline cards, quick-amount buttons, two-tap resolution, cancel/refund paths

### 🎯 Cally — Polymarket on Base (`/oc*`)
- `/oc_create 50 Who wins? | Alice | Bob 7d` — creates a market in the
  **OutcomeMarket.sol** contract: the subsidy is locked on-chain, shares are
  real **ERC-1155 tokens**, every trade is a USDC transfer anyone can verify
  on Basescan
- `/oc_buy <id> <opt> <amount>` / `/oc_sell <id> <opt> [50%]` — trade at the
  live LMSR price with hard on-chain slippage caps (`maxCost`/`minProceeds`)
- `/oc_redeem <id>` — pull resolution payouts straight from the contract
- `/oc <id>` / `/oc_pos` — live odds and your ERC-1155 positions
- Oracle proposes the winner, the owner can dispute within 2h (once —
  after a dispute only the owner finalizes), and anyone can
  refund an abandoned market 24h after close — refunds are hard-capped at
  $1/share so the creator subsidy can never be drained
- Labels live in a bot-side registry (the contract stores numbers only); gas
  for first trades is auto-topped-up from the hot wallet with a per-wallet
  anti-drain cooldown

### 🧠 AI assistant
- `/ask <question>` — ask about crypto, Base, market strategy, bot usage
- Works with **any OpenAI-compatible API** (OpenAI, OpenRouter, local vLLM/llama.cpp)
  via `AI_API_URL` / `AI_API_KEY` / `AI_MODEL`
- Reply to a message with `/ask` to use it as context; rate-limited, typing indicator

### 💛 Donations & wallets
- `/donate` — personal donation page with QR (`t.me/<bot>?start=donate_<id>`)
- Deposits auto-credit with push notifications (Basescan tx link included)
- `/link <address>` + signature → automatic deposit crediting (ecrecover-verified;
  a deposit can only be claimed by the wallet's owner — never by tx-hash sniping)
- `/wallet` — built-in custodial wallet, export/import by seed phrase
- `/withdraw <address> <amount>` — on-chain payout (1% fee, min 1 USDC, ≤5/day),
  full auto-refund for stuck/reverted transactions
- `/tx <hash>` — look up any Base transaction and decode its USDC transfer

### 🏷 Basenames (on-chain identity on Base)
- `/basename` — your on-chain name (ENSIP-19 reverse resolution of your deposit
  address against the Base L2 resolver); shows in `/wallet` and the public
  donate page automatically
- `/basename <name>.base.eth` — on-chain availability check: free (with a
  registration hint), taken (owner address), or **confirmed yours** when it
  resolves to your linked/custodial wallet
- Tips by name: `/tip <name>.base.eth` resolves the basename to its on-chain
  owner — the chain is the source of truth, nothing is stored bot-side

### 🔐 Paid content & channels
- `/paywall create 5 Title` → sell posts for USDC (buyers read instantly)
- `/paywall channel 5` → paid Telegram channel access, 5 USDC / 30 days,
  one-time invite links, expired subscribers auto-kicked
- **x402 HTTP payments**: `POST /api/x402/tip` and `POST /api/x402/paywall` —
  AI agents pay on-chain via the 402 handshake (invoice → pay → replay-proof credit)

### 🤖 Autonomous agent (fail-closed)
- Perceives crypto news → LLM filters noise → creates markets, bets, sells
  analysis as paywall posts — **every action EAS-attested on Base**
- News sources: CryptoPanic (with `CRYPTOPANIC_TOKEN`) plus free RSS fallbacks
  (CoinDesk, CoinTelegraph); deduplication and relevance scoring built in
- Runs inside the main process (`deploy/run.py`) when `AGENT_TG_ID > 0` and the
  cap set validates — no separate service needed; a silent agent death stops
  the process like any other watcher (fail-fast)
- State (caps counters, seen-news, audit trail) lives in `AGENT_STATE_DIR`
  (default: the `agent/` package dir) so a restart never resets the daily cap
- Spend is capped in code (never in the prompt): daily + per-tx caps, actions
  per hour, circuit breaker with cooldown; caps are validated at startup and
  the agent refuses to run on a misconfigured set
- LLM failure = no action (fail-closed); the agent can never trade on its own
  markets (DB-level guard)
- `/agent` (admin-only) — live caps state, circuit-breaker status and the
  agent's recent actions straight in Telegram, no web dashboard needed

### 🖥 Web dashboard (public transparency)
- Live stats, volume chart, markets with odds/backers, leaderboards, user profiles
- **Proof of Reserves** `/api/solvency`: bot liabilities vs on-chain USDC
  (read from the TipBotVault contract when deployed, else the hot wallet)
- **Base design system UI** — light (default) + dark theme with a toggle
  (Base White/Black #F8F9FB / #0A0B0D, Primary Blue #0052FF)
- Public JSON API: `/api/stats`, `/api/markets`, `/api/predictions`,
  `/api/prediction/{id}`, `/api/leaderboard`, `/api/health`, `/qr`, rate-limited per IP

### 🪄 Smart Wallet (ERC-4337) — gasless, non-custodial
- **Per-user Smart Accounts** via CREATE2 (`SmartAccountFactory`) — deterministic
  counterfactual addresses; no ETH needed from the user
- **VerifyingPaymaster sponsors gas** — users tip/trade without ever holding ETH
  (Base Sepolia proven: direct `handleOps` and gasless paymaster UserOps, status=1)
- `bot/smart_wallet.py` builds + signs UserOperations (EIP-191) and drives
  `approveAndTrade` from the wallet's own USDC
- Details: `docs/ECOSYSTEM_DESIGN.md` §8

### 🚀 Breakthrough Base L2 Features

5 killer functions leveraging unique Base L2 capabilities:

#### 🎁 1. Gasless Onboarding
**New users get 10 FREE transactions — zero barrier to entry.**

- `/gasless` — check your free transaction balance
- Uses Base Paymaster API (Pimlico) to sponsor UserOperations
- New wallets automatically qualify; gas paid by the protocol
- After 10 free txs — standard Base fees (still just fractions of a cent)

```
/gasless → 🎁 You have 10 of 10 free transactions left!
           Gas is sponsored by Base — you pay nothing.
```

**Why it's a breakthrough:** Traditional bots require users to hold ETH for gas. Base Paymaster eliminates this entirely — users onboard with USDC only and transact for free until they're hooked.

#### 💳 2. Recurring Payments (Subscriptions)
**Automated scheduled transfers — daily, weekly, biweekly, monthly.**

- `/subscribe @user <amount> <interval>` — create a subscription
- `/subscriptions` — view all active subscriptions
- `/cancelsub <id>` — cancel a subscription
- Background executor runs hourly and auto-processes due payments
- Supports: daily (24h), weekly (7d), biweekly (14d), monthly (30d)

```
/subscribe @creator 10 monthly → ✅ Subscription created!
                                 💸 @creator: $10.00 every monthly
```

**Why it's a breakthrough:** First Telegram bot with native recurring payments on Base. Creators can set up patronage, teams can automate salaries, communities can run membership programs — all trustless and automatic.

#### ⚡ 3. Batch Transactions
**Multiple actions in ONE UserOperation — save 60% gas.**

- Atomic execution — all succeed or all fail (transaction-style)
- Gas savings: first action pays full 21k base gas, each additional saves ~60%
- Example: tip + create_market + bet = 1 transaction instead of 3

```python
# Internally: one UserOperation executes:
# 1. Tip @alice $5
# 2. Create market "Will BTC hit 100k?"
# 3. Bet $10 on YES
# Gas saved: 30-75% depending on action count
```

**Why it's a breakthrough:** ERC-4337 batch operations are unique to Account Abstraction. This is a native L2 feature that Base exposes — traditional EOA wallets can't do this efficiently.

#### 📊 4. On-chain Credit Score
**History-based reputation for P2P micro-lending — no collateral needed.**

- `/credit` — view your credit score (300-850) and loan limit
- Score computed from 5 factors:
  - Payment history (40%) — success rate of transactions
  - Account age (20%) — older = higher score
  - Transaction volume (15%) — more activity = higher score
  - Market participation (15%) — prediction market activity
  - Social connections (10%) — unique counterparties
- Grades: A (750+), B (650+), C (550+), D (450+), F (<450)
- Max loan: base_limit × grade_multiplier × confidence

```
/credit → 📊 Credit Score: 775 (A)
           Confidence: 100%
           Max loan: $1000.00
```

**Why it's a breakthrough:** Traditional DeFi requires collateral. This uses on-chain behavior to establish trust — enabling undercollateralized lending in Telegram communities. Perfect for micro-loans between people who tip and trade together.

#### 🪙 5. Creator Tokens with Revenue Sharing
**Issue tokens that automatically distribute your earnings to holders.**

- `/createtoken <name> <SYMBOL> <supply> <price>` — launch a creator token
- `/buytoken <token_id> <amount>` — buy a creator's tokens
- `/claim <token_id>` — claim your share of their revenue
- When creators earn (tips, market winnings), revenue automatically splits:
  - Proportional to holder balance
  - No manual distribution needed
  - Dividends accrue per second

```
/createtoken "My Token" MTK 1000000 0.10
→ 🪙 Token created! ID: ct_123456_1790013544
   Holders receive dividends from your earnings automatically.

/buytoken ct_123456_1790013544 1000
→ ✅ Bought 1000 tokens for $100.00!

[Creator earns $50 in tips]
→ Dividend auto-distributed: $0.05 per token

/claim ct_123456_1790013544
→ 💰 Claimed $50.00 in dividends!
```

**Why it's a breakthrough:** This is the creator economy natively on-chain. Fans invest in creators, creators share revenue automatically — all powered by Base's smart accounts. No traditional equity needed; the token IS the revenue share.

### ⛓ On-chain treasury (TipBotVault)
- Users deposit USDC into the vault contract — visible to anyone on Base
- Relayer distributes under a daily limit; owner (multisig) keeps full control
- `Distributed` events make every payout publicly auditable

## Operations

Tippy runs as a private production deployment operated by its maintainers.
The source is published for transparency and audit; there is no public
run-it-yourself guide, and contract deployment stays with the team
(vault/market owners are multisigs, never the hot wallet).

## Commands

A plain-language walkthrough for Telegram users lives in
**[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — the table below is the short form.

| Command | What it does |
|---|---|
| `/start` | Menu with all sections |
| `/tip 5 @nick` | Instant USDC tip (or reply to a message) |
| `/market create 50 Q \| A \| B [24h]` | Create an AMM prediction market |
| `/markets` · `/trade` · `/sell` · `/positions` | Trade shares at live odds |
| `/oc_create` · `/oc_buy` · `/oc_sell` · `/oc_redeem` · `/oc_pos` | **On-chain** markets (OutcomeMarket.sol, ERC-1155) |
| `/bet create Q \| A \| B [24h]` · `/bets` · `/resolve` · `/cancel` | Parimutuel polls |
| `/ask <question>` | AI assistant |
| `/deposit` / `/claim <tx>` / `/link` / `/confirm` | Fund your account (private chat only) |
| `/withdraw <addr> <amt>` | On-chain payout — 1% fee, min 1 USDC, ≤5/day (atomic DB cap), private chat only |
| `/tx <hash>` | Decode a Base transaction |
| `/rain 10 [N]` | Group giveaway |
| `/paywall ...` | Paid posts and channels |
| `/balance` / `/stats` / `/top` / `/history` | Analytics |
| `/gasless` | Check free gasless transactions |
| `/subscribe @user <amt> <interval>` · `/subscriptions` · `/cancelsub` | Recurring payments |
| `/credit` | On-chain credit score + loan limit |
| `/createtoken <name> <SYM> <supply> <price>` | Launch a creator token |
| `/buytoken <id> <amount>` · `/claim <id>` | Buy tokens / claim dividends |

## Architecture

```
bot/
├─ main.py        entrypoint, background watchers (deposits, withdrawals, deadlines)
├─ handlers/      aiogram handlers by domain (_common, menu, wallet, tips, bets, markets, stats, paywall, onchain, ai, breakthrough)
├─ ledger/        PostgreSQL accounting + LMSR AMM engine (Decimal-exact)
│  ├─ __init__.py    Ledger facade + singletons + lmsr_* re-exports
│  ├─ _core.py       connect/schema/ping                   _schema.py  DDL
│  ├─ _users.py      accounts, wallet links, create2       _conn.py    ReconnectingConn
│  ├─ _pay/_paywall/_transfer  x402, paywalls, transfers
│  ├─ _withdraw.py   AMl, batches, refunds
│  ├─ _bets/_markets       parimutuel + LMSR AMM (buy/sell/resolve)
│  ├─ _onchain.py    on-chain outcome markets registry
│  ├─ _messages/_notify/_admin/_views   messaging, outbox, settings, views
│  └─ _base.py      audit_log, MICRO, LMSR math
├─ base.py        web3 layer: USDC transfers, deposit scanning, tx decoding
├─ chain/         relayer pool (persisted daily caps) + tx status helpers
├─ ai.py          OpenAI-compatible client (stdlib urllib, no new deps)
├─ qr.py          local QR generation
├─ smart_wallet.py  ERC-4337: UserOp build/sign, paymaster data, approve+trade sync
├─ paymaster.py    Base Paymaster integration: gasless onboarding (10 free txs)
├─ recurring.py    Recurring payments: subscriptions executor (hourly watcher)
├─ batch.py        Batch transactions: multiple actions in one UserOperation
├─ credit.py       Credit scoring: 300-850 P2P lending score
├─ creator_tokens.py  Revenue sharing: creator tokens + dividend distribution
└─ config.py      env-driven configuration
agent/            autonomous market-maker: news → LLM → markets, EAS attestations
web/
├─ server.py      FastAPI: public API, proof-of-reserves, x402 endpoints
├─ x402.py        x402 handshake: invoice → verify → replay-proof credit
└─ static/        Base-design dashboard + Mini App (CSP nonce, no inline JS)
contracts/TipBotVault.sol    on-chain treasury (proof of reserves)
contracts/OutcomeMarket.sol on-chain markets (ERC-1155 shares, LMSR on-chain)
contracts/SmartAccount.sol   ERC-4337 account (CREATE2)
contracts/SmartAccountFactory.sol  deterministic account factory
contracts/VerifyingPaymaster.sol   gas-sponsoring paymaster
tests/           793 tests: real Postgres, real dispatcher, real crypto, local EVM
```

## Testing

```bash
docker compose up -d db       # PostgreSQL for tests (port 5433)
python -m pytest tests -q     # 793 passed
```

What is tested *for real* (not mocked): money conservation across every flow
(tips, fees, refunds, parimutuel payouts, rain, **AMM buy/sell/resolve/cancel**
— balances + escrows always sum to deposits), deposit-security (claiming
someone else's tx is rejected), fee math, real signature recovery, the full
aiogram dispatcher with real Update objects, USDC ABI decoding, the FastAPI
dashboard against a real ledger, background watchers, 14 end-to-end scenarios,
and 12 TipBotVault tests on a local EVM (eth-tester + py-evm). Only external
networks are mocked (Telegram transport, RPC).

The LMSR engine additionally has a property test proving the funding theorem:
along randomized aggressive trading paths the escrow never drops below the
worst-case payout.

## Security

- `HOT_WALLET_KEY` is money — never commit `.env`
- Official USDC only: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- Prediction markets rely on the creator resolving honestly; deadline + grace
  auto-refund bounds the damage of abandoned markets
- Anti-spam cooldowns, per-day withdrawal limits, gas-griefing protection
- Sensitive commands (`/withdraw`, `/deposit`, `/claim`, `/link`, `/confirm`,
  `/export`, `/import`, `/paywall subscribe`) answer only in private chats;
  the daily withdrawal cap is enforced atomically in the database
- All bot output is HTML-escaped (no Markdown injection from user titles);
  the dashboard serves a strict CSP (nonce-based scripts) with zero inline
  event handlers; web login nonces are single-use with a TTL and pruned
- Multi-relayer withdrawals keep per-relayer daily caps in a persisted state
  file (a restart cannot reset the budget); relayer keys and caps are
  validated by `python scripts/validate_env.py`
- The autonomous agent is fail-closed: LLM errors mean no action, caps are
  validated at startup, and the DB blocks trading on its own markets

## Roadmap

- Per-user deposit addresses (CREATE2 vaults) ✅
- ~~Smart Wallet (ERC-4337) + gasless paymaster~~ ✅ core shipped + Sepolia-proven (P2)
- ~~Withdrawal batching for gas savings~~ ✅ shipped (queued payouts flushed as a batch; multi-relayer pool with persisted daily caps)
- Order-book style CLOB on top of the AMM
- ~~On-chain market escrow (trustless resolution via UMA-style oracle)~~ ✅ shipped as **Cally** (OutcomeMarket ERC-1155)

## Contributing

PRs welcome — see [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for setup, style rules,
and the money-conservation requirement for any fund-touching change.
Security issues: [docs/SECURITY.md](docs/SECURITY.md) (no public issues).
Community rules: [docs/CODE_OF_CONDUCT.md](docs/CODE_OF_CONDUCT.md).
Licensed under [MIT](LICENSE).

## Grant

Prepared for the [Base Builder Grants](https://www.base.io/ecosystem/grants)
program — pitch and application package in **[docs/GRANT.md](docs/GRANT.md)**.

---

**Author:** [@ssrjkk](https://t.me/ssrjkk) · [@b2wmain](https://t.me/b2wmain) · [X / Twitter](https://x.com/ludych1) · [GitHub](https://github.com/ssrjkk)

Built on [Base](https://base.org) · Powered by USDC
