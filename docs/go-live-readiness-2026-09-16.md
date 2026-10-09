# Pacifica deployment readiness - 2026-09-16

## Status

Updated requirement: Bot3 has not completed operational paper testing and must
remain in paper testing for at least one month from the actual continuous
testing start. No start is recorded. Offline tests and backtests do not count.
Keep strategies stopped until a non-live forward-testing mode is configured and
verified. The supplied testnet setting is not a simulated-paper mode; the current
service has no wired continuous paper simulator. See the current
[release checklist](../deploy/vps/RELEASE_CHECKLIST.md) for the required record and
separate user approval after review. Elapsed time never authorizes live trading.

This is an offline-tested deployment candidate, not a live deployment approval. No VPS was provisioned, image started, authenticated exchange request sent, or trading enabled during this work. Actual account and network verification remain required.

## Completed preparation

- Added Pacifica native stop support and offline wire/adapter tests. A true capability flag means the adapter implements the interface; it does not certify exchange acceptance.
- Standalone stop attempts reserve a durable client ID in SQLite before submission. An unresolved attempt remains blocked across adapter/process restarts until the exact ID is observed at the venue; an empty listing does not authorize replay of an unresolved request. This journal shares the persistent database and its backup. Unsupported/transient/unavailable journal storage refuses submission.
- Prepared the standalone `deploy/vps/compose.yaml` candidate with Pacifica testnet, private loopback dashboard publishing, external credentials, non-root image user, persistent SQLite/log volumes, and an environment-configurable persistent supervisor-pause file.
- Included the dashboard HTML and the SQLite backup helper in the image recipe. Default-deny `.dockerignore` excludes workstation environment files, databases, logs, keys, and historical data from the intended build context.
- Added a SQLite online-backup/offline-restore helper. Existing destinations are protected from accidental overwrite, replacement preserves a prior snapshot, and destination WAL/SHM files prevent unsafe restore.
- Preserved the legacy supervisor-pause location by default. Deployment can place it on persistent storage. Invalid or unreadable state pauses new entries rather than silently permitting them.
- Added a deployment runbook covering reserved outbound address verification, private dashboard access, backup/restore, rollback, and testnet checks. See `deploy/vps/README.md`.

## Offline validation

The final combined run passed **313 tests in 3.18 seconds**, with four existing FastAPI `on_event` deprecation warnings. This includes 14 deployment/persistence regressions and the final journal hardening checks: blank/transient storage refuses submission, and a changed trigger cannot bypass an unresolved attempt.

The combined scope includes Pacifica client/stops, exchange abstraction, venue protection, stop repair, fill accounting, reduce-only exits, reconciliation, WebSocket authority, API access controls, backup scheduling, and deployment recovery. Tests use mocked exchange clients and disposable SQLite databases. A socket guard rejects network connections, with a narrowly scoped exception for Windows asyncio's internal socketpair creation. No real API server lifespan or exchange-connected bot is started; the startup regression substitutes a mocked integration object.

Deployment regressions verify committed WAL data survives backup and restore, overwrite refusal, preserved prior snapshots, corrupt-source rejection, WAL/SHM destination refusal, default pause-path compatibility, pause persistence across isolated module reloads, damaged/unreadable pause state, and startup leaving the strategy loop stopped.

Changed deployment and fallback test files pass Ruff lint. Native-stop tests cover documented request shapes, conservative tick rounding, stop-vs-profit filtering, full-position coverage checks, unknown acknowledgments, durable restart ambiguity, stop-preserving cancellation, and replacement verification before old-stop cancellation. A review identified the restart-ambiguity gap during implementation; the SQLite intent journal was added before final validation.

The test selection was:

```text
test_deployment_readiness.py  test_position_reconciler.py
test_backup_scheduler.py     test_api_access_control.py
test_fill_accounting.py      test_stop_repair.py
test_exit_reduce_only.py     test_websocket_authority.py
test_venue_stops.py           test_pacifica_stops.py
test_pacifica_client.py       test_exchange_abstraction.py
```

All paths are under `trading_bot_v2/tests`. The isolated run set `DOTENV_OVERRIDE=false`, `DATA_AUTODOWNLOAD=false`, `DATABASE_BACKEND=sqlite`, a disposable database path, and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; pytest used `-q -p no:cacheprovider --tb=short` and a dedicated temporary base directory. The in-process network guard described above remained active throughout.

The deployment specialist validated Compose configuration using the example environment file. Docker's daemon was unavailable, so no image build or container runtime verification was possible. Static configuration and mocked lifecycle checks are not substitutes for the host checks below.

## Still required before unattended operation

1. Select an authorized host/account and obtain access. No paid server or reserved address has been created here.
2. Build the image on the intended Linux host and record its digest. Verify mounted-file ownership, SQLite/log/backup persistence, and pause-state persistence across container replacement and host reboot.
3. Confirm private dashboard access from an authorized device, API authentication, and denial from outside the private network. A green HTTP health response proves liveness only.
4. Verify the bot container's actual outbound source address and whitelist the intended Pacifica testnet agent key. A retained IPv4 does not establish IPv6 routing or exchange eligibility.
5. Supply valid testnet credentials and verify account reads, order/fill/cancel behavior, attached and standalone stops, stop listing, amendment/cancellation, partial coverage, ambiguous-response reconciliation, and restart recovery on the actual venue.
6. Complete a restore drill with the service stopped, reconcile the exchange before resuming, and establish an off-server backup destination/retention policy. Restoring a database never reverses exchange orders or positions.
7. Approve a specific strategy/risk configuration using regenerated results, then conduct bounded testnet operation before considering mainnet.

The supplied candidate remains testnet. Starting the API leaves the strategy loop stopped; `ENABLE_AUTO_TRADING=false` alone is not an independently enforced trading kill switch. An authenticated Start action can initiate testnet trading. Restart supervision restarts the service, not an approved trading strategy.

Keep unresolved stop-attempt records intact. Operator recovery must establish venue state before clearing an ambiguity; deleting the journal to permit retries defeats its protection. The new protection journal currently requires persistent SQLite, matching this deployment candidate; a PostgreSQL deployment would need its own durable journal implementation first.

A VPS used only as an outbound exit node would leave computation and local stop handling dependent on the Windows desktop. Moving the bot itself to a supervised host addresses that separate availability dependency; neither architecture has been deployed by this work.

## Backtesting evidence and limits

The earlier audit and historical reruns are documented in `docs/backtesting-audit-2026-09-16.md` and `docs/backtesting-rerun-2026-09-16.md`. The latter records 256 focused tests, eight paired historical cases, 281 repaired-run fills, and 284,544 completed-candle visibility checks. Those results support bounded replay/accounting correctness, not profitability or live execution parity. Older optimization rankings require regeneration.

OHLC path ambiguity, funding proxies, funding allocation to individual closes, capped-order slippage approximation, and liquidation/liquidity modeling remain limitations. No full code-coverage measurement or complete optimization campaign was performed. Existing changes are uncommitted; capture a reviewed release before deploying them.
