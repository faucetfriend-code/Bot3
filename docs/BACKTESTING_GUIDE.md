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
5. [Step 3 — Read the signal funnel](#step-3--read-the-signal-funnel)
6. [Step 4 — Sweep every strategy](#step-4--sweep-every-strategy)
7. [Step 5 — Chunked validation and the promotion gate](#step-5--chunked-validation-and-the-promotion-gate)
8. [Step 6 — Optimize, then explain](#step-6--optimize-then-explain)
9. [Reading the results](#reading-the-results)
10. [Tuning parameters](#tuning-parameters)
11. [Troubleshooting](#troubleshooting)

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

# Chunked validation + promotion gate verdict
python -m trading_bot_v2.validation.runner \
    --strategies mean_reversion --symbols SUI-USDC --windows 3 --once

# Parameter optimization
python -m trading_bot_v2.optimization --strategy mean_reversion --trials 100

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
`BACKTEST_DATA_DIR` and `DATA_AUTODOWNLOAD` are currently in that category, which makes
them the two useful per-run overrides:

```bash
# Run fully offline (no public API calls) against a specific store
DATA_AUTODOWNLOAD=false BACKTEST_DATA_DIR=/some/other/data \
    python -m trading_bot_v2.backtesting.run_backtest --symbol BTC-USDC
```

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
```

Sources are public and keyless: Binance spot klines, Bitstamp (pre-Binance 1h/4h),
Coinbase (1h). Spot is used as a proxy for Pacifica perp prices.

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

### Two strategies cannot be backtested at all

`OrderBookImbalance` and `FundingArb` are **force-excluded** from every backtest. They
depend on live-only surfaces (real L2 orderbook depth, the funding-history API) that
`SimulatedExchange` cannot provide. The engine logs:

```
SKIPPING OrderBookImbalance in backtest mode: not backtestable (depends on
live-only data surfaces - real L2 orderbook depth / funding-history API - that
SimulatedExchange cannot provide)
```

The sweep reports them as `N/A (not backtestable)` rather than a zero row. **Do not
spend time chasing their zeros, and do not include them in optimization runs.**

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
# 3 windows of 2 months each, over the configured symbols
python -m trading_bot_v2.validation.runner --strategies mean_reversion \
    --symbols SUI-USDC,BTC-USDC --window-months 2 --windows 3 --once

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
| `--windows` | `3` | `VALIDATION_WINDOWS` |
| `--capital` | `10000` | — |
| `--refresh-data` | off | `VALIDATION_REFRESH_DATA` |
| `--loop-hours` / `--once` | `--once` | — |

Windows are cut walking backward from the most recent candle available offline, and
**anchors clamp to 1m coverage** so a window can never be requested that the engine would
reject. Verdicts are persisted to the `validation_runs` table and served read-only by the
bot API (`GET /api/validation/runs`, `GET /api/validation/latest`).

Real output:

```
  Strategy                 Verdict   Trades   PF       PSR/DSR                        Consistent
  ----------------------------------------------------------------------------------------------
  mean_reversion           FAIL      42       0.78     PSR 0.2500 / DSR n/a (N trials unknown) 0
  ----------------------------------------------------------------------------------------------
  windows: 2x1mo | symbols: SUI-USDC
```

A `FAIL` verdict still exits 0 — the process only returns non-zero on a configuration
error, so do not use the exit code as a pass/fail signal.

### The 4-check promotion gate

| # | Check | Requirement | Env override |
|---|---|---|---|
| 1 | `min_closed_trades` | Every symbol has >= 30 closed trades | `GATE_MIN_TRADES` |
| 2 | `profit_factor` | Pooled PF > 1.3 | `GATE_MIN_PF` |
| 3 | `psr_or_dsr` | Pooled PSR >= 0.95, **or** DSR >= 0.95 when the trial registry knows N | `GATE_MIN_PSR` |
| 4 | `cross_symbol` | Positive expectancy on >= 2 symbols | fixed |

All four must pass. Check 3 is the multiple-testing defence: if you tried 200
configurations, a Sharpe that looks good is expected by chance, and DSR deflates it.
Check 4 is the overfitting defence: a strategy that only works on one symbol is
usually curve-fitted.

You can run the gate standalone against a single window:

```bash
python -m trading_bot_v2.validation.gate --strategy mean_reversion \
    --symbols SUI-USDC,BTC-USDC --start 2024-01-01 --end 2025-01-01
```

It prints a per-check `PASS`/`FAIL` table with the observed value against the threshold,
then an `OVERALL:` line.

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
`(strategy, regime)` pair; requires `--regime`), `--export results.csv`.

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

**`test_profiler_benchmark.py::TestPerformanceBenchmark::test_check_regression` fails** —
known timing flake. It passes in isolation and fails under full-suite load. Do not panic
and do not "fix" it.

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
