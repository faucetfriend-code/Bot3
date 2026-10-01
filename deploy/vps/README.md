# Bot3 Hetzner VPS deployment candidate

Deployment refreshed 2026-10-01. Pacifica is the primary trade-bot venue. No VPS has been purchased, configured, or started by this
work. Use this standalone Compose file, **not** the root development stack,
which publishes additional services publicly.

Start with [the release checklist](RELEASE_CHECKLIST.md) for the single handoff
covering host details, image acceptance, private access and recovery. The same
host is intended to run the separately located Discord follow bot; its verified
runtime and remaining Linux adaptation work are tracked in the checklist.

## Proposed host and cost

First inspect the existing agents-for-hire Hetzner project. An existing server
is not assumed to exist or to have spare capacity. A separate small bot server
avoids competing with agent builds and sharing exchange credentials.

Candidate: CX23, x86-64, 2 vCPU, 4 GB RAM, 40 GB disk, Ubuntu 24.04 LTS, in
Germany or Finland. This is a starting capacity estimate for one bot, not a
measured production capacity guarantee; keep historical optimization elsewhere.
[Specifications](https://www.hetzner.com/cloud/cost-optimized/).

| Monthly cap, before tax | EUR | USD |
| --- | ---: | ---: |
| CX23 | 5.49 | 6.49 |
| Retained Primary IPv4 | 0.50 | 0.60 |
| Base total | 5.99 | 7.09 |
| Optional automatic server backups, 20% of server price | 1.10 | 1.30 |
| Total with server backups | 7.09 | 8.39 |

This is the retained 2026-09-16 estimate, not a refreshed checkout quote.
Prices were verified against the [June 2026 price update](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[Primary IP pricing](https://docs.hetzner.com/cloud/servers/primary-ips/overview/),
and [billing and backup terms](https://docs.hetzner.com/cloud/billing/faq/).
Billing is hourly up to the monthly cap, including powered-off allocated
servers. These estimates exclude tax, transfer overages and any separate
off-server backup destination. They do not assume promotional credit or a
prepaid annual contract; confirm the actual account checkout terms.

On 2026-09-16, the [public cloud page](https://www.hetzner.com/cloud/) labeled the
cost-optimized class unavailable. Check current account inventory and regional stock
before approving a purchase; do not silently substitute a more expensive plan.
Hetzner lists Germany, Finland, USA and Singapore, not Canada. Server location
does not change the account holder's exchange eligibility.

Create a Primary IPv4 with **auto-delete disabled** and **deletion protection
enabled**. Keep it allocated through reboots and rebuilds; it is a separate paid
resource. Reassignment has location/power-state restrictions, so record its ID
and location and verify replacement compatibility before deleting a server.
See [Primary IP FAQ](https://docs.hetzner.com/cloud/servers/primary-ips/faq/).
No additional Floating IP is required for this single-server design.

## Architecture and defaults

- Docker restarts the API service after process failure or host reboot.
- SQLite lives in the `bot3-vps_bot-data` named volume; logs have a separate volume.
- Supervisor pause state also lives in that data volume, at
  `/app/data/supervisor_pause.json`, via `SUPERVISOR_STATE_PATH`. Container
  replacement preserves it. Corrupt/unreadable state blocks new entries until
  operator recovery; a genuinely missing state file keeps the existing unpaused
  default, so verify the mount and pause state after host recovery.
- The dashboard is published only to the VPS's `127.0.0.1:8000`.
- Tailscale provides private admin access. Do not enable Funnel or a Tailscale
  exit node; an exit node would change outbound exchange addressing.
- Compose forces Pacifica testnet, disables debug routes, and supplies
  `ENABLE_AUTO_TRADING=false`. The API initializes stopped; that flag alone is
  not a verified execution kill switch. An authenticated Start can trade
  testnet. This candidate does not automatically resume a strategy after reboot.
- API startup can connect to market-data services even while strategies are
  stopped. A green `/health` only proves HTTP liveness, not feed or trading health.
- External environment file supplies credentials. No `.env` is copied into the
  image, and `DOTENV_OVERRIDE=false` prevents hidden file precedence.
- The image includes root `interface.html`, required by the dashboard route.

## Server preparation, after a host is selected

These commands are for the selected Linux VPS, not the Windows workstation.
Use SSH keys, install Docker Engine and its Compose plugin using the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/),
and install Tailscale using its [Linux instructions](https://tailscale.com/download/linux).
Enable Docker at boot. Join the intended tailnet and restrict its access policy
to designated administrators. Restrict public SSH to the administrator's current
IP during setup; after testing private access, close public SSH while retaining
Hetzner console recovery. Do not allow public ports 8000, 3000 or 9090.

Copy the reviewed repository release to `/opt/bot3` without workstation secrets,
databases, logs or historical outputs. On the VPS:

```sh
cd /opt/bot3
sudo install -d -m 700 /etc/bot3
sudo install -m 600 deploy/vps/bot.env.example /etc/bot3/bot.env
sudo install -d -o 10001 -g 10001 -m 700 /var/backups/bot3
sudoedit /etc/bot3/bot.env
```

Set a unique random `API_TOKEN` (32 random bytes or more); initially leave wallet
credentials blank. The server refuses a non-loopback container bind with an
empty token. When testnet credentials are later supplied, use a separate testnet
wallet. Never put a mainnet private key in this candidate.

Build a distinct immutable release tag. Run privileged Docker commands using an
approved administrator account; membership in the Docker group is root-equivalent.
Use a root shell for these commands if `/etc/bot3/bot.env` is root-only:

```sh
cd /opt/bot3
export BOT3_IMAGE_TAG=reviewed-candidate-1
docker compose -f deploy/vps/compose.yaml config --quiet
docker compose -f deploy/vps/compose.yaml build bot
docker image inspect "bot3:$BOT3_IMAGE_TAG" --format '{{.Id}}'
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --entrypoint python "bot3:$BOT3_IMAGE_TAG" /app/deploy/vps/image_smoke.py
docker run --rm --network none --read-only --entrypoint python "bot3:$BOT3_IMAGE_TAG" -m pip check
docker compose -f deploy/vps/compose.yaml up -d --no-build bot
docker compose -f deploy/vps/compose.yaml ps
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ -o /dev/null
```

Do not use unredacted `docker compose config` or `docker inspect` environment
output in tickets: resolved environment variables include secrets. Record only
the release tag, image ID, source revision and non-secret checks.

## Private dashboard and address verification

On the VPS, enable HTTPS to the loopback service within the tailnet:

```sh
sudo tailscale serve --bg http://127.0.0.1:8000
tailscale serve status
tailscale ip -4
ss -lnt
curl -4 --fail https://api.ipify.org
```

The [Serve command](https://tailscale.com/docs/reference/tailscale-cli/serve)
publishes to the tailnet and `--bg` persists its configuration. Check the
tailnet access policy and confirm the returned private HTTPS URL from an
authorized device. The dashboard's API token is still required for protected
operations. An alternative is SSH over Tailscale with local port forwarding:
`ssh -L 8000:127.0.0.1:8000 admin@SERVER_TAILSCALE_IP`.

Compare the host's observed IPv4 with the allocated Hetzner Primary IPv4. Check
the container separately, because container routing is what the bot uses:

```sh
docker compose -f deploy/vps/compose.yaml exec -T bot python -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode())"
```

The supplied Docker bridge uses default IPv4 networking. If IPv6, NAT gateways,
proxies or VPN egress are later introduced, verify their source addresses
separately; a retained IPv4 does not guarantee an IPv6 source address. Repeat
these checks after a host reboot and deployment restart. From a device outside
the tailnet, verify `http://PUBLIC_IPV4:8000/health` cannot connect. Do not assume
the cloud firewall alone compensates for an accidental public Docker port.

## Backup and recovery

The application backup scheduler is configured for daily local backups with
30-day retention. Confirm actual backup creation and free disk space on the
VPS; local copies are lost with the server disk. Enable provider backups if
approved, and copy verified SQLite snapshots to a separate encrypted destination
under an agreed retention policy. No external backup account is provisioned here.

Create an explicit consistent SQLite snapshot while the API is running:

```sh
cd /opt/bot3
snapshot="manual-$(date -u +%Y%m%dT%H%M%SZ).sqlite"
docker compose -f deploy/vps/compose.yaml exec -T bot python /app/deploy/vps/sqlite_backup.py backup /app/data/trading_bot.db "/app/backups/$snapshot"
```

The helper uses SQLite's backup API, validates integrity and refuses to overwrite
an existing snapshot. Do not copy a live database file with `cp`: committed
changes can still be in its WAL file.

For a restore drill, first restore into a fresh file and inspect it independently.
To restore the deployed database, stop trading and reconcile testnet positions
and open orders first, then stop the service. Restoring local state never reverses
orders already sent to an exchange. Replace `CHOSEN.sqlite` with the verified
snapshot filename:

```sh
docker compose -f deploy/vps/compose.yaml stop bot
docker compose -f deploy/vps/compose.yaml run --rm --no-deps --entrypoint python bot /app/deploy/vps/sqlite_backup.py restore /app/backups/CHOSEN.sqlite /app/data/trading_bot.db --replace
docker compose -f deploy/vps/compose.yaml up -d --no-build bot
```

The helper preserves the previous database as `.before-TIMESTAMP` and refuses
restore if destination WAL/SHM files remain. Do not delete these files to bypass
the guard: investigate active writers or unfinished recovery first. The helper
cannot establish that every process is stopped; the Compose stop and operational
reconciliation are required. Preserve prior snapshots until the restore is
validated; protected `.before-` files require a deliberate retention policy.
SQLite backups do not contain the separate supervisor JSON. Preserve that file
with release backups and explicitly check/reapply the intended pause after
restoring or replacing a host; never infer a paused state from restored SQLite.

## Upgrade, rollback, and readiness gate

Before upgrading, take a SQLite snapshot and retain the previous image ID/tag
and source release. Build a new unique tag, then recreate only `bot`. For rollback,
set `BOT3_IMAGE_TAG` to the recorded prior tag and run `up -d --no-build bot`.
If a new version changed the database schema incompatibly, restore its matching
pre-upgrade snapshot while stopped before reverting the image. Never run
`docker compose down -v`; it deletes persistent volumes.

Exercise server reboot, container crash/restart, health failure, private-access
denial, backup restore, stale market data and protective-order recovery on testnet.
Docker's restart policy restarts an exited process; it does not restart a merely
unhealthy container or authorize strategy resumption. Keep strategies stopped
until the separate protection checks and user go-live decision are complete.

Local validation limitation, rechecked 2026-10-01: Docker CLI exists, but no Docker daemon was available
on the development workstation. No image build, VPS boot, Tailscale change or
exchange-account call was executed as part of this deployment preparation.
Dependency versions are inherited from existing requirements; no broad upgrades
were made. Record the successfully built image digest before promoting a release.
