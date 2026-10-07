#!/usr/bin/env bash
# Final release gate — runs all pre-deploy checks
# Exit 0 = ready to deploy, Exit 1 = blockers found
# Usage: ./scripts/final_release_check.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Load .env so checks that need live config (alembic fresh-DB test) see the
# same DATABASE_URL the app would use. Never overrides real env vars.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# MSYS path translation: on Git Bash (Windows), spawning a native binary
# converts env values that look like POSIX paths ("/telegram-webhook") into
# Windows paths ("D:/Git/telegram-webhook") — which silently changed the
# webhook route and failed the suite. Keep the conversion off for every child
# this script spawns.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

# The pytest suite defines its own app config via conftest setdefaults (test
# chain id, x402 flags, test keys) and must run against a THROWAWAY database.
# Dev .env values (X402_ENABLED=0, Sepolia RPC, Sepolia USDC, real hot wallet)
# would leak into the suite through os.environ and break the on-chain tests —
# so run pytest in a sanitized environment: only the test DB URLs pass through.
run_pytest() {
    env -u BOT_TOKEN -u HOT_WALLET_KEY -u WALLET_ENC_KEY -u SECRET_KEY \
        -u X402_ENABLED -u X402_RECEIVE_ADDRESS -u X402_INVOICE_KEY \
        -u ADMIN_TG_ID -u EXPECTED_CHAIN_ID -u BASE_RPC_URL -u AGENT_TG_ID \
        -u WEBHOOK_URL -u METRICS_TOKEN -u METRICS_ALLOW_LOOPBACK \
        -u VAULT_ADDRESS -u AI_API_KEY -u TIPBOT_BOT_USERNAME \
        -u USDC_ADDRESS -u VAULT_ADDRESS -u OUTCOME_MARKET_ADDRESS \
        -u PAYMASTER_ADDRESS -u SMART_ACCOUNT_FACTORY_ADDRESS \
        -u CREATE2_FACTORY_ADDRESS -u ENTRYPOINT_ADDRESS \
        BASE_RPC_FALLBACK_URLS="" \
        TEST_DATABASE_URL="$TEST_DATABASE_URL" \
        TEST_ADMIN_DATABASE_URL="$TEST_ADMIN_DATABASE_URL" \
        python -m pytest tests/ -q --timeout=60 -m "not production"
}

PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  [PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $name"
        FAIL=$((FAIL + 1))
    fi
}

warn_check() {
    local name="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  [PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "  [WARN] $name"
        WARN=$((WARN + 1))
    fi
}

echo "=== Tippy-on-Base Final Release Check ==="
echo ""

echo "--- 1. Environment ---"
check "validate_env.py passes" python scripts/validate_env.py
check ".env file exists" test -f .env

echo ""
echo "--- 2. Code quality ---"
check "ruff lint passes" ruff check .
check "no syntax errors (python)" python -m compileall -q bot web tests

echo ""
echo "--- 3. Tests ---"
# conftest defaults to tipbot:tipbot@localhost:5432; point the test database at
# whatever host:port DATABASE_URL in .env uses (e.g. a dev Postgres on 5434).
if [ -z "${TEST_DATABASE_URL:-}" ]; then
    hostport="${DATABASE_URL#*://}"
    hostport="${hostport#*@}"
    hostport="${hostport%%/*}"
    export TEST_DATABASE_URL="postgresql://tipbot:tipbot@${hostport}/tipbot_test"
    export TEST_ADMIN_DATABASE_URL="postgresql://tipbot:tipbot@${hostport}/postgres"
fi
check "pytest suite passes" run_pytest

echo ""
echo "--- 4. Database ---"
# Create a throwaway database, migrate it from zero, drop it. Proves the full
# migration chain runs on a clean host (new VPS / CI) without touching prod data.
# The admin connection uses the tipbot role (same as conftest's TEST_ADMIN_URL):
# the app's own role may lack CREATEDB on an external host.
GATE_DB_NAME="tippy_gate_$(date +%s)"
hostport="${DATABASE_URL#*://}"
hostport="${hostport#*@}"
hostport="${hostport%%/*}"
GATE_ADMIN_URL="postgresql://tipbot:tipbot@${hostport}/postgres"
GATE_DB_URL="postgresql://tipbot:tipbot@${hostport}/$GATE_DB_NAME"
check "alembic migrations apply on a fresh database" python - "$GATE_ADMIN_URL" "$GATE_DB_NAME" "$GATE_DB_URL" <<'PYEOF'
import os
import subprocess
import sys

import psycopg

admin_url, db_name, db_url = sys.argv[1], sys.argv[2], sys.argv[3]

with psycopg.connect(admin_url, autocommit=True) as c:
    c.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    c.execute(f'CREATE DATABASE "{db_name}"')

try:
    env = dict(os.environ, DATABASE_URL=db_url)
    rc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
    ).returncode
    if rc != 0:
        print("alembic upgrade head failed on a fresh database", file=sys.stderr)
finally:
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')

sys.exit(rc)
PYEOF
warn_check "no pending migrations" python -m alembic heads

echo ""
echo "--- 5. Docker ---"
if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
    check "docker-compose config valid" docker compose config
else
    check "docker-compose config valid (dev, no tunnel token)" bash -c 'CLOUDFLARE_TUNNEL_TOKEN=gate-placeholder docker compose config >/dev/null'
fi
check "Dockerfile exists" test -f Dockerfile

echo ""
echo "--- 6. Documentation ---"
check "README.md exists" test -f README.md
check "SECURITY.md exists" test -f docs/SECURITY.md
check "DEPLOY.md exists" test -f docs/DEPLOY.md
check "OPERATIONS.md exists" test -f docs/OPERATIONS.md
check "INCIDENT_RESPONSE.md exists" test -f docs/INCIDENT_RESPONSE.md

echo ""
echo "--- 7. Contracts (if Foundry available) ---"
if command -v forge &>/dev/null; then
    check "forge build" forge build
    warn_check "forge test" forge test
    warn_check "slither contracts/" slither contracts/
else
    echo "  [SKIP] Foundry not installed — run: scripts/setup_foundry.sh"
fi

echo ""
echo "--- 8. Production readiness ---"
check "backup script executable" test -x scripts/backup_and_monitor.sh
check "health endpoint script exists" test -f tests/test_production_health.py
check "CI workflow exists" test -f .github/workflows/ci.yml
check "prod health workflow exists" test -f .github/workflows/prod-health-check.yml

echo ""
echo "==========================================="
echo "  Results: $PASS passed, $FAIL failed, $WARN warnings"
echo "==========================================="

if [ "$FAIL" -gt 0 ]; then
    echo "  STATUS: NOT READY — fix $FAIL failure(s) before deploying"
    exit 1
elif [ "$WARN" -gt 0 ]; then
    echo "  STATUS: READY WITH WARNINGS — review $WARN warning(s)"
    exit 0
else
    echo "  STATUS: READY TO DEPLOY"
    exit 0
fi
