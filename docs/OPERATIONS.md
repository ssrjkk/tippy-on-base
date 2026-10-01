# Operations Guide

Monitoring, backup, and audit procedures for Tippy on Base.

## Monitoring

### Prometheus Metrics

Endpoint: `GET /metrics`

Authentication:
- Set `METRICS_TOKEN` env var for Bearer token auth
- Or set `METRICS_ALLOW_LOOPBACK=1` for loopback-only access (no auth)

Available metrics:
- `tipbot_liabilities_usdc` — Total user liabilities (gauge)
- `tipbot_reserves_usdc` — On-chain reserves (gauge)
- `tipbot_solvent` — Solvency status (1=solvent, 0=insolvent)
- `tipbot_users_total` — Registered users (gauge)
- `tipbot_markets_open` — Open prediction markets (gauge)
- `tipbot_volume_usdc` — Total volume (counter)

Example Prometheus config:
```yaml
scrape_configs:
  - job_name: 'tipbot'
    scrape_interval: 30s
    metrics_path: '/metrics'
    authorization:
      credentials: <METRICS_TOKEN>
    static_configs:
      - targets: ['localhost:8080']
```

### Health Check

Endpoint: `GET /api/health`

Returns:
```json
{
  "status": "ok",
  "hot_wallet": "0x...",
  "chain_head": 12345678,
  "last_scanned_block": 12345670,
  "deposit_lag": 8,
  "db": "ok"
}
```

Status values:
- `ok` — All systems operational
- `degraded` — Database or RPC issues

### Alerting Recommendations

Set up alerts for:
1. **Solvency**: `tipbot_solvent == 0` for 5 minutes → CRITICAL
2. **Deposit lag**: `deposit_lag > 100` blocks → WARNING
3. **Database down**: `db == "down"` → CRITICAL
4. **High error rate**: 5xx responses > 1% → WARNING

## Backup

### Manual Backup

```bash
./scripts/backup_db.sh [backup_dir]
```

Creates `tipbot_YYYYMMDD_HHMMSS.sql.gz` in `backups/` (default).

Retention: Keeps last 7 backups automatically.

### Automated Backup (cron)

Add to crontab:
```bash
# Daily backup at 2 AM
0 2 * * * /path/to/scripts/backup_db.sh /path/to/backups >> /var/log/tipbot-backup.log 2>&1
```

### Restore

```bash
./scripts/restore_db.sh <backup_file>
```

**WARNING**: Restore overwrites the current database. Confirm before proceeding.

After restore:
```bash
alembic upgrade head  # Apply any pending migrations
```

### Backup Verification

Test restore regularly:
```bash
# Create test database
createdb tipbot_test_restore

# Restore to test DB
DATABASE_URL=postgresql://user:pass@localhost:5432/tipbot_test_restore \
  ./scripts/restore_db.sh backups/tipbot_latest.sql.gz

# Verify
psql $DATABASE_URL -c "SELECT count(*) FROM users;"
```

## Audit Logs

### Schema

Table: `audit_logs`

| Column | Type | Description |
|--------|------|-------------|
| id | BIGSERIAL | Primary key |
| created_at | BIGINT | Unix timestamp |
| user_id | BIGINT | Telegram ID (nullable) |
| action | TEXT | Action type |
| resource | TEXT | Resource identifier |
| metadata | JSONB | Additional context |
| ip_address | TEXT | Client IP (nullable) |
| success | BOOLEAN | Whether action succeeded |

### Action Types

- `withdraw` — User withdrawal initiated
- `deposit` — Deposit credited
- `admin_freeze` — Account frozen by admin
- `admin_unfreeze` — Account unfrozen
- `market_create` — Prediction market created
- `market_resolve` — Market resolved

### Query Examples

```sql
-- Last 10 withdrawals
SELECT created_at, user_id, metadata->>'amount_usdc' as amount
FROM audit_logs
WHERE action = 'withdraw'
ORDER BY created_at DESC
LIMIT 10;

-- Failed actions in last hour
SELECT * FROM audit_logs
WHERE success = false
  AND created_at > EXTRACT(EPOCH FROM NOW()) - 3600;

-- User activity
SELECT action, count(*)
FROM audit_logs
WHERE user_id = 123456789
GROUP BY action;
```

### API Access

Add admin endpoint for audit queries (future enhancement):
```python
@app.get('/api/admin/audit')
async def admin_audit(request: Request, action: str = None, limit: int = 100):
    _require_admin(request)
    return await audit.query_logs(action=action, limit=limit)
```

## Incident Response

### Insolvency Alert

1. Check `GET /api/solvency` for details
2. Verify on-chain reserves: `base.vault_balance()` or `base.hot_balance()`
3. Check for large unprocessed withdrawals
4. If reserves are correct, check for accounting bugs in `ledger.total_liabilities()`

### Deposit Scanner Lag

1. Check RPC node status: `base.w3.eth.block_number`
2. Verify RPC endpoint is responsive
3. Check deposit watcher logs: `docker logs tipbot | grep deposit_watcher`
4. Restart bot if lag persists > 100 blocks

### Database Failure

1. Check Postgres status: `systemctl status postgresql`
2. Check connection: `psql $DATABASE_URL -c "SELECT 1;"`
3. If down, restore from backup: `./scripts/restore_db.sh backups/tipbot_latest.sql.gz`
4. Apply migrations: `alembic upgrade head`

## Security

### Secret Rotation

1. Rotate `HOT_WALLET_KEY`:
   - Generate new key: `python -c "from eth_account import Account; print(Account.create().key.hex())"`
   - Transfer funds from old to new wallet
   - Update `.env` and restart bot

2. Rotate `WALLET_ENC_KEY`:
   - Run: `python scripts/reencrypt_wallets.py`
   - Update `.env` and restart bot

3. Rotate `SECRET_KEY`:
   - Update `.env` and restart bot
   - **Note**: Invalidates all active sessions

### Audit Log Review

Weekly review checklist:
- [ ] Check for failed withdrawal attempts (possible attack)
- [ ] Verify no unauthorized admin actions
- [ ] Review large transactions (> $1000 USDC)
- [ ] Check for unusual patterns (rapid withdrawals, new user activity)

## Performance

### Database Optimization

```sql
-- Check table sizes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_all_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(relid) DESC;

-- Analyze slow queries
EXPLAIN ANALYZE SELECT * FROM tx_log WHERE tg_id = 123456;

-- Vacuum after large deletes
VACUUM ANALYZE audit_logs;
```

### Rate Limiting

Environment variables:
- `WEB_RATE_LIMIT` — Max requests per window (default: 60)
- `WEB_RATE_WINDOW` — Window size in seconds (default: 60)
- `WEB_RATE_MAX_CLIENTS` — Max tracked clients (default: 10000)

Adjust based on traffic patterns. Monitor 429 responses in metrics.
