# Incident Response Plan

## Severity levels

| Level | Definition | Response time | Examples |
|-------|-----------|---------------|----------|
| P1 — Critical | Money at risk, data loss, or complete outage | Immediate | Private key compromise, USDC drain, DB corruption |
| P2 — High | Major feature broken, partial outage | < 1 hour | Deposit scanner stuck, bot unresponsive, API errors > 50% |
| P3 — Medium | Degraded but functional | < 4 hours | Single endpoint failing, slow queries, stale chain head |
| P4 — Low | Cosmetic, non-blocking | Next business day | UI glitch, wrong label, minor log noise |

## P1 — Immediate actions

### Private key compromise (hot wallet)

1. **Stop the bot**: `docker compose stop app`
2. **Revoke**: transfer remaining funds from hot wallet to cold via a fresh key
3. **Rotate**: generate new `HOT_WALLET_KEY`, update `.env`, restart
4. **Audit**: `SELECT * FROM tx_log WHERE kind = 'withdraw' AND created_at > <compromise_time>;`
5. **Notify**: affected users if funds were lost

### USDC drain / smart contract exploit

1. **Stop**: `docker compose stop app`
2. **Check on-chain**: verify vault balance on [BaseScan](https://basescan.org/address/$VAULT_ADDRESS)
3. **If vault has `pause()`**: call it from the multisig
4. **Forensics**: export tx logs, audit_logs, onchain_trades for the last 24h
5. **Report**: file at [Base security contact](https://www.base.org/security)

### Database corruption

1. **Stop app**: `docker compose stop app`
2. **Assess**: `docker compose exec db pg_isready -U tippy`
3. **Restore from backup**: `scripts/restore_db.sh <backup_file>`
4. **Verify**: check user balances sum, tx_log count, last_block value
5. **Restart**: `docker compose up -d`

### Full outage (VPS down)

1. **Check**: `ssh <vps> "docker compose ps"`
2. **Restart**: `ssh <vps> "docker compose up -d"`
3. **Check tunnel**: `ssh <vps> "docker compose logs cloudflared --tail 50"`
4. **Verify**: `curl -sf https://tippy-egi.pages.dev/api/health`

## P2 — Response procedures

### Deposit scanner stuck (chain_head - last_scanned_block > 100)

1. Check health: `curl https://tippy-egi.pages.dev/api/health`
2. Check RPC: `curl $BASE_RPC_URL -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'`
3. If RPC is down: switch to backup RPC in `.env`, restart app
4. If RPC is fine: check logs `docker compose logs app --tail 200 | grep -i "scan\|block\|rpc"`

### Bot unresponsive

1. Check webhook: `curl https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo`
2. Check logs: `docker compose logs app --tail 100`
3. Restart: `docker compose restart app`
4. If webhook URL wrong: `curl https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=$WEBHOOK_URL`

## Communication

- **Internal**: log incident in this section after resolution
- **Users**: Telegram channel post if downtime > 30 min or funds affected
- **Post-mortem**: update OPERATIONS.md with root cause and prevention

## Incident log

| Date | Severity | Summary | Root cause | Resolution | Duration |
|------|----------|---------|------------|------------|----------|
| (none yet) | | | | | |
