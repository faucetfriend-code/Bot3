# Backtesting correctness audit - 2026-09-16

## Outcome

A bounded audit found and fixed material defects in candle timing, simulated execution, and performance reporting. The focused offline suite passed **245 tests**, including **30 new regression tests**. This is evidence for the tested behaviors, not a guarantee that the backtesting system has no bugs.

**Earlier backtest results and optimization rankings need to be rerun.** In particular, higher-timeframe lookahead could have materially inflated earlier results.

## Verified fixes

| Area | Defect | Corrected behavior |
| --- | --- | --- |
| Candle timing | Higher-timeframe candles were available at their open timestamp, exposing unfinished OHLC data. Missing earlier history selected a future first candle. | Replay decisions use the five-minute bar close; each timeframe supplies only completed candles. Unavailable history stays empty. |
| Rejected fills | Unaffordable orders were marked filled, charged fees, and logged without opening a position. | Fee-inclusive affordability is checked before mutation, including additions and reversals. Rejected orders produce no fill or fee. |
| Protective exits | Stops and take-profits could execute before their entry, creating an unintended opposite position. | Exits track their entry and are reduce-only, with executed size capped at the position. |
| Entry-bar protection | Delaying all protection until the following candle could miss a certain stop after an entry. | Newly filled entries incur a touched protective stop in that candle under an explicit conservative rule; take-profit activation waits until the following candle. |
| Pyramiding | Replacing protection before an unsuccessful addition could leave an existing position unprotected. | Rejected additions retain prior protection; pending accepted additions retain active protection with reduce-only sizing. |
| Gap stops | A stop could fill at its trigger even when the entire next candle had gapped beyond it. | Existing stops account for an adverse opening gap and slippage. |
| Equity and fees | Immediate fills could leave stale unrealized PnL. Closing-trade statistics excluded price breakevens and ignored allocated trading fees. | Fills refresh unrealized PnL; explicit closed quantity and allocated entry/exit fees support closing-trade statistics, including partial closes. |
| Annualization | Five-hour equity snapshots were annualized as hourly observations. | Sharpe and Sortino annualization use observed snapshot cadence. |

Implementation: `trading_bot_v2/backtesting/engine.py`, `simulated_exchange.py`, and `performance.py`.

## Validation evidence

Final tester result: **245 passed in 9.77 seconds; zero warnings or failures**.

```powershell
$env:DATA_AUTODOWNLOAD='false'
$env:DATABASE_BACKEND='sqlite'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest trading_bot_v2/tests/test_backtest_safety_regressions.py trading_bot_v2/tests/test_backtesting.py trading_bot_v2/tests/test_simulated_equity.py trading_bot_v2/tests/test_cost_model.py trading_bot_v2/tests/test_entry_ttl.py trading_bot_v2/tests/test_execution_policy.py trading_bot_v2/tests/test_trailing_exit.py trading_bot_v2/tests/test_funding.py trading_bot_v2/tests/test_coverage_guard.py -q --basetemp=.pytest-backtest-final -p no:cacheprovider --tb=short
```

The new regression file contains 30 tests. The existing backtesting test fixture was corrected to generate valid timestamps using datetime arithmetic rather than invalid hours such as 99. Replaying the earlier implementation in isolated module namespaces produced 13 behavioral failures and eight failures from missing new helpers; these are not 21 independent bugs.

The tester reported Ruff lint/format checks passing for both changed test files and a clean whitespace diff check. A separate read-only review of the production changes identified two protection regressions during implementation; both were revised and covered before the final run.

## Scope and remaining limitations

- Validation used synthetic market data, mocked funding downloads, synthetic coverage stores, and isolated SQLite. No authenticated exchange calls, orders, or bot startup were performed.
- No full real-parquet end-to-end replay, full repository suite, or numerical coverage measurement was performed. Actual strategy profitability and historical data quality remain unverified.
- OHLC bars cannot determine every intrabar event sequence. The conservative entry-bar stop/deferred-profit rule is a modeling choice, not tick-level execution reconstruction. Existing seeded ordering remains relevant when already active stops and profit targets are both touched.
- Drawdown and risk statistics still use sparse equity snapshots, normally every five hours. Intrabar losses and a final unsampled loss can be missed by that series. Final equity and headline total return use the directly computed terminal equity and are not affected by omission of a final snapshot.
- Closing-trade net PnL includes allocated trading fees but does not allocate funding to individual closed trades. Funding remains included at account level and reported separately.
- Reduce-only orders capped below their requested quantity can conservatively overestimate slippage impact because the impact calculation still uses requested quantity.
- These tests do not establish parity with live exchange execution, liquidity, queue position, liquidation, or outages.

## Next verification

Rerun a representative historical campaign on the same stored data and configuration, preserving the old artifacts for comparison. Compare fill counts, exposure, fee/funding totals, terminal equity, and per-strategy results before relying on new optimization rankings or moving to exchange validation.
