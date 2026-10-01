# Discord follower - Pacifica paper-mode Linux candidate

This deployment template targets the separately migrated source at
`C:/Users/z_shi/Desktop/N8NPROJECTS/Discord Bot`. The old Blofin implementation
is historical; the intended migrated release uses Pacifica with its own account.
Use only the final migration release after its offline checks pass. These files
do not copy, execute, or modify that source automatically.

No VPS process, browser login, exchange connection or order was started while
preparing this template. Linux execution and final migration compatibility are
acceptance gates, not completed deployment claims.

## Why a separate host service

The follower reads a logged-in Chrome session through `localhost:9222` and
stores SQLite/CSV/log files beside its source. A dedicated Linux user and two
systemd services keep that verified layout without pretending an ordinary
isolated browser container shares localhost. Bot3 remains its own Docker
service, account, environment, database and private dashboard.

| Component | Host location |
| --- | --- |
| Reviewed follower source plus state | `/var/lib/discord-follow/app` |
| Python environment and preflight launcher | `/opt/discord-follow` |
| External secrets/configuration | `/etc/discord-follow/bot.env` |
| Browser profile | `/var/lib/discord-follow/chrome-profile` |
| Consistent database/profile backups | `/var/backups/discord-follow` |
| Dashboard | `127.0.0.1:5050` |
| Chrome debugging | `127.0.0.1:9222` only |

The migrated follower keeps `bot.db` and a separate durable
`pacifica_requests.db` request journal. Preserve both: restoring an old request
journal may lose duplicate-order protection. Back up SQLite files with their
writers stopped or with SQLite's backup API, never by copying live main files
while ignoring WAL sidecars. Stop the browser before copying its profile.

## Prerequisites to resolve on the Linux host

1. Confirm the migrated source revision and install its final requirements into
   a dedicated Python 3.11 virtual environment. Use the source's complete
   requirements; the migration adds `solders` and `base58`. Do not substitute a
   hand-written dependency subset. Run `pip check` and save `pip freeze` for the
   accepted release; versions are not claimed pinned before an actual install.
2. Install a supported Google Chrome build. The browser unit assumes
   `/usr/bin/google-chrome-stable`; verify that exact executable/version or
   adjust the unit. Its normal sandbox stays enabled; do not add `--no-sandbox`.
3. Provide an OpenAI-compatible private model endpoint and the exact text and
   vision model IDs used by the migrated source. No model is downloaded or
   launched by these templates. A small shared VPS is not assumed sufficient
   for local models. A desktop-hosted endpoint means the desktop must remain
   available; decide whether that meets the always-on requirement.
4. Allocate separate service credentials. Do not copy Bot3's Pacifica wallet,
   account keys, API token, database or environment into this follower.
5. Confirm Chrome/Discord access is authorized for the intended account and
   channels. Complete login manually through a private graphical session.

Create a dedicated `discord-follow` user, source/profile directories owned by
that user, and root-owned `/opt/discord-follow` and `/etc/discord-follow`.
Copy `launch.py` to `/opt/discord-follow/launch.py`; copy the example environment
to `/etc/discord-follow/bot.env` with mode 0600. The application source directory
must not contain `.env`, nor may an ancestor directory: the wrapper refuses
hidden configuration that could override the external safety settings.

Copy reviewed source only. Do not carry workstation databases, cookies, `.env`,
tokens, logs or stale request journals into an initial fresh test environment.
Any intended state migration requires its own reconciled backup/restore plan.

## Manual browser enrollment

Keep both supplied services stopped during enrollment. In a private graphical
session as the `discord-follow` user, start the installed Chrome executable
with the dedicated `--user-data-dir=/var/lib/discord-follow/chrome-profile`,
`--remote-debugging-address=127.0.0.1`, and `--remote-debugging-port=9222` flags.
Open the intended Discord channels and complete login manually. Never publish
port 9222 or a remote desktop publicly. Do not run two Chrome processes against
the same profile.

Close the graphical browser cleanly, then test the supplied headless browser
unit against that profile. Some authentication/session changes require renewed
manual enrollment; the templates do not bypass challenges or automate login.
Set `FOLLOWBOT_SESSION_CONFIRMED=true` only after verifying the Linux session.

## Configuration and startup gate

The external environment must explicitly retain:

```text
EXCHANGE=pacifica
PAPER_MODE=true
PACIFICA_TESTNET=true
DASHBOARD_HOST=127.0.0.1
```

Supply the separate `PACIFICA_ACCOUNT_PUBLIC_KEY` and
`PACIFICA_AGENT_PRIVATE_KEY` only as needed by the final reviewed testnet release.
`DRY_RUN` is not used as a safety switch. Use `LOCAL_LLM_BASE_URL` with the
server's API prefix, usually ending in `/v1`, and the source's text/vision model
configuration. The preflight requires both configured model IDs to appear at
the endpoint's `/models` path. Endpoints requiring additional authentication
need an explicitly reviewed client configuration; no silent public fallback
is supplied.

Install the reviewed unit files under `/etc/systemd/system`, then on the VPS:

```sh
sudo systemd-analyze verify /etc/systemd/system/discord-follow-browser.service /etc/systemd/system/discord-follow.service
sudo systemctl daemon-reload
sudo systemctl start discord-follow-browser.service
sudo systemctl start discord-follow.service
sudo systemctl status discord-follow-browser.service discord-follow.service
```

`launch.py` checks configuration before touching any service, then probes only
the local browser and configured model endpoint before replacing its process
with `python bot.py`. A failed preflight exits 78 and systemd deliberately does
not retry until an operator repairs the configuration/session and starts it.
Other crashes are restart-limited. Model presence and a Discord channel tab
do not prove valid authentication, parsing quality or vision capability: test
those explicitly in paper mode.

The browser is ordered before the follower, but ordering does not guarantee
browser readiness. A slow browser can block initial preflight; verify it is
ready and manually restart the follower. Enable both units at boot only after
the paper-mode acceptance checks pass. No unit is enabled by this preparation.

## Acceptance and rollback

- Verify loopback-only 5050/9222, private administration, and separate account IDs.
- Test text/image interpretation against the chosen model without live orders.
- Verify session loss, model outage and reconnect behavior in paper mode.
- Verify restart duplicate-message behavior and the migrated request journal.
- Measure combined Bot3, browser and follower resource use before deciding RAM.
- Stop follower and browser before restoring paired database/profile backups.
- Retain the reviewed source release, dependency manifest and matching state
  backups for rollback; never roll back the request journal while leaving
  unreconciled exchange activity behind.

Live mode is deliberately unsupported by this launcher. Removing these guards
requires a separate reviewed deployment decision after the migration and
testnet protection checks are complete.
