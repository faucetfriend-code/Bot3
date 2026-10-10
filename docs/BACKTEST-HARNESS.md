# Backtest harness

One command that runs a backtest end to end, offline, from local candle
files to a written report. It is a thin layer over the existing
`trading_bot_v2/backtesting/` package (engine, simulated exchange, cost
model, funding model, performance tracker), not a second framework.

```bash
# From the repo root. Runs the committed synthetic sample, writes
# backtesting/reports/harness/<symbol>_<start>_<end>_<strategies>.{json,txt}
python -m trading_bot_v2.backtesting.run_harness
```

No network access happens at any point: the loader is built in offline
mode, a missing file is an error, and the strategies and engine are the
unmodified live modules.

## Contents

1. [What you get](#what-you-get)
2. [Running it](#running-it)
3. [Data loader](#data-loader)
4. [Strategy interface](#strategy-interface)
5. [Two replay modes](#two-replay-modes)
6. [Report](#report)
7. [Sample data](#sample-data)
8. [Tests](#tests)
9. [What is and is not reused](#what-is-and-is-not-reused)

## What you get

On the committed sample the default command prints this (fees and
funding are the engine's shipped defaults: 0.06 % taker, 0.02 % maker,
0.2 % slippage, flat 0.01 % hourly funding) and writes the same thing to
`backtesting/reports/harness/`:

```
================================================================
BACKTEST HARNESS  SYN-USDC  2024-01-08 -> 2024-01-30
mode=direct  strategies=mean_reversion, ma_crossover
================================================================
  Initial capital : 10,000.00
  Final equity    : 10,021.83
  Net PnL         : +21.83
  Total return    : +0.22%
  CAGR            : +3.69%
  Sharpe          : 2.30
  Sortino         : 2.21
  Max drawdown    : 0.19%  (peak 2024-01-25T21:25:00, trough 2024-01-28T13:45:00, recovered 2024-01-29T06:50:00)
  Longest u/water : 181.2h
  Win rate        : 53.7%
  Profit factor   : 1.25
  Fills / closed  : 109 / 54
  Fees            : 10.77
  Funding paid    : 3.93

  Per strategy:
    ma_crossover         fills=0    closed=0    win=n/a     pf=n/a    pnl=n/a          fees=n/a       calls=5905 signals=7 errors=0
    mean_reversion       fills=109  closed=54   win=53.7%   pf=1.25   pnl=+23.11       fees=10.77     calls=6336 signals=1101 errors=0

  Data:
     5m: OK 8640 rows 2024-01-01T00:00:00 .. 2024-01-30T23:55:00
    15m: OK 2880 rows 2024-01-01T00:00:00 .. 2024-01-30T23:45:00
     1h: OK 720 rows 2024-01-01T00:00:00 .. 2024-01-30T23:00:00
     4h: OK 180 rows 2024-01-01T00:00:00 .. 2024-01-30T20:00:00
================================================================
```

The numbers mean nothing about the strategies: the data is synthetic.
What the run proves is the plumbing: files are read and validated, two
live strategies are driven on every bar, fills are costed, funding is
charged, and every metric lands in the JSON.

Why `ma_crossover` shows 7 signals and 0 fills: the harness shares one
simulated account across the listed strategies, exactly as the engine
does, and the engine allows one position per symbol. MeanReversion was
already in a position on each bar MACrossover signalled, so the engine
dropped the signal (`exec:same_direction_skip` or
`exec:hedge_mode_block` in the funnel block of the JSON). Run
`--strategies ma_crossover` alone and it fills.

## Running it

```bash
# What can be driven
python -m trading_bot_v2.backtesting.run_harness --list-strategies

# One strategy, a different window, no files written
python -m trading_bot_v2.backtesting.run_harness \
    --strategies ma_crossover --start 2024-01-10 --end 2024-01-30 --no-write

# Constructor overrides per strategy (JSON object keyed by registry key)
python -m trading_bot_v2.backtesting.run_harness \
    --strategies ma_crossover \
    --params '{"ma_crossover": {"fast_ma_period": 10, "slow_ma_period": 30}}'

# Your own store: files named {SYMBOL}_{tf}.csv, .csv.gz or .parquet
python -m trading_bot_v2.backtesting.run_harness \
    --data-dir trading_bot_v2/backtesting/data --symbol BTC-USDC \
    --start 2024-03-01 --end 2024-03-21 --strategies mean_reversion

# The live StrategyManager path (needs 1m candles too, see below)
python -m trading_bot_v2.backtesting.run_harness --mode pipeline \
    --data-dir /path/to/store-with-1m
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--data-dir` | the committed sample | Directory of `{symbol}_{tf}` files |
| `--symbol` | `SYN-USDC` | File prefix |
| `--start` / `--end` | `2024-01-08` / `2024-01-30` | Window (ISO date or datetime) |
| `--strategies` | `mean_reversion,ma_crossover` | Registry keys or display names, replay order |
| `--mode` | `direct` | `direct` or `pipeline` |
| `--capital` | `10000` | Starting cash |
| `--lookback` / `--warmup` | from config | History candles per timeframe, pre-window candles |
| `--params` | none | JSON constructor overrides per strategy |
| `--output-dir` | `backtesting/reports/harness` | Where the two files go (gitignored) |
| `--basename` | derived | File stem |
| `--no-write` | off | Print only |
| `--log-level` | `INFO` | Level for the harness's own modules |
| `--strategy-log-level` | `ERROR` | Level for strategies and engine (they log a WARNING per low-confidence signal) |

Exit code 0 on success, 2 on a data or configuration error (missing
file, failed validation, window outside the store, unknown strategy).

The same thing from Python:

```python
from trading_bot_v2.backtesting.harness import HarnessConfig, run_harness

run = run_harness(HarnessConfig(
    data_dir="trading_bot_v2/backtesting/sample_data",
    symbol="SYN-USDC", start="2024-01-08", end="2024-01-30",
    strategies=["mean_reversion", "ma_crossover"],
    output_dir=None,            # or a Path to write json + txt
))
run.result        # BacktestResult (the engine's own result type)
run.report        # the JSON document as a dict
run.summary       # the text summary
run.adapters      # StrategyAdapter list with call/signal/error counters
run.validations   # {timeframe: CandleValidation}
```

Everything else the engine reads from `.env` still applies (strategy
parameters, `BACKTEST_*` cost and policy knobs, `BACKTEST_FUNDING_MODEL`).
There is no `.env` in a fresh clone, so the defaults in `config.py` are
what the sample numbers above were produced with.

## Data loader

`trading_bot_v2/backtesting/ohlcv.py` reads one timeframe from `.csv`,
`.csv.gz` or `.parquet` and validates it before anything replays it:

| Check | Outcome |
|-------|---------|
| Column names | `timestamp,open,high,low,close,volume`; common aliases accepted (`time`, `date`, `datetime`, `o/h/l/c/v`, `vol`) |
| Timezone | tz-aware inputs are converted to UTC and stored naive, canonical `%Y-%m-%dT%H:%M:%S`; the report says so |
| Ordering | out-of-order rows counted; repaired by a stable sort |
| Duplicates | duplicate timestamps counted; repaired, first row wins |
| Grid | a timestamp off the timeframe grid is fatal |
| Gaps | missing bars counted and the first 20 located; **reported, never filled** |
| Values | NaN/inf, non-positive price, `high < low`, open/close outside `[low, high]`, negative volume: fatal |

`BacktestDataLoader` (the loader the engine already uses) gained two
flags: `offline=True` removes every network path (missing file raises
`FileNotFoundError`), `validate=True` routes every read through the
validator and keeps the reports in `loader.validations`. The harness
always sets both. The existing loader also now finds `.csv.gz` stores.

## Strategy interface

`trading_bot_v2/backtesting/strategy_interface.py` pins down what the
live strategies already share:

```python
class BacktestStrategy(Protocol):
    def generate_signals(self, symbol, multi_tf_data, current_price,
                         execution_tf_data=None) -> List[Signal]: ...
```

`multi_tf_data` carries `15m`, `1h`, `4h`; `execution_tf_data` carries
`5m` and, when the store has it, `1m`. Each is an OHLCV dict of equal
length lists oldest first plus a `timestamp` list.

`StrategyAdapter` wraps a live instance and smooths the differences
between them: MACrossover's `generate_signals` takes no
`execution_tf_data`, MomentumScalping swallows it through `**kwargs`,
MeanReversion and VWAPScalping use it. The adapter inspects the
signature once, passes exactly what the strategy declares, propagates
simulated time (`_sim_time`, the same attribute `StrategyManager.set_sim_time`
pokes), exposes `required_history()` where declared, and counts calls,
signals and exceptions for the report. A raising strategy yields no
signals for that bar and a non-zero `errors` count, never a dead run.

Wired up in `STRATEGY_REGISTRY`: `mean_reversion`, `ma_crossover`,
`momentum_scalping`, `vwap_scalping`. Adding one is a `StrategySpec`
entry; nothing in `strategies/` changes.

## Two replay modes

Both share the loader, `SimulatedExchange`, `CostModel`, the funding
model, `BacktestEngine._execute_signal` (SL/TP placement,
anti-pyramiding, opposing-signal policy, entry TTL, trailing and time
exits) and `PerformanceTracker`. They differ in who produces the signals.

**`direct`** (default). Every adapter is called on every 5m bar once its
primary timeframe has `required_history()` candles (35 when a strategy
declares none), and its raw signals go straight to the engine's order
placement. There is no regime gating, no StrategyManager conflict
resolution and no eight-flag validation: this measures the strategy's
own signal logic under the engine's execution model. Needs
`5m/15m/1h/4h`; `1m` is used when present. Strategies run in the listed
order against one shared account, so when two signal on the same bar the
first one's position blocks the second (see the sample output above).

**`pipeline`**. Delegates to `BacktestEngine.run`, i.e. the live
`StrategyManager` with ADX regime admission, directional and confidence
gates, conflict resolution and signal validation. This is what the
existing `run_backtest.py` does, with the offline validated loader
substituted. The engine's coverage guard treats `1m` as an execution
timeframe, so this mode needs 1m candles; the committed sample omits
them for size. Generate the full set and point at it:

```bash
python -m trading_bot_v2.backtesting.sample_data --out /tmp/syn --timeframes 1m,5m,15m,1h,4h
python -m trading_bot_v2.backtesting.run_harness --mode pipeline --data-dir /tmp/syn \
    --start 2024-01-08 --end 2024-01-20
```

`BacktestEngine.run` now accepts a comma-separated `strategy_filter`
("mean_reversion,ma_crossover"), which is how the harness passes a list.

## Report

Two files per run. `<basename>.txt` is the summary printed above.
`<basename>.json` (`schema` = `bot3.backtest-harness/1`) holds:

| Key | Contents |
|-----|----------|
| `run` | symbol, window, mode, strategies and their overrides, data dir, capital, lookback/warmup, slippage, fees, funding model and rate, resolved execution policy |
| `data` | per-timeframe validation: rows, span, duplicates, out-of-order, off-grid, gaps and their locations, tz-aware input, repaired flag, fatal list |
| `metrics` | initial/final equity, net PnL, return, CAGR, Sharpe, Sortino, max drawdown, Calmar, win rate, profit factor (null when no losses), fills, closed trades, total fees, average fee per fill, funding paid |
| `drawdown` | deepest drawdown with peak/trough/recovery stamps, longest underwater stretch in hours |
| `by_strategy` | fills, closed trades, wins, losses, net PnL, fees, win rate, profit factor per strategy |
| `by_regime` | closed trades per entry regime (populated in pipeline mode; `unknown` in direct mode, which has no regime) |
| `adapters` | calls, signals, errors, last error per adapter (direct mode) |
| `trades` | every fill: timestamp, side, quantity, fill price, fee, realised and net PnL, strategy, regime, liquidity role |
| `equity_curve` | one snapshot per replayed 5m bar |
| `diagnostics` | the signal funnel (`SignalFunnel.to_dict()`), including the execution policy and, in direct mode, per-strategy warmup skips |

Metric definitions are the engine's (`performance.py`): Sharpe and
Sortino annualise per-bar equity changes at 8760 hours a year, win rate
and profit factor count closing fills on fee-adjusted net PnL, max
drawdown is on the per-bar equity curve. Per-strategy breakdown was
declared on `BacktestResult` since the first version but never
populated; `finalise` now fills it.

## Sample data

`trading_bot_v2/backtesting/sample_data/` holds 30 days of `SYN-USDC`
at 5m/15m/1h/4h (under 200 KB), generated deterministically by
`trading_bot_v2/backtesting/sample_data.py` and checked against the
generator by the tests. See the README in that directory. It is
synthetic; nothing about it resembles a market.

## Tests

```bash
python -m pytest trading_bot_v2/tests/test_backtest_harness.py -q
```

59 tests: the validator (every check above, every file format, the
offline guarantee with a download manager that raises), the adapter
(signature handling, error counting, sim time, registry, every
registered strategy builds and runs on a sample bundle), the metrics and
report (hand-built trade logs, drawdown location, JSON round trip,
infinities serialised as null), the sample generator (committed files
match, timeframes roll up exactly, validates clean), and end to end
(direct mode on the committed sample with accounting cross-check,
determinism, refusal of uncovered windows and missing files, pipeline
mode on a regenerated full store, the CLI's exit codes).

## What is and is not reused

Reused unchanged: `SimulatedExchange`, `CostModel`, `funding.py`,
`PerformanceTracker` metrics, `BacktestEngine._execute_signal` and its
bookkeeping helpers, `StrategyManager` (pipeline mode), every strategy
class.

Changed inside `backtesting/` only:

- `data_loader.py`: `offline` and `validate` flags, `.csv.gz` support.
- `engine.py`: `loader_factory` constructor hook; the per-run state reset
  factored into `begin_run()` so the direct replay starts from the same
  slate; `strategy_filter` accepts a comma-separated list. Behaviour of
  `run()` with the old arguments is unchanged.
- `performance.py`: `by_strategy` populated (`per_strategy_breakdown`).

New: `ohlcv.py`, `strategy_interface.py`, `harness.py`, `report.py`,
`run_harness.py`, `sample_data.py`, the sample files, this document.

Not done here, by design: no live-trading module was touched, the
existing `run_backtest.py` and its HTML report are left as they are, and
direct mode does not reproduce StrategyManager's validation or
regime admission (that is what pipeline mode is for).
