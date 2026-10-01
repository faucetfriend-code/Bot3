# Historical backtesting rerun - 2026-09-16

## Outcome

Completed eight paired historical cases against saved market data: eight runs using the original committed core backtesting modules and eight using the repaired modules. A ninth repaired run reproduced the full BTC mean-reversion result exactly. The repaired cases produced 281 fills and passed 284,544 completed-candle visibility checks. Terminal account values reconciled within 0.000051 quote-currency units, consistent with four-decimal reported equity and rounded trade PnL.

Results changed in both directions. These runs verify specific accounting and replay behaviors; they do not establish strategy profitability or live exchange parity. The 91-day ETH all-strategy case still lost money.

The implementation specialist separately reports **256 focused tests passed in 9.42 seconds**, comprising the previous 245 tests plus 11 follow-up regressions. Follow-up fixes cover complete equity sampling and terminal drawdown, elapsed-time risk annualization, net optimizer scoring, walk-forward strategy forwarding, and resting-entry expiry/time exits.

## Controlled comparison

- Initial capital: 10,000 per case; deterministic seed: 0.
- Local `trading_bot_v2/backtesting/data` parquet files; downloads disabled and Python socket connections blocked by the audit harness.
- Historical funding enabled; stored Binance rates remain a proxy for Pacifica funding. No authenticated exchange calls, orders, bot startup, or live account validation.
- Configured strategy settings were retained, with no optimization parameter overrides or new tuning. Single-strategy and all-strategy cases use the same settings on both sides. Effective non-secret backtest settings are saved in repaired-run JSON.
- `head` means the three core modules `engine.py`, `simulated_exchange.py`, and `performance.py` loaded from commit `f1e9c96340aa14333a913279ef55b9bcec3e5466`, before either audit's changes. Supporting modules and data were shared with the current workspace. It is not a separate full historical checkout.
- Previous user artifacts in `out/` were preserved. They contain optimization folds and tuned/composite configurations, so comparing those headline scores to these untuned cases would not be like-for-like.

## Results

Dates use the engine's start/end arguments. March cases span March 1 through March 22, 2024; the longer ETH case spans January 1 through April 1, 2024 (91 days).

| Symbol / strategy | Window | Original equity | Repaired equity | Original / repaired fills | Repaired max drawdown |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC mean reversion | March 2024 | 10,002.2498 | 10,011.0519 | 18 / 20 | 0.1097% |
| ETH mean reversion | March 2024 | 10,002.2887 | 10,004.4832 | 30 / 36 | 0.1314% |
| SUI mean reversion | March 2024 | 10,005.9464 | 10,003.3062 | 16 / 24 | 0.2084% |
| BTC grid trading | March 2024 | 9,993.0217 | 10,008.8838 | 24 / 18 | 0.0592% |
| ETH MA crossover | March 2024 | 10,000.0000 | 10,000.0000 | 0 / 0 | 0.0000% |
| SUI all enabled strategies | March 2024 | 9,972.6428 | 9,998.0589 | 5 / 4 | 0.5457% |
| BTC mean reversion | June 1–July 1, 2022 | 9,994.9773 | 9,991.3420 | 26 / 30 | 0.1358% |
| ETH all enabled strategies | Jan 1–Apr 1, 2024 | 9,906.0029 | 9,901.5718 | 156 / 149 | 1.5268% |

The MA case only verifies that the pipeline completes: no MA orders executed. Its warnings about active but uninitialized strategies reflect regime selection interacting with the single-strategy filter; this alone does not prove an execution bug. All-strategy runs explicitly exclude the live-only order-book overlay.

The BTC March mean-reversion drawdown changed from 0.0564% to 0.1097%; ETH's long-case drawdown changed from 1.4104% to 1.5268%. Corrected candle visibility and denser equity sampling both affect comparisons. Multiple repairs landed together, so no single fix is credited for each numerical difference.

## Data and invariants checked

- Inventoried 18 parquet files across BTC, ETH, SUI and funding. All had ascending timestamps and no duplicate timestamps. Coverage extends through September 1, 2026; SUI starts in May 2023, and BTC/ETH funding starts in 2019.
- Scanned all 15 symbol/timeframe candle slices over March 1–23, 2024: no cadence gaps, missing values, negative volume, or inconsistent OHLC bounds. This is a bounded window check, not a whole-store data-quality certification.
- Every repaired replay checked that each selected 1m, 15m, 1h and 4h candle closed no later than the replay decision time.
- All repaired terminal equity values were finite; fill timestamps were monotonically ordered and within their requested windows.
- Terminal equity reconciled against initial capital + realized gross PnL - trading fees + funding cash flow + unrealized PnL. The maximum residual was 0.000050826.
- A repeated BTC March mean-reversion run produced identical full results, including trade logs, equity curves and diagnostics.
- Core source SHA256 fingerprints matched the frozen repaired files after the runs.

## Reproduce

Run from the repository root. The harness installs offline guards, sets a dedicated database path, writes separate JSON and warning logs, and records source fingerprints.

```powershell
python out/backtest_audit_20260916/run_historical.py --revision head --symbol BTC-USDC --strategy mean_reversion
python out/backtest_audit_20260916/run_historical.py --revision current --symbol BTC-USDC --strategy mean_reversion
python out/backtest_audit_20260916/run_historical.py --revision current --symbol BTC-USDC --strategy mean_reversion --suffix repeat
python out/backtest_audit_20260916/run_historical.py --revision current --symbol BTC-USDC --strategy mean_reversion --start 2022-06-01 --end 2022-07-01
python out/backtest_audit_20260916/run_historical.py --revision current --symbol ETH-USDC --strategy all --start 2024-01-01 --end 2024-04-01
```

For the remaining March cases substitute `ETH-USDC / mean_reversion`, `SUI-USDC / mean_reversion`, `BTC-USDC / grid_trading`, `ETH-USDC / ma_crossover`, and `SUI-USDC / all`. Run each with both revisions. Use `--suffix` to preserve a prior audit result when repeating a case. Default dates are `2024-03-01` and `2024-03-22`.

Artifacts live in `out/backtest_audit_20260916/`:

- `comparison.json`: paired metrics, accounting checks and source freshness.
- `current_*.json` / `head_*.json`: full metrics, trade logs, equity curves and diagnostics.
- `data_inventory.json` / `window_data_quality.json`: data inventory and bounded quality checks.
- `*.log`: complete warning-level replay logs, including expected funding-proxy, strategy-filter and validation messages.
- `prefinal_*`: the first diagnostic run before the follow-up sampling changes; excluded from the final comparison.

The repaired core SHA256 fingerprints are:

```text
simulated_exchange.py 524c8991748a88473d4cddaa3a613da4d7fb2a6675e09a16332171f1dd9ed627
performance.py        fbf050a8b5cdb8adfd23b8b596c69fca45dc517002176b4246de5324772efe89
engine.py             2ddded46e8c9611fc0e5095630930570594a3f9e95f456141b7e69c1c0ab4182
```

## Remaining limits

OHLC bars cannot recover every intrabar path, real queue position, market depth or liquidation behavior. Protective-stop ambiguity follows the documented conservative model. The reruns do not directly prove stop-fill realism on every historical trade; stop scenarios have focused regressions. Closing-trade net PnL allocates trading fees but not funding. Reduce-only slippage impact can still use requested rather than capped quantity. Risk metrics now observe each five-minute close, not intrabar equity extremes.

No numerical code-coverage measurement, full optimization campaign, or live exchange validation was performed. Older optimization rankings should be regenerated before reuse, because their candle visibility and net scoring differed.
