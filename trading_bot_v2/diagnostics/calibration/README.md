# Gate-metric calibration artifacts

One JSON file per `(symbol, strategy)`, recording the observed distribution of
every metric that gates a strategy's entry. Produced by
`python -m trading_bot_v2.diagnostics.calibrate`, consumed by
`trading_bot_v2/optimization/search_spaces.py::check_param_feasibility`.

## Why these are committed

`VWAP_SD_ENTRY_THRESHOLD` shipped at 4.037 against a metric whose typical
ceiling is around 3.1 (p99.9). Zero signals were possible, the strategy looked
alive, and finding out took a multi-hour manual investigation with hand-written
replication scripts. These files are the measurement that makes that verdict
automatic: a candidate threshold above the observed `max` is pruned before a
trial burns a backtest, and one above `p99` is flagged.

The candle store is gitignored (574 MB), so without committed artifacts the
check would be dead in a fresh clone or a worktree.

## Reading a diff

Regenerating produces a diff in `provenance.generated_at` every time — ignore
that. What matters:

| Field changed | What it means |
|---|---|
| `max`, `p99`, `p999` | **Data drift.** The metric's range moved. If a configured threshold now sits outside it, the strategy is about to stop trading. |
| `count`, `bars_observed` | The window or the data store changed. Check `provenance.window_*`. |
| `pass_rate`, `n_pass` | The threshold or the data changed; cross-check `threshold_at_calibration`. |
| `threshold_at_calibration` | Someone retuned `.env`. Expected when a parameter changes, suspicious otherwise. |
| `history_lookback` | Invalidates comparison with older artifacts for any window-dependent metric (a cumulative VWAP over 60 candles is not the same statistic as one over 250). |

## Validity caveats

An artifact is measured at one parameter set, recorded in
`provenance.params_at_calibration`. Most metrics are threshold-independent by
construction — `deviation_sd` does not depend on `sd_entry_threshold`,
`bars_since_cross` does not depend on the entry window — so the artifact stays
valid across candidate values of the threshold it judges. A few metrics do
depend on OTHER searched parameters (`pullback_pct` moves with
`fast_ma_period`), so treat verdicts near the boundary as advisory when a trial
samples far from the calibrated value.

## Regenerating

```bash
# From the MAIN checkout (.env sets a relative BACKTEST_DATA_DIR and
# load_dotenv(override=True) clobbers shell exports), or pass an absolute path:
DATA_AUTODOWNLOAD=false python -m trading_bot_v2.diagnostics.calibrate \
    --symbols BTC-USDC,ETH-USDC,SUI-USDC \
    --start 2024-01-01 --end 2026-07-01 \
    --data-dir /abs/path/to/Bot3/trading_bot_v2/backtesting/data
```

The run prints each metric's range and immediately judges the CURRENTLY
configured thresholds against what it just measured, so a live unreachable
threshold surfaces without waiting for an optimizer run.
