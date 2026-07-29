# VWAP scalping: three levers, measured

**Run date:** 2026-07-29. **Verdict: no.** None of the three levers proposed for
`vwap_scalping` produces an edge, and the third one explains why the other two
cannot: the strategy's gross, frictionless edge is about **1.8 basis points per
round trip** and it pays about **14 basis points** to trade. Nothing that
re-partitions or re-thresholds a 2 bp signal closes an 8 bp gap.

Everything below is BTC-USDC only, contiguous, seeded (`BACKTEST_SEED=0`),
`DATA_AUTODOWNLOAD=false`, anchored at `2026-07-01`, cost profile `pacifica`,
funding model `flat`. Reproduce commands are inline.

| lever | best result | trades | vs shipped PF 0.74 |
|---|---|---|---|
| 1. regime mapping | PF **0.81** (`ranging_volatile, indecisive`) | 1308 | +0.07, for -73% sample |
| 2. entry threshold, fixed sweep | PF **0.86** (at the 3.0 guard ceiling) | 448 | +0.12, for -91% sample |
| 2. entry threshold, **out-of-sample walk-forward** | PF **0.85**, Sharpe **-0.086** | 1230 | +0.11, IS/OOS gap 0.357 |
| 3. execution costs | PF 1.14 only at **zero fees and zero slippage** | 1216 | unreachable |

Nothing clears 1.0. The gate wants 1.3.

---

## Why contiguous, and why that matters here

`docs/REGIME-CENSUS.md` was cut from `6x2mo@8y` **spread** windows - 12 of 96
months. That understated `momentum_scalping` badly (see the 8-year contiguous
re-run: PF 1.84 on 306 trades). It **over**stated VWAP's best cell just as
badly, in the other direction, and the correction is the first result here.

---

## Lever 1 - the regime mapping

The census's headline VWAP finding was that its losses concentrate in
`ranging_calm` (PF 0.65, n=1246) while `indecisive` was the best cell in the
whole census (PF 1.05, n=337 across three symbols). Removing a losing mapping
costs zero search budget, so it deflates nothing. It was the strongest of the
three leads.

`VWAP_ACTIVE_REGIMES` now makes the mapping configurable
(`strategy_manager.py::resolve_vwap_active_regimes`); it was a literal list
inside `generate_signals_for_market`. Trending regimes are refused whatever the
env asks for - VWAP's entries are counter-trend by construction.

```bash
VWAP_ACTIVE_REGIMES=INDECISIVE \
python -m trading_bot_v2.validation.runner --strategies vwap_scalping \
    --symbols BTC-USDC --window-mode recent --windows 48 --window-months 2 \
    --anchor-end 2026-07-01 --data-dir <abs path> --once
```

(`VWAP_ACTIVE_REGIMES` is not set in `.env`, so a shell export reaches it.
`VWAP_SD_ENTRY_THRESHOLD` **is**, and `config.py` calls
`load_dotenv(override=True)`, so the threshold sweep below had to set
`os.environ` *after* importing `trading_bot_v2.config`.)

48 contiguous 2-month windows, 2018-07-01 .. 2026-07-01:

| mapping | closed trades | PF | PSR |
|---|---|---|---|
| `ranging_volatile, ranging_calm, indecisive` (shipped) | 4851 | **0.74** | 0.0000 |
| `ranging_calm, indecisive` | 4483 | 0.72 | 0.0000 |
| `ranging_volatile, indecisive` (census recommendation) | 1308 | **0.81** | 0.0125 |
| `indecisive` only | 909 | **0.78** | 0.0145 |

**The census's PF 1.05 does not reproduce.** On BTC's own full history the
`indecisive` cell is **PF 0.78 on 909 closed trades** - 7.6x the sample the
census had for BTC in that cell (119 trades), and it lands within noise of the
pooled 0.74 rather than above 1. The best cell in the census was a
small-sample artifact of the spread layout, exactly as the momentum result
should have warned.

The census's own recommendation - stop mapping VWAP into `RANGING_CALM` - is
the best of the four, and it is **PF 0.81 on 1308 trades**. It buys +0.07 of
profit factor for 73% of the sample. Every variant remains far below 1.0, let
alone the gate's 1.3, and none has a PSR above 0.015 against a 0.95 bar.
**Lever 1 is dead.**

Note what the trade counts say about the mechanism. The `indecisive` trade
count is 902 under the shipped mapping, 907 with `ranging_volatile` alongside,
and 909 alone - a 0.8% spread across configurations that differ by 3900
trades elsewhere. Removing a mapping does **not** unlock trades that hedge
mode had been blocking; the regime subsets are essentially additive. That is
why the pooled number was already telling the truth, and why re-partitioning
it could never have produced a different one.

