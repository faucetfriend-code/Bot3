# VPS handoff - October 1, 2026

## Current result

**Bot3 is not operationally tested or approved for live trading.** It must
remain in paper testing for at least one month beginning with actual continuous
paper operation, not backtests, preparation or deployment. That start has not
been recorded. Keep its strategy stopped until a non-live forward-testing mode
is configured and verified. The supplied deployment hardcodes `TESTNET=true`;
exchange testnet orders are not simulated paper fills, and this service has no
wired continuous simulated-paper mode. Record the chosen method and observation
period in the release checklist. Review failures and interruptions, extend the
period where needed, and require a separate explicit user decision before any
live change. Time passing never activates live trading. The Discord follower's
prior Blofin testing does not count toward Bot3's requirement.

Bot3 is configured with Pacifica as its primary exchange and remains on testnet.
The separate Discord follower now selects Pacifica in **paper mode only**.
Its runtime rejects attempts to disable paper mode or select Pacifica mainnet;
the exchange facade also refuses Pacifica account mutations. Its new order,
stop and recovery backend has been exercised with mocks, not an exchange.

These are local deployment candidates. No VPS was purchased or configured,
Docker image built, service launched, browser session opened, account authenticated,
or trade submitted during this preparation. The Docker daemon was unavailable.

## Offline verification

| Scope | Result | What it establishes |
| --- | --- | --- |
| Bot3 deployment, Pacifica, defaults, reconciliation, accounting and access-control suites | 359 passed in 3.25 seconds | Scoped local regression checks; four existing FastAPI lifecycle deprecation warnings |
| Follower deployment wrapper | 16 passed in 0.44 seconds | Rejects unsafe flags, missing model/session settings, source or ancestor `.env`, and public dashboard binding; blocked startup never executes the bot |
| Follower backend, mode selection and historical identity | 46 passed in 0.40 seconds | Mocked contract/failure paths, frozen selection, paper guards and historical Blofin ownership retained |
| Existing follower identity, resting orders, risk/reservations, exchange failures and dashboard authentication | 160 passed in 9.64 seconds | Compatibility of the shared execution and reporting paths with the existing offline fixtures |

Follower verification blocked external socket connections, disabled local dotenv
loading, redirected logs to temporary storage and prevented SQLite access to the
real sibling database. Tests used isolated fixtures. The two projects were
validated in separate Python runs: an exploratory mixed-project run exposed
test monkeypatch/import interference and is not a supported shared runtime.

Review corrected replacement-stop coverage checks, including the unchanged-price
path. Pacifica entries wait for exact order evidence instead of assuming fills.
Entry request identities use Discord message IDs and survive pending queues;
an absent request identity refuses submission. Ambiguous requests retain a
durable journal and cannot be blindly replayed. These safeguards are offline
findings, not proof of behavior against a funded account.

## Keep the services separate

- **Bot3:** this repository; Docker service; its own Pacifica account and secret
  environment; persistent SQLite database, pause state, logs and backups.
  Dashboard published only on `127.0.0.1:8000`.
- **Discord follower:** `G:/ai-workspace/Discord Bot`; dedicated
  Linux user, Python environment and systemd services; separate account/config,
  `bot.db`, `pacifica_requests.db`, browser profile and backups. Dashboard on
  `127.0.0.1:5050`; Chrome debugging on `127.0.0.1:9222`.
- Do not share exchange accounts, databases, credentials or Python environments.
  A VPS used only as an outbound network relay does not move either application
  off the desktop. Always-on operation requires deploying the applications too.

## Remaining acceptance steps

1. Choose the host, retained outbound IP and private administration access.
   Size the host after deciding where the follower's text/vision models run;
   the earlier single-Bot3 memory estimate excludes those models and Chrome.
2. Follow [Bot3 deployment instructions](../deploy/vps/README.md) and the
   [release checklist](../deploy/vps/RELEASE_CHECKLIST.md). Build the actual image,
   run its inert network-disabled smoke check and dependency check, then record
   the tested source/image revision. Offline layout tests do not replace this.
3. Follow the [follower Linux candidate instructions](../deploy/followbot/README.md).
   Install its complete dependencies in a separate environment, verify Chrome,
   enroll the authorized Discord session privately and verify the model service.
   The wrapper confirms a Discord channel tab and model identifiers; it does
   not prove login health, parser accuracy or vision capability.
4. Keep the follower paper-only. Test real message parsing, duplicate suppression,
   browser/model outages and process restarts in paper mode. Pacifica execution
   activation requires a separate reviewed change; changing an environment flag
   does not enable it in this migration.
5. On the actual host, verify private dashboard access, independent restarts,
   durable state, consistent backups and restore/reboot drills. Bot3 service
   recovery does not automatically resume its trading strategy.
6. Before any Bot3 test-strategy activation, configure and verify the non-live
   forward-testing method. Complete the account/IP and authenticated
   testnet checks for entry, stop placement/replacement, partial exits and restart
   reconciliation if exchange testnet is used; those checks remain outstanding.
   They do not replace the required month-plus paper evaluation or authorize live
   trading. Record testnet separately from simulated paper testing.

The earlier [backtesting audit](backtesting-audit-2026-09-16.md) and
[historical rerun report](backtesting-rerun-2026-09-16.md) remain scoped evidence.
Old backtest/optimization results require regeneration after the fixes; no test
result here guarantees profitability, absence of bugs or production readiness.
