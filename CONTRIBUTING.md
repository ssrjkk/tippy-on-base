# Contributing

Tippy is a community economy in USDC on Base: tips, prediction markets,
paywalls and an AI assistant, served by one Telegram bot, one web app and one
agent process that share one database and one ledger.

The full guide - local setup, code style, the money-conservation rule and how a
pull request is reviewed - is [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## Before you open a pull request

These are the two commands CI runs. Both have to be green: the lint gate reads
every directory the repository ships code in, and the suite is the one without
the `production` marker.

```bash
python -m pytest -q -m "not production"
python -m ruff check bot web tests deploy agent scripts alembic
```

The suite needs a PostgreSQL 16 answering on `localhost:5432` with the `tipbot`
role: `tests/conftest.py` drops and recreates `tipbot_test` once per session, so
the dev database is never touched. The `production`-marked tests check the
deployed site and belong to the `Prod Health Check` workflow - a local run that
includes them fails for a reason this checkout did not cause.

`docker compose` starts the deployable stack, not a development database: the
compose files publish no ports, so its PostgreSQL answers only to the `app`
service on the compose network.

## Reporting a security problem

Open a private channel instead of an issue - the routes and the timelines are
in [SECURITY.md](SECURITY.md).