---

## Lever 2 - the entry threshold

`VWAP_SD_ENTRY_THRESHOLD=2.0` is an interim value
(`docs/PARAMETER-PROVENANCE.md` class **D**) picked after the transplanted
4.037 was found unreachable. The optimizer bounds it to `[1.0, 3.0]`.

### 2a. The curve, at fixed values, over the same 48 contiguous windows

Not a search - four points, reported as a curve, so the shape is visible
before any budget is spent on fitting it.

| `sd_entry_threshold` | closed trades | PF | PSR |
|---|---|---|---|
| 1.5 | 8907 | 0.69 | 0.0000 |
| 2.0 (shipped) | 4851 | 0.74 | 0.0000 |
| 2.5 | 1784 | 0.82 | 0.0039 |
| 3.0 (guard ceiling) | 448 | **0.86** | 0.1327 |

**The direction of the lead was right and it still does not get there.**
Profit factor rises monotonically with selectivity - this is the largest
single effect measured in this document, +0.17 of PF across the range - while
the trade count falls by 20x. But at 3.0, the top of the range
`validate_sd_entry_threshold()` allows, PF is still **0.86**, and PSR is
0.1327 against a 0.95 bar.

Extrapolating VWAP's own curve (about +0.115 of PF per 1.0 of threshold) puts
break-even at roughly **4.2 SD**. The calibration artifacts in
`trading_bot_v2/diagnostics/calibration/` give BTC's `deviation_sd` p99 as
2.589, p99.9 as 3.142 and the observed maximum as 4.562. So the threshold at
which this strategy would stop losing money is above the 99.9th percentile of
the metric it gates on, and within noise of that metric's all-time maximum.

That number should look familiar: **4.037** is the value removed from `.env`
in July 2026 precisely because it produced zero signals
(`docs/PARAMETER-PROVENANCE.md`, `cf20c8b`). VWAP's break-even threshold and
its silencing threshold are, to a first approximation, the same number. The
strategy is not mis-tuned; the tuning range that would fix it is the range in
which it cannot trade.

### 2b. The out-of-sample walk-forward

The curve above is fixed-value, so it fits nothing - but it also cannot find a
threshold that only works alongside a particular stop or cooldown. That is
what the rolling-origin walk-forward is for: each fold runs a full Optuna
study on its training windows and the winner is graded on the next window,
which no trial saw.

```bash
VALIDATION_WINDOW_MODE=recent DATA_AUTODOWNLOAD=false \
python -m trading_bot_v2.optimization --strategy vwap_scalping --chunked \
    --symbols BTC-USDC --windows 16 --window-months 3 --train-windows 2 \
    --trials 12 --end 2026-07-01 --data-dir <abs path> --quiet
```

**Sizing, and why.** Runtime scales with total replayed window-months, which
here is `trials x folds x train_windows x window_months + folds x
window_months` = `12 x 14 x 2 x 3 + 42` = **1050 window-months**. Measured
cost on this machine is ~4.75 s per window-month for a regime-gated strategy
(48 x 2mo BTC validation = 96 window-months in 7m38s), giving an estimate of
**83 minutes**; it took 105 with three other jobs sharing the box. The design
spends its budget on **calendar reach rather than folds**, because VWAP closes
about 50 trades a month on BTC, so even one 3-month test window clears the
gate's 33-trade floor several times over. A slow strategy would have to make
the opposite trade.

| | value |
|---|---|
| windows | 16 x 3mo contiguous, 2022-07-01 .. 2026-07-01 |
| folds | 14 (rolling, 2 train windows each) |
| trials | 12 per fold, **168 total** |
| **OUT-OF-SAMPLE (headline)** | **Sharpe -0.086, PF 0.85, 1230 closed trades** |
| in-sample | Sharpe 0.271 |
| **overfit gap (IS - OOS)** | **0.357** |
| PSR / DSR | 0.0368 / 0.0000 (deflated for N=172) |
| verdict | OVERFIT - positive in sample, negative out of sample |

Two things are worth reading off the fold table beyond the headline.

**The optimizer independently rediscovered lever 2, and it was not enough.**
In 13 of 14 folds the winning `sd_entry_threshold` was **above** the shipped
2.0 (range 2.13 - 2.97, median ~2.4); the single exception is fold 9 at 1.749.
A 4-dimensional search with no knowledge of section 2a converged on the same
answer that section's fixed sweep gives - more selectivity is better - and the
held-out grade is still PF 0.85. The two methods agree, which is the strongest
form this evidence can take.

