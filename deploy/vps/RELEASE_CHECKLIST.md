# VPS release handoff - Pacifica Bot3 and separate Discord follower

Updated 2026-10-01. This is a preparation checklist, not a deployment record.
Pacifica is selected for Bot3; its supplied Compose file remains testnet-only
with the strategy loop stopped on API startup.
The Discord follower lives in `C:/Users/z_shi/Desktop/N8NPROJECTS/Discord Bot`.
Its original Blofin implementation is being migrated to Pacifica at the user's
request. It retains a logged-in Chrome/CDP session and a separate account;
it is not a Discord bot-token relay. The migration and Linux launcher are
paper-only: non-paper or non-testnet Pacifica settings are rejected.

## 1. Information to supply

| Item | Value to record |
| --- | --- |
| Hetzner project and server ID | Pending user-provisioned host |
| Region, architecture, RAM, disk | Measure combined Bot3/browser/follower capacity |
| Retained Primary IPv4 / resource ID | Record both; disable auto-delete, enable protection |
| Administrator SSH user and key fingerprint | No private keys in this document |
| Tailscale hostname and allowed administrators | Private access only |
| Bot3 source revision and image tag/digest | Record the reviewed build |
| Discord source revision | Separate Discord Bot repository; record separately |
| Discord service and browser session | Separate paper-only systemd templates; Linux acceptance pending |
| Discord language/vision model endpoint | Confirm host, models, authentication and reachability |
| Separate backup destination and retention | Local host backups alone are insufficient |

Do not paste API tokens, wallet keys, Discord tokens or environment-file contents
into this checklist. The detailed commands are in [README.md](README.md).

## 2. Build acceptance - safe before any exchange login

- [ ] External `/etc/bot3/bot.env` exists, owner-only permissions, unique API token.
- [ ] Initial wallet fields blank; no live credentials in the candidate.
- [ ] Standalone Compose validates with `config --quiet`.
- [ ] Build succeeds on Linux x86-64; record image ID and dependency manifest.
- [ ] Run `image_smoke.py` with `--network none --read-only`; it compiles packaged
  code, checks dashboard/secrets/non-root user and imports third-party libraries.
  It never imports the bot application or opens the API server.
- [ ] Run `python -m pip check` in the same network-disabled image.
- [ ] Record `docker run --rm --network none --entrypoint python IMAGE -m pip freeze`
  for the successful release. Existing requirements are not pinned; retain this
  exact image for reproducible rollback instead of relying on a future rebuild.
- [ ] SQLite and supervisor JSON both map to the persistent Bot3 volume;
  backup mount belongs to UID/GID 10001.

Build and image acceptance remain pending because the local Docker daemon was
unavailable on 2026-10-01. No normal application container was launched here.

## 3. Host and private access acceptance

- [ ] Docker enabled at boot; SSH-key access and provider-console recovery tested.
- [ ] API port is bound to `127.0.0.1:8000`, not all host interfaces.
- [ ] Tailscale admin access works only for permitted identities; no Funnel or exit node.
- [ ] Authorized private dashboard loads; protected requests reject an invalid token.
- [ ] Public-IP port 8000 is unreachable from outside the tailnet.
- [ ] Host and container observed outbound IPv4 equal the retained Primary IPv4.
- [ ] Repeat address and access checks after a host reboot.
- [ ] `/health` and dashboard pass; strategy remains stopped. HTTP liveness alone
  is not proof that prices, risk controls or venue orders work.

## 4. Testnet recovery acceptance

- [ ] Confirm Pacifica testnet endpoint and separate testnet wallet before Start.
- [ ] Verify stop placement/repair, lost-connection recovery, partial fills,
  stale-feed blocking and failed protective-order handling with controlled tests.
- [ ] Process restart and host reboot restore the API but do not silently start
  strategies. Reconcile positions and outstanding orders before explicit resumption.
