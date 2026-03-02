# Backtesting Guide — Trading Bot v2

How to run isolated strategy tests, interpret results, and tune strategy parameters via `.env`.
Intended as context for both manual tuning and an automated loop agent.

**Last updated:** 2026-03-02 — all strategy parameters now fully wired to env vars.

---

## Contents

1. [How the System Works](#how-the-system-works)
2. [The Two Test Scripts](#the-two-test-scripts)
3. [Reading the Results](#reading-the-results)
4. [Tuning via .env — Parameter Reference](#tuning-via-env--parameter-reference)
   - [How to Read the Tables](#how-to-read-the-tables)
   - [Global Backtest Settings](#global-backtest-settings)
   - [MeanReversion](#meanreversion)
   - [MACrossover](#macrossover)
   - [GridTrading](#gridtrading)
   - [LiquidationCapture](#liquidationcapture)
   - [VWAPScalping](#vwapscalping)
   - [MomentumScalping](#momentumscalping)
   - [FundingArb](#fundingarb)
   - [OrderBookImbalance](#orderbookimbalance)
5. [Workflow: Tuning a Strategy](#workflow-tuning-a-strategy)
6. [Designing an Automated Loop Agent](#designing-an-automated-loop-agent)
7. [Quick Reference — All Env Vars](#quick-reference--all-env-vars)

---

## How the System Works

### The Full Chain

```
.env file
  └─> config.py (reads all env vars into typed fields)
        └─> strategy_manager.py (reads env vars, initialises each strategy with its params)
              └─> BacktestEngine (engine.py)
                    ├─> loads OHLCV candle data from trading_bot_v2/backtesting/data/
                    ├─> steps through candles chronologically
                    ├─> calls each active strategy's generate_signals()
                    ├─> executes signals via SimulatedExchange
                    └─> collects fills -> PerformanceTracker -> BacktestResult
```

### Key Rules

- **`.env` values always win.** Every strategy parameter now has a corresponding env var. Edit `.env`, re-run — no code changes needed.
- **hedge_mode = False.** Pacifica does not allow opposing positions. Any signal that goes against an open position is dropped entirely. Strategies do not close each other's trades — only SL/TP orders close positions.
- **Strategies are regime-gated.** Each strategy only fires when the market regime detector (ADX + volatility analysis) classifies the current candle as a compatible regime. Zero trades may mean the regime never occurred in the test window — not a bug.
- **Backtest data is pre-downloaded.** Lives in `trading_bot_v2/backtesting/data/`. No internet required during a run.

### Timeframe Hierarchy

| Timeframe | Role |
|-----------|------|
| 1m | SL/TP placement precision — tighter stops from tighter candles |
| 5m | LiquidationCapture cascade detection |
| 15m | Signal generation for VWAP, Momentum |
| 1h | Regime sanity check |
| 4h | HTF trend filter — MACrossover entries, MomentumScalping alignment gate |

---

## The Two Test Scripts

### Script 1: `run_strategy_sweep.py` — Isolation sweep

The primary tuning tool. Runs each strategy one at a time in its own subprocess.
Each subprocess is fully isolated — no shared state, no log bleed between strategies.

**Run from the repo root (`Bot3/`):**

```bash
# All strategies on SUI (default)
python -m trading_bot_v2.backtesting.run_strategy_sweep

# Choose your token
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol ETH-USDC

# Custom date range and capital
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC \
    --start 2024-06-01 --end 2024-12-31 --capital 5000

# Run only specific strategies
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol SUI-USDC \
    --strategies MomentumScalping VWAPScalping GridTrading

# Save per-strategy HTML reports
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC --save-reports
```

**Available strategy names:** `MeanReversion`, `MACrossover`, `GridTrading`, `LiquidationCapture`, `VWAPScalping`, `MomentumScalping`, `FundingArb`, `OrderBookImbalance`

---

### Script 2: `run_backtest.py` — Single run with HTML report

Runs all strategies (or one) together and saves a detailed HTML trade-by-trade report.
Good for inspecting individual trades after a sweep identifies a strategy worth digging into.

```bash
# All strategies, default symbol and dates
python -m trading_bot_v2.backtesting.run_backtest

# Single strategy with custom report path
python -m trading_bot_v2.backtesting.run_backtest \
    --strategy MomentumScalping \
    --symbol ETH-USDC \
    --report backtesting/reports/momentum_eth.html

# Custom date range
python -m trading_bot_v2.backtesting.run_backtest \
    --start 2024-06-01 --end 2024-12-31 \
    --symbol BTC-USDC --strategy GridTrading

# Walk-forward analysis (rolling train/test windows)
python -m trading_bot_v2.backtesting.run_backtest --walk-forward
```

The sweep is better for clean comparison tables. This script is better for debugging a single strategy in detail.

---

## Reading the Results

| Metric | What it means | Target |
|--------|---------------|--------|
| Return | Total % gain/loss on starting capital | > 0% |
| Sharpe | Risk-adjusted return (annualised) | > 0.5 acceptable, > 1.0 good |
| Max DD | Largest peak-to-trough equity drawdown | < 10% acceptable, < 5% good |
| Win Rate | % of closed trades that were profitable | Depends on RRR — see table below |
| Profit Factor | Gross profit ÷ gross loss | > 1.0 profitable, > 1.5 good |
| Calmar | Annualised return ÷ Max Drawdown | > 0.5 good |
| Closed Trades | Round-trip trades with realised PnL | 30+ needed for statistical validity |
| Total Fills | All order executions including opens | Should be Closed Trades × 2 |
| Fees | Total taker/maker fees paid | Watch: if fees > 50% of gross profit, strategy unviable at that frequency |

### Break-even Win Rate by RRR

The minimum win rate needed to be profitable depends on your reward-to-risk ratio.

| ATR Stop | ATR Target | RRR | Break-even WR |
|----------|-----------|-----|---------------|
| 1.5x | 2.5x | 1.67:1 | 37.5% |
| 1.5x | 3.0x | 2.0:1 | 33.3% |
| 1.5x | 3.5x | 2.33:1 | 30.0% |
| 2.0x | 3.0x | 1.5:1 | 40.0% |
| 2.0x | 4.0x | 2.0:1 | 33.3% |
| 2.0x | 5.0x | 2.5:1 | 28.6% |

If `Win Rate < Break-even WR` → the strategy loses money regardless of any other setting.

---

## Tuning via `.env` — Parameter Reference

All strategy parameters are controlled from `.env` in the repo root (`Bot3/.env`).
**Edit `.env`, then re-run the backtest — no code changes needed.**

Each subprocess launched by the sweep re-reads `.env` fresh, so changes take effect immediately on the next run.

### How to Read the Tables

Each parameter table includes:
- **Current `.env` value** — what is actually running right now
- **Code default** — what runs if the env var is absent
- **Effect of increasing / decreasing** — how the bot's behaviour changes

---

### Global Backtest Settings

These control the test window and cost model. Changing these affects all strategies simultaneously.

| Env Var | Current | Effect |
|---------|---------|--------|
| `BACKTEST_START_DATE` | 2024-01-01 | Move later to test recent behaviour only |
| `BACKTEST_END_DATE` | 2024-12-31 | Move earlier to shorten test window |
| `BACKTEST_SYMBOL` | SUI-USDC | Default token; overridden by `--symbol` flag |
| `BACKTEST_INITIAL_CAPITAL` | 10000.0 | Larger capital → larger absolute fee drag |
| `BACKTEST_SLIPPAGE_PCT` | 0.002 | Higher = simulates worse execution. Hurts high-frequency strategies most. |
| `BACKTEST_TAKER_FEE_PCT` | 0.0006 | Higher = more fee drag per fill. Kills strategies with many trades. |
| `BACKTEST_MAKER_FEE_PCT` | 0.0002 | Lower cost for limit orders. |
| `BACKTEST_FUNDING_HOURLY_PCT` | 0.0001 | Hourly funding charged on open positions. Long positions pay, short positions receive. |

---

### MeanReversion

**Active regimes:** RANGING_QUIET, RANGING_VOLATILE
**Status:** P1 priority — was generating 2,821 trades / $496.90 fees on SUI due to no cooldown and loosened thresholds.

`ENABLE_MEAN_REVERSION=true` to activate.

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `MEAN_REVERSION_RSI_OVERSOLD` | 30 | 35 | **Lower = fewer but stronger BUY signals.** RSI must be more oversold to enter. 30 = classic, 35 = loosened. Set to 25 for very selective entries. |
| `MEAN_REVERSION_RSI_OVERBOUGHT` | 70 | 65 | **Higher = fewer but stronger SELL signals.** RSI must be more overbought to enter. 70 = classic, 65 = loosened. |
| `MEAN_REVERSION_RSI_PERIOD` | 14 | 14 | **Lower = more responsive, noisier RSI.** Period 7 fires more frequently on smaller swings. Period 21 is slower and smoother. |
| `MEAN_REVERSION_BB_PERIOD` | 20 | 20 | **Longer period = smoother, more stable bands.** Shorter reacts faster but produces more false edges. |
| `MEAN_REVERSION_BB_STD_DEV` | 2.0 | 2.0 | **Higher = wider bands, fewer signals.** 2.0 = standard. 2.5 = only very extreme deviations trigger. 1.5 = more signals but noisier. |
| `MEAN_REVERSION_BB_PROXIMITY` | 0.20 | 0.30 | **Lower = price must be closer to the band edge.** 0.20 means price must be within the bottom 20% of the band range. Tighter proximity = higher-quality entries. 0.30 was too loose. |
| `MEAN_REVERSION_SMA_PERIOD` | 20 | 20 | **Sets the take-profit target (mean).** Longer SMA = target is further away = larger potential wins but lower hit rate. Shorter SMA = closer target, higher win rate, smaller gains. |
| `MEAN_REVERSION_ATR_PERIOD` | 14 | 14 | **Affects how stop loss distance is calculated.** Shorter period = ATR reacts faster to recent volatility = tighter stops in quiet periods. |
| `MEAN_REVERSION_ATR_STOP_MULTIPLIER` | 2.0 | 2.0 | **Larger = wider stop loss.** 1.5x = tighter, stops get hit more often but losses are smaller. 2.5x = wider, fewer stop-outs but larger losses when they occur. |
| `MEAN_REVERSION_MIN_CONFIDENCE` | 0.50 | 0.45 | **Higher = fewer but higher-quality signals pass through.** Acts as a final gate after all other conditions are checked. |

> **Key issue remaining:** No cooldown parameter exists yet in the strategy code. Until a cooldown is added, even with tightened RSI/BB settings, the strategy can still fire repeatedly within the same range period. Adding `MEAN_REVERSION_COOLDOWN_MINUTES` requires a small code change.

---

### MACrossover

**Active regimes:** TRENDING_STRONG only
**Status:** Fixed — `.env` now correctly set to 20/50 MA periods (was 50/200 which never fired).

`ENABLE_MA_CROSSOVER=true` to activate.

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `MA_CROSSOVER_FAST_PERIOD` | 20 | 20 | **Shorter fast MA = more crossover signals, but noisier.** The fast line crosses the slow more frequently with a shorter period. Use 20 for 4h data window. 50 requires much more candle history. |
| `MA_CROSSOVER_SLOW_PERIOD` | 50 | 50 | **Longer slow MA = fewer, more significant crossovers.** 50 = medium-term trend. 200 = very long-term (requires 200+ 4h candles, was the original bug). |
| `MA_CROSSOVER_PULLBACK_MIN` | 0.01 | 0.02 | **Minimum retracement after crossover before entry.** Lower = enters sooner after the cross. Setting too low risks entering on the initial momentum rather than the pullback. |
| `MA_CROSSOVER_PULLBACK_MAX` | 0.06 | 0.04 | **Maximum allowed retracement.** Higher = catches more entries but risks entries where the original trend has already reversed. |
| `MA_CROSSOVER_VOLUME_THRESHOLD` | 1.2 | 1.2 | **Volume must be this multiple of average to confirm entry.** Higher = only enters on high-conviction volume spikes. Lower = more entries including weak-volume moves. |
| `MA_CROSSOVER_MACD_FAST` | 12 | 12 | **MACD fast EMA period.** Standard is 12. Shorter = MACD responds faster, more frequent crosses. |
| `MA_CROSSOVER_MACD_SLOW` | 26 | 26 | **MACD slow EMA period.** Standard is 26. Shorter gap between fast/slow = more MACD signals. |
| `MA_CROSSOVER_MACD_SIGNAL` | 9 | 9 | **MACD signal line smoothing.** Shorter = signal line reacts faster, more crossovers, noisier. |
| `MA_CROSSOVER_ATR_PERIOD` | 14 | 14 | **ATR period for stop loss sizing.** Shorter = stops sized on recent volatility only. Longer = smoother, more stable stop distances. |
| `MA_CROSSOVER_ATR_STOP_MULTIPLIER` | 2.5 | 2.5 | **Stop loss distance in ATR units.** Larger = wider stops, fewer stop-outs but larger losses when hit. |
| `MA_CROSSOVER_MIN_CONFIDENCE` | 0.50 | 0.50 | **Signal quality gate.** Lower to 0.40 if 0 trades and you want to force more signals for testing. |

> **Note:** This strategy fires 0 trades when the market is classified as RANGING. BTC and SUI spent most of 2024 in ranging regime. Test on ETH-USDC for more TRENDING periods.

---

### GridTrading

**Active regimes:** RANGING_VOLATILE only (hard-disabled in TRENDING_MODERATE and TRENDING_STRONG)
**Status:** Only profitable strategy in isolation — +0.24% BTC, PF 2.26, WR 68.2%.

`ENABLE_GRID_TRADING=true` to activate.

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `GRID_TRADING_LEVELS` | 5 | 8 | **Number of buy and sell orders placed around current price.** More levels = more positions = more capital at risk = more fees. Fewer levels = less exposure but misses some oscillations. |
| `GRID_SPACING_ATR_MULTIPLIER` | 1.0 | 0.4 | **Distance between each grid level, in ATR units.** Higher = wider grid = larger profit per fill but fewer fills. Lower = tighter grid = more fills but must cover round-trip fees. At 0.4x ATR, each level was too tight to cover fees — 1.0x is the recommended minimum. |
| `GRID_MAX_POSITIONS_PER_SYMBOL` | 5 | 10 | **Hard cap on concurrent open positions.** Lower cap prevents runaway accumulation in trending periods that break the grid's range assumption. |
| `GRID_EMERGENCY_STOP_PCT` | 0.05 | 0.05 | **Portfolio loss % that triggers emergency position exit.** Lower = exits sooner when wrong. Higher = gives more room but risks larger losses in trending breakouts. |
| `GRID_ADX_THRESHOLD` | 20.0 | 20.0 | **ADX level above which no new grid orders are placed.** ADX > 20 indicates trending conditions where grid accumulation is dangerous. Lower this (e.g. 15) to be more conservative, raise (e.g. 25) to allow grid in moderate trends. |
| `GRID_ATR_PERIOD` | 14 | 14 | **ATR period used for grid spacing calculation.** Shorter = spacing reacts to recent volatility more quickly. Longer = more stable spacing. |
| `GRID_ADX_PERIOD` | 14 | 14 | **ADX period for regime detection within the strategy.** Shorter = faster regime detection, more whipsaws. Longer = slower but smoother regime calls. |
| `GRID_MIN_CONFIDENCE` | 0.55 | 0.45 | **Minimum signal confidence to place a grid order.** Higher = only places grid when conditions are clearly ranging. Lower = more grid activity. |
| `GRID_MIN_SPACING_PCT` | 0.003 | 0.003 | **Absolute minimum spacing between levels (0.3%).** Floor to prevent grid levels collapsing on top of each other in low-volatility periods. |
| `GRID_MAX_SPACING_PCT` | 0.06 | 0.06 | **Absolute maximum spacing (6%).** Ceiling to prevent grid levels spreading so wide they never fill. |

---

### LiquidationCapture

**Active regimes:** ALL (runs regardless of regime)
**Status:** Fixed — env vars now set to the moderate values, resolving the strict/loose discrepancy. Previously `.env` was absent and strategy_manager.py was using strict hardcoded defaults.

`ENABLE_LIQUIDATION_CAPTURE=true` to activate.

| Env Var | Current | Strict | Moderate | Effect |
|---------|---------|--------|----------|--------|
| `LIQUIDATION_PRICE_THRESHOLD` | 0.025 | 0.03 | 0.025 | **Minimum % price move in the cascade window.** Higher = only captures larger, more obvious cascades. Lower = catches smaller events but more false positives. |
| `LIQUIDATION_VOLUME_MULTIPLIER` | 2.5 | 3.0 | 2.5 | **Volume spike required vs rolling average.** Higher = only trades on extreme panic volume. Lower = more signals including moderate surges. |
| `LIQUIDATION_RSI_OVERSOLD` | 20.0 | 15 | 20 | **RSI must be below this to enter a long (fade a cascade).** Lower = requires extreme oversold (RSI 15 almost never happens). Higher = catches more recoveries. |
| `LIQUIDATION_RSI_OVERBOUGHT` | 80.0 | 85 | 80 | **RSI must be above this to enter a short (fade a squeeze).** Higher = requires extreme overbought. Lower = catches more. |
| `LIQUIDATION_RSI_PERIOD` | 14 | 14 | 14 | **RSI calculation period.** Shorter = RSI reaches extremes more easily (more signals). Longer = only genuine extremes qualify. |
| `LIQUIDATION_MIN_CONSECUTIVE_MOVES` | 4 | 5 | 4 | **Number of consecutive same-direction candles required.** Higher = only clear cascade sequences. Lower = catches more reversals but may catch ordinary pullbacks. |
| `LIQUIDATION_MIN_WICK_RATIO` | 1.5 | 2.0 | 1.5 | **Wick length relative to candle body — measures panic.** Higher = only candles with extreme rejection wicks. Lower = any candle with a noticeable wick qualifies. |
| `LIQUIDATION_RRR_TARGET` | 3.0 | 3.0 | 3.0 | **Minimum reward-to-risk for the trade.** Higher = only takes trades with very wide targets. Lower break-even WR required with higher RRR. |
| `LIQUIDATION_MAX_PER_SESSION` | 2 | 1 | 2 | **Max trades allowed per 4-hour session.** Lower = very selective. Higher = multiple opportunities per session. |
| `LIQUIDATION_MIN_HOURS_BETWEEN` | 2 | 4 | 2 | **Cooldown hours between any two LC trades.** Higher = more time between entries. Lower = can re-enter sooner after a completed trade. |

---

### VWAPScalping

**Active regimes:** RANGING_VOLATILE, RANGING_CALM, INDECISIVE
**Status:** Currently ~13% WR due to an inverted MACD gate and floating TP (TP = live VWAP which drifts every candle). These require code fixes, not just `.env` changes.

`ENABLE_VWAP_SCALPING=true` to activate.

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `VWAP_SD_ENTRY_THRESHOLD` | 2.2 | 1.8 | **SD units from VWAP required to trigger entry.** Higher = only enters on more extreme deviations, fewer signals but higher quality. Lower = enters on smaller deviations, more signals but noisier. 1.8 = frequent, 2.2 = moderate, 2.5 = rare/high-conviction. |
| `VWAP_ATR_PERIOD` | 14 | 14 | **ATR period for stop loss sizing.** Shorter = stop sized on recent volatility only. |
| `VWAP_ATR_STOP_MULTIPLIER` | 2.0 | 1.5 | **Stop loss distance in ATR units.** Larger = wider stops, fewer stop-outs but larger losses when they hit. 1.5x with tight TP was causing frequent SL hits. 2.0x gives more room. |
| `VWAP_MACD_FAST` | 12 | 12 | **MACD fast period for entry confirmation.** Standard value. Shorter = more MACD crossovers = more entries permitted. |
| `VWAP_MACD_SLOW` | 26 | 26 | **MACD slow period.** Wider gap between fast/slow = fewer MACD signals. |
| `VWAP_MACD_SIGNAL` | 9 | 9 | **MACD signal line smoothing.** Shorter = signal line crosses more often = more entries permitted. |
| `VWAP_SD_MULTIPLIERS` | 1.0,2.0,3.0 | 1.0,2.0,3.0 | **SD band levels drawn around VWAP.** These define the 1SD, 2SD, 3SD zones. Changing these shifts where entries are considered extreme. Comma-separated floats. |
| `VWAP_MIN_CONFIDENCE` | 0.68 | 0.62 | **Signal quality gate.** Higher = fewer signals pass through. Raise to reduce overtrading, lower to increase activity. |
| `VWAP_COOLDOWN_MINUTES` | 20 | 8 | **Minutes after a trade before the strategy can fire again.** Raise to reduce trade frequency. Lower to allow re-entry sooner. At 8 min (default), it was firing too often on bouncing ranging markets. |

> **Note:** MACD gate and floating TP are code-level issues. Env vars alone cannot fix the ~13% WR until those are corrected.

---

### MomentumScalping

**Active regimes:** TRENDING_STRONG, TRENDING_MODERATE
**Status:** ETH: 36.9% WR vs 40.0% break-even needed at current 2.0x stop / 3.0x target (RRR 1.5:1). Needs either higher target multiplier or improved entry filtering.

`ENABLE_MOMENTUM_SCALPING=true` to activate.

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `MOMENTUM_EMA_FAST` | 9 | 9 | **Fast EMA period for crossover signal.** Shorter = more crossovers, noisier. Longer = fewer but stronger trend signals. |
| `MOMENTUM_EMA_SLOW` | 21 | 21 | **Slow EMA period.** Wider gap between fast and slow = stronger confirmation needed. Try 9/21 (fast), 10/30 (moderate), 12/50 (slower). |
| `MOMENTUM_RSI_PERIOD` | 14 | 14 | **RSI calculation period for momentum filter.** Shorter = more responsive, extremes hit more often. |
| `MOMENTUM_RSI_OVERSOLD` | 25 | 35 | **BUY gate: RSI must be above this threshold.** Lower = allows longs even in weak momentum. 35 was blocking valid BTC uptrend entries where RSI stayed in 35–50 range. 25 = more permissive. |
| `MOMENTUM_RSI_OVERBOUGHT` | 75 | 65 | **SELL gate: RSI must be below this threshold.** Higher = allows shorts further into strong uptrends. 65 was blocking valid short entries. 75 = more permissive. |
| `MOMENTUM_MACD_FAST` | 12 | 12 | **MACD fast period for trend confirmation.** Standard. |
| `MOMENTUM_MACD_SLOW` | 26 | 26 | **MACD slow period.** Standard. |
| `MOMENTUM_MACD_SIGNAL` | 9 | 9 | **MACD signal line.** Shorter = more MACD crossovers allowed. |
| `MOMENTUM_ATR_PERIOD` | 14 | 14 | **ATR period for stop/target sizing.** |
| `MOMENTUM_ATR_STOP_MULTIPLIER` | 2.0 | 1.5 | **Stop loss distance.** Raise to give positions more room before stopping out. With 1.5x, small whipsaws were closing trades early before momentum continued. 2.0x reduces SL churn. Note: wider stop raises break-even WR. |
| `MOMENTUM_ATR_TARGET_MULTIPLIER` | 3.0 | 2.5 | **Take profit distance.** Raise to improve RRR, which lowers break-even WR. At 2.0x stop + 3.0x target → RRR 1.5:1 → break-even WR = 40%. To hit 33% break-even, try 2.0x stop + 4.0x target (RRR 2.0:1). |
| `MOMENTUM_MIN_CONFIDENCE` | 0.65 | 0.60 | **Signal quality gate.** Higher = fewer but cleaner signals. |
| `MOMENTUM_COOLDOWN_MINUTES` | 20 | 5 | **Minutes between trades.** At 5 min, it was firing ~400 times on ETH. At 20 min, frequency drops significantly. Try 10–15 for a middle ground. |
| `MOMENTUM_VOLUME_MULTIPLIER` | 1.5 | 1.2 | **Volume must be this multiple of average to confirm entry.** Higher = only trades on strong-volume moves. At 1.2x, too many low-volume entries. 1.5x filters out weak moves. Try 2.0x for very selective entries. |

---

### FundingArb

**Active regimes:** ALL (passive strategy)
**Status:** Live trading only — produces 0 trades in backtest. No changes needed for backtesting purposes.

`ENABLE_FUNDING_ARB=true` to activate (live only).

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `FUNDING_ARB_MIN_RATE` | 0.0001 | 0.0001 | **Minimum funding rate (0.01%) to open a position.** Lower = takes more positions including near-zero rates. Higher = only acts when funding is clearly elevated. |
| `FUNDING_ARB_MAX_ALLOCATION` | 0.20 | 0.20 | **Max % of account allocated to funding arb positions.** Lower = less capital at risk. Higher = more income but more basis risk. |
| `FUNDING_ARB_REBALANCE_THRESHOLD` | 0.02 | 0.02 | **Delta drift % that triggers a rebalance.** Lower = rebalances more often, higher fees. Higher = allows more directional drift before correcting. |
| `FUNDING_ARB_LOOKBACK_HOURS` | 8 | 8 | **Hours of funding history used to assess trend.** Shorter = reacts to very recent rate changes. Longer = requires sustained elevated rates. |
| `FUNDING_ARB_MIN_CONFIDENCE` | 0.70 | 0.70 | **Signal quality gate.** High confidence required since positions are held for hours. |

---

### OrderBookImbalance

**Active regimes:** ALL (overlay strategy)
**Status:** Live trading only — produces 0 trades in backtest. No changes needed for backtesting purposes.

`ENABLE_ORDERBOOK_IMBALANCE=true` to activate (live only).

| Env Var | Current | Code Default | Effect |
|---------|---------|--------------|--------|
| `ORDERBOOK_LEVELS` | 10 | 10 | **Depth levels to analyse.** More levels = broader view of order flow. Fewer = only analyses best bids/asks. |
| `ORDERBOOK_IMBALANCE_THRESHOLD_LONG` | 0.62 | 0.62 | **Bid/ask ratio above which a BUY signal is generated.** Higher = only acts on strong buying pressure. Lower = more signals including marginal imbalances. |
| `ORDERBOOK_IMBALANCE_THRESHOLD_SHORT` | 0.38 | 0.38 | **Bid/ask ratio below which a SELL signal is generated.** Lower = only acts on strong selling pressure. |
| `ORDERBOOK_STRONG_IMBALANCE` | 0.72 | 0.72 | **Threshold for HIGH_CONVICTION signal classification.** Signals above this threshold get priority in conflict resolution. |
| `ORDERBOOK_MIN_ORDER_DENSITY` | 5 | 5 | **Minimum orders on the winning side to qualify.** Higher = requires more participants on the dominant side, filters spoofs. Lower = more signals including thin-orderbook moves. |
| `ORDERBOOK_SPOOF_DETECTION` | true | true | **Enable/disable spoof order filtering.** True = large anomalous orders are flagged and ignored. False = all orders treated as genuine. |
| `ORDERBOOK_SPOOF_SIZE_RATIO` | 5.0 | 5.0 | **Size/count ratio above which an order is flagged as a spoof.** Lower = more orders treated as spoofs (more conservative). Higher = only flags very large outliers. |
| `ORDERBOOK_ATR_PERIOD` | 14 | 14 | **ATR period for stop/target sizing.** |
| `ORDERBOOK_ATR_STOP_MULTIPLIER` | 0.75 | 0.75 | **Tight stop — OBI is a fast in-and-out strategy.** Wider stop improves survival but reduces RRR. |
| `ORDERBOOK_ATR_TARGET_MULTIPLIER` | 1.5 | 1.5 | **Quick target — RRR 2.0:1 at default settings.** Raise for better RRR, lower for quicker TP hits. |
| `ORDERBOOK_MIN_CONFIDENCE` | 0.55 | 0.55 | **Signal quality gate.** Lower threshold appropriate since this is a high-frequency overlay strategy. |
| `ORDERBOOK_COOLDOWN_SECONDS` | 30 | 30 | **Seconds between OBI trades.** 30s is already very fast. Lower only if latency allows. Raise to reduce fee drag. |
| `ORDERBOOK_UPDATE_INTERVAL_MS` | 500 | 500 | **Minimum ms between orderbook analyses.** Lower = analyses more frequently. Raise if CPU usage is a concern. |

---

## Workflow: Tuning a Strategy

1. **Pick a strategy** with at least 30 closed trades — smaller samples give unreliable metrics.
2. **Diagnose the problem** from the sweep output:

   | Symptom | Likely cause | First thing to change |
   |---------|-------------|----------------------|
   | Very high trade count, negative return | Overtrading / fee drag | Raise cooldown, tighten entry thresholds |
   | Low win rate (<30%) | Entry conditions too loose | Tighten RSI/SD/volume thresholds |
   | Win rate OK but still losing | RRR too tight (TP too close) | Raise ATR target multiplier |
   | Win rate >60% but small return | TP too tight, exiting early | Raise ATR target multiplier |
   | 0 trades | Regime never matched, or threshold too strict | Check regime; lower confidence/thresholds |
   | Fees > gross profit | Too many small trades | Widen stop+target, raise cooldown |

3. **Edit one or two parameters** in `.env`. Never change multiple strategies at once — you won't know what caused the change.
4. **Run the sweep** for the target strategy and symbol:
   ```bash
   python -m trading_bot_v2.backtesting.run_strategy_sweep \
       --symbol ETH-USDC --strategies MomentumScalping
   ```
5. **Record the result.** Compare Return, PF, WR, and Fees to the previous run.
6. Repeat.

---

## Designing an Automated Loop Agent

The sweep is designed for automation. Each run is a clean subprocess call that prints structured output.

### What the agent needs to do

```
LOOP:
  1. Choose a strategy and symbol to test
  2. Decide parameter values (grid search, random, or directed hill climb)
  3. Write updated values to .env (only change one strategy's params)
  4. Run:
       python -m trading_bot_v2.backtesting.run_strategy_sweep
           --symbol <SYMBOL> --strategies <STRATEGY>
  5. Capture stdout, parse the summary table
  6. Store: strategy, params dict, return, sharpe, max_dd, win_rate, profit_factor,
            closed_trades, fees
  7. Compare to current best — update best if improved
  8. Decide next param values based on result
  9. GOTO 1
```

### Parsing sweep output

Live progress line (printed immediately, before full results):
```
  [01/01]  MomentumScalping        +1.23%  45 closed  12.3s
```

Summary table structure (printed after all strategies finish):
```
  Strategy             Return     Sharpe  MaxDD    WinRate   ProfFactor  Calmar  Closed  Fees
```

For fully machine-readable output: modify the sweep to write a `results.json` file alongside the stdout table, or use `--save-reports` and parse the saved HTML.

### Constraints the agent must respect

- **One strategy at a time.** Changing multiple strategies' params in a single run makes it impossible to attribute any improvement or degradation.
- **Use the full date range** (2024-01-01 → 2024-12-31) for all comparisons. Shorter windows give results that don't generalise.
- **Require minimum 30 closed trades.** If a parameter combination results in fewer, flag it as statistically insufficient and skip recording it as a result.
- **Monitor fees.** If `fees > 0.5 × gross_profit`, the strategy is fee-dominated regardless of sign on return. Flag and deprioritise high-frequency configs.
- **Skip FundingArb and OrderBookImbalance.** They produce 0 trades in backtest — no useful data to optimise against.
- **Don't cross RRR floors.** Never set `ATR_TARGET_MULTIPLIER` lower than `ATR_STOP_MULTIPLIER` — that inverts the RRR below 1.0:1.

### Recommended parameter search order

| Strategy | Start here | Then |
|----------|-----------|------|
| MeanReversion | `MEAN_REVERSION_RSI_OVERSOLD` / `RSI_OVERBOUGHT` | `BB_PROXIMITY`, then `ATR_STOP_MULTIPLIER` |
| MACrossover | Test on `--symbol ETH-USDC` first | `MA_CROSSOVER_PULLBACK_MIN`, `MIN_CONFIDENCE` |
| GridTrading | `GRID_SPACING_ATR_MULTIPLIER` | `GRID_TRADING_LEVELS` |
| LiquidationCapture | `LIQUIDATION_PRICE_THRESHOLD` | `LIQUIDATION_RSI_OVERSOLD` / `RSI_OVERBOUGHT` |
| VWAPScalping | Needs code fix first (MACD gate + static TP) | Then `VWAP_SD_ENTRY_THRESHOLD` |
| MomentumScalping | `MOMENTUM_ATR_TARGET_MULTIPLIER` | `MOMENTUM_COOLDOWN_MINUTES` |

---

## Quick Reference — All Env Vars

Complete list of every tunable parameter, organised by strategy.

### MeanReversion
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_MEAN_REVERSION` | true | true |
| `MEAN_REVERSION_RSI_OVERSOLD` | 30 | 35 |
| `MEAN_REVERSION_RSI_OVERBOUGHT` | 70 | 65 |
| `MEAN_REVERSION_RSI_PERIOD` | 14 | 14 |
| `MEAN_REVERSION_BB_PERIOD` | 20 | 20 |
| `MEAN_REVERSION_BB_STD_DEV` | 2.0 | 2.0 |
| `MEAN_REVERSION_BB_PROXIMITY` | 0.20 | 0.30 |
| `MEAN_REVERSION_SMA_PERIOD` | 20 | 20 |
| `MEAN_REVERSION_ATR_PERIOD` | 14 | 14 |
| `MEAN_REVERSION_ATR_STOP_MULTIPLIER` | 2.0 | 2.0 |
| `MEAN_REVERSION_MIN_CONFIDENCE` | 0.50 | 0.45 |

### MACrossover
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_MA_CROSSOVER` | true | true |
| `MA_CROSSOVER_FAST_PERIOD` | 20 | 20 |
| `MA_CROSSOVER_SLOW_PERIOD` | 50 | 50 |
| `MA_CROSSOVER_PULLBACK_MIN` | 0.01 | 0.02 |
| `MA_CROSSOVER_PULLBACK_MAX` | 0.06 | 0.04 |
| `MA_CROSSOVER_VOLUME_THRESHOLD` | 1.2 | 1.2 |
| `MA_CROSSOVER_MACD_FAST` | 12 | 12 |
| `MA_CROSSOVER_MACD_SLOW` | 26 | 26 |
| `MA_CROSSOVER_MACD_SIGNAL` | 9 | 9 |
| `MA_CROSSOVER_ATR_PERIOD` | 14 | 14 |
| `MA_CROSSOVER_ATR_STOP_MULTIPLIER` | 2.5 | 2.5 |
| `MA_CROSSOVER_MIN_CONFIDENCE` | 0.50 | 0.50 |

### GridTrading
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_GRID_TRADING` | true | true |
| `GRID_TRADING_LEVELS` | 5 | 8 |
| `GRID_SPACING_ATR_MULTIPLIER` | 1.0 | 0.4 |
| `GRID_MAX_POSITIONS_PER_SYMBOL` | 5 | 10 |
| `GRID_EMERGENCY_STOP_PCT` | 0.05 | 0.05 |
| `GRID_ADX_THRESHOLD` | 20.0 | 20.0 |
| `GRID_ATR_PERIOD` | 14 | 14 |
| `GRID_ADX_PERIOD` | 14 | 14 |
| `GRID_MIN_CONFIDENCE` | 0.55 | 0.45 |
| `GRID_MIN_SPACING_PCT` | 0.003 | 0.003 |
| `GRID_MAX_SPACING_PCT` | 0.06 | 0.06 |

### LiquidationCapture
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_LIQUIDATION_CAPTURE` | true | true |
| `LIQUIDATION_PRICE_THRESHOLD` | 0.025 | 0.025 |
| `LIQUIDATION_VOLUME_MULTIPLIER` | 2.5 | 2.5 |
| `LIQUIDATION_RSI_OVERSOLD` | 20.0 | 20.0 |
| `LIQUIDATION_RSI_OVERBOUGHT` | 80.0 | 80.0 |
| `LIQUIDATION_RSI_PERIOD` | 14 | 14 |
| `LIQUIDATION_MIN_CONSECUTIVE_MOVES` | 4 | 4 |
| `LIQUIDATION_MIN_WICK_RATIO` | 1.5 | 1.5 |
| `LIQUIDATION_RRR_TARGET` | 3.0 | 3.0 |
| `LIQUIDATION_MAX_PER_SESSION` | 2 | 2 |
| `LIQUIDATION_MIN_HOURS_BETWEEN` | 2 | 2 |

### VWAPScalping
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_VWAP_SCALPING` | true | true |
| `VWAP_SD_ENTRY_THRESHOLD` | 2.2 | 1.8 |
| `VWAP_ATR_PERIOD` | 14 | 14 |
| `VWAP_ATR_STOP_MULTIPLIER` | 2.0 | 1.5 |
| `VWAP_MACD_FAST` | 12 | 12 |
| `VWAP_MACD_SLOW` | 26 | 26 |
| `VWAP_MACD_SIGNAL` | 9 | 9 |
| `VWAP_SD_MULTIPLIERS` | 1.0,2.0,3.0 | 1.0,2.0,3.0 |
| `VWAP_MIN_CONFIDENCE` | 0.68 | 0.62 |
| `VWAP_COOLDOWN_MINUTES` | 20 | 8 |

### MomentumScalping
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_MOMENTUM_SCALPING` | true | true |
| `MOMENTUM_EMA_FAST` | 9 | 9 |
| `MOMENTUM_EMA_SLOW` | 21 | 21 |
| `MOMENTUM_RSI_PERIOD` | 14 | 14 |
| `MOMENTUM_RSI_OVERSOLD` | 25 | 35 |
| `MOMENTUM_RSI_OVERBOUGHT` | 75 | 65 |
| `MOMENTUM_MACD_FAST` | 12 | 12 |
| `MOMENTUM_MACD_SLOW` | 26 | 26 |
| `MOMENTUM_MACD_SIGNAL` | 9 | 9 |
| `MOMENTUM_ATR_PERIOD` | 14 | 14 |
| `MOMENTUM_ATR_STOP_MULTIPLIER` | 2.0 | 1.5 |
| `MOMENTUM_ATR_TARGET_MULTIPLIER` | 3.0 | 2.5 |
| `MOMENTUM_MIN_CONFIDENCE` | 0.65 | 0.60 |
| `MOMENTUM_COOLDOWN_MINUTES` | 20 | 5 |
| `MOMENTUM_VOLUME_MULTIPLIER` | 1.5 | 1.2 |

### FundingArb
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_FUNDING_ARB` | true | false |
| `FUNDING_ARB_MIN_RATE` | 0.0001 | 0.0001 |
| `FUNDING_ARB_MAX_ALLOCATION` | 0.20 | 0.20 |
| `FUNDING_ARB_REBALANCE_THRESHOLD` | 0.02 | 0.02 |
| `FUNDING_ARB_LOOKBACK_HOURS` | 8 | 8 |
| `FUNDING_ARB_MIN_CONFIDENCE` | 0.70 | 0.70 |

### OrderBookImbalance
| Env Var | Current `.env` | Code Default |
|---------|:--------------:|:------------:|
| `ENABLE_ORDERBOOK_IMBALANCE` | true | true |
| `ORDERBOOK_LEVELS` | 10 | 10 |
| `ORDERBOOK_IMBALANCE_THRESHOLD_LONG` | 0.62 | 0.62 |
| `ORDERBOOK_IMBALANCE_THRESHOLD_SHORT` | 0.38 | 0.38 |
| `ORDERBOOK_STRONG_IMBALANCE` | 0.72 | 0.72 |
| `ORDERBOOK_MIN_ORDER_DENSITY` | 5 | 5 |
| `ORDERBOOK_SPOOF_DETECTION` | true | true |
| `ORDERBOOK_SPOOF_SIZE_RATIO` | 5.0 | 5.0 |
| `ORDERBOOK_ATR_PERIOD` | 14 | 14 |
| `ORDERBOOK_ATR_STOP_MULTIPLIER` | 0.75 | 0.75 |
| `ORDERBOOK_ATR_TARGET_MULTIPLIER` | 1.5 | 1.5 |
| `ORDERBOOK_MIN_CONFIDENCE` | 0.55 | 0.55 |
| `ORDERBOOK_COOLDOWN_SECONDS` | 30 | 30 |
| `ORDERBOOK_UPDATE_INTERVAL_MS` | 500 | 500 |

### Global Backtest
| Env Var | Current `.env` |
|---------|:--------------:|
| `BACKTEST_START_DATE` | 2024-01-01 |
| `BACKTEST_END_DATE` | 2024-12-31 |
| `BACKTEST_SYMBOL` | SUI-USDC |
| `BACKTEST_INITIAL_CAPITAL` | 10000.0 |
| `BACKTEST_SLIPPAGE_PCT` | 0.002 |
| `BACKTEST_TAKER_FEE_PCT` | 0.0006 |
| `BACKTEST_MAKER_FEE_PCT` | 0.0002 |
| `BACKTEST_FUNDING_HOURLY_PCT` | 0.0001 |

---

*Internal use only — Pacifica Solana Perps*