**Every one of the 14 folds lost money out of sample.** Fold return ranges
from -0.01% to -0.72%, 14 of 14 negative, even in the 11 folds whose
trade-Sharpe objective was positive. A positive per-trade Sharpe with a
negative return is what a costed high-frequency strategy looks like when the
per-trade edge is smaller than the per-trade cost - which is exactly what
lever 3 measures below.

---

## Lever 3 - maker vs taker, and the number that settles it

### The 91% taker share is structural, not a choice

Measured directly off the fill log, BTC-USDC, 2022-07-01 .. 2024-07-01
contiguous, 1241 closed trades / 2483 fills:

| fill kind | count | share |
|---|---|---|
| open, taker | 1242 | 50.0% |
| close, taker (stop) | 1016 | 40.9% |
| close, maker (take-profit limit) | 225 | 9.1% |
| open, maker | **0** | 0% |

Maker share 9.06%, which is exactly `win_rate / 2` (18.13% / 2 = 9.07%). Two
mechanisms produce it and neither is tunable by a parameter:

1. **Every entry is a market order.** `vwap_scalping.py` sets
   `entry_price=current_price`, and `BacktestEngine._execute_signal` places a
   resting limit only when the requested entry is more than
   `LIMIT_ENTRY_PRICE_GAP_PCT` (0.1%) away from the market. At a gap of
   exactly zero, every entry crosses the spread.
2. **82% of exits leave via the stop**, which is a `stop` order and therefore
   a taker fill by construction. Only the 18% that reach the take-profit fill
   as resting limits.

So "VWAP pays taker on ~92% of fills" is not an execution defect to be fixed;
it is a 18%-win-rate strategy that market-orders its entries.

### What better execution could possibly be worth

Three runs over the identical window. The middle one prices **every** fill at
the maker fee - impossible in practice, since a stop can never be a maker
fill - and is therefore a strict upper bound on the fee lever alone. Fill
prices are unchanged, so the trades are the same trades. The third removes
fees and slippage entirely.

```bash
# fee lever, strict upper bound: price the taker fill at the maker fee
BACKTEST_TAKER_FEE_PCT_BTC=0.00015 ...
# frictionless: no fee, no spread, no impact, no volatility drift
BACKTEST_TAKER_FEE_PCT_BTC=0 BACKTEST_MAKER_FEE_PCT_BTC=0 \
BACKTEST_HALF_SPREAD_PCT_BTC=0 BACKTEST_SLIPPAGE_VOL_COEF=0 \
BACKTEST_SLIPPAGE_IMPACT_COEF=0 ...
```

(None of those names appear in `.env`, so shell exports reach them.)

| configuration | closed | PF (pre-fee) | realised P&L | return* |
|---|---|---|---|---|
| shipped (`pacifica` profile) | 1241 | 0.771 | -$301 | -3.01% |
| every fill at the maker fee | 1237 | 0.781 | -$187 | -1.87% |
| zero fees **and** zero slippage | 1216 | 1.138 | **+$44** | +0.44% |

\* `realised P&L - fees - funding`, not the engine's `total_return_pct` - see
"An accounting bug found on the way" below.

Read the last row first. **With no transaction costs at all, two contiguous
years of VWAP on BTC make $44 on $10,000.** That is the entire raw signal.
Against it:

| component | cost over the window | per round trip |
|---|---|---|
| exchange fees | $184 | 7.5 bp |
| slippage | $161 | 6.6 bp |
| **total friction** | **$345** | **14.1 bp** |
| gross edge | +$44 | **1.8 bp** |

The friction bill is **7.9x the gross edge**. Converting only the entries to
resting maker orders - the one change actually available - saves the entry's
fee differential (2.5 bp) and its slippage (about 3.3 bp), call it 6 bp of the
14. That leaves roughly 8 bp of cost against a 1.8 bp signal.

It is a one-line change if it were ever worth making: offset the signal's
`entry_price` past the market in the reversion direction by more than 0.1% and
the existing engine path turns it into a resting limit automatically. It is
not worth making, and it would additionally introduce non-fill and
adverse-selection risk the simulator models optimistically (a resting limit
fills whenever the bar's range touches it, with no queue position and no
expiry).

**Note on `profit_factor`:** it is computed from realised per-trade `pnl`,
which is booked **before** fees (`performance.py:188-192`; `self.balance -= fee`
happens separately). Every PF in this repo is a pre-fee number. That is why
the frictionless row shows PF 1.138 - slippage moves fill prices and therefore
PF, fees do not.

### An accounting bug found on the way

