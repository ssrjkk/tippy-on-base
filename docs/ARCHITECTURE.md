# Architecture

Tippy is a Telegram bot for USDC tipping, prediction markets, and paid
content on Base. Two processes, one database.

```
┌─────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│  bot (aiogram)   │  │  web (FastAPI)        │  │  agent (optional) │
│  long-polling or │  │  /, /app, /api/*      │  │  news → LLM →    │
│  webhook         │  │  Telegram Login Widget │  │  markets + EAS   │
└────────┬────────┘  └──────────┬───────────┘  └────────┬─────────┘
         │                      │                        │
         └──────────┬───────────┴────────────────────────┘
                    │
          ┌─────────▼────────┐
          │   PostgreSQL      │
          │  (Ledger class)   │
          │  RLock per-proc   │
          └─────────┬─────────┘
                    │
          ┌─────────▼─────────┐
          │  Base chain        │
          │  (web3.py)         │
          │  USDC deposits     │
          │  withdrawals       │
          │  TipBotVault       │
          │  EAS attestations  │
          └────────────────────┘
```

## Processes

### bot (`python -m bot.main`)
- Telegram bot via aiogram 3 (exact pins live in `requirements.txt`)
- Handles all user commands (/balance, /tip, /deposit, /market, etc.)
- One Ledger instance, one DB connection, serialized by `threading.RLock`
- Runs deposit scanner (polls `eth_getLogs` for USDC transfers)
- Runs market deadline watcher, grace period watcher

### web (`uvicorn web.server:app`)
- FastAPI dashboard + API
- `/api/stats`, `/api/solvency`, `/api/wallet` — public
- `/`, `/api/auth/telegram`, `/api/auth/wallet` — auth (the login widget and
  the wallet signature both live in the cabinet block on the landing page;
  `/login` and `/me` only redirect there)
- `/u/{id}`, `/m/{id}`, `/m/oc/{id}` — shareable public pages
- One Ledger instance, one DB connection, serialized by `threading.RLock`
- Serves static files (HTML/CSS/JS)
- Strict CSP: nonce-based `script-src` (no inline `<script>` without a nonce,
  no inline event handlers anywhere), `style-src 'self' 'unsafe-inline'`
  (inline style attributes only)
- x402 endpoints (`/api/x402/tip`, `/api/x402/paywall`): invoice → on-chain
  payment → verify → replay-proof credit capped at the invoice price
  (an overpay is credited at price, never more)

### agent (`python -m agent.main [--loop]`, optional)
- Autonomous market-maker: fetches crypto news → cheap LLM filter → strong
  LLM decision → creates markets, places bets, sells analysis via paywall
- Fail-closed: an LLM error means NO action; spend caps are validated at
  startup (refuses to run on a misconfigured set); circuit breaker with
  cooldown after consecutive errors
- Every action is EAS-attested on Base (dedicated `AGENT_EAS_KEY`; without a
  registered `EAS_SCHEMA_UID` it degrades to a local JSONL audit trail with a
  warning)
- Calls the bot's web API (`TIPPY_BASE_URL`) as its tool surface; the DB
  blocks it from trading on its own markets

Both processes share the same PostgreSQL database. Cross-process safety
comes from atomic SQL (transactions, `SELECT FOR UPDATE`, unique
constraints) — NOT from Python locks.

## Database

PostgreSQL 16+ with `dict_row` factory. Schema managed by:
1. **Alembic** (`alembic/versions/`) — tracked migrations
2. **ensure_schema()** — idempotent DDL fallback (runs on every startup)

Key tables:
- `users` — Telegram user IDs and USDC balances (internal ledger)
- `tx_log` — the audit trail of every balance change. A row is never deleted,
  and its money (`amount`, `kind`, counterparty) is written once; only a
  withdrawal's `status` and `tx_hash` are updated as the payout moves through
  its stages — see `tests/test_ledger.py` for the gate that keeps it that way.
- `markets` — LMSR prediction markets (escrow, options, status)
- `market_shares` — per-user share positions
- `user_wallets` — encrypted custodial wallet keys/seeds
- `wallet_links` — on-chain address ↔ Telegram user binding

## On-chain

- **Hot wallet** (EOA) — the vault's relayer for withdrawals and tips. Holds
  USDC. Private key in `HOT_WALLET_KEY` env var. Daily cap enforced by vault.
- **TipBotVault** (contract) — holds user USDC reserves. Relayer has a
  24h rolling daily limit. Owner (multisig) has full control. Two-step
  ownership transfer.
- **Deposit scanner** — polls `eth_getLogs` for USDC `Transfer` events
  to the hot wallet or vault address. Credits internal ledger. Supports
  per-user CREATE2 deposit proxies (deterministic addresses).
- **Withdrawals** — debited and queued atomically in one transaction; the
  per-user daily cap is a guarded `INSERT ... SELECT COUNT(*)` (no
  check-then-act race, safe across processes). Queued rows flush as a
  batch (`WITHDRAW_BATCH_*`) with a direct-send fallback.
- **OutcomeMarket** — on-chain prediction markets: ERC-1155 shares, LMSR
  pricing on-chain, oracle resolution with a 2h owner dispute window, 24h
  public cancel for abandoned markets. The oracle is a separate key only when
  `ORACLE_PRIVATE_KEY` is set; without it `bot/handlers/onchain.py` resolves
  through `owner_resolve` with the hot wallet, and one signature then holds both
  the proposal and the dispute power — so the window is a check on a second
  party, not on a single one.

## Smart Wallet (ERC-4337) — P2

Gasless per-user accounts on Base via account abstraction — custodied by the
operator: `SmartAccount.owner` is the relayer key, and that key signs every
UserOperation, so a user's control over the account is exactly the operator's
restraint. A VerifyingPaymaster sponsors gas so users don't need ETH.
Deployed & proven on Base Sepolia.

- **SmartAccount** (CREATE2, deterministic) — each Telegram user gets a
  counterfactual address; no creation until first fund. `validateUserOp`
  verifies an EIP-191 signature over the EntryPoint-provided `userOpHash`
  (which binds chainid and the EntryPoint address).
- **SmartAccountFactory** — `createAccount(owner, salt)` via CREATE2 with
  deployer/owner enforcement.
- **EntryPoint v0.6** — standard ERC-4337 singleton.
- **VerifyingPaymaster** — sponsors gas; the relayer signature binds
  (op fields, chainid, paymaster address), so it cannot replay across
  chains or deployments; daily limits, `_recordUsage`,
  correct `postOp(PostOpMode, bytes, uint256)` for EP v0.6.
- **bot/smart_wallet.py** — UserOp build + EIP-191 signing, paymaster data,
  `create_account` / `approve_and_trade_sync`. Config `SMART_WALLET_*`.

## Security model

- Funds are custodial by design (hot wallet holds USDC)
- Vault reduces hot wallet exposure via daily limit
- WALLET_ENC_KEY encrypts user wallet keys at rest
- Stateless HMAC sessions (no server-side store; rotate SECRET_KEY = logout all)
- Web login nonces are single-use (DB PK) and pruned after
  `LOGIN_NONCE_TTL_SECONDS`
- Private-chat guards on sensitive commands (see docs/SECURITY.md)
- All bot replies that interpolate user-controlled text use `html.escape`
  (HTML parse mode, no Markdown injection)
- Rate limiting on /api/* endpoints; strict CSP on the dashboard
