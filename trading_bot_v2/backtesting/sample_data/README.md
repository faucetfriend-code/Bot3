# Synthetic sample candles (`SYN-USDC`)

Small, deterministic, freely redistributable OHLCV files so the backtest
harness runs end to end with no network access and no proprietary data.

| File | Rows | Span (UTC) |
|------|------|------------|
| `SYN-USDC_5m.csv.gz`  | 8640 | 2024-01-01T00:00 .. 2024-01-30T23:55 |
| `SYN-USDC_15m.csv.gz` | 2880 | 2024-01-01T00:00 .. 2024-01-30T23:45 |
| `SYN-USDC_1h.csv.gz`  | 720  | 2024-01-01T00:00 .. 2024-01-30T23:00 |
| `SYN-USDC_4h.csv.gz`  | 180  | 2024-01-01T00:00 .. 2024-01-30T20:00 |

Columns: `timestamp,open,high,low,close,volume`. Timestamps are candle
open times, naive UTC, `%Y-%m-%dT%H:%M:%S`, the same canonical format
as the real store in `trading_bot_v2/backtesting/data/`. Total size is
under 200 KB.

## How it is made

`trading_bot_v2/backtesting/sample_data.py` walks a 1-minute geometric
random walk through four regime segments (7 days ranging, 8 days
trending up, 6 days ranging, 9 days trending down) with an hourly
activity process that clusters volume and volatility, then rolls the
1m path up to 5m/15m/1h/4h. Every timeframe is therefore an exact
aggregate of the same path. The generator uses only `random.Random`,
whose sequence is stable across Python versions, so regenerating gives
byte-identical files:

```bash
python -m trading_bot_v2.backtesting.sample_data --out trading_bot_v2/backtesting/sample_data
```

The 1m series is not committed (43,200 rows). The engine's regime-gated
`pipeline` mode needs it; generate the full set into a scratch directory
with `--timeframes 1m,5m,15m,1h,4h`.

`tests/test_backtest_harness.py::TestSampleData` checks the committed
files against the generator, so a change to the generator must be
accompanied by regenerating these files.

## What it is not

Nothing here resembles a real market. The symbol does not exist. Do not
draw conclusions about any strategy from numbers produced on it; its
only job is to prove the plumbing from file to report works.
