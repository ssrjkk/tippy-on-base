# Tippy — User Guide

Tippy is a Telegram bot for money and markets on **Base**: instant USDC tips,
prediction markets, parimutuel bets, creator tokens — all from a chat window.

**Open the bot: [@tippy_on_base_bot](https://t.me/tippy_on_base_bot)**

---

## Getting started

1. Open the bot in Telegram and press **Start** (or send `/start`).
2. Pick your language — the whole interface speaks English, Russian and Chinese.
3. Send `/menu` any time to get the main menu back, `/help` for the command list.

You don't need a wallet, seed phrase or gas to begin — your account works
right away inside Telegram. When you're ready to move funds on-chain, see
*Deposits & withdrawals* below.

---

## Money: deposits, balance, withdrawals

Everything in Tippy is denominated in **USDC** (1 USDC ≈ $1) on Base.

| Command | What it does |
|---|---|
| `/balance` | Show your in-bot balance |
| `/deposit` | Get a deposit address (private chat only) |
| `/claim <tx>` | Credit a deposit after it's confirmed on Base |
| `/link` | Link an external wallet to your account |
| `/confirm` | Confirm a wallet link from the other side |
| `/withdraw <addr> <amt>` | Withdraw USDC to any Base address |
| `/tx <hash>` | Decode any Base transaction in plain language |

**How to deposit:** send `/deposit` in a private chat with the bot, transfer
USDC on Base to the address it shows, then send `/claim <transaction hash>`.
Deposits are credited after a short confirmation window on Base.

**Withdrawals** go straight on-chain from the bot's vault. There is a 1% fee,
a 1 USDC minimum, and a limit of 5 withdrawals per day — the bot will tell you
exactly where you stand.

`/wallet` shows your linked addresses; `/import` and `/export` move a wallet
into and out of the bot in private chat.

---

## Tipping

| Command | What it does |
|---|---|
| `/tip 5 @nick` | Send someone 5 USDC |
| `/tip 5` (as a reply) | Tip the author of the message you're replying to |
| `/rain 20` | Split 20 USDC across recent active chatters |
| `/top` | Leaderboard of the biggest tippers |
| `/history` | Your recent transactions |

Tips land instantly and cost you nothing in gas — it's a normal Telegram
message. Anyone can tip anyone in any group where the bot is present.

---

## Prediction markets (AMM)

Anyone can open a market on any question; everyone trades against a built-in
AMM, so odds move live with every purchase.

| Command | What it does |
|---|---|
| `/market create 50 Will X win? \| Yes \| No [24h]` | Create a market (50 = starting bank in USDC) |
| `/markets` | Browse open markets |
| `/trade` | Buy YES/NO shares at live odds |
| `/sell` | Sell shares back |
| `/positions` | Your open positions and P&L |

The optional number at the end of `/market create` is the market duration in
hours (default 24). When the deadline hits, the market creator resolves it and
winning shares pay out.

---

## On-chain markets (ERC-1155)

For markets that live fully on Base — shares are real ERC-1155 tokens you can
hold in any wallet, and the outcome is enforced by the smart contract, not the
bot.

| Command | What it does |
|---|---|
| `/oc_create Q \| A \| B [hours]` | Create an on-chain market |
| `/oc` / `/oc_buy` / `/oc_sell` | Browse and trade outcome tokens |
| `/oc_pos` | Your token positions |
| `/oc_redeem` | Redeem winning tokens after resolution |
| `/oc_resolve` | Resolve a market you created |

---

## Parimutuel bets

A simple poll-style bet: everyone picks a side, the pot is split among the
winners proportionally to their stake.

| Command | What it does |
|---|---|
| `/bet create Will it work? \| Yes \| No [24h]` | Create a bet poll |
| `/bets` · `/mybets` | Browse open bets · your bets |
| `/resolve <id> <side>` | Creator resolves the bet |
| `/cancel <id>` | Creator cancels (stake returned) |

---

## Creator tokens & dividends

Launch a token on yourself; buyers hold shares and earn dividends from your
revenue.

| Command | What it does |
|---|---|
| `/createtoken` | Launch your own creator token |
| `/buytoken` | Buy a creator's token |
| `/claim <token_id>` | Claim accumulated dividends |
| `/credit` | Check your on-chain credit score |

---

## Smart wallet, gasless & basenames

- **`/gasless`** — get a smart wallet sponsored by the paymaster: your first
  on-chain actions cost you zero gas.
- **`/basename`** — register a `you.base.eth` name and use it as your address.
- **`/app`** — open the Tippy Mini App: charts, markets and your portfolio in
  a native-feeling window.

---

## Subscriptions & extras

| Command | What it does |
|---|---|
| `/subscribe <id>` · `/subscriptions` · `/cancelsub` | Recurring payments to a creator |
| `/paywall` | Paid channels and content |
| `/donate` | Personal donation page with QR |
| `/ask <question>` | Ask the AI assistant |
| `/agent` | Autonomous agent mode |
| `/settings` · `/language` | Preferences |
| `/stats` | Platform stats |

---

## Safety notes

- Commands that touch money (`/deposit`, `/withdraw`, `/claim`, `/link`,
  `/confirm`, `/import`, `/export`) only work in a **private chat** with the
  bot — the bot will never ask for them in a group.
- The bot will **never** DM you first asking for a seed phrase, private key,
  or a "verification" payment. Anyone doing that is a scammer.
- On-chain actions (withdrawals, on-chain markets) are visible on Base and
  verifiable by anyone — `/tx <hash>` decodes any of them.

## Questions

Ping the maintainers: [@ssrjkk](https://t.me/ssrjkk) ·
[@b2wmain](https://t.me/b2wmain)