- [ ] Supervisor pause survives container recreation; damaged state blocks entries.
- [ ] Consistent SQLite backup and fresh-file restore drill succeed.
- [ ] Recover supervisor JSON separately; verify pause state explicitly.
- [ ] Rollback to the prior image and matching database snapshot is demonstrated.
- [ ] Check CPU, memory, disk and log growth under representative testnet load.

These host and exchange acceptance checks have not been executed by this handoff.
Changing the candidate to live trading requires its own reviewed configuration
and explicit go-live decision; this checklist does not perform that change.

## 5. Separate Discord follower: verified contract and remaining work

Source inspection and the paper-only migration on 2026-10-01 established:

| Concern | Existing implementation | VPS acceptance requirement |
| --- | --- | --- |
| Entrypoint | `python bot.py`, Python 3.10+ syntax | Isolated Linux environment and pinned successful dependency manifest |
| Venue | Pacifica paper-only migration; Blofin is legacy | Separate follower account/keys; do not reuse Bot3's Pacifica wallet |
| Trading prevention | `PAPER_MODE=true` blocks exchange orders | Force paper mode before any launch; `DRY_RUN` is not a safety control |
| Discord access | Direct Chrome CDP at hardcoded `http://localhost:9222` | Browser and follower must share loopback access; CDP must never be public |
| Login state | Windows launcher uses persistent `C:\chrome-cdp-profile` and manual Discord login | Provision a new persistent Linux browser profile and complete authorized login privately; do not copy assumed portable cookies |
| Dashboard | Flask at `127.0.0.1:5050`; nonlocal bind requires password | Keep host port 5050 loopback-only with a separate private admin endpoint |
| Storage | `bot.db`, `pacifica_requests.db`, CSV and logs beside Python source | Separate writable follower layout, paired database backups and sidecars; never mount Bot3 database |
| Models | OpenAI-compatible `LOCAL_LLM_BASE_URL`, configured text/vision models | Decide model hosting and verify both model capabilities before startup |

The [follower Linux candidate](../followbot/README.md) supplies separate systemd
units, external environment and a paper-only preflight wrapper. It relies on
an explicitly configured OpenAI-compatible model service; a small VPS must not
be assumed to host these models. Chrome and follower run under one dedicated
host user so the verified `localhost:9222` contract holds. The browser profile
requires manual authorized enrollment; no login or model fallback is automated.
The candidate has not yet run on Linux and is not a validated deployment.

Prepare these independent host boundaries once that adaptation is reviewed:

- Source/state: `/var/lib/discord-follow/app`; support code/virtual environment:
  `/opt/discord-follow`. This preserves next-to-source database and CSV paths.
- Secrets: `/etc/discord-follow/bot.env`, owner-only permissions, different from
  `/etc/bot3/bot.env`. Configure `PAPER_MODE=true` explicitly.
- Browser profile: a dedicated persistent path owned only by the follower user;
  access session enrollment through a private admin session.
- Backups: `/var/backups/discord-follow`, independent schedule and restore drill.
- Service: a distinct process/container and restart policy; no Docker socket or
  credential/state sharing with Bot3. Keep 5050 and 9222 off public interfaces.
- Models: use a reviewed private reachable service or select resources from
  actual model requirements. A lost model connection must not produce duplicate
  executions or replay previously handled signals.

Before the Discord follower is considered VPS-ready:

- [ ] Confirm model location, identifiers and private connectivity.
- [ ] Validate supplied Linux Chrome unit and persistent profile recovery.
- [ ] Keep source/ancestor `.env` absent; the wrapper rejects hidden overrides.
- [ ] In paper mode, test signal parsing, image interpretation and duplicate-message
  suppression across restarts without making authenticated exchange requests.
- [ ] Verify database/profile backups and private dashboard/CDP exposure.
- [ ] Measure simultaneous browser, model-client and Bot3 CPU/RAM/disk usage;
  finalize VPS sizing from that evidence. The earlier 4 GB Bot3 estimate does
  not include unmeasured local models.
- [ ] Reboot/restart each service independently and verify both account boundaries.

Linux acceptance remains incomplete. It does not block building and privately
testing the separate Bot3 candidate, but the two-service deployment as a whole
must not be described as complete.
