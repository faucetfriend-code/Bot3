# Trading Bot v2 — Strategy Analysis Report

**Generated:** 2026-03-01
**Exchange:** Pacifica (Solana Perps)
**Backtest Period:** 2024-01-01 → 2024-12-31
**Initial Capital:** $10,000

---

## Contents

1. [Most Recent Backtest Results](#1-most-recent-backtest-results)
   - [1a. Combined Run — All Strategies Active](#1a-combined-run--all-strategies-active)
   - [1b. Per-Strategy Isolation Sweep](#1b-per-strategy-isolation-sweep)
2. [Current Strategy Settings](#2-current-strategy-settings)
3. [Identified Issues & Recommended Fixes](#3-identified-issues--recommended-fixes)
4. [Technical Notes — Fixes Applied This Session](#4-technical-notes--fixes-applied-this-session)

---

## 1. Most Recent Backtest Results

### 1a. Combined Run — All Strategies Active

All strategies running simultaneously, `hedge_mode=False` (Pacifica does not allow opposing positions).

| Symbol | Return | Sharpe | Max DD | Win Rate | Profit Factor | Closed Trades | Total Fills | Fees |
|--------|-------:|-------:|-------:|---------:|--------------:|--------------:|------------:|-----:|
| SUI-USDC | -0.8% | +0.51 | 4.9% | — | — | 72 | 144 | $14 |
| BTC-USDC | -1.3% | — | — | 90% | 7.56 | 10 | — | — |
| ETH-USDC | -2.2% | — | — | 36.8% | 0.52 | 321 | — | — |

> **Note:** Combined run results are misleading — strategies interfere with each other's positions. Use the isolation sweep below for per-strategy diagnosis.

---

### 1b. Per-Strategy Isolation Sweep

Run via `run_strategy_sweep.py` — each strategy runs in its own subprocess against one symbol at a time.

#### SUI-USDC

| Strategy | Return | Win Rate | Profit Factor | Closed Trades | Fees | Verdict |
|----------|-------:|---------:|--------------:|--------------:|-----:|---------|
| MeanReversion | **-18.73%** | — | — | **2,821** | **$496.90** | LOSING |
| GridTrading | -0.09% | — | 1.06 | — | — | MARGINAL |
| VWAPScalping | — | ~13% | — | — | — | LOSING |
| MACrossover | 0 trades | — | — | 0 | $0 | 0 trades (RANGING regime) |
| MomentumScalping | — | — | — | — | — | — |
| LiquidationCapture | — | — | — | — | — | — |

#### BTC-USDC

| Strategy | Return | Win Rate | Profit Factor | Closed Trades | Fees | Verdict |
|----------|-------:|---------:|--------------:|--------------:|-----:|---------|
| **GridTrading** | **+0.24%** | **68.2%** | **2.26** | — | — | **GOOD** |
| VWAPScalping | — | ~13% | — | — | — | LOSING |
| MACrossover | 0 trades | — | — | 0 | $0 | 0 trades (RANGING regime) |

#### ETH-USDC

| Strategy | Return | Win Rate | Profit Factor | Closed Trades | Fees | Verdict |
|----------|-------:|---------:|--------------:|--------------:|-----:|---------|
| MomentumScalping | -3.0% | 36.9% | 0.30 | 428 | — | LOSING |

> **Key finding:** GridTrading is the only profitable strategy in isolation. MeanReversion is the single biggest loss driver — hidden in combined runs but exposed at -18.73% in isolation.

---

## 2. Current Strategy Settings

### MeanReversion

**Active regimes:** RANGING_QUIET, RANGING_VOLATILE
**⚠ Critical issue: No cooldown — root cause of 2,821 trades and $496.90 fees on SUI**

| Parameter | Value | Note |
|-----------|-------|------|
| RSI Oversold | 35 | Loosened from 30 |
| RSI Overbought | 65 | Loosened from 70 |
| BB Period | 20 | |
| BB Std Dev | 2.0 | |
| BB Proximity Threshold | 30% | Loosened from 20% |
| ATR Stop Multiplier | 2.0x | |
| SMA Period | 20 | |
| Min Confidence | 0.45 | |
| Cooldown | **NONE** | **← critical** |

---

### MACrossover

**Active regimes:** TRENDING_STRONG only
**ℹ 0 trades on BTC/SUI — both classified as RANGING during test period. Correct behavior.**

| Parameter | Value | Note |
|-----------|-------|------|
| Fast MA | 20 | Updated from 50 |
| Slow MA | 50 | Updated from 200 |
| Pullback Range | 2% – 4% | After crossover |
| Volume Threshold | 1.2x | |
| MACD | 12/26/9 | |
| ATR Stop | 2.5x | |
| Min Confidence | 0.50 | |

---

### GridTrading

**Active regimes:** RANGING_VOLATILE
**✓ Only profitable strategy in isolation: +0.24% BTC, PF 2.26, WR 68.2%**

> **Note:** Runtime values from `strategy_manager.py` differ from `__init__` defaults.

| Parameter | Runtime Value | `__init__` Default |
|-----------|:-------------:|:------------------:|
| Grid Levels | 8 | 5 |
| Grid Spacing | 0.4x ATR | 0.5x ATR |
| Max Positions | 10 | 10 |
| Emergency Stop | 5% | 5% |
| ADX Threshold | 20.0 | 25.0 |
| Min Spacing (env) | 0.3% | — |
| Max Spacing (env) | 6.0% | — |
| Min Confidence | 0.45 | 0.45 |

---

### LiquidationCapture

**Active regimes:** ALL
**⚠ Parameter discrepancy: `__init__` defaults were loosened (Prompt 058) but env var overrides in `strategy_manager.py` restore the strict values at runtime.**

| Parameter | Runtime (env) — strict | `__init__` Default — loose |
|-----------|:----------------------:|:--------------------------:|
| Price Threshold | 3.0% | 2.5% |
| Volume Multiplier | 3.0x | 2.5x |
| RSI Oversold | 15 | 20 |
| RSI Overbought | 85 | 80 |
| Min Consecutive Candles | 5 | 4 |
| Min Wick Ratio | 2.0 | 1.5 |
| Max Per Session | 1 | 2 |
| Min Hours Between Trades | 4h | 2h |
| RRR Target | 3.0 | 3.0 |

---

### VWAPScalping

**Active regimes:** ALL
**⚠ Consistent ~13% win rate across all symbols — likely inverted MACD gate + floating TP**

| Parameter | Value | Note |
|-----------|-------|------|
| ATR Period | 14 | |
| SD Entry Threshold | 1.8 | Entry when price deviates >1.8 SD from VWAP |
| ATR Stop Multiplier | 1.5x | |
| MACD | 12/26/9 | |
| Min Confidence | 0.62 | |
| Cooldown | 8 min | |
| Take Profit | Floating VWAP | **Problem: VWAP moves each candle, TP is never static** |
| ATR Source | 1m (falls back to 15m) | Fixed this session |

---

### MomentumScalping

**Active regimes:** TRENDING_STRONG, TRENDING_MODERATE
**⚠ RRR 1.67:1 with 36.9% WR on ETH — break-even is 37.5%**

| Parameter | Value | Note |
|-----------|-------|------|
| EMA Fast / Slow | 9 / 21 | |
| RSI Period | 14 | |
| RSI Oversold / Overbought | 35 / 65 | |
| ATR Period | 14 | |
| ATR Stop Multiplier | 1.5x | |
| ATR Target Multiplier | **2.5x** | RRR = 1.67:1 — break-even WR = 37.5% |
| Min Confidence | 0.60 | |
| Cooldown | 5 min | |
| Volume Threshold | 1.2x | |
| MACD | 12/26/9 | |
| 4h HTF Filter | Enabled | Added this session |

---

### FundingArb

**Active regimes:** ALL — **live trading only, 0 trades in backtest (requires live API client)**

| Parameter | Value |
|-----------|-------|
| Min Funding Rate | 0.01% |
| Max Allocation | 20% of account |
| Rebalance Threshold | 2% delta |
| Lookback | 8 hours |
| Min Confidence | 0.70 |

---

### OrderBookImbalance

**Active regimes:** ALL — **live trading only, 0 trades in backtest (requires live orderbook feed)**

| Parameter | Value |
|-----------|-------|
| Depth Levels | 10 |
| Long Threshold | >62% imbalance |
| Short Threshold | <38% imbalance |
| Strong Imbalance | >72% |
| Min Order Density | 5 |
| ATR Stop | 0.75x |
| ATR Target | 1.5x → RRR 2.0:1 |
| Min Confidence | 0.55 |
| Cooldown | 30 seconds |

---

## 3. Identified Issues & Recommended Fixes

| Priority | Strategy | Issue | Recommended Fix |
|----------|----------|-------|-----------------|
| **P1** | MeanReversion | No cooldown → 2,821 trades, $496.90 fees on SUI. Overtrading is the primary loss driver. | Add 15–30 min cooldown. Tighten RSI back to 30/70, BB proximity back to 20%. |
| **P2** | VWAPScalping | Inverted MACD gate + floating TP → consistent ~13% WR across all symbols. | Flip MACD gate (histogram < 0 required for BUY). Fix TP as static price at signal candle's VWAP value. |
| **P3** | MomentumScalping | RRR 1.67:1 with 36.9% WR on ETH is just below break-even (37.5% required). | Raise ATR target multiplier 2.5x → 3.5x (RRR 2.33:1, break-even drops to ~30%). Raise cooldown 5 min → 15 min. |
| **P4** | LiquidationCapture | Strict runtime thresholds (RSI 15/85, 5 consecutive) → very few signals. Loose `__init__` defaults never used. | Decide: update env var defaults to match loosened `__init__` values, or revert `__init__` to remove the discrepancy entirely. |
| **P5** | GridTrading | SELL-side grid fires without directional confirmation. Currently the only profitable strategy (+0.24% BTC). | Add 4h bearish EMA filter to SELL signals only. Avoid touching BUY logic — it's working. |
| **P6** | MACrossover | Only activates in TRENDING_STRONG. Most test periods are RANGING → 0 trades. | Consider enabling in TRENDING_MODERATE, or lower min confidence threshold. |

---

## 4. Technical Notes — Fixes Applied This Session

### [FIX] hedge_mode = False
Pacifica does not support opposing positions simultaneously. All cross-strategy position flips eliminated.
- SUI fills: 6,895 → 144
- SUI fees: $599 → $14

This was the single largest profitability lever.

---

### [FIX] SL/TP same-candle randomization
Previously, stop-loss was always processed before take-profit within the same candle (insertion order), introducing a systematic pessimistic bias.
Now: `random.shuffle(triggered)` before processing — removes directional preference.

---

### [NEW] Strategy attribution on trade logs
`exchange._current_strategy` set before each `place_order` call.
Every trade log entry now carries `"strategy"` and `"pnl"` fields for per-strategy performance attribution.

---

### [FIX] closed_trades vs total_fills
- `total_fills` = all order executions (opens + closes)
- `closed_trades` = completed round-trips with realised PnL only

Previously `total_trades` was `len(trade_log)` (total fills), making win rate calculations incorrect.

---

### [FIX] LiquidationCapture session auto-reset
4-hour boundary now automatically resets `session_trades = 0` inside `_can_trade()`:
```python
if now.hour // 4 != self.last_trade_time.hour // 4:
    self.session_trades = 0
```
Also wired `lc.record_trade()` call from the engine after LC execution.

---

### [FIX] GridTrading BUY + SELL pair
Grid now calls both `_create_grid_buy_signal()` and `_create_grid_sell_signal()` per candle, returning `[buy, sell]`.
Previously only the BUY side was wired up. Symbol whitelist fixed: `base_asset = symbol.split("-")[0]` before checking against `["BTC", "ETH", "SUI", ...]`.

---

### [NEW] 4h HTF filter on MomentumScalping
Added `_check_4h_trend_alignment()`:
- BUY signals require 4h EMA-fast > EMA-slow
- SELL signals require 4h EMA-fast < EMA-slow
- Returns `True` (allow) if insufficient 4h data

---

### [FIX] VWAP 1m ATR source
VWAPScalping now uses 1m candle ATR for SL/TP placement when `execution_tf_data["1m"]` is available, falling back to 15m ATR. Improves SL/TP precision for short-duration scalps.

---

### [CHANGE] MACrossover 20/50 SMA (was 50/200)
Previous 50/200 configuration required 200+ 4h candles; engine only provided 60.
Changed to 20/50. 4h history window also increased from 50 → 60 candles in engine.

---

### [NEW] run_strategy_sweep.py
New script: runs all strategies for a single selectable symbol, one at a time in isolated subprocesses.
- `logger.remove()` called before any imports in subprocess — suppresses all log spam
- Worker prints single JSON line to stdout
- Ranked summary table with color-coded verdicts
- Args: `--symbol`, `--start`, `--end`, `--capital`, `--strategies` (subset), `--save-reports`

```bash
# Run sweep on SUI
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol SUI-USDC

# Run only specific strategies on BTC
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC \
    --strategies GridTrading MomentumScalping VWAPScalping
```

---

*Internal use only — Pacifica Solana Perps*
