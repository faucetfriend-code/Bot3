# Trading Bot System Review And Cleanup Report

Date: 2026-05-09

## Scope

Reviewed the main runtime path around `run_bot.bat`, `trading_bot_v2/api_server.py`,
`trading_bot_v2/trading_bot.py`, configuration, database access, WebSocket ownership,
signal execution, and the current project-root layout.

## Runtime Findings

1. Critical: unauthenticated API control surface.
   `api_server.py` exposes bot start/stop, cancel-all-orders, grid clearing,
   position sync, and live debug endpoints without an auth dependency. The app
   binds to `0.0.0.0`, so the API should be treated as unsafe if port 8000 is
   reachable outside the local machine.

2. Critical: debug GET endpoints can trigger trading side effects.
   `/api/debug/test-order` calls `client.place_order(...)`. Several signal-debug
   routes publish `SIGNAL_GENERATED` events, which are subscribed by the live bot
   and can execute through `_coordinate_signal_execution`.

3. High: standalone `TradingBot()` initialization is broken.
   `TradingBot` references `config.agent_wallet_private_key` and
   `config.account_public_key`, while `Config` defines `pacifica_private_key` and
   `pacifica_public_key`. API startup avoids this by injecting a Pacifica client,
   but direct construction and tests fail.

4. High: SQLite connection pooling has thread-safety risk.
   The pool leaves thread-local connections attached after release and can mark
   a connection free while the same thread can still reuse it directly. With
   FastAPI and the bot thread both active, this can cause intermittent locking or
   cross-thread reuse problems.

5. Medium: duplicate `/api/grids` route.
   `api_server.py` registers two `GET /api/grids` handlers. FastAPI keeps both
   routes, making behavior order-dependent and confusing to maintain.

6. Medium: shared WebSocket client has conflicting lifecycle owners.
   `BotIntegration.initialize()` starts the shared WebSocket client for the UI,
   while `TradingBot.start()` starts it again and `TradingBot.stop()` stops it.
   Stopping trading can therefore stop interface market data.

7. Medium: packaging metadata is stale.
   `pyproject.toml` only includes `core_logic*`, not `trading_bot_v2*`, so an
   installed package may omit the main bot code.

## Verification Performed

- `python -m compileall -q trading_bot_v2`
  - Completed without syntax errors.
  - Reported it could not list `trading_bot_v2\.pytest_cache`.

- `pytest trading_bot_v2\test_trading_bot.py -q`
  - Failed 10/10.
  - Failures include the real config attribute mismatch plus stale expectations
    from older `TradingBot` APIs.

## Cleanup Decisions

The project root had a mix of launch files, active runtime files, generated
backtest reports, one-off analysis scripts, and planning notes.

Kept in root because current code expects them there:

- `.env`
- `interface.html`
- `run_bot.bat`
- `trading_bot.db` because `.env` sets `DATABASE_PATH=trading_bot.db`
- `signals_log.csv` because `SignalLogger` writes to the project root
- Python/Node project metadata files

Moved non-runtime clutter into named folders:

- Root backtest HTML reports moved to `backtesting/reports/html/`
- Root one-off analysis/sweep scripts moved to `scripts/analysis/`
- Large planning/update note moved to `docs/planning/`

No files were deleted.

## Recommended Next Fixes

1. Add API authentication/authorization before exposing the server beyond localhost.
2. Disable or guard live debug endpoints in production, especially order and signal
   trigger routes.
3. Fix `TradingBot` config attribute names and update the stale tests.
4. Refactor database connection handling to avoid sharing SQLite connections across
   threads.
5. Remove the duplicate `/api/grids` route and keep one authoritative implementation.
6. Make API/server own the WebSocket client lifecycle, with the bot only consuming it.
7. Update packaging metadata to include `trading_bot_v2*`.
