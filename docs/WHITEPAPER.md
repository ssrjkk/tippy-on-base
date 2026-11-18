# Tippy on Base — Whitepaper

**Community economy in USDC on Base: tips, prediction markets, creator revenue and
agent payments, inside Telegram.**

Author: [@ssrjkk](https://github.com/ssrjkk) · Bot: [@tippy_on_base_bot](https://t.me/tippy_on_base_bot)

This document describes what the product does, what it can become, and how it can
earn. Every capability is labelled with its real status, and every internal number
is reproducible from the repository (see [Appendix A](#appendix-a--reproducing-the-numbers)).
External funding figures are quoted from the programs' own pages and listed in
[Sources](#sources).

---

## Кратко (RU)

- **Что это:** Telegram-бот + веб + Mini App, которые превращают любое сообщество в
  денежную экономику на Base: чаевые в USDC, два движка рынков прогнозов
  (паримутуэл и LMSR-AMM), платный контент, токены авторов с дивидендами,
  рекуррентные подписки, автономный AI-агент и приём платежей от AI-агентов по x402.
- **Что уже написано:** 47 команд бота, 53 HTTP-маршрута, 9 контрактов, 18 доменных
  миксинов леджера, 10 модулей агента, 1473 обычных тестов + 198 боевых. Три языка.
- **Что выключено флагами:** ончейн-рынки (ERC-1155) и gasless-кошельки (ERC-4337).
  Код есть, деплой на mainnet — нет.
- **Что приносит деньги сегодня:** ровно одна строка — комиссия 1% с вывода.
  Комиссия 2% с выигрыша — доход **создателя** рынка, не платформы.
- **Точки роста:** включить ончейн и gasless, выйти на mainnet (multisig-владелец
  vault, задержка передачи владения — в контракте её нет, редеплой
  SmartAccount/paymaster, мультиподпись и восстановление аккаунта), добавить платформенную долю в
  денежные потоки, продавать подписку на премиум-функции, получить грант
  Base Builder Grants (1–5 ETH, ретроактивно) и попасть в Base Batches ($100K).
- **Чего не будет:** кредитования. Леджер не умеет давать в долг, и обещать лимит
  займа без операции займа — значит обещать деньги, которых продукт не может двигать.

---

## 1. Status legend

| Label | Meaning |
| --- | --- |
| **Live** | Implemented, tested, and running on the demo origin |
| **Built, off** | Implemented and tested, disabled by configuration on the demo |
| **Proposed** | Not implemented. Named here as a plan, never as a capability |

The demo origin runs on **Base Sepolia** (chain id 84532). Its database holds
seeded demonstration data, not users. No figure in this paper is presented as
traction, revenue or market share.

## 2. Problem and positioning

Crypto payments never reached the place where communities actually talk. A Telegram
group of 5,000 people has no way to tip a contributor, run a prediction market on
its own outcome, sell a paid analysis post, or let an AI agent pay for an action —
without sending everyone to an external dApp, a seed phrase and a gas token.

Tippy puts the whole economy inside the chat:

- **Custody is explicit, not hidden.** Balances are internal ledger entries backed
  by a hot wallet on Base. `docs/SECURITY.md` states the model in one sentence: the
  hot wallet is custodial by design, and a multisig owner caps what the relayer key
  can do. The product publishes its reserves against its liabilities instead of
  asking users to trust the wording.
- **Money moves without gas.** Tips, stakes, trades and paywall purchases are ledger
  operations: instant, free, and reversible only through the documented refund path.
  Gas appears once, at withdrawal, where it is batched.
- **One accounting truth.** `tx_log.amount` is always positive; sign, label and
  counterparty come from `kind` through `bot/tx_view.py`. A row is never deleted and
  its money columns are written once — the only `UPDATE tx_log` in the codebase sets
  a withdrawal's status and hash.

Positioning: **the community economy layer for Telegram on Base**, with agent-native
payment rails. Not a wallet, not a DEX, not a lending protocol.

## 3. Architecture at a glance

Four processes, one Postgres ledger, one chain:

| Process | Role |
| --- | --- |
| `bot/` | aiogram handlers, 47 commands, notifications, deadline jobs |
| `web/` | FastAPI: public site, Mini App API, x402 endpoints, metrics |
| `agent/` | autonomous market participant: perceive → decide → act → attest |
| `deploy/` | container entrypoints, backup, the origin watchdog |

Cross-process safety comes from `SELECT … FOR UPDATE` and guarded
`INSERT … WHERE` statements, not from the in-process `RLock`. The lock serializes
one process only; the database serializes the rest.

Contracts (9 total: 8 production plus a test-only USDC stand-in, with a deploy
script and two Forge test suites):

| Contract | Purpose |
| --- | --- |
| `TipBotVault.sol` | treasury: separate owner and relayer roles, two-step ownership transfer, rolling 24 h spend cap on both |
| `SmartAccount.sol` | ERC-4337 account for gasless user operations |
| `SmartAccountFactory.sol` | deterministic account deployment |
| `VerifyingPaymaster.sol` | sponsors gas, signature bound to chain id |
| `Create2Factory.sol` | deterministic address deployment |
| `USDCForwarder.sol` | shared EIP-1167 implementation behind every per-user CREATE2 deposit proxy: deterministic address derived from `tg_id`, sweeps USDC and ETH to the hot wallet, no admin path, immutable target |
| `OutcomeMarket.sol` | ERC-1155 outcome shares, on-chain resolution |
| `LMSR.sol` | logarithmic market scoring rule pricing |
| `MiniUSDC.sol` | test-only USDC stand-in |

## 4. Complete function inventory

### 4.1 Telegram bot — 47 commands, 7 groups (Live)

| Group | Commands |
| --- | --- |
| account (10) | `/menu` `/help` `/stats` `/top` `/history` `/settings` `/language` `/about` `/app` `/ask` |
| wallet (10) | `/balance` `/deposit` `/withdraw` `/link` `/confirm` `/wallet` `/import` `/export` `/tx` `/gasless` |
| tips (6) | `/tip` `/rain` `/donate` `/subscribe` `/subscriptions` `/cancelsub` |
| markets (5) | `/markets` `/market` `/trade` `/sell` `/positions` |
| bets (5) | `/bet` `/bets` `/mybets` `/resolve` `/cancel` |
| onchain (6) | `/oc` `/oc_create` `/oc_buy` `/oc_sell` `/oc_redeem` `/oc_pos` |
| creator (5) | `/claim` `/paywall` `/basename` `/createtoken` `/buytoken` |

The catalog is plain data in `bot/commands_catalog.py`, read by both the bot (which
wraps it into `BotCommand`) and the web process (which publishes it through
`/api/info`), so the menu and the website cannot drift apart.

### 4.2 Web and Mini App — 53 HTTP routes (Live)

- **35 routes in `web/server.py`**: the landing page with a working workspace
  (balance, tip, withdraw, markets, bets, create, onchain, history), a slide-over
  trade drawer that quotes LMSR prices from the server, a ⌘K market search over the
  lists already on screen, the x402 handshake sandbox, `/app`,
  `/agent`, `/me`, `/u/{tg_id}`, `/m/{bet_id}`, `/m/oc/{market_id}`, `/tos`,
  `/metrics`, `/qr`, `robots.txt`, `sitemap.xml`, the Farcaster manifest, and the
  public API: `stats`, `volume_history`, `markets`, `market/{id}`, `predictions`,
  `prediction/{id}`, `prediction/{id}/quote`, `onchain/markets`,
  `onchain/market/{id}`, `leaderboard`,
  `user/{id}`, `info`, `health`, `solvency`, `ai/status`, `wallet`, `agent/status`,
  `agent/audit`, `POST /ask`, the x402 endpoints `POST /api/x402/tip` and
  `POST /api/x402/paywall`, and `POST /api/webhook-miniaction`.
- **12 routes in `web/mini.py`**: `auth`, `state`, `lang`, `tip`, `betplace`,
  `create`, `trade`, `smartbuy`, `smartwallet`, `withdraw`, `withdraw/confirm`,
  `onchain/{market_id}`. Every money-moving POST
  requires an `Idempotency-Key`; withdrawal uses a staged single-use token instead.
- **5 routes in `web/auth.py`**: `/login` (a bookmark redirect that still
  issues the login-state cookie), `/api/auth/telegram` (the widget callback — a
  refusal 303s to `/?auth=<code>` instead of answering a body to a browser),
  `POST /api/auth/wallet` (sign-in by wallet signature), `/logout`, `/api/me`.
- **1 route in `web/frame.py`** — the Farcaster frame contract the Base App feed calls.

Interface rules are enforced by tests, not by review: touch targets ≥ 44 px, no
horizontal overflow at 360 px, dark theme default, one `<h1>` per page, focus ring
≥ 3:1 on both canvases, pinch zoom allowed, every clickable element a real
`<button data-act>`, percentages formatted in the interface locale. A screen that
exists only after a tap is measured by opening it: both landing dialogs, the three
Mini App money sheets and the market detail are rendered, sampled for contrast and
clipping, and walked by a real Tab key that must stay inside the layer — the
screen under a full-width panel is made `inert`, which is the only mechanism that
removes it from the tab order and the accessibility tree at the same time.

### 4.3 Prediction markets — two engines (Live) plus a third (Built, off)

**Parimutuel pools (`/bet`).** Stakes accumulate into a pot; at resolution the pot
is split pro-rata among the winning stakes using integer arithmetic. The floor
remainder goes to the creator together with the win commission. Resolution is
gated: creator or admin, deadline reached, no re-entry after the status flip. All
balance updates and the status change commit in one transaction — a crash before
the commit rolls everything back, and after it the guard blocks a second payout.

**LMSR AMM (`/market`, `/trade`, `/sell`, `/positions`).** Prices come from a
logarithmic market scoring rule computed on `Decimal`, so a share price is a
function of inventory rather than of an order book. The creator subsidizes
liquidity (10–1000 USDC, daily subsidy cap 2000 USDC across the product), trades
are capped at 500 USDC, markets default to a 168-hour deadline with a 72-hour
grace window. At resolution the escrow is distributed to winning shares, the
creator takes the documented win commission on the payout, and every remaining
micro goes pro-rata to winners — capped by what the escrow can still give, so it
can never reach money that belongs to a loser. Cancellation refunds
proportionally when the escrow is short.

**On-chain outcome shares (`/oc*`).** `OutcomeMarket.sol` mints ERC-1155 outcome
tokens priced by `LMSR.sol`, with redemption after resolution. **Built, off**:
`/api/info` reports `onchain_markets_enabled: false` because
`OUTCOME_MARKET_ADDRESS` is unset on the demo.

One percentage rounder exists for the whole product: `onchain_market.price_pct`
with `ROUND_HALF_UP`. A gate fails the build if the market list and the market page
print different prices for the same share.

### 4.4 Creator economy (Live)

| Function | What it does | Limits |
| --- | --- | --- |
| `/paywall` | sell a post: the list shows the item, the body is delivered only after payment | 50 items per user, title 120 chars, body 4000 chars |
| paid channels | sell time-limited access to a Telegram channel, with expiry | 5 channels per user |
| `/createtoken` `/buytoken` | author token; revenue accrues and is distributed to holders as dividends | distribution is idempotent across processes |
| `/subscribe` `/subscriptions` `/cancelsub` | recurring payments to a creator: daily, weekly, biweekly, monthly | compare-and-set on `next_execution`, so two executor loops cannot double-debit |
| `/basename` | Base name service: reverse-resolve the deposit address (ENSIP-19 on the L2 resolver), or check a name's availability and owner on-chain | nothing stored — the chain is the source of truth |
| `/donate` | one-off donation to a creator or community | — |
| `/rain` | split an amount across a group's members; groups and supergroups only | 100 USDC per rain, 25 recipients |
| reaction tips | a reaction on an indexed message tips its author | message indexing in `bot/handlers/paywall.py` |

The win commission (2% of profit in parimutuel, 2% of the payout in LMSR) is
**creator income** in both engines. That is a deliberate incentive: the person who
brings liquidity and resolves the market honestly is paid by the market itself.

### 4.5 Agent-native payments (Live)

**x402.** An AI agent POSTs `/api/x402/tip?recipient=…&amount=…` with no payment
header and receives `402 Payment Required` in both shapes at once: the `x-402-*`
headers and the flat JSON keys (`detail`, `amount_usdc`, `pay_to`) that
first-generation clients read, and the official `accepts[]` envelope (x402 v1,
scheme `exact`) that an x402-spec agent reads. Each invoice carries its own
deposit address, minted from (recipient, amount, kind), so funds sent for one tip
can never be redeemed for another. Two payment paths then unlock the same credit.

* **Standard path** — repeat with `X-PAYMENT: <base64 EIP-3009 authorization>`.
  The endpoint recovers the signer from the authorization (which must name this
  invoice's address and amount), broadcasts `transferWithAuthorization` itself,
  credits the Telegram recipient and answers 200 with an `X-PAYMENT-RESPONSE`
  receipt. One HTTP round trip, no waiting for the payer to mine a transaction.
  An authorization that signs for more than the quote is credited at the quote;
  the rest stays in the receive pool.
* **Legacy path** — repeat with `x-402-payment: <tx_hash>`. The endpoint verifies
  the Transfer on-chain before crediting. The transaction hash is the primary key
  of `x402_payments` and the deposit scanner skips it, so a payment is never
  credited twice and liabilities stay exact. A repeated hash answers 409.

Either rail also sells paywall content through `POST /api/x402/paywall`. The floor is
`X402_MIN_USDC` (0.01 USDC) and an invoice lives 300 seconds after it is issued. The
ceiling is `MAX_TIP_USDC` (1000 USDC), the same cap the Telegram handlers enforce. An
amount outside that range answers 400
naming the bound it broke — `amount is over the 1000 USDC limit of this site` —
because a generic "recipient and amount are required" tells an agent to add a
parameter it already sent, and it resends the identical request forever. The
landing runs this handshake against the site that serves it — the section
«Счёт за один HTTP-запрос»: a real 402, the invoice's own address and amount
filled into both request forms, and the refusal paths next to it.

**Autonomous agent** (`agent/`, 10 modules). A perceive → decide → act → attest
loop: RSS news ingestion (CryptoPanic with a token, plus free public fallbacks),
an LLM decision layer that turns news into structured market/bet decisions, direct
tool calls into the ledger, spend caps and a circuit breaker, PnL attribution with
a stop-loss on drawdown, EAS attestations of its actions, and **signal selling** —
the agent publishes its own analysis as a paywall post.

Agent safety knobs are real configuration, not prose: daily spend cap 50 USDC,
per-transaction cap 10 USDC, 20 actions per hour, stop-loss at 20% drawdown, halt
after 3 consecutive errors with a 300 s cooldown, LLM budget 5 USD/day.

### 4.6 AI assistant (Live)

`/ask` and `POST /api/ask` answer questions about the product and its data. The
provider chain is discovered and health-tracked in database rows (`ai_usage_day`,
`ai_provider`), so uvicorn, the bot and the agent spend **one** daily budget
(300 answers). Providers come from an explicit chain, keys already present in the
environment, any model server answering `GET /v1/models` on loopback (re-probed on
a TTL, so starting a local server needs no restart), and one keyless public
fallback that can be switched off.

There is no "AI disabled" state: when no model answers, `/ask` serves real ledger
data through `bot/ai_data.py` and says that it did.

### 4.7 Wallet, deposits and withdrawals (Live; gasless Built, off)

- **Deposits**: a block scanner credits USDC transfers to the hot wallet; the
  x402 hash set is excluded so an agent payment is not credited twice.
  `/claim <tx_hash>` lets a user claim a deposit manually, under the same
  confirmation-blocks maturity gate as the scanner — a deposit that may still be
  reorged is never credited, because that would mint unbacked balance.
  `/api/health` publishes `chain_head`, `last_scanned_block` and `deposit_lag`.
- **Withdrawals**: staged single-use confirmation token (TTL 600 s), minimum
  1 USDC, 5 per day, large-withdrawal threshold 500 USDC, stuck-withdrawal timeout
  600 s with a compensating ledger row so the money is never lost in limbo.
  Batching flushes on 60 s / 20 withdrawals / 50 USDC, with a direct-send fallback.
- **Wallet linking**: up to 10 addresses per user, `/link` + `/confirm`,
  `/import` `/export` for key material, `/tx <hash>` for an on-chain lookup
  (status, USDC value, sender, recipient, explorer link).
- **Gasless (Built, off)**: ERC-4337 `SmartAccount` + `VerifyingPaymaster`, with
  10 sponsored transactions per account (`PAYMASTER_FREE_TX`), a per-operation USD
  gas ceiling and a global ceiling. The account's owner is the operator key, so
  this is sponsored custody rather than user custody — the same model as the
  ledger, with gas removed from the user's path. `/api/info` reports
  `smart_wallet_enabled: false` on the demo.

### 4.8 Trust, observability and operations (Live)

- **Published solvency.** `/api/solvency` sums **six** liability categories: user
  balances, open market escrow, open bet pools, community treasuries, unswept x402
  credit, unclaimed pending deposits. It returns `liabilities_usdc`,
  `pending_deposits_usdc`, `reserve_usdc` and `solvent`, and the landing page shows
  «недостаточно» when reserves fall short. One number, one key: a second name for
  the same figure was removed because two names are two numbers waiting to disagree.
- **One definition of volume.** `VOLUME_KINDS` in `bot/ledger/_views.py` decides
  what counts; receiver mirrors and payouts stay out because they are the same
  movement booked once. `/api/stats`, `/api/volume_history`, `/metrics` and the
  landing legend all read that tuple, and a gate fails when the sentence and the
  tuple diverge.
- **Audit trail** (`ledger/_audit.py`), **Prometheus `/metrics`**, **`/api/health`**,
  an owner-only **`/admin`** panel that is a batch of read-only SELECTs and refuses
  to render outside a private chat, database backup scripts, and an origin watchdog
  that keeps the demo tunnel and services up.
- **Localized refusals.** A refusal is the only text a user gets instead of a
  receipt, so `web/refusals.py` answers from the same i18n catalog in the user's
  language. A signature failure says *that* it failed, never *which* comparison
  broke; the reason goes to the log.
- **Strict CSP** with a fresh nonce stamped on every inline script and style.

### 4.9 Engineering quality (Live)

| Measure | Value |
| --- | --- |
| Tests | 1473 ordinary + 198 production (rendered-page and live-site gates) |
| Languages | ru / en / zh, two independent catalogs, key sets gated equal |
| Ledger domain mixins | 18 (every base of `Ledger` in `bot/ledger/__init__.py`) |
| Agent modules | 10 (`git ls-files agent/*.py`) |
| Contracts | 9 in `contracts/` (8 production + `MiniUSDC.sol`, the test stand-in), 2 Forge suites in `contracts/test/forge/`, 2 Foundry deploy scripts (`script/DeployTimelock.s.sol`, `contracts/script/DeployOutcomeMarket.s.sol`) |

The gates are structural, not stylistic. Examples that exist because prose review
missed the bug: every `ledger.<method>()` call site must resolve on the real class;
no pre-formatted money string may be handed a numeric placeholder; every i18n call
must pass the placeholders its string needs; every i18n argument carrying user text
must use the escaper (the bot answers with `parse_mode=HTML`); button labels ≤ 64
chars and `callback_data` ≤ 64 bytes in all three languages; every env knob the code
reads must be written down in a template or a doc, and no template may offer a knob
nothing reads; the design contract is measured from the tokens (contrast, fonts,
focus, zoom, headings, dead CSS, skeleton ownership) and from the rendered page
across the nine facets named in `tests/test_production_design.py` (`FACETS` there —
both landings, a market page, the Mini App, the agent dashboard, `/tos`, `/u/{id}`,
an on-chain market, the dead end), which include all four Mini App tabs.

## 5. What is measurably different

1. **Negative reserves are published.** Most products in this niche show a
   proof-of-reserves number and stop. Tippy publishes the liability side too, in six
   named categories, and lets the answer be `solvent: false`.
2. **Two prediction engines plus an on-chain one, in one ledger.** Parimutuel for
   community polls, LMSR for continuous trading, ERC-1155 shares for on-chain
   settlement — with one fee definition and one percentage rounder across all three.
3. **Agent payments are a first-class rail, not a webhook.** x402 invoices,
   on-chain verification, hash-as-primary-key deduplication, and an autonomous agent
   that both consumes and sells through the same rails.
4. **The creator is paid by the market.** The 2% win commission is creator income in
   both engines, which makes liquidity provision and honest resolution somebody's
   job rather than the operator's.
5. **The AI cannot be "off".** A keyless fallback answers from real ledger data, so
   the assistant degrades to truth instead of to an error card.
6. **Claims are gated.** Custody wording, resolution trust, volume definition,
   solvency categories, fee percentages, Telegram limits and design tokens are all
   checked against the code by tests that fail the build. A whitepaper in this
   repository cannot quietly promise something the ledger does not do.

## 6. Growth roadmap

### G0 — Turn on what is already written (configuration + deployment)

- Set `OUTCOME_MARKET_ADDRESS` after deploying `OutcomeMarket` → on-chain markets
  appear in the bot, the site and the Mini App.
- Set `SMART_WALLET_ENABLED=1` with factory and paymaster addresses → gasless
  onboarding, `PAYMASTER_FREE_TX` (10 by default) sponsored transactions per account.
- Both are **Built, off** today; neither needs new product code.

### G1 — Mainnet readiness (blocking, in order)

1. Transfer `TipBotVault` ownership to a **multisig** with the two-step
   `transferOwnership` / `acceptOwnership` flow. **Measured gap:** the contract
   enforces no waiting period between the two calls, so a delay must come from the
   Safe's own policy or from a contract change. The relayer is a separate role
   (`setRelayer`), capped by the same rolling 24 h spend window as the owner.
2. Redeploy `SmartAccount` and `VerifyingPaymaster` for mainnet and re-bind the
   chain id into the signature hashes (already implemented).
3. Give the account a signature scheme and a recovery path that survive the
   operator. **Measured today:** `SmartAccount.validateUserOp` accepts a single
   owner signature, that owner is the operator key, and ownership moves only
   through the operator's two-step `transferOwnership` / `acceptOwnership`. A
   multi-signature scheme (for example 2-of-7 guardians) and user-driven recovery
   are **Proposed** — neither exists in the contract.
4. Set `ORACLE_PRIVATE_KEY` so on-chain resolution is not signed by the same key
   that operates the wallet. Until then the resolution path is operator-signed, and
   the docs say so.
5. Move the demo origin off a laptop tunnel onto a hosted server; the public
   surface must not depend on a residential connection.

### G2 — Distribution (product work, small)

- Community templates: a ready-made market set for a chat topic, one-tap creation.
- Creator storefronts: `/u/{id}` already renders a public profile; add the paywall
  and token shelves to it.
- Referral attribution on deposits and first trades (new ledger rows, no new engine).
- Telegram Stars / fiat on-ramp partnership so a user can fund a balance without
  already holding USDC. **Proposed** — no on-ramp exists in the code today.

### G3 — Agent economy (the differentiated bet)

- Sell agent signals as a subscription rather than a single post (`agent/signals.py`
  already publishes paywall analysis).
- Expose more ledger actions over x402 so any external agent can pay to tip, buy a
  share or unlock content — Tippy becomes a **paid API for agents**, not only a bot.
- Publish the agent's attested track record (EAS) as a public, verifiable page.

### G4 — Protocol surface (only after G1–G3)

- Permissionless market creation with a stake bond and a dispute window.
- Composable outcome shares: transfer, collateralize in external venues, settle
  against the same oracle.
- A public fee switch governed by the multisig, so the revenue levers below become
  a community decision rather than an operator's edit.

**Explicit non-goal: lending.** There is no loan, collateral or repayment path in
the ledger, and the credit-score feature was deleted because it printed a limit the
product could not fund. No score, limit or credit entry returns without a lending
ledger operation behind it.

## 7. Monetization

### 7.1 What the code earns today (measured)

**Exactly one revenue line exists: the withdrawal commission, 1%.**

The only `INSERT … kind='fee'` statements in the repository are
`bot/ledger/_withdraw.py:117` and `bot/ledger/_withdraw.py:201`. The win commission
(2%) is credited to the market creator, not to the platform:
`bot/ledger/_bets.py` (`creator_income = fee_sum + remainder`) and
`bot/ledger/_markets.py` (`creator_fee = min(win_fee_micro(distributed), leftover)`).
Paywall sales, channel access, creator-token trades, recurring subscriptions and
x402 payments contain **no platform cut at all**.

The structural consequence matters more than the rate: **all internal activity is
untaxed.** Tips, stakes, trades and content sales circulate freely, and the platform
earns only when money leaves. A product with this shape needs either very large
exit volume or additional revenue levers.

### 7.2 Revenue levers (Proposed — each needs new code)

| Lever | Where it plugs in | Trade-off |
| --- | --- | --- |
| Platform share of the win commission | `bot/fees.py` + the two payout paths | reduces the creator incentive that brings liquidity |
| Paywall / channel commission | `ledger/_paywall.py` at purchase time | creators can move to a free tool; keep it low |
| Creator-token trade commission | `ledger/_creator.py` at buy/sell | taxes the newest, most fragile economy |
| x402 facilitator commission | `web/x402.py` at verification | must stay below the value of the rail itself |
| Withdrawal tier pricing | `ledger/_withdraw.py` | already implemented at a flat 1%; tiers are new |
| Sponsored gas beyond the free allowance | `bot/paymaster.py` | a natural paid tier, cost is measurable per operation |
| Hosted / white-label deployment | `deploy/` | services revenue, not protocol revenue |

The ordering is deliberate: gas and hosted deployments first (they charge for a cost
already incurred), market and content cuts last (they charge for value the creator
created).

### 7.3 Subscriptions for premium functions (Proposed)

Today `/subscribe` is a **recurring payment to a creator**, not a platform plan.
A platform subscription is new code: a plan row, an expiry, a charge through the
existing recurring executor, and per-user limits read from the plan instead of from
global config. The recurring engine, the idempotency and the ledger already exist —
this is a entitlement layer on top, not a new money path.

The tiers below are grounded in knobs that already exist, so "premium" means a
higher number in a real limit, not a badge.

| Tier | Price (USDC/month) | What it unlocks | Existing knob it raises |
| --- | --- | --- | --- |
| **Free** | 0 | everything in §4, current limits | — |
| **Plus** | 5 | 20 withdrawals/day instead of 5; 25 linked wallets instead of 10; 100 paywall items instead of 50; 10 paid channels instead of 5; priority withdrawal batch | `MAX_WITHDRAWS_PER_DAY`, `MAX_WALLETS_PER_USER`, `PAYWALL_MAX_ITEMS_PER_USER`, `PAYWALL_MAX_CHANNELS_PER_USER` |
| **Pro** | 20 | 1000 answers/day from the assistant instead of a shared 300; larger market subsidy ceiling; 5000-char paywall bodies; agent signal subscription; CSV export of history | `AI_DAILY_ANSWERS`, `MARKET_MAX_SUBSIDY_USDC`, `PAYWALL_MAX_CONTENT_LEN` |
| **Community** | 50 per chat | a whole group on Pro: shared treasury dashboard, market templates, moderation tools, the chat's own solvency page | new entitlement scope (chat, not user) |
| **Agent / API** | usage-based | x402 metered access, higher rate limits, attested agent track record, webhooks | `X402_*`, agent caps |

Pricing is a **proposal**, not a measured willingness to pay. The honest way to set
it: charge for the two things whose cost is already measurable — sponsored gas and
AI answers — and keep every money-moving function free, because taxing tips or
stakes would attack the product's own reason to exist.

Illustrative arithmetic, with the assumption stated: 1,000 paying users at an
average of 8 USDC/month is 8,000 USDC/month. That is a projection from an assumed
subscriber count, not a forecast backed by usage data.

### 7.4 Grants and non-dilutive funding

This project is unusually well matched to Base's funding ladder, because the
criteria are **shipped work on Base**, and the repository already ships it.

| Program | What it is | Amount (as published) | Fit |
| --- | --- | --- | --- |
| **Base Builder Grants** | retroactive, ETH-denominated, operated by the Base team at Coinbase; for live work rather than proposals | typically **1–5 ETH** per recipient | strongest fit: a live bot, a live site, x402 and ERC-4337 already implemented |
| **Base Builder Rewards** | ongoing weekly ETH incentives for builders shipping real products, distributed through Talent Protocol | amount published by the program | fits once mainnet activity exists |
| **Base Batches** | 8-week virtual accelerator for early-stage teams, focused on sectors including trading and payments | **$100,000 investment** | fits after G1; the product is exactly "trading + payments" |
| **Base Ecosystem Fund** | pre-seed and seed investment in on-chain businesses on Base, with Coinbase Ventures | not published | fits after a mainnet launch with real usage |
| **Coinbase Ventures** | growth-stage investment | not published | later stage |
| **x402 Foundation** | neutral open standard for internet-native payments, created by Coinbase with Cloudflare | no grant amount published | Tippy already implements x402 **and** runs on Cloudflare Pages/Workers — a concrete contribution target |
| **CDP builder cohorts** | Coinbase Developer Platform runs periodic grant cohorts (e.g. the AI Builder Program announced **$15K in grants**, applications closed September 2024) | per cohort | watch for an AI-agent or payments cohort; the agent and x402 rails are the pitch |

Amounts and availability change; re-check each program's own page before applying.
The figures above are quoted from the sources listed at the end, not from memory.

**What to put in a Base Builder Grants application** (all of it verifiable in this
repository today):

1. A live Telegram bot with 47 commands and a live web workspace with 47 routes.
2. Two prediction-market engines with integer/Decimal accounting and one shared
   fee definition, plus an implemented ERC-1155 outcome market.
3. **x402 implemented end to end** — invoice, on-chain verification, deduplication
   by transaction hash — on a stack that already runs on Cloudflare.
4. **ERC-4337 gasless onboarding** implemented: account, factory, verifying
   paymaster with chain-id-bound signatures, per-operation and global gas ceilings.
5. An autonomous agent with spend caps, a circuit breaker, stop-loss and EAS
   attestations, which also sells its analysis through the same paywall rail.
6. Published solvency across six liability categories, an append-only audit trail,
   and 1473 + 198 tests including structural gates that fail the build when a
   documented claim stops being true.
7. Three languages, with the Mini App as the primary mobile surface.

**What the application must not claim:** mainnet deployment, user counts, revenue,
an audit by a third party, or a custody model the code does not implement. The demo
runs on Base Sepolia with seeded data; say exactly that.

### 7.5 Projection (assumptions stated, not a forecast)

Let `D` be monthly deposit volume, `w` the share of deposits that eventually exits,
`V` monthly internal volume (tips + stakes + trades + content), `S` monthly
subscription revenue.

- **Today's code:** `revenue = D × w × 0.01`. Nothing else.
- **With the levers in §7.2 at a 0.5% platform cut on internal volume:**
  `revenue = D × w × 0.01 + V × 0.005 + S`.

| Scenario (assumed) | D | w | V | S | Today | With levers |
| --- | --- | --- | --- | --- | --- | --- |
| Small community | 10,000 | 0.5 | 40,000 | 500 | **50** | **750** |
| Several communities | 100,000 | 0.5 | 400,000 | 5,000 | **500** | **7,500** |
| Network scale | 1,000,000 | 0.5 | 4,000,000 | 50,000 | **5,000** | **75,000** |

All figures in USDC per month. Every input is an assumption chosen to show the
shape of the model, not a measurement. The point of the table is the ratio: with
internal volume at 4× deposits, a small cut on internal flow is worth an order of
magnitude more than the exit commission alone.

**Cost side (real, measurable):** sponsored gas beyond the free allowance, RPC,
AI provider spend (the agent's LLM budget alone defaults to 5 USD/day), hosting,
Cloudflare, and maintainer time. A subscription tier priced below the marginal cost
of its own AI answers and gas is a loss, which is why §7.3 charges for exactly those.

### 7.6 Why grants come first

Subscription revenue needs users; users need a mainnet launch; a mainnet launch
needs the G1 safety work and a hosted origin. Grants are the only lever that pays
for that work **before** there are users, and Base's builder grants are explicitly
retroactive — they reward shipped code, which is what this repository already is.
The order is: **grant → G1 safety → mainnet → distribution → subscriptions →
protocol fees.**

## 8. Risks and honest limitations

- **Custody.** The hot wallet is custodial by design. A key compromise is a loss of
  user funds; the mitigations are the multisig-owned vault, the separate relayer
  role and the rolling 24 h spend cap, not a promise that the operator cannot move
  money. The vault's ownership transfer has no enforced delay (§6, G1).
- **Resolution.** Without `ORACLE_PRIVATE_KEY`, on-chain resolution falls back to
  the operator key. That is a trusted resolution path, and the docs are gated to
  keep saying so.
- **Demo reserves are negative.** The seeded demo database shows liabilities above
  reserves. That is an artifact of demonstration data, and it is published rather
  than hidden — but it must never be quoted as the product's financial state.
- **Regulatory.** Prediction markets and paid content are regulated differently per
  jurisdiction; a mainnet launch needs a legal position, not only a technical one.
- **Single maintainer.** The bus factor is one. The gates, the docs and the backup
  scripts exist to make the project operable by someone else.
- **No third-party audit.** The contracts have Forge suites and internal security
  fixes; they have not been audited by an external firm, and this paper does not
  imply otherwise.

## Appendix A — reproducing the numbers

Every internal figure above can be re-measured from a clean checkout:

```bash
# command inventory (47 commands, 7 groups)
python -c "import ast,pathlib,collections;t=ast.parse(pathlib.Path('bot/commands_catalog.py').read_text(encoding='utf-8-sig'));r=[ast.literal_eval(e) for n in ast.walk(t) if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='BOT_COMMAND_SPECS' for e in n.value.elts];print(len(r),dict(collections.Counter(x[2] for x in r)))"

# HTTP routes — every module under web/ that carries them (35 + 12 + 5 + 1)
python -X utf8 -c "import re,pathlib;n=[(p.as_posix(),len(re.findall(r'@(?:app|router)\.(get|post|put|delete)\(',p.read_text(encoding='utf-8-sig')))) for p in sorted(pathlib.Path('web').glob('*.py'))];print([x for x in n if x[1]],sum(c for _,c in n))"

# the only platform revenue line
grep -rn "kind='fee'\|'fee', %s" bot/ledger/

# test counts
python -m pytest -q -m "not production" --collect-only | tail -1
python -m pytest -q -m production --collect-only | tail -1

# lines of code per top-level directory (the sentence below)
python -X utf8 -c "import collections,pathlib,subprocess as s;S={'.py','.sol','.js','.css','.html','.sh','.ps1'};P=[p for p in s.run(['git','ls-files'],capture_output=True,text=True,encoding='utf-8').stdout.split() if pathlib.PurePosixPath(p).suffix in S];c=collections.Counter();[c.update({(p.split('/')[0] if '/' in p else '.'):len(pathlib.Path(p).read_text(encoding='utf-8-sig',errors='replace').splitlines())}) for p in P];print(sum(c.values()),sorted(c.items()))"
```

Lines of code: every file `git ls-files` reports with one of `.py .sol .js .css
.html .sh .ps1`, counted as `len(splitlines())` and grouped by top-level directory
— so the vendored `contracts/lib/` (a `.gitkeep` in git) never enters, and neither
does a scratch file `.gitignore` keeps out of the clone. Measured this way the tree
is **80,100** lines: tests 37,634 · bot 20,675 · web 11,424 · scripts 2,906 ·
contracts 2,649 · agent 1,670 · alembic 1,462 · deploy 1,373 · functions 251 ·
script 56 (the one Foundry deploy script at the repository root).

Live configuration is published by the origin itself: `GET /api/info` (chain,
addresses, fees, limits, feature flags), `GET /api/health` (scanner lag, database),
`GET /api/solvency` (reserves against the six liability categories).

## Sources

External program figures, retrieved 2026-11-13:

- [Base Builder Grants — application portal](https://gitcoin.co/apps/base-builder-grants) — retroactive, ETH-denominated, operated by the Base team at Coinbase, typically 1–5 ETH, for shipped work.
- [Base Ecosystem Fund — Base documentation](https://docs.base.org/get-started/base-ecosystem-fund) — pre-seed and seed, with Coinbase Ventures; focus areas include trading, payments and AI agents.
- [Base Batches — Base documentation](https://docs.base.org/get-started/base-batches.md) — 8-week virtual program, $100,000 investment.
- [Base funding ladder — base.org](https://www.base.org/ecosystem-fund) — Batches → Ecosystem Fund → Coinbase Ventures → Echo → Sonar → Coinbase listing.
- [Launching the x402 Foundation with Coinbase — Cloudflare](https://blog.cloudflare.com/x402/) — Cloudflare partnering with Coinbase to create the x402 Foundation.
- [x402](https://x402.org/) — open, neutral standard for internet-native payments, maintained by the x402 Foundation.
- [Introducing x402 — Coinbase Developer Platform](https://www.coinbase.com/developer-platform/discover/launches/x402) — instant stablecoin payments over HTTP.
- [CDP AI Builder Program — Coinbase](https://www.coinbase.com/de/developer-platform/discover/launches/ai-builder-grants) — $15K in grants, applications closed September 2024 (a past cohort, cited as precedent).
- [Coinbase and Cloudflare Will Launch the x402 Foundation](https://www.coinbase.com/en-gb/blog/coinbase-and-cloudflare-will-launch-x402-foundation) — the standard-setting initiative.

Internal references: [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md),
[DEPLOY.md](DEPLOY.md), [USER_GUIDE.md](USER_GUIDE.md), [OPERATIONS.md](OPERATIONS.md),
[KEY_ROTATION.md](KEY_ROTATION.md), [../README.md](../README.md).