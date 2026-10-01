# Pacifica native protection

Implemented and checked against official API documentation on 2026-09-16.
Validation is offline with mocked HTTP and signing. No authenticated API calls,
orders, account changes, or bot startup were performed. Existing exchange and
demo/live configuration is unchanged. API support does not mean deployment or
account permissions have been validated.

## Contract

- Entry market and limit requests can carry `stop_loss` with `stop_price`,
  a UUID client identifier, and `last_trade_price` trigger selection. The stop
  has no limit price, so it is a market-triggered stop. Acknowledgement of the
  entry alone is not verification of protection; the existing fill/protection
  lifecycle must enumerate the venue's open stops afterward.
- Standalone protection uses `/orders/stop/create`, signed with
  `create_stop_order`, with the opposite close side (`ask` for a long),
  `reduce_only=true`, and no amount: the documented omitted-amount semantics
  protect the full position. This also avoids rounding away a remainder when
  position size changes. Stop triggers use the exact market tick, rounding
  toward tighter protection; unknown market precision prevents submission.
- Auth uses the existing agent-wallet signature and original account public
  address. The documented endpoints permit `agent_wallet`; no main-wallet
  private key is required by this implementation.
- Recovery strictly reads `/orders`. Errors or malformed lists raise; they
  cannot appear as an empty list. Only reduce-only `stop_market` and
  `stop_loss_market` orders qualify. Take-profit orders and stop-limit orders
  do not count. Explicit amounts must cover the remaining current position.
  Zero quantities are treated as unknown until verified against real venue
  behavior, rather than assumed to mean full position.
- Amendment creates a replacement, verifies its presence, and only then
  cancels the old stop via `/orders/stop/cancel`. The ordinary `/orders/edit`
  endpoint edits limit price/size and is not used to modify stop triggers.
  Failed old-stop cancellation can leave two reduce-only stops; the result
  reports that condition and retains the confirmed replacement identifier.
- Default cancel-all excludes reduce-only orders. Explicit `include_stops`
  allows their removal. This preserves other reduce-only exit orders too.

## Ambiguous requests and limitations

Stop mutations and entries carrying an attached stop are sent once. A timeout
is never blindly replayed. Stop creation is reconciled using the precise UUID
against open orders. Missing or malformed venue identifiers remain UNKNOWN.
Before a stop creation POST, intent and its UUID are committed to the
`pacifica_stop_attempts` table in the configured SQLite `DATABASE_PATH`.
Unresolved attempts block repeated submissions across process restarts, even
when a fresh venue listing is empty. A matching visible UUID can resolve the
attempt. Unwritable or nonpersistent journals prevent submission. Non-SQLite
backends currently cannot submit standalone protection. The database must live
on persistent storage (the VPS template uses `/app/data`). Retain this table
with database backups; do not delete pending rows to force a retry without
reconciling the venue first. Losing or replacing the database also loses this
history and requires venue reconciliation before resuming. Pending attempts
block the same account/market/position side even if a new stop price is requested.
CLOID deduplication guarantees were not assumed.

Protection support is now advertised by the adapter so existing entry, trailing,
and repair code can use it. Stop placement/trigger execution, partial fills,
actual returned full-position order quantities, agent permissions, and timing
under real venue failures remain unverified. A stop-market trigger is not a
guaranteed execution price or guaranteed fill. Local fallback and the existing
stop-failure policy remain necessary. No take-profit strategy was added.

## Official references

- [Create market order and attached TP/SL](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/create-market-order)
- [Create limit order](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/create-limit-order)
- [Create stop order](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/create-stop-order)
- [Position TP/SL alternative](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/create-position-tp-sl)
- [Open orders and stop types](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/get-open-orders)
- [Cancel stop](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/cancel-stop-order)
- [Edit limit order](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/edit-order)
- [Cancel all and exclude-reduce-only](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/cancel-all-orders)
- [Agent keys](https://docs.pacifica.fi/api-documentation/api/signing/api-agent-keys)

Tests: `trading_bot_v2/tests/test_pacifica_stops.py` plus the existing Pacifica
client, exchange lifecycle, entry protection, and stop-repair suites.
