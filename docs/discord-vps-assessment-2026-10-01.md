# Discord follower: read-only VPS assessment

Date: 2026-10-01. Source inspected read-only:
`G:/ai-workspace/Discord Bot`.
No source changes, application imports, startup, account requests or trades were
performed. Instructions read: `AGENTS.md` and its referenced `CLAUDE.md`.

## Actual runtime contract

- Python entry point: `python bot.py` from the project directory. Source uses
  Python 3.10+ type syntax. A specific Linux interpreter/dependency combination
  has not been tested. Requirements are lower bounds rather than a lockfile:
  blofin, python-dotenv, openai, websocket-client, requests, flask.
- `run_bot.bat` is Windows-specific. It launches Chrome with CDP port 9222,
  persistent profile `C:/chrome-cdp-profile`, and a Discord tab. It also kills
  existing Chrome processes; it is not a suitable VPS launcher.
- `discord_reader.py:24` hardcodes `http://localhost:9222`. The reader uses direct
  HTTP/WebSocket CDP and injected page observers. It needs an authenticated
  Discord browser session and accessible notification inbox, not a Discord
  bot-token gateway. CLAUDE.md's agent-browser reference is stale here.
- No Dockerfile or Compose file was found at the source project root. A Linux
  browser/login/profile workflow and service restart behavior remain untested.
  Keep CDP private. A separate browser container cannot simply be addressed by
  service name while the current hardcoded localhost remains in use.

## Execution and isolation

- This project executes through `blofin_client.py`; it has no inspected Pacifica
  adapter or exchange selector. Bot3's Pacifica selection does not migrate it.
- `exec_mode.py` resolves PAPER when `PAPER_MODE=true`; otherwise the demo Blofin
  URL means DEMO and the live URL means LIVE. DEMO submits exchange demo orders.
  `DRY_RUN` is a derived reporting flag and does not disable orders.
- The inspected local configuration selected the DEMO endpoint, had PAPER_MODE
  unset, and DRY_RUN=false. Do not copy it into an automatically started service.
  RSI and OracleAlgo strategy-enable flags were true. No values of credentials
  or private URLs are included in this assessment.
- `bot.main()` initializes storage, freezes execution mode, starts dashboard and
  market connections, reconciles positions, and processes signals. There is no
  separate manual 'start trading' step to rely upon after launching it.
- Keep the two applications' environment files, databases, process identities,
  and browser profiles separate. Exchange-account ownership is recorded by this
  follower, but that does not coordinate it with an independent trading bot.
  If both later use the same venue, use separate accounts/subaccounts with
  independently scoped credentials and risk budgets where supported.

## Persistence and monitoring

- `logger.py`, `position_tracker.py`, `accounts.py`, and `dashboard.py` locate
  `bot.db` beside the source. CSV/log files are also beside source; persist the
  application state directory and SQLite journal/WAL sidecars, not just a single
  database file mount. Keep backups separate from Bot3's database.
- Preserve seen-message IDs, entry reservations, resting orders and protection
  repair state when migrating; starting with empty state is not equivalent to
  a safe restart of an existing trading instance.
- Observed sizes: bot.db about 2.1 MiB, current bot.log about 2 MiB,
  signals_log.csv about 1.4 MiB. These are current files, not growth estimates.
  Logs rotate; CSV and database retention need a deployment policy.
- Flask dashboard is started in-process on port 5050, loopback by default.
  `/api/status` is a status surface; no dedicated health endpoint was identified.
  Non-loopback `DASHBOARD_HOST` requires `DASHBOARD_PASSWORD`. The inspected
  configuration had no dashboard password. Some routes are intentionally public,
  so keep the whole listener private rather than assuming global Basic Auth.
- `watchdog.py` is a separate monitor; its state/log files also need their own
  writable location. It can send alerts, so it was not executed during review.

## Capacity and remaining work

The Python services are small compared with the browser and model workloads.
There is no measured RAM/CPU baseline from this review. Budget browser memory
separately and measure both applications in paper mode before sizing tightly.

`signal_parser.py` uses an OpenAI-compatible model server (default LM Studio at
loopback port 1234) for fallback parsing and optional vision. Local configuration
has a model-server URL, text model and vision model configured. The model service
is not in requirements.txt and must be placed explicitly; do not assume it fits
on a small shared VPS or that desktop loopback works remotely. An external model
service, secure desktop connection, or a tested reduced-function configuration
needs a deliberate choice. CLAUDE.md's Anthropic fallback description is stale.

Before runtime deployment: prepare/test Linux browser session initialization and
recovery, persistent paths, model access, private dashboard access, separate
credentials and paper-mode guards. A future Pacifica migration for this follower
is separate adapter work. Inert staging artifacts can be prepared now; this
assessment does not certify unattended operation or authorize service startup.
