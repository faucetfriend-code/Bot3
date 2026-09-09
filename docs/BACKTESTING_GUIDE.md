# Backtesting Guide — Trading Bot v2

How to check candle coverage, run a backtest, **read the signal funnel**, sweep every
strategy, run chunked validation, and optimize parameters.

**Last updated:** 2026-07-28 — rewritten against the canonical candle store, the
data-coverage guard, the signal-funnel diagnostics and the validation runner.

Every flag, path and env var below was read from current source. The console output
quoted in this guide is real, captured from actual runs — see
[Verification status](#verification-status) for exactly which commands were executed.

---

## Contents

1. [Cheat sheet](#cheat-sheet)
2. [Rules that will bite you first](#rules-that-will-bite-you-first)
3. [Step 1 — Check data coverage](#step-1--check-data-coverage)
4. [Step 2 — Run a single backtest](#step-2--run-a-single-backtest)
5. [Funding: the one structural cost in the model](#funding-the-one-structural-cost-in-the-model)
6. [Step 3 — Read the signal funnel](#step-3--read-the-signal-funnel)
7. [Step 4 — Sweep every strategy](#step-4--sweep-every-strategy)
8. [Step 5 — Chunked validation and the promotion gate](#step-5--chunked-validation-and-the-promotion-gate)
9. [Step 6 — Optimize, then explain](#step-6--optimize-then-explain)
10. [Reading the results](#reading-the-results)
11. [Tuning parameters](#tuning-parameters)
12. [Troubleshooting](#troubleshooting)

---

## Cheat sheet

Run everything from the repo root (`Bot3/`), always as `python -m ...`.

```bash
# Coverage of the canonical candle store (add 1m - the default omits it)
python -m trading_bot_v2.data_manager --coverage --timeframes 1m,5m,15m,1h,4h

# Top up the trailing edge of every store to now
python -m trading_bot_v2.data_manager --update

# One strategy, one symbol, with an HTML report and a funnel block
python -m trading_bot_v2.backtesting.run_backtest \
    --symbol SUI-USDC --start 2024-03-01 --end 2024-06-30 \
    --strategy MeanReversion --report backtest_report.html

# Every strategy in isolation, ranked
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol SUI-USDC

# Chunked validation + promotion gate verdict (6 x 2mo spread over 8 years)
python -m trading_bot_v2.validation.runner \
    --strategies mean_reversion --symbols SUI-USDC --once

# ...and the plan + runtime estimate without running anything
python -m trading_bot_v2.validation.runner --strategies all --dry-run

# Parameter optimization
python -m trading_bot_v2.optimization --strategy mean_reversion --trials 100

# Which (strategy, regime) pairs have the sample to be tuned separately?
python -m trading_bot_v2.validation.regime_census --quiet

# Does the regime label mean anything, and does a volatility taxonomy do better?
# (--data-dir must be ABSOLUTE from a worktree; see docs/REGIME-VOLATILITY.md)
DATA_AUTODOWNLOAD=false python -m trading_bot_v2.analysis.regime_discrimination \
    --mode taxonomy --iterations 2000 --quiet --data-dir /abs/path/to/backtesting/data

# Why did those trials not trade?
python -m trading_bot_v2.diagnostics.explain --strategy mean_reversion

# Tests
python -m pytest trading_bot_v2/tests/ -q
```

---

## Rules that will bite you first

### `.env` overrides your shell, not the other way round

`config.py` calls `load_dotenv(override=True)`. That means **`.env` clobbers environment
variables you exported in the shell.** You cannot vary a strategy parameter for a single
backtest by exporting it.

Verified directly:

```bash
$ MA_CROSSOVER_SLOW_PERIOD=200 python -c "
import os; from trading_bot_v2.config import config
print(os.getenv('MA_CROSSOVER_SLOW_PERIOD'))"
30
```

The export was silently replaced by the `.env` value. **To change a strategy parameter,
edit `.env`.**

The exception is any variable `.env` does not set — those still come from the shell.
`DATA_AUTODOWNLOAD` is in that category and works as a per-run override:

```bash
# Run fully offline (no public API calls)
DATA_AUTODOWNLOAD=false \
    python -m trading_bot_v2.backtesting.run_backtest --symbol BTC-USDC
```

**`BACKTEST_DATA_DIR` is NOT overridable from the shell.** `.env:207` sets it to the
relative path `trading_bot_v2/backtesting/data`, so `load_dotenv(override=True)`
clobbers anything you export. Two separate agents lost time to this — commands appear
to accept the override and silently read the wrong store. Consequences:

- **Always run backtests from the main checkout root.** Because the configured path is
  relative, running from a git worktree resolves it to that worktree's own (parquet-less)
  data directory, and you get "Loaded 0 candles" or a silently truncated window.
- To point at a different store, use the explicit flag — not the shell:

```bash
python -m trading_bot_v2.backtesting.run_backtest --symbol BTC-USDC \
    --data-dir "C:/Users/z_shi/Desktop/N8NPROJECTS/Bot3/trading_bot_v2/backtesting/data"
```

  It prints `Candle store: <path>` so you can see which store a run actually read.
  `data_manager` has had `--data-dir` all along; `validation.runner` and
  `optimization` (added 2026-07-29) have it too, and all of them print the store they
  resolved. `run_strategy_sweep` does not, so for that one the workaround is still to
  edit `.env`, or set `config.backtest_data_dir` in-process after importing
  `trading_bot_v2.config`.

**Varying a parameter `.env` DOES set, without editing `.env`.** Import
`trading_bot_v2.config` first - that is what runs `load_dotenv(override=True)` - then
mutate `os.environ`, then import and call the entry point. Strategy parameters are read
by `StrategyManager.__init__` at construction time, which is after that:

```python
import os
import trading_bot_v2.config  # runs load_dotenv(override=True)

os.environ["VWAP_SD_ENTRY_THRESHOLD"] = "2.5"

from trading_bot_v2.validation.runner import main
main(["--strategies", "vwap_scalping", "--symbols", "BTC-USDC", "--once"])
```

This is how the threshold curve in `docs/VWAP-LEVERS.md` was produced from a git
worktree without touching the main checkout's `.env`.

To see what your `.env` actually resolves to rather than guessing, print it:

```bash
python -c "
import os
from trading_bot_v2.config import config
for k in sorted(os.environ):
    if k.startswith(('BACKTEST_','MOMENTUM_','VWAP_','MEAN_REVERSION_','GRID_','MA_CROSSOVER_','LIQUIDATION_')):
        print(k, '=', os.environ[k])"
```

`.env` is found by walking **up** from the package directory, so a git worktree under
`Bot3/.claude/worktrees/` inherits the main checkout's `Bot3/.env`.

### Always `python -m`, never bare `pytest`

```bash
python -m pytest trading_bot_v2/tests/ -q     # works
pytest trading_bot_v2/tests/ -q               # ModuleNotFoundError: No module named 'trading_bot_v2'
```

Bare `pytest` does not put the repo root on `sys.path`, so collection fails outright.

### Candle data is not in git

`trading_bot_v2/backtesting/data/*.parquet` is gitignored. A **fresh clone or any git
worktree has no parquets**, only the legacy 2024 CSVs that are tracked. Consequences:

- `test_backtest_e2e.py` skips its whole module (7 tests) with
  `real candle data not present in ...`. That is expected, not a failure.
- `--coverage` reports zeros, because it reads the parquet store only — not the CSVs.
- Backtests still run off the CSVs, but only across the range those CSVs cover.

Point at the real store instead of re-downloading:

```bash
python -m trading_bot_v2.data_manager --coverage --timeframes 1m,5m,15m,1h,4h \
    --data-dir /path/to/Bot3/trading_bot_v2/backtesting/data
```

### `.pyc` files are tracked in this repo

156 `.pyc` files are committed. This pollutes `git status` and `git diff`, and makes
`git stash` hazardous. Filter to source when reviewing:

```bash
git status --short -- '*.py' '*.md'
git diff -- '*.py' '*.md'
```

Stale bytecode can also serve old code after an edit. If behaviour does not match source:

```powershell
Get-ChildItem -Recurse -Filter "*.pyc" | Remove-Item -Force
```

---

## Step 1 — Check data coverage

The canonical store is per-`(symbol, timeframe)` parquet under
`trading_bot_v2/backtesting/data/`, managed by `trading_bot_v2/data_manager.py`.

```bash
python -m trading_bot_v2.data_manager --coverage --timeframes 1m,5m,15m,1h,4h
```

> **The `--timeframes` default is `5m,15m,1h,4h` — it omits `1m`.** A plain `--coverage`
> call therefore hides the 1m stores entirely, which are the ones the engine hard-fails
> on. Always pass `1m` explicitly when you are checking whether a window will run.

Real output from the maintained store:

```
symbol     tf   first                last                   candles  gaps  largest_gap
--------------------------------------------------------------------------------------
BTC-USDC   1m   2018-01-01T00:00:00  2026-07-28T11:35:00    4499808    31  2018-02-08..09
BTC-USDC   5m   2017-08-17T04:00:00  2026-07-28T11:30:00     939272    34  2018-02-08..09
BTC-USDC   15m  2017-08-17T04:00:00  2026-07-28T00:00:00     313052    33  2018-02-08..09
BTC-USDC   1h   2015-01-01T00:00:00  2026-07-28T00:00:00     101425     0  -
BTC-USDC   4h   2015-01-01T00:00:00  2026-07-28T00:00:00      25357     0  -
ETH-USDC   1m   2017-08-17T04:00:00  2026-07-28T10:27:00    4696236    35  2018-02-08..09
ETH-USDC   1h   2016-05-18T00:00:00  2026-07-28T00:00:00      89103    62  2016-05-20..23
SUI-USDC   1m   2023-05-03T12:00:00  2026-07-28T10:30:00    1701991     0  -
SUI-USDC   4h   2023-05-03T12:00:00  2026-07-28T00:00:00       7090     0  -
```

Practical floors: BTC 1h/4h reach 2015, BTC/ETH 5m/15m reach 2017-08-17 (Binance
listing), SUI everything starts 2023-05-03. The `gaps` column counts internal holes —
the 2018-02-08 gap is real exchange downtime and is not fillable.

### Backfilling and topping up

```bash
# Extend the trailing edge of every store to now (cheap, run this routinely)
python -m trading_bot_v2.data_manager --update

# Full backfill of one symbol/timeframe from a start date
python -m trading_bot_v2.data_manager --symbols SUI-USDC --timeframes 1m --start 2023-01-01

# Ingest an external read-only parquet store (BTV2 layout, e.g. BTCUSDT_5m.parquet)
python -m trading_bot_v2.data_manager --ingest-dir "G:/Candle Data"

# Perpetual funding history (separate store, see the Funding section)
python -m trading_bot_v2.data_manager --symbols BTC-USDC --funding-only --start 2019-09-01
python -m trading_bot_v2.data_manager --symbols BTC-USDC --coverage --funding
```

Sources are public and keyless: Binance spot klines, Bitstamp (pre-Binance 1h/4h),
Coinbase (1h). Spot is used as a proxy for Pacifica perp prices.

### The funding store

`--funding` adds a `{SYMBOL}_funding.parquet` store to the same directory, with the same
canonical timestamp convention as the candles. It is reported under the pseudo-timeframe
`fund` so it shows up in the ordinary coverage table:

```
symbol     tf   first                last                   candles  gaps  largest_gap
--------------------------------------------------------------------------------------
BTC-USDC   fund 2019-09-10T08:00:00  2026-07-29T00:00:00       7542     0  -
```

7542 settlements, zero gaps, 2019-09-10 to now. Columns are `timestamp`,
`funding_rate`, `mark_price`, `rate_type`. `mark_price` is null for the early years
(Binance did not publish it then); `rate_type` is `Regular` for every BTC row so far and
exists so a special settlement can never be mistaken for a scheduled one.

The source is Binance USD-M perpetual funding
(`fapi.binance.com/fapi/v1/fundingRate`, keyless, 1000 settlements per page). Rates are
stored **exactly as published, per 8-hour interval**. No rescaling happens at ingest.

### Auto-download during a run

`BacktestDataLoader` fills leading/trailing gaps on demand, controlled by:

| Env var | Default | Meaning |
|---|---|---|
| `DATA_AUTODOWNLOAD` | `true` | Master switch for loader-side downloads |
| `DATA_AUTODOWNLOAD_TIMEFRAMES` | `5m,15m,1h,4h` | Allowlist — **`1m` is deliberately excluded** |

1m is excluded because multi-year 1m pulls are enormous. When a run needs 1m it does not
have, you get a warning naming the exact backfill command, not a download.

Set `DATA_AUTODOWNLOAD=false` for reproducible offline runs.

---

## Step 2 — Run a single backtest

```bash
python -m trading_bot_v2.backtesting.run_backtest \
    --symbol SUI-USDC --start 2024-03-01 --end 2024-06-30 \
    --strategy MeanReversion --report backtest_report.html
```

| Flag | Default | Notes |
|---|---|---|
| `--symbol` | `BACKTEST_SYMBOL` (`SUI-USDC`) | |
| `--start` / `--end` | `BACKTEST_START_DATE` / `_END_DATE` (2024-01-01 / 2024-12-31) | |
| `--capital` | `BACKTEST_INITIAL_CAPITAL` (10000.0) | |
| `--strategy` | `BACKTEST_STRATEGY` (empty = all) | One display name |
| `--report` | `backtest_report.html` | |
| `--walk-forward` | off | Rolling train/test; writes `backtest_wf_NN.html` per window |

Strategy display names: `MeanReversion`, `MACrossover`, `GridTrading`,
`LiquidationCapture`, `VWAPScalping`, `MomentumScalping`, `FundingArb`,
`OrderBookImbalance`, `SessionRangeBreakout`, `CalendarFlow`.

### One strategy cannot be backtested at all

`OrderBookImbalance` is **force-excluded** from every backtest. It depends on real L2
orderbook depth, which the candle store does not contain and `SimulatedExchange` cannot
invent. The engine logs:

```
SKIPPING OrderBookImbalance in backtest mode: not backtestable (depends on
live-only data surfaces - real L2 orderbook depth / funding-history API - that
SimulatedExchange cannot provide)
```

The sweep reports it as `N/A (not backtestable)` rather than a zero row. **Do not spend
time chasing its zeros, and do not include it in optimization runs.**

`FundingArb` was excluded alongside it until 2026-07-29. It is now backtestable against
real ingested funding history — see [Funding](#funding-the-one-structural-cost-in-the-model)
below, and read that section before believing any FundingArb number, because the rates
come from a **different venue on a different settlement clock**.

### The 1m coverage guard

The replay loop always feeds a 1m execution slice to the pipeline. If 1m data does not
cover the window, the engine refuses to run instead of silently serving wrong-date
candles:

```
ValueError: 1m candle data for SUI-USDC does not cover the requested backtest window
2023-01-01 .. 2023-03-31 (available 1m coverage: 2024-01-01T00:00:00 ..
2025-01-01T00:00:00). Backfill it with: python -m trading_bot_v2.data_manager
--symbols SUI-USDC --timeframes 1m
```

Do what the message says. **This hard guard exists only for 1m.** The higher timeframes
rely on auto-download; if `DATA_AUTODOWNLOAD=false` and your 5m store stops early, the
replay window silently shrinks to whatever is on disk. Check `bars_evaluated` in the
funnel against the window you asked for (a 5m replay is ~288 bars/day).

### Warmup and history lookback

| Env var | Default | Meaning |
|---|---|---|
| `BACKTEST_HISTORY_LOOKBACK` | `60` | Candles of rolling history handed to each strategy, per timeframe |
| `BACKTEST_WARMUP_CANDLES` | `0` | Candles loaded *before* the window start; `0` = mirror the lookback |

**The lookback trap.** A strategy whose longest indicator needs more than
`BACKTEST_HISTORY_LOOKBACK` candles emits *nothing at all* — not fewer signals, none.
MA Crossover needs `max(slow_period + 1, macd_slow + macd_signal)` 4h candles, so a slow
MA above ~59 exceeds the default 60-candle window. It now logs loudly instead of
returning silently:

```
MACrossover SUI-USDC: insufficient 4h history - 'close' has 60 candles but 201 are
required (slow_ma_period=200, macd=26+9). This disables the strategy entirely: raise
the caller's history lookback (BACKTEST_HISTORY_LOOKBACK in backtests, lookback_candles
live) or lower MA_CROSSOVER_SLOW_PERIOD.
```

If you want the classic 50/200 pair, raise `BACKTEST_HISTORY_LOOKBACK` to at least 201
first. Note that lookback applies per timeframe and increases memory and runtime.

### Window length floor

4h regime detection needs about 29 candles (~4.8 days) before it classifies anything.
Combined with warmup, **windows shorter than about a week can legitimately produce zero
trades.** Use at least a month for anything you intend to interpret.

---

## Funding: the one structural cost in the model

### It has always been charged, and it was always the wrong number

`SimulatedExchange._apply_funding` has debited open positions since the simulator was
written — it is not a stub. Every backtest this project has ever run charged funding.
What it charged was a **flat constant** `BACKTEST_FUNDING_HOURLY_PCT=0.0001` at every
hourly settlement, with a fixed sign: longs always pay, shorts always receive.

Measured against the real series now in the store (BTC, 7542 settlements, 2019-09 to
2026-07):

| | flat model | real BTC funding |
|---|---|---|
| Mean rate per Binance 8h settlement | — | 0.00010651 |
| Implied hourly rate (pro-rata) | 0.0001 | 0.0000133 |
| Annualized carry | **87.6%** | **11.66%** |
| Sign | always positive | negative on 14.5% of settlements |

`0.0001` is the mean Binance **8-hour** rate applied as an **hourly** one. The flat model
is therefore ~7.5x too expensive and cannot express a funding flip at all.

**How much did that distort the 8-year campaign? Almost nothing.** A/B on BTC-USDC over
three 2-month windows (2021-03, 2022-06, 2024-03), flat vs historical, seeded engine:
closed-trade counts and profit factors were **identical to three decimal places for all
six chart strategies**; total return moved by at most 0.02 percentage points. The reason
is sizing, not luck — the engine risks 2% of balance per trade and holds for hours, so
funding on a $200 notional is cents either way. The overcharge only bites a strategy that
holds a large position for a long time, which is exactly `funding_arb`: under the flat
model it lost 1.25-2.20% per 2-month window purely to carry.

### The interval mismatch (read this before trusting any funding number)

Binance settles every **8 hours**. Pacifica settles every **1 hour**
(`exchanges/base.py::ExchangeCapabilities.funding_interval_hours`: Pacifica 1, Blofin 8).
Binance is the only source with deep keyless history, so it is the best available
**signal** — but it is not the cash Pacifica would actually move.

`backtesting/funding.py` makes the mapping explicit instead of burying it in a constant:

```
venue_rate = observed_8h_rate * BACKTEST_FUNDING_SCALE * factor(conversion)
```

| `BACKTEST_FUNDING_CONVERSION` | factor | Assumes |
|---|---|---|
| `prorata` (default) | `venue_hours / source_hours` = 1/8 | Both venues carry the same **annualized** cost and differ only in slice size. Funding is a rate per unit time, so this is the economically neutral reading. |
| `identity` | 1 | Pacifica quotes the same *number* hourly that Binance quotes 8-hourly, i.e. 8x the carry. Almost certainly wrong — but it is exactly what the shipped flat default assumed, so it is kept to reproduce and bound that error. |

`BACKTEST_FUNDING_SCALE` (default 1.0) is the knob for the cross-venue basis. **It is 1.0
because nobody has measured it, not because it is known to be 1.0.** Using Binance rates
to price Pacifica funding is a MODELLING ASSUMPTION with an unmeasured error term. Every
`funding_arb` result carries that caveat.

The venue interval itself is read from the selected exchange adapter's capabilities, not
hardcoded, so switching `EXCHANGE=blofin` moves settlement to 8h and the pro-rata factor
to 1. `BACKTEST_FUNDING_INTERVAL_HOURS` overrides it for experiments.

### Causality

A settlement stamped `T` is the rate that was **paid** at `T`. For a bar at time `t` the
schedule serves the last settlement with `fundingTime <= t`. That lags the true accrual by
up to one source interval, and that is deliberate: using the settlement that *covers* the
bar is lookahead, and a funding strategy that only works with lookahead is not a strategy.

### Switching models

| Env var | Default | Meaning |
|---|---|---|
| `BACKTEST_FUNDING_MODEL` | `flat` | `flat` = the shipped constant; `historical` = the ingested series |
| `BACKTEST_FUNDING_CONVERSION` | `prorata` | Source-to-venue rate mapping |
| `BACKTEST_FUNDING_SCALE` | `1.0` | Unmeasured cross-venue basis multiplier |
| `BACKTEST_FUNDING_INTERVAL_HOURS` | venue capability | Override the settlement cadence |
| `BACKTEST_FUNDING_HOURLY_PCT` | `0.0001` | Flat-model rate (ignored under `historical`) |

**The default is `flat` only because every published number in this repo was produced
under it**, and changing the default silently would invalidate the campaign without anyone
noticing. It is not the better model. `historical` is, and `funding_arb` is meaningless
without it.

A run under `historical` whose window predates 2019-09-10 logs an ERROR naming the
uncovered range. Nothing is charged there and `FundingArb` sees no rate — those bars are
silently funding-free, which is a hole, not a zero.

`BacktestResult.total_funding_paid` is now populated (positive = paid out) and printed in
the summary. It was declared and never assigned before, so funding was invisible in every
report even though it was hitting the balance.

### Backtests are now seeded

`SimulatedExchange` randomises SL/TP fill order when both trigger in one candle. That draw
used to come from the process-global `random` module, so **the same window over the same
data could give different results**: measured on `vwap_scalping`/BTC-USDC 2022-06..08,
PF 0.9485 vs 1.0287 on consecutive runs in one process. It now draws from a per-exchange
`random.Random(BACKTEST_SEED)` (default 0) and reproduces exactly. This corrects
`docs/FOLLOW-UPS.md` 8e, which asserted the engine was deterministic.

---

## Step 3 — Read the signal funnel

This is the most useful thing in the guide. Every backtest, sweep and optimize run now
prints a funnel block **on both success and failure**. It turns "0 trades" from a mystery
into a diagnosis.

### An annotated real run

This is actual output from `run_backtest --symbol SUI-USDC --strategy MeanReversion`:

```
==============================================================================
SIGNAL FUNNEL: MeanReversion | SUI-USDC
==============================================================================
  Outcome                    traded                                        # (1)
                             255 raw signals over 8641 bars produced 24
                             closed trades; largest attrition at
                             execution_blocked
  --------------------------------------------------------------------------
  Stage                           Count   Attrition                        # (2)
  --------------------------------------------------------------------------
  bars_evaluated                   8641   -
  strategy_invoked                 2280   -
  raw_signals                       255   -
  execution_blocked                 231   90.6% of raw
  orders_placed                      24   -
  fills                              48   -
  closed_trades                      24   -
  --------------------------------------------------------------------------
  BY STRATEGY                                                              # (3)
    strategy                    invoked      raw   dropped
    mean_reversion                 2280      255         0
  --------------------------------------------------------------------------
  Regimes (bars)             ranging_volatile=3193, ranging_calm=2280,     # (4)
                             trending_strong=2232, indecisive=600,
                             trending_moderate=336
  --------------------------------------------------------------------------
  BINDING CONSTRAINT: execution_blocked                                    # (5)
    exec:same_direction_skip                          180
    exec:hedge_mode_block                              51
  --------------------------------------------------------------------------
  SUGGESTED FIX: signals reached execution but were blocked                # (6)
                 (exec:same_direction_skip) - check hedge mode, min-hold
                 candles and position sizing
==============================================================================
```

**(1) Outcome** — the shape of the funnel, one of six classifications. This is the first
thing to read; it tells you *which kind of problem* you have. See the table below.

**(2) Stage counts** — the pipeline, top to bottom. Read it as a waterfall:
8641 bars replayed → the regime selected MeanReversion on 2280 of them → those produced
255 raw signals → 231 died at execution → 24 became orders → 48 fills (open + close) →
24 closed trades. The `Attrition` column shows each drop stage as a percentage of raw
signals.

Note `strategy_invoked` (2280) exactly equals the `ranging_calm` bar count (2280): this
strategy is regime-gated to RANGING_CALM, so it was never even asked on the other 6361
bars. **That is not a bug** — it is the regime mapping working.

**(3) By strategy** — matters when running all strategies together; it attributes raw
signals and drops per strategy so one noisy strategy does not mask another.

**(4) Regimes** — how many bars fell in each regime. If your strategy's regime never
occurred in the window, that is your answer, and no parameter change will help. Pick a
different window or symbol.

**(5) Binding constraint** — the single stage that lost the most candidates, plus the
ranked rejection reasons behind it. **This is the line to act on.** Here, 180 signals
were skipped because a same-direction position was already open, and 51 were blocked by
hedge mode (Pacifica does not allow opposing positions).

**(6) Suggested fix** — generated from the binding stage and the sampled parameters.
Treat it as a starting hypothesis, not gospel.

### The six outcomes

| Outcome | What it means | What to do |
|---|---|---|
| `traded` | Closed at least one trade | Still read the binding constraint — the largest attrition is actionable even at a good Sharpe |
| `no_data` | Zero bars evaluated | Data problem. Check coverage and the window |
| `never_invoked` | Bars evaluated, but the regime filter never selected this strategy | Wrong window or symbol for this strategy's regime. Check the `Regimes` line |
| `no_opportunities` | Invoked, but no setup ever matched | Genuinely nothing there, or thresholds are too tight. Loosen the entry condition |
| `structurally_blocked` | Invoked, zero signals, and a gate was declared unreachable | A threshold is above the metric's mathematical ceiling. Fix the config — no amount of data will help |
| `all_discarded_downstream` | Signals were generated and every one was thrown away | Read the binding constraint; the drop stage names the culprit |

The distinction between `no_opportunities` and `structurally_blocked` is the point of the
whole system: both used to print "0 trades".

### Rejection reason prefixes

| Prefix | Meaning |
|---|---|
| `data:` | The input bundle was unusable (quality check, missing regime timeframe) |
| `regime:` | No strategy was active for the detected regime |
| `strategy:` | Not initialized, threw, or lacked orderbook data |
| `confidence:` | Below the regime's confidence threshold |
| `conflict:` | Lost conflict resolution to another strategy's signal |
| `validity:<flag>` | Failed one of the 8 validation flags |
| `exec:` | Reached the execution layer and was blocked there |
| `structural:` | A gate was declared unreachable for this data |

A second real example — same repo, MACrossover, a zero-trade run that is **not** a
regime problem:

```
  Outcome                    all_discarded_downstream
                             36 signals generated, 0 survived to a closed
                             trade - dropped at validity_dropped, top reason
                             validity:volume_confirmation (36 times)
  ...
  raw_signals                        36   -
  validity_dropped                   36   100.0% of raw
  ...
  BINDING CONSTRAINT: validity_dropped
    validity:volume_confirmation                       36
  SUGGESTED FIX: every signal failed the volume_confirmation validity flag
                 - relax that gate or fix the strategy so it stops emitting
                 signals that cannot pass
```

100% attrition at a single validity flag is unambiguous: `MA_CROSSOVER_VOLUME_THRESHOLD`
is unreachable on this data. That is a five-second diagnosis that used to be a
multi-hour hunt.

### The 8 validity flags

A signal must have all eight `True`: `volume_confirmation`,
`multi_timeframe_alignment`, `support_resistance_valid`, `rrr_meets_minimum`,
`liquidation_buffer_safe`, `account_risk_ok`, `margin_drawdown_ok`,
`forbidden_conditions_clear`. A `validity:` reason names exactly which one failed.

---

## Step 4 — Sweep every strategy

The primary comparison tool. Each strategy runs in its own subprocess, so there is no
shared state and no log bleed.

```bash
# All strategies on the default symbol
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol SUI-USDC

# A subset, custom window and capital
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC \
    --start 2024-06-01 --end 2024-12-31 --capital 5000 \
    --strategies MomentumScalping VWAPScalping GridTrading

# Per-strategy HTML reports into reports/<SYMBOL>/
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC --save-reports
```

`--strategies` is validated against the full list, so a typo fails fast rather than
silently running nothing.

Real output:

```
  Excluded : FundingArb, OrderBookImbalance  (not backtestable)

  [01/02]  MeanReversion             -0.22%  24 closed  3.7s
  [02/02]  MACrossover               0 trades  4.1s

  === Results: SUI-USDC  2024-03-01 -> 2024-04-30 ===

  Strategy         Return    Sharpe  MaxDD  WinRate  ProfFactor  PSR    Calmar  Closed  Fees   Outcome
  ------------------------------------------------------------------------------------------------------
  MeanReversion    -0.22%    -0.15   2.3%   45.8%    0.78        0.309  -0.57   24      $4.88  traded
    MACrossover      0 trades fired  [all_discarded_downstream]
  FundingArb       N/A (not backtestable)
  OrderBookImbalance N/A (not backtestable)
  ------------------------------------------------------------------------------------------------------

  Verdicts

  MeanReversion   -> MARGINAL  PF 0.78  WR 45.8%  return -0.22%  (24 closed)  UNVALIDATED (N trials unknown)
  MACrossover     -> 0 trades (regime not matched or insufficient data)
```

Then, for every zero-trade strategy, the sweep prints a **full funnel block** — so a
zero row is always accompanied by its diagnosis.

The `PSR` column and the `DSR-PASS` / `DSR-FAIL` / `UNVALIDATED` tag come from the trial
registry: Deflated Sharpe adjusts for how many configurations were tried. `UNVALIDATED
(N trials unknown)` means nothing was ever recorded for that strategy — it is not a
failure, just an absence of multiple-testing correction.

---

## Step 5 — Chunked validation and the promotion gate

`validation/runner.py` is an independent service. It never imports the live bot runtime,
so it can run while the bot trades. Instead of one long backtest it evaluates a **series
of smaller windows** (house preference) and pools the results.

```bash
# See the plan and the runtime estimate before committing to a run
python -m trading_bot_v2.validation.runner --strategies mean_reversion \
    --symbols BTC-USDC,ETH-USDC,SUI-USDC --dry-run

# Default: 6 windows of 2 months spread across the last 8 years
python -m trading_bot_v2.validation.runner --strategies mean_reversion \
    --symbols SUI-USDC,BTC-USDC --once

# The legacy layout: 3 abutting windows at the trailing edge
python -m trading_bot_v2.validation.runner --strategies mean_reversion \
    --window-mode recent --windows 3 --window-months 2 --once

# Every ENABLE_*-enabled strategy, topping up candle data first
python -m trading_bot_v2.validation.runner --strategies all --refresh-data --once

# Run continuously
python -m trading_bot_v2.validation.runner --strategies all --loop-hours 24
```

| Flag | Default | Env override |
|---|---|---|
| `--strategies` | `all` | — |
| `--symbols` | `SUI-USDC,BTC-USDC` | `VALIDATION_SYMBOLS` |
| `--window-months` | `2` | `VALIDATION_WINDOW_MONTHS` |
| `--windows` | `6` | `VALIDATION_WINDOWS` |
| `--window-mode` | `spread` | `VALIDATION_WINDOW_MODE` |
| `--span-years` | `8` | `VALIDATION_SPAN_YEARS` |
| `--shared-windows` | off (per-symbol series) | `VALIDATION_PER_SYMBOL_SPAN=false` |
| `--dry-run` | off | — |
| `--capital` | `10000` | — |
| `--refresh-data` | off | `VALIDATION_REFRESH_DATA` |
| `--data-dir` | config | — |
| `--anchor-end` | store trailing edge | `VALIDATION_ANCHOR_END` |
| `--loop-hours` / `--once` | `--once` | — |

`--anchor-end` pins the newest window to a fixed date instead of the store's advancing
trailing edge, which is the fix for the reproducibility problem in FOLLOW-UPS 8e. It only
ever moves the anchor earlier. `--data-dir` **must be absolute when running from a git
worktree** — `BACKTEST_DATA_DIR` in `.env` is relative and `config.py` loads it with
`override=True`, so exporting the variable does nothing.

### Window modes

`spread` (default) distributes the N windows evenly across `--span-years` ending at the
data anchor. Runtime depends only on `N x window-months`, **not** on the span, so 8 years
of calendar reach costs the same as 12 contiguous months — you just get out-of-sample
seams between every pair of windows instead of one recent block. `recent` is the legacy
contiguous layout walking backward from the anchor.

Six months of contiguous data is a single regime epoch; a strategy that grades well there
may simply be fitted to it. That is what the default is widened away from.

### Per-symbol spans

Symbols list at different times: BTC-USDC 1m starts 2018-01-01, ETH-USDC 2017-08-17,
SUI-USDC 2023-05-03. By default each symbol gets its **own** window series over its own
1m-clamped coverage, and the summary prints the spans so an 8-year BTC record is never
silently compared against a 3-year SUI one:

```
  Symbol     Data span (1m-clamped)     Months  Windows evaluated                     Note
  BTC-USDC   2018-01-01 .. 2026-07-28      102  2018-07-28 -> 2026-07-28 (6 windows)
  ETH-USDC   2017-08-18 .. 2026-07-28      107  2018-07-28 -> 2026-07-28 (6 windows)
  SUI-USDC   2023-05-04 .. 2026-07-28       38  2023-05-04 -> 2026-07-28 (6 windows)  SHORTER HISTORY
  Per-symbol history is ASYMMETRIC: the pooled verdict weighs a long record against a
  short one. Read the cross-symbol check per symbol, not as an average.
```

`SHORTER HISTORY` marks a symbol whose usable history starts 6+ months after the
longest-history symbol in the run.

`--shared-windows` forces one intersected series instead — comparable window-for-window,
but SUI's 2023 listing then truncates BTC's history too.

Anchors and span starts **clamp to 1m coverage at both ends**, so a window can never be
requested that the engine would reject. The 1m start rounds up to a whole day (SUI listed
at 12:00, and a window starting at that day's midnight is not covered).

### Regime coverage

Every run prints a per-window regime breakdown built from the funnel diagnostics. This is
the whole point of a multi-year span — if a strategy only ever traded one regime across
eight years, that is the finding:

```
  Symbol     Window                         Bars  Regimes (share of bars)
  BTC-USDC   2018-07-28 -> 2018-09-28      17856  trending_strong 64%, ranging_calm 24%, indecisive 5%
  BTC-USDC   2022-06-28 -> 2022-08-28      17568  trending_strong 57%, ranging_calm 21%, trending_moderate 10%
  SUI-USDC   2024-11-04 -> 2025-01-04      17568  trending_strong 44%, ranging_calm 30%, ranging_volatile 12%
  POOLED (104496 bars over 6 windows)
    trending_strong           62844   60.1%
    ranging_calm              22140   21.2%
  5 regimes observed; most common trending_strong at 60% of bars.
```

A regime above 70% of all bars prints a `WARNING: ... single regime epoch` line: a PASS
earned there is not evidence of robustness.

Verdicts are persisted to the `validation_runs` table (per-chunk regime histograms
included in `chunks_json`) and served read-only by the bot API
(`GET /api/validation/runs`, `GET /api/validation/latest`).

### Runtime

Runtime scales with total replayed bars — `strategies x symbols x windows x
window-months`. Measured here: **~5s per window-month** for a regime-gated strategy.
`--dry-run` prints the estimate up front; tune the constant with
`VALIDATION_SECONDS_PER_MONTH` (default 8, deliberately conservative).

Real output:

```
  Strategy                 Verdict   Trades   PF       PSR/DSR                        Consistent
  ----------------------------------------------------------------------------------------------
  mean_reversion           FAIL      228      0.63     PSR 0.0029 / DSR 0.0000        0
                           regimes: trending_strong 60%, ranging_calm 21%, trending_moderate 7%, indecisive 7%, ranging_volatile 5%
  ----------------------------------------------------------------------------------------------
  windows: 3x2mo@8y | symbols: BTC-USDC,SUI-USDC | shared span: 2023-05-04 .. 2026-07-28
```

The `window_spec` reads `NxMmo@Yy` in spread mode (`NxMmo` in recent mode).

A `FAIL` verdict still exits 0 — the process only returns non-zero on a configuration
error, so do not use the exit code as a pass/fail signal.

### The 4-check promotion gate

| # | Check | Requirement | Env override |
|---|---|---|---|
| 1 | `sample_adequacy` | >= 33 **pooled** closed trades (derived, see below) **and** >= 2 symbols with >= 5 trades each | `GATE_REFERENCE_SR`, `GATE_MIN_TRADES_PER_SYMBOL`, `GATE_MIN_TRADES` |
| 2 | `profit_factor` | Pooled PF > 1.3 | `GATE_MIN_PF`, `GATE_PF_CONFIDENCE` |
| 3 | `psr_or_dsr` | Pooled PSR >= 0.95, **or** DSR >= 0.95 when the trial registry knows N | `GATE_MIN_PSR` |
| 4 | `cross_symbol` | Positive expectancy on >= 2 eligible symbols | fixed |

All four must pass. Check 3 is the multiple-testing defence: if you tried 200
configurations, a Sharpe that looks good is expected by chance, and DSR deflates it.
Check 4 is the overfitting defence: a strategy that only works on one symbol is
usually curve-fitted.

### Why check 1 is derived and pooled

The old check demanded 30 closed trades **per symbol**, which measures **bar frequency,
not strategy quality**. A 5m grid strategy clears it in a fortnight; `ma_crossover`
trades on 4h bars with a 5-bar entry window, so a two-month window (~360 bars) contains
maybe 10-20 crossovers and the check was *structurally unreachable* — guaranteed FAIL
regardless of how good the strategy was.

The replacement asks a statistical question: how many observations does the PSR need
before an edge worth deploying would be provable at the confidence we demand? That is
`min_observations_for_sharpe(GATE_REFERENCE_SR, GATE_MIN_PSR)` — the MinTRL formula run
forwards. At the defaults (Sharpe-per-trade 0.30, 95% confidence) it derives **33**,
which is where the legacy "30" was informally aiming. Raise `GATE_MIN_PSR` to 0.99 and
the requirement rises to 64 on its own; there is no second number to keep in sync.

It is applied to the **pooled** sample (all symbols, all windows) because pooling is how
a slow strategy accumulates evidence. The per-symbol number drops to a floor of 5 whose
only job is deciding which symbols are allowed to vote in check 4 — a symbol with 2
trades has no measurable expectancy.

### Four outcomes, not two

A 3-trade sample cannot support a confident verdict in *either* direction, and calling
that FAIL reads identically to a genuine rejection. The gate reports a `GateOutcome`:

| Outcome | Meaning | Exit code (CLI) |
|---|---|---|
| `PASS` | Adequate sample, every check cleared | 0 |
| `FAIL` | Adequate sample, a quality check failed — a real rejection | 1 |
| `INSUFFICIENT_DATA` | It traded, but not enough to decide. **Not** a rejection | 2 |
| `NO_TRADES` | Zero closed trades — a plumbing question, not a quality one | 2 |

On an inadequate sample the quality checks are still computed and printed but marked
`advisory` (`PASS*`/`FAIL*`) — they are not decisive, and `verdict.passed` is False
either way, so a thin sample can never promote anything.

The remedy for `INSUFFICIENT_DATA` is **more data, not a lower bar**. The verdict carries
`data_multiple_needed` and the reason line names a concrete window, extrapolated from the
trade rate actually observed rather than from a hand-maintained table of per-strategy
frequencies:

```
OVERALL: INSUFFICIENT_DATA
REASON:  sample too small to decide: pooled 10 < 33; only 0 symbol(s) with >= 5 trades
         - needs ~3.3x more data (about 20 calendar months, i.e. 3x7mo)
         - lengthen the window rather than lowering the bar
```

`gate.required_window_months(observed_trades, observed_calendar_months)` exposes the same
calculation for callers that want to re-run automatically.

### The PF/PSR asymmetry

`GATE_MIN_PSR` being uniform is *correct*: the PSR is a significance test with a
`sqrt(n-1)` term, so a small sample already needs a proportionally larger Sharpe to reach
0.95. It is the mechanism that makes "small sample, larger effect required" true.

`GATE_MIN_PF` is the one that is genuinely sample-size blind — a profit factor is a point
estimate of a ratio with no confidence interval, so PF 1.5 on 4 trades and PF 1.5 on 400
score identically. Setting `GATE_PF_CONFIDENCE=true` switches check 2 to a one-sided
bootstrap lower bound on the PF (deterministic, fixed seed), which shrinks toward 1.0 as
the sample shrinks. It is **off by default because it is materially stricter** — a true
PF of 1.5 needs several hundred trades before its 95% lower bound clears 1.3.

### The first honest measurement of `funding_arb`

Run 2026-07-29, shipped parameters, nothing tuned:

```bash
DATA_AUTODOWNLOAD=false BACKTEST_FUNDING_MODEL=historical \
python -m trading_bot_v2.validation.runner --strategies funding_arb \
    --symbols BTC-USDC --data-dir <abs path> --anchor-end 2026-07-01 --once
```

```
  Strategy                 Verdict   Trades   PF       PSR/DSR                        Consistent
  funding_arb              FAIL      9        0.57     PSR 0.2925 / DSR n/a           0
                           regimes: trending_strong 70%, ranging_calm 15%, ...
  windows: 6x2mo@8y | symbols: BTC-USDC | shared span: 2018-01-01 .. 2026-07-01

OVERALL: INSUFFICIENT_DATA   (the summary line prints non-PASS as FAIL)
  sample_adequacy   FAIL   9 pooled / 1 eligible symbols   >= 33 and >= 2 symbols
  profit_factor     FAIL*  0.57                            > 1.3        [advisory]
  psr_or_dsr        FAIL*  PSR 0.2925                      >= 0.95      [advisory]
  cross_symbol      FAIL*  0                               >= 2 symbols [advisory]
```

Per-window funnel — this is the finding, not the PF:

```
window                     bars    raw orders   closed       PF    funding
2018-07-01..2018-09-01    17765      0      0        0        -      -0.00   <- no funding data
2020-02-01..2020-04-01    17173     18     18        9    0.569      -4.24
2021-09-01..2021-11-01    17544      0      0        0        -      -0.00
2023-03-01..2023-05-01    17552      0      0        0        -      -0.00
2024-10-01..2024-12-01    17568      0      0        0        -      -0.00
2026-05-01..2026-07-01    17568      0      0        0        -      -0.00

POOLED: bars_evaluated 105170 | raw_signals 18 | orders_placed 18 | fills 18 | closed 9
```

**Five of six windows produced zero signals.** Nothing was dropped — attrition is empty,
18 raw signals became 18 orders became 18 fills. The strategy simply never fired. Its own
threshold `FUNDING_ARB_MIN_RATE=0.0001` is compared against a **per-settlement** rate, and
under the pro-rata mapping Pacifica's hourly rate averages 0.0000133. Requiring 0.0001
hourly is requiring an 87.6% annualized carry — roughly the top 1% of all settlements
since 2019. Every trade it made came from one window: the COVID crash of March 2020.

Two separate conclusions, and they must not be merged:

1. **Measurable now**: `funding_arb` is no longer silently excluded, and the exception
   that used to swallow its signals is gone.
2. **Not validated**: 9 trades from a single regime epoch cannot support a verdict in
   either direction. The remedy is more data (and one symbol can never clear check 4),
   not a lower bar. The threshold/interval mismatch above is a real parameter-provenance
   bug worth fixing *before* re-measuring — but fixing it to make the verdict look better
   is exactly the kind of tuning this gate exists to catch.

You can run the gate standalone against a single window:

```bash
python -m trading_bot_v2.validation.gate --strategy mean_reversion \
    --symbols SUI-USDC,BTC-USDC --start 2024-01-01 --end 2025-01-01
```

It prints a per-check `PASS`/`FAIL` table with the observed value against the threshold,
then an `OVERALL:` line and, when the outcome is not `PASS`, a `REASON:` line.

---

## Step 6 — Optimize, then explain

```bash
python -m trading_bot_v2.optimization --strategy mean_reversion --trials 100
python -m trading_bot_v2.optimization --all --trials 50
python -m trading_bot_v2.optimization --list
```

> Prefer `python -m trading_bot_v2.optimization` over
> `python -m trading_bot_v2.optimization.run_optimize`; both work, but the latter emits a
> `RuntimeWarning` about double-importing the package.

Optimizable strategies: `mean_reversion`, `ma_crossover`, `grid_trading`,
`liquidation_capture`, `vwap_scalping`, `funding_arb`, `momentum_scalping`,
`orderbook_imbalance`. In practice skip the last two overlays — they cannot be
backtested, so every trial scores identically.

Useful flags: `--sampler tpe|random`, `--objective` (default `sharpe_ratio`),
`--walk-forward`, `--regime RANGING_CALM` (score only trades entered in that regime),
`--min-trades`, `--save-overlay` (persist best params as the active overlay for a
`(strategy, regime)` pair; requires `--regime`), `--export results.csv`,
`--data-dir` (absolute path to the candle store - required from a worktree).

`--chunked` cuts its windows with `resolve_chunk_windows`, which reads
`VALIDATION_WINDOW_MODE` (not set in `.env`, so a shell export works). Export
`VALIDATION_WINDOW_MODE=recent` for a **contiguous** series; the default `spread`
samples a fraction of the calendar and will understate any strategy that trades
slowly.

Studies persist in `trading_bot_v2/optimization/optimization_studies.db` (untracked).

### The chunked sweep is a walk-forward (`--chunked`)

`--chunked` does **not** fit and score on the same data. It cuts the same chunk window
series the validation runner uses (`resolve_chunk_windows`) and runs a **rolling-origin
walk-forward** over it: with windows W1..WN oldest→newest, fold *i* runs a full chunked
Optuna study on the preceding window(s) and grades the winning parameters on Wi — data
no trial ever saw.

```bash
# 3 x 2-month windows on 3 symbols -> 2 folds, 40 trials each
python -m trading_bot_v2.optimization --strategy ma_crossover --chunked \
    --symbols BTC-USDC,ETH-USDC,SUI-USDC \
    --windows 3 --window-months 2 --trials 40
```

| Flag | Default | Meaning |
|---|---|---|
| `--windows` / `--window-months` | `3` / `2` | The chunk series to cut folds from |
| `--train-windows` | `1` | Chunk windows per training set (needs `--windows` > this) |
| `--anchored` | off | Expanding training set instead of rolling |
| `--trials` | `100` | Trials **per fold** (total = trials x folds) |
| `--in-sample-only` | off | **Legacy**: fit and score on the same windows |

`--trials` is per fold, so a 3-window run costs `trials x folds x symbols` backtests.
Fewer, shorter windows means faster feedback — that is the point of the chunking.

The report prints in-sample beside out-of-sample on one banded scale, then:

```
  IN-SAMPLE vs OUT-OF-SAMPLE (same banded scale)
    In-sample  (optimized on) :     1.800
    OUT-OF-SAMPLE  (HEADLINE)  :    -1.200
    Overfit gap (IS - OOS)    :     3.000
    VERDICT: OVERFIT - positive in sample, non-positive out of sample.
```

**The headline number is out-of-sample.** A large `Overfit gap` is the overfitting
signal. A winner that produces no trades out of sample is banded and diagnosed by the
signal funnel (`OOS outcome:` / `OOS funnel:` lines), not scored as zero.

Every fold records its trial count in `trial_registry`, and the report reads that back
and deflates the Sharpe for it (`DSR ... N=<total>`), so the search cost is charged
against the result. The same out-of-sample series is then run through the standing
4-check promotion gate and printed.

`--in-sample-only` reproduces the pre-2026-07-28 behaviour. It logs a warning because
the result is unfalsifiable: with 60 trials over 3 symbols some parameter set looks
excellent by chance, and nothing separates that from an edge.

### Regime-conditional tuning (`--regime`)

`--regime RANGING_CALM` scores every trial on the closed trades whose **entry
regime** matches, and prunes trials with fewer matching trades than the
gate-derived minimum (33 at the current settings - `REGIME_OPT_MIN_TRADES`
still overrides). It **composes with `--chunked`**, which is the only form
worth running:

```bash
python -m trading_bot_v2.optimization --strategy momentum_scalping \
    --chunked --regime TRENDING_STRONG \
    --symbols BTC-USDC,ETH-USDC,SUI-USDC \
    --windows 3 --window-months 2 --trials 40
```

Each fold optimizes on its train window(s), the winner is graded on the
held-out window, and the standing 4-check gate is applied to the
regime-filtered out-of-sample series. Without `--chunked` the run fits and
scores on one contiguous window and nothing is falsifiable.

**Check the sample before spending the budget.**
`python -m trading_bot_v2.validation.regime_census` reports closed trades per
(strategy, regime) over the same window series and marks each cell tunable or
not against the derived requirement. Only 9 of 30 cells clear it today, and
only 3 of those have a profit factor above 1 - see `docs/REGIME-CENSUS.md`.
A regime cell below the bar can only ever return `INSUFFICIENT_DATA`, and the
study's trials are still charged to the pooled strategy's deflated Sharpe.

`python -m trading_bot_v2.optimization.run_regime_optimization` is the older
per-regime orchestrator. It is **in-sample only** (and its `--walk-forward`
does not refit per fold - it discards the first `train_months` and evaluates
one parameter set on windows every trial also sees). Prefer
`--chunked --regime`.

### Banded trial scoring

Zero-trade trials are no longer scored as flat zero or negative infinity. Each trial is
scored by **funnel depth** within a reserved band, so TPE can distinguish "never fired"
from "fired but blocked at the last step" by rank, and steer toward configurations that
at least reach signal generation. This is why an optimization run that produces no
profitable trial can still make progress.

### Explaining a study

```bash
python -m trading_bot_v2.diagnostics.explain --strategy mean_reversion
python -m trading_bot_v2.diagnostics.explain --study <exact_study_name> --top 3
python -m trading_bot_v2.diagnostics.explain --study <name> --trial 17
python -m trading_bot_v2.diagnostics.explain --study <name> --all
```

It reads funnel diagnostics off each trial's `user_attrs` (zero extra storage), prints an
outcome histogram for the study, then a full funnel block per selected trial. Trials are
ranked most-informative-first: traded trials, then by funnel depth.

Studies created before the diagnostics package say so rather than pretending:

```
STUDY: mean_reversion_RANGING_VOLATILE_sharpe_20260720_071054  (25 trials)
  unknown                      25

No funnel diagnostics stored on this study (it predates the diagnostics package).
```

Re-run the study to get diagnostics.

---

## Reading the results

### Superseded numbers: the equity-accounting fix (2026-07-29)

`SimulatedExchange.get_account_balance()` used to report `cash +
unrealised_pnl`. Because opening a position debits its whole cost basis
(`quantity * entry_price`) from cash and only credits it back on close, an
open position read as an instant loss of its entire notional. Equity now adds
that cost basis back (`SimulatedExchange.equity()`), pinned by
`trading_bot_v2/tests/test_simulated_equity.py`.

**Every equity-curve metric published before this date is wrong and should be
re-run before being quoted.** Measured on BTC-USDC, 2022-07-01..2024-07-01:

| strategy | metric | before | after |
|---|---|---|---|
| VWAPScalping | return % | -4.95 | **-3.01** |
| | max DD % | 5.06 | **3.13** |
| | Sharpe | +0.163 | **-10.939** |
| MomentumScalping | return % | 0.99 | 0.99 |
| | max DD % | 2.25 | **0.26** |
| | Sharpe | +0.251 | **+3.482** |
| GridTrading | max DD % | 4.20 | **0.51** |
| | Sharpe | +0.104 | **-2.055** |

Trade counts, profit factor and win rate are **identical in every arm** - the
engine sizes from `exchange.balance` (raw cash) directly, so no trade changed.

Read the Sharpe column carefully: before the fix, all three strategies sat in
a narrow +0.10..+0.25 band and could not be told apart, because the phantom
equity swing as each position opened and closed dominated the variance. After
it, they separate by sign and magnitude. **Any past decision made on backtest
Sharpe, Sortino, Calmar or max drawdown was made on noise.** Max drawdown was
inflated by 3.7x to 8.6x.

**What is NOT superseded**, because these paths already read realised trade
P&L rather than the equity curve:

- `profit_factor`, `win_rate_pct`, `closed_trades` and the trade log.
- The **promotion gate and the chunked validation runner**, including check 3
  (`psr_or_dsr`): `validation/runner.py` builds its returns from
  `closed_trade_returns(result.trade_log, capital)`. The 2026-07-28 8-year
  campaign verdicts stand.
- `docs/REGIME-CENSUS.md` - `validation/regime_census.py` sums `trade["pnl"]`.
- **Regime-conditional** Optuna objectives, which recompute from trade P&L
  (`optimization_adapter.py`). **Pooled/full-run** objectives do read
  `result.sharpe_ratio` / `total_return_pct` / `max_drawdown_pct`, so any
  full-run study optimising Sharpe, Calmar or return was optimising a
  distorted metric and is superseded.

| Metric | Meaning | Target |
|---|---|---|
| Return | Total % on starting capital | > 0% |
| Sharpe | Annualised risk-adjusted return | > 0.5 acceptable, > 1.0 good |
| Max DD | Largest peak-to-trough equity drawdown | < 10% acceptable, < 5% good |
| Win Rate | % of closed trades profitable | Depends on RRR — see below |
| Profit Factor | Gross profit / gross loss | > 1.0 profitable, > 1.5 good; gate wants > 1.3 |
| PSR | Probabilistic Sharpe: P(true Sharpe > 0) | Gate wants >= 0.95 |
| Calmar | Annualised return / max drawdown | > 0.5 good |
| Closed Trades | Round trips with realised PnL | 30+ for statistical validity |
| Total Fills | All executions including opens | Should be Closed Trades x 2 |
| Fees | Total taker/maker fees | If fees > 50% of gross profit, unviable at that frequency |
| Outcome | Funnel diagnosis | See [Step 3](#step-3--read-the-signal-funnel) |

### Break-even win rate by RRR

The minimum win rate to break even is `1 / (1 + RRR)`.

| ATR Stop | ATR Target | RRR | Break-even WR |
|---|---|---|---|
| 1.5x | 2.5x | 1.67:1 | 37.5% |
| 1.5x | 3.0x | 2.0:1 | 33.3% |
| 1.5x | 3.5x | 2.33:1 | 30.0% |
| 2.0x | 3.0x | 1.5:1 | 40.0% |
| 2.0x | 4.0x | 2.0:1 | 33.3% |
| 2.0x | 5.0x | 2.5:1 | 28.6% |

If `Win Rate < Break-even WR`, the strategy loses money regardless of any other setting.

### Cost model

| Env var | Default | Effect |
|---|---|---|
| `BACKTEST_SLIPPAGE_PCT` | `0.002` | Higher simulates worse execution; hurts high-frequency strategies most |
| `BACKTEST_TAKER_FEE_PCT` | `0.0006` | Fee drag per fill |
| `BACKTEST_MAKER_FEE_PCT` | `0.0002` | Limit-order cost |
| `BACKTEST_FUNDING_HOURLY_PCT` | `0.0001` | Pacifica funding is **hourly** (24x/day), not 8h |

### Structural facts about the engine

- **Replay is driven by 5m candles.** 15m/1h/4h are indexed as-of each 5m bar; 1m is used
  for SL/TP placement precision.
- **`hedge_mode = False`** (`BACKTEST_HEDGE_MODE`). Pacifica allows no opposing positions,
  so a signal against an open position is dropped. Strategies never close each other's
  trades — only SL/TP do. This is what produces `exec:hedge_mode_block` and
  `exec:same_direction_skip`.
- **Strategies are regime-gated.** Zero trades may simply mean the regime never occurred.
  The funnel's `Regimes (bars)` line tells you.

---

## Tuning parameters

All strategy parameters live in `.env` at the repo root. **Edit `.env`, re-run** — every
sweep subprocess re-reads it fresh.

This guide deliberately does **not** table your current `.env` values. The previous
version did, and every number in it had drifted by the time anyone read it. Print your
own instead (see [the command above](#env-overrides-your-shell-not-the-other-way-round)),
and read code defaults from the strategy modules, where they are the `os.getenv` fallback.

### Code defaults worth knowing

| Parameter | Code default | Note |
|---|---|---|
| `MA_CROSSOVER_FAST_PERIOD` / `SLOW_PERIOD` | 20 / 50 | The shipped `.env` runs the validated **10/30** pair, not 20/50 |
| `MEAN_REVERSION_RSI_OVERSOLD` / `OVERBOUGHT` | 35 / 65 | |
| `VWAP_SD_ENTRY_THRESHOLD` | 2.0 | Range-checked, see below |
| `MOMENTUM_ATR_STOP_MULTIPLIER` / `TARGET` | 1.5 / 2.5 | |
| `MOMENTUM_MIN_RRR` | 1.5 | Momentum's RRR is constant, so this decides whether it can trade at all |
| `GRID_SPACING_ATR_MULTIPLIER` | 0.4 | Below ~1.0x, levels often cannot cover round-trip fees |

`MA_CROSSOVER_MIN_ENTRY_BARS` / `MA_CROSSOVER_MAX_ENTRY_BARS` appear in `.env` but **the
code does not read them.** The entry window is hardcoded as the class constants
`MIN_ENTRY_BARS = 1` and `MAX_ENTRY_BARS = 5` in
`trading_bot_v2/strategies/ma_crossover.py`. Changing them in `.env` does nothing.

### Config guards: when the bot overrides you

Two strategies now refuse configurations that would silently disable them. If you see
these warnings, the run did **not** use the value you set.

**VWAP** — `VWAP_SD_ENTRY_THRESHOLD` must be within `[1.0, 3.0]`. This strategy uses a
rolling cumulative VWAP whose deviation distribution is tight (mean ~1.0 SD, observed max
~4.0 SD), so a higher threshold is mathematically unreachable and produces zero signals:

```
VWAP_SD_ENTRY_THRESHOLD=4.037 is outside the supported range [1.0, 3.0] used by the
optimizer search space. Values above the upper bound are unreachable for this strategy's
rolling cumulative VWAP (observed max deviation is ~4.0 SD) and produce ZERO signals.
Falling back to 2.0. Fix VWAP_SD_ENTRY_THRESHOLD in .env.
```

VWAP also warns about leftover env vars it does not read (`VWAP_ATR_TARGET_MULTIPLIER`,
`VWAP_ENTRY_MODE`, `VWAP_TP_MODE`, `VWAP_USE_HTF_EMA` and others) — imports from the BTV2
tuning harness, which used a different entry model.

**Momentum** — stop and target are multiples of the same ATR, so implied RRR is a
constant. If it falls below `MOMENTUM_MIN_RRR`, every signal would fail
`rrr_meets_minimum`, so the target is repaired upward:

```
MomentumScalping: atr_stop=2.5x / atr_target=3.0x implies RRR 1.20 < min_rrr 1.50 -
every signal would be discarded by validation. Raising target to 3.83x (stop is the
validated parameter and is left unchanged).
```

### Diagnosing from the sweep

| Symptom | Likely cause | First thing to change |
|---|---|---|
| High trade count, negative return | Overtrading / fee drag | Raise cooldown, tighten entry thresholds |
| Low win rate (< 30%) | Entry conditions too loose | Tighten RSI/SD/volume thresholds |
| Win rate OK but still losing | RRR too tight | Raise ATR target multiplier |
| Win rate > 60% but small return | TP too close, exiting early | Raise ATR target multiplier |
| 0 trades | **Read the funnel** — regime, structural block, or downstream discard | Whatever `BINDING CONSTRAINT` names |
| Fees > gross profit | Too many small trades | Widen stop+target, raise cooldown |

Change **one or two parameters at a time**, for **one strategy at a time**, and keep the
window fixed across comparisons. Never set an ATR target multiplier below the stop
multiplier — that inverts RRR below 1.0:1.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'trading_bot_v2'`** — you ran bare `pytest` or a
script directly. Use `python -m` from the repo root.

**`ValueError: 1m candle data ... does not cover ...`** — run the backfill command in the
error message.

**Zero trades** — read the funnel block. `never_invoked` means the regime never occurred;
`structurally_blocked` means a threshold is unreachable; `all_discarded_downstream` means
the binding stage names your culprit.

**`Strategy GridTrading is active but not initialized`** — expected noise during a
single-strategy backtest. The regime detector selected a strategy that `--strategy`
disabled. Harmless.

**Your `.env` edit had no effect** — confirm you edited `.env` and not the shell, and that
the code actually reads that variable (see the `MIN_ENTRY_BARS` case above).

**Results identical across different `--end` dates** — your parquet store probably ends
before both dates, silently truncating the window. Check coverage including 1m, and
compare `bars_evaluated` against the days you requested.

**Performance tests are excluded from the default run.** `pyproject.toml` sets
`addopts = "-m 'not perf'"`, so a clean suite reports `1461 passed, 1 deselected`. To run
them, on a quiet machine:

```bash
python -m pytest trading_bot_v2/tests/ -m perf
```

The two long-standing "known timing flakes" were retired on 2026-07-28 — they were not
flaky tests of real properties, they were tests of nothing. `test_check_regression` ran
the benchmark suite twice *in the same process* and asserted no regression appeared:
both sides were identical code, so it had zero power to detect a slowdown and noise was
its only possible failure mode. It also compared means; across 224 same-code comparisons
the mean drifted up to +212% while the median stayed within +19%. The funnel-overhead
test derived a ~0.06% answer from the difference of two ~0.35s wall-clock loops, and a
null-vs-null control (true answer 0%) reported up to +5.5% — its noise floor exceeded
its own 3% budget.

Both now measure what they claim: the regression check compares medians against a
locally generated baseline and detects an 11% slowdown deterministically, and the funnel
test times the funnel calls directly (overhead 0.056-0.059%, noise floor +/-0.001%).
Measured full-suite pass rate went from 2/5 to 5/5.

**Data-dependent tests skip** — expected in any git worktree; the parquets are gitignored.

**Loguru output missing from a test** — pytest's `caplog` does not intercept loguru in
this repo. Add a sink:

```python
from loguru import logger as loguru_logger
messages = []
sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="WARNING")
...
loguru_logger.remove(sink_id)
```

### Suite baseline

```
python -m pytest trading_bot_v2/tests/ -q
1067 passed, 7 skipped, 1 failed in ~158s
```

The 7 skips are `test_backtest_e2e.py` (no parquets in a worktree) and the 1 failure is
the profiler flake above. In the main checkout with the data present, those 7 run.

---

## Verification status

Executed while writing this guide (output quoted above is genuine):

- `data_manager --coverage` (with and without `1m`, and with `--data-dir`)
- `run_backtest` — single strategy, plus the 1m-coverage-guard `ValueError`
- `run_strategy_sweep` — including the non-backtestable exclusion path
- `validation.runner --once`
- `optimization --list` and `diagnostics.explain --strategy`
- `python -m pytest trading_bot_v2/tests/ -q`, and bare `pytest` (to confirm it fails)
- The `.env`-clobbers-shell check

Read from source but **not executed** here — flags and defaults are accurate, output
shape is not quoted: `data_manager --update` / `--ingest-dir` / full backfill (network
writes), `run_backtest --walk-forward`, `run_strategy_sweep --save-reports`,
`validation.gate` standalone CLI, `optimization --strategy ... --trials N`, and the
`--study` / `--trial` / `--all` forms of `diagnostics.explain`.

---

*Internal use only — Pacifica Solana Perps*