`SimulatedExchange.get_account_balance()` returns `cash + unrealised_pnl`, but
opening a position debits its whole cost basis from cash and only the
unrealised P&L is added back. With zero costs and a flat price, opening a
2-unit position at 100 takes reported equity from 10000.0 to **9800.0**.

Consequences: `BacktestResult.final_equity`, `total_return_pct` and `cagr_pct`
are understated by the terminal open positions' cost basis (about 2% of
capital per position at the shipped `max_risk_per_trade`), and the equity
curve is depressed for the whole time any position is open, which inflates max
drawdown and depresses Sharpe, Sortino, Calmar and PSR. `profit_factor`,
`win_rate`, `closed_trades` and the trade log are computed off realised trade
P&L and are **not** affected.

The return column in the table above is therefore quoted as
`realised P&L - fees - funding`, not as the engine's `total_return_pct`. The
uncorrected engine figures for the same three rows are -4.95% / -3.82% /
-1.56%. **This does not change any conclusion in this document** - every lever
is judged on profit factor and trade counts - but it does mean the campaign's
returns, Sharpes and drawdowns are all slightly wrong in the pessimistic
direction. Fixing it changes every published number in the repo, so it was
left for its own change.

---

## The trial registry

The registry had **12 rows, all `mean_reversion`** (158 trials), and nothing
for any other strategy. Every PSR in this project outside `mean_reversion` is
therefore **undeflated**, including `momentum_scalping`'s PSR 1.0.

The walk-forward populated it. Each fold records its own study:

| strategy | regime | rows | trials |
|---|---|---|---|
| `vwap_scalping` | (pooled) | 16 | **172** |

168 from the 14 folds plus 4 from a 2-fold smoke test. The report reads it
back and deflates against it: `DSR 0.0000 (FAIL, N=172, benchmark SR 0.9801)`.
The benchmark Sharpe is the point - having searched 172 configurations, a
Sharpe of 0.98 is what you would expect from the luckiest draw alone, and this
one produced -0.055.

**Caveat on where those rows live.** `DATABASE_PATH` in `.env` is the relative
path `trading_bot.db`, so the registry a run writes to is the one in its own
working directory. These 16 rows are in the **worktree's** `trading_bot.db`.
The main checkout's registry still holds only the 12 `mean_reversion` rows,
and merging them is a deliberate act, not a side effect of merging this
branch. Until someone does that, `momentum_scalping`'s PSR 1.0 remains
undeflated.

---

## Two dead dimensions removed from the VWAP search space

`rsi_oversold` and `rsi_overbought` were being sampled by every VWAP study.
The constructor stores them and the class docstring already called them
RESERVED, but `generate_signals()` only ever interpolates the RSI *value* into
a note string - no branch reads either threshold. They were the same class of
defect as `sd_exit_threshold`, removed on 2026-07-28: pure noise dimensions
whose trials were still charged against the strategy's deflated Sharpe.

The space is now four real dimensions - `sd_entry_threshold`,
`atr_stop_multiplier`, `min_confidence`, `cooldown_minutes` - and
`test_vwap_config_guard.py::TestSearchSpaceHasNoDeadDimensions` fails if RSI
ever becomes a real gate, so the dimensions can legitimately come back.

---

## Verdict

**VWAP scalping has no edge that any of these three levers can reach.** The
strategy is not mis-mapped, it is not mis-thresholded, and it is not badly
executed. It is a high-frequency mean-reversion signal worth under 2 bp a
round trip on an instrument that costs 14 bp to trade, and the census cell
that suggested otherwise does not survive contact with the full history of the
symbol it was measured on.

If VWAP is to be revisited, the question worth asking is not "which
parameters" but "why is the gross edge 2 bp" - i.e. whether a rolling
cumulative VWAP on 15m bars with a MACD-sign confirmation is a mean-reversion
signal at all. That is a strategy-design question, not a tuning one.

---

## Structural limits of these runs, stated plainly

- **BTC-only, so the promotion gate can never return PASS.** Check 4
  (`cross_symbol`) requires positive expectancy on >= 2 eligible symbols, and
  check 1 requires >= 2 symbols with >= 5 trades. Every gate verdict here
  reads FAIL for that reason alone. The numbers that carry the argument are
  the profit factors and the trade counts, not the verdicts.
- **The regime variants were selected using the census, which was cut from the
  same tape.** Testing `indecisive` because the census said `indecisive` is
  in-sample selection. That is precisely why the result mattered: it
  *disconfirmed* the census, which is the direction a re-test on the same tape
  cannot fake.
- The 2-month window series restarts the engine per window, so a position
  open at a window boundary is discarded rather than carried. At ~100 trades
  per window this is a sub-1% effect.
