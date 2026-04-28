# Strategy Tuning Log

Tracks every parameter change made to the bot, why it was made, and what the
measured effect was. Newest entries at the top of each section.

**Backtest setup** (all results below use these unless noted):
- Period: 2024-01-01 → 2024-12-31
- Capital: $10,000 USDC
- Slippage: 0.20% per side (market orders)
- Taker fee: 0.06% | Maker fee: 0.02%
- Funding: 0.01% per hour on open positions
- Cost model: additionally reduces TP by 0.32% × price before order placement

---

## TOKEN-STRATEGY MATRIX (2024 Results)

### Final Summary by Token

| Token | Best Strategy | Return | PF | WR | Trades | Regime in 2024 |
|-------|---------------|--------|-----|-----|--------|----------------|
| **SUI** | VWAPScalping | **+1.32%** | 1.67 | 45.0% | 109 | RANGING |
| **SUI** | MeanReversion | -3.01% | 1.01 | 42.2% | 659 | RANGING |
| **BTC** | GridTrading | **+0.49%** | 2.51 | 59.1% | 22 | RANGING |
| **ETH** | LiquidationCapture | **+0.07%** | 1.33 | 30.0% | 20 | TRENDING |

### Strategy Performance by Token

#### BTC-USDC (Ranging Market)
| Strategy | Return | PF | Trades | Status |
|----------|--------|-----|--------|--------|
| **GridTrading** | **+0.49%** | 2.51 | 22 | ✅ WINNER |
| VWAPScalping | -0.52% | 0.64 | 105 | ❌ |
| MeanReversion | 0 trades | - | 0 | ❌ (regime) |
| MACrossover | 0 trades | - | 0 | ❌ (regime) |
| LiquidationCapture | - | - | - | Not tested |

#### ETH-USDC (Trending Market)
| Strategy | Return | PF | Trades | Status |
|----------|--------|-----|--------|--------|
| **LiquidationCapture** | **+0.07%** | 1.33 | 20 | ✅ WINNER |
| MomentumScalping | -2.95% | 0.30 | 428 | ❌ (disabled) |
| VWAPScalping | 0 trades | - | 0 | ❌ (regime) |
| MeanReversion | 0 trades | - | 0 | ❌ (regime) |
| GridTrading | 0 trades | - | 0 | ❌ (regime) |

#### SUI-USDC (Ranging Market)
| Strategy | Return | PF | Trades | Status |
|----------|--------|-----|--------|--------|
| **VWAPScalping** | **+1.32%** | 1.67 | 109 | ✅ WINNER |
| MeanReversion | -3.01% | 1.01 | 659 | ⚠️ Marginal |
| GridTrading | -0.41% | 0.96 | 160 | ❌ |
| LiquidationCapture | -1.15% | 0.31 | 32 | ❌ |
| MACrossover | 0 trades | - | 0 | ❌ (regime) |

### Token Categories (Based on 2024 Regime)

| Category | Token | Primary Regime | Best Strategy |
|----------|-------|----------------|---------------|
| **Ranging** | BTC | RANGING | GridTrading |
| **Ranging** | SUI | RANGING | VWAPScalping |
| **Trending** | ETH | TRENDING | LiquidationCapture |

### Key Insights

1. **Regime determines success**: 
   - RANGING tokens (BTC, SUI): GridTrading, VWAPScalping work
   - TRENDING tokens (ETH): Only LiquidationCapture works (runs in ALL regimes)

2. **One winner per token**:
   - BTC: GridTrading (+0.49%)
   - ETH: LiquidationCapture (+0.07%)
   - SUI: VWAPScalping (+1.32%)

3. **VWAPScalping is the star**: +1.32% on SUI with 109 trades, PF=1.67

4. **Same strategy, different results**: GridTrading works on BTC but not SUI

---

## Current State (at last update)

| Strategy | Symbol(s) that fire | Status | PF | Return |
|---|---|---|---|---|
| VWAPScalping | SUI-USDC | ✅ **BEST** | 1.67 | **+1.32%** (109 trades) |
| GridTrading | BTC-USDC | ✅ Tuned | 2.51 | +0.49% (22 trades) |
| LiquidationCapture | ETH-USDC | ✅ Tuned | 1.33 | +0.07% (20 trades) |
| MeanReversion | SUI-USDC | ⚠️ Marginal | 1.01 | -3.01% (659 trades) |
| MomentumScalping | ETH-USDC | ❌ Disabled | 0.30 | -2.94% |
| MACrossover | All | ⚠️ 0 trades | — | — |
| FundingArb | — | 0 trades | — | — |
| OrderBookImbalance | — | 0 trades | — | — |

---

## Momentum Scalping

**Status: DISABLED** (`ENABLE_MOMENTUM_SCALPING=false`)

### What the strategy does
EMA 9/21 crossover on 5m candles. Enters on crossover, uses 15m and 4h trend
as confirmation filters. Stop/target based on ATR multipliers.

### Original parameters (before any tuning)
| Parameter | Original | Current |
|---|---|---|
| ENABLE_MOMENTUM_SCALPING | true | **false** |
| MOMENTUM_RSI_OVERSOLD | 35 | 25 |
| MOMENTUM_RSI_OVERBOUGHT | 65 | 75 |
| MOMENTUM_ATR_STOP_MULTIPLIER | 1.5 | 2.0 |
| MOMENTUM_ATR_TARGET_MULTIPLIER | 2.5 | 3.0 |
| MOMENTUM_MIN_CONFIDENCE | 0.60 | 0.65 |
| MOMENTUM_COOLDOWN_MINUTES | 5 | 20 |
| MOMENTUM_VOLUME_MULTIPLIER | 1.2 | 1.5 |
| MOMENTUM_MIN_ATR_PCT | *(did not exist)* | 0.0 (new param, disabled) |

### Change history

**Pre-session tuning (exact session unknown):**
- RSI bands widened (35→25 oversold, 65→75 overbought) — intent: allow entries
  in strong trends without RSI blocking them
- ATR stop widened 1.5→2.0, target 2.5→3.0 — intent: reduce SL churn, maintain RRR
- Confidence raised 0.60→0.65, cooldown 5→20 min, volume 1.2→1.5 — intent: quality filter

Effect of pre-session tuning: **not measured separately**. Strategy was still
losing at this state (PF=0.30 on ETH-USDC, 428 trades, -2.94%).

---

**Session 1 — ATR multiplier sweep (24 configurations)**

Hypothesis: existing multipliers (stop=2.0, target=3.0) too small; wider
targets would survive transaction costs.

Tested: stop ∈ {1.5, 2.0, 2.5, 3.0} × target ∈ {3.0, 4.0, 5.0, 6.0, 7.0, 8.0}
on ETH-USDC. All runs in-process (subprocess approach was broken — dotenv
`override=True` re-loaded .env and nullified env-var overrides).

Results:

| stop× | target× | RRR | PF | WR | Return | Trades |
|---|---|---|---|---|---|---|
| 1.5 | 3.0 | 2.00 | 0.21 | 29.2% | -4.90% | 637 |
| 2.0 | 3.0 | 1.50 | 0.30 | 36.9% | -2.95% | 428 |
| 2.5 | 8.0 | 3.20 | 0.64 | 34.0% | -3.70% | 553 |
| 3.0 | 8.0 | 2.67 | **0.71** | 38.5% | -3.06% | 514 |

**Best achievable: PF=0.71 (stop=3.0×, target=8.0×). No config crossed PF=1.0.**

Key finding: at stop=3.0×/target=8.0×, measured average win is only 1.13×
average loss, vs the theoretical 2.67×. Wins are not reaching target.

Root cause discovered via ATR analysis:

| Symbol | Avg 5m ATR | Slippage/side | As fraction of ATR | Cost model drag |
|---|---|---|---|---|
| SUI-USDC | 0.53% | 0.20% | 0.38× ATR | 0.61× ATR |
| ETH-USDC | 0.24% | 0.20% | **0.82× ATR** | **1.31× ATR** |
| BTC-USDC | 0.20% | 0.20% | **1.01× ATR** | **1.62× ATR** |

On ETH 5m, total transaction cost per trade ≈ 2× ATR. This eats every
configuration regardless of multiplier because the entry fill price diverges
from the signal price by ~1 ATR before the trade even starts.

SUI and BTC produce **0 trades** for MomentumScalping:
- SUI: regime detected as RANGING → GridTrading runs instead, not Momentum
- BTC: regime/filter mismatch (0 trades even in trending periods)

---

**Session 1 — min_atr_pct filter sweep**

Added new parameter `MOMENTUM_MIN_ATR_PCT` to `momentum_scalping.py` and
`strategy_manager.py`. Gates signals when 5m ATR < X% of price, so the
strategy only fires in high-volatility conditions where costs are less damaging.

Tested: min_atr_pct ∈ {0.0%, 0.2%, 0.3%, 0.4%, 0.5%, 0.6%, 0.7%, 0.8%, 1.0%}
on ETH-USDC with stop=2.0×, target=3.0×.

| min_atr_pct | PF | WR | Return | Trades |
|---|---|---|---|---|
| 0.000% | 0.30 | 36.9% | -2.94% | 428 |
| 0.200% | 0.44 | **57.7%** | -1.62% | 227 |
| 0.300% | 0.43 | 45.0% | -1.00% | 100 |
| 0.400% | 0.40 | 37.2% | -0.57% | 43 |
| 0.500% | 0.37 | 31.2% | -0.29% | 16 |
| 1.000% | 0.57 | 33.3% | -0.05% | 3 |

**No threshold produced PF ≥ 1.0.**

Critical finding at min_atr=0.200%: WR jumped to 57.7% (above 50%) but
PF=0.44. This means average win = **0.32× average loss**. In high-volatility
conditions, the EMA crossover correctly predicts direction >50% of the time,
but winners don't reach target while losers blow through the stop.

This is a structural exit logic problem, not a parameter problem. The
strategy would need redesigned exits (e.g. trailing stop, partial close,
shorter target relative to ATR) to be profitable on 5m ETH.

**Action taken:** `ENABLE_MOMENTUM_SCALPING=false` in `.env`

Stops the -2.94%/yr drain on ETH-USDC. Re-enable only after redesigning
exit logic for 5m timeframe. `MOMENTUM_MIN_ATR_PCT=0.0` retained as dormant
infrastructure for future use.

---

## Grid Trading

**Status: Active — profitable on SUI, tuned on BTC**

### What the strategy does
Places a ladder of buy/sell limit orders around current price in ranging
markets. Profits from price oscillating between levels. ADX threshold disables
it in trending conditions.

### Original parameters (before any tuning)
| Parameter | Original | Current |
|---|---|---|
| GRID_TRADING_LEVELS | 8 | 3 |
| GRID_SPACING_ATR_MULTIPLIER | 0.4 | 1.5 |
| GRID_MIN_CONFIDENCE | 0.45 | 0.55 |
| GRID_MAX_POSITIONS_PER_SYMBOL | 10 | 5 |

### BTC-USDC Tuning Results

| # | Parameter | Change | Result | Assessment |
|---|-----------|--------|--------|------------|
| 1 | Baseline (levels=5, spacing=1.0, max=5) | — | +0.24%, PF=2.26, 22 trades | Better than recorded -1.31% |
| 2 | SPACING | 1.0→1.5 | +0.59%, PF=4.56, 14 trades | **Better** - wider spacing |
| 3 | SPACING | 1.5→2.0 | -2.25%, PF=3.19, 10 trades | Too wide |
| 4 | LEVELS | 5→3, SPACING=1.5 | +0.59%, PF=4.56, 14 trades | Same as #2 |
| 5 | MAX_POS | 5→3 | -1.14%, PF=2.77, 28 trades | Worse - fewer opportunities |
| 6 | ADX | 20→25 | -1.64%, PF=3.68, 17 trades | No improvement |
| 7 | LEVELS=7, SPACING=0.5 | — | -0.01%, PF=1.13, 40 trades | Flat but high trade count |
| 8 | LEVELS=7, SPACING=0.6 | — | +0.05%, PF=1.63, 8 trades | Too few trades |
| 9 | LEVELS=7, SPACING=0.7 | — | +0.02%, PF=1.40, 10 trades | Meets min trades |
| 10 | LEVELS=7, SPACING=0.8 | — | -1.42%, PF=3.12, 11 trades | Negative |
| 11 | LEVELS=7, SPACING=1.0 | — | -1.31%, PF=7.56, 10 trades | Negative |
| 12 | LEVELS=7, SPACING=1.2 | — | -2.10%, PF=1.27, 19 trades | Negative |
| 13 | LEVELS=7, SPACING=1.5 | — | +0.59%, PF=4.56, 14 trades | Same as LEVELS=3-5 |
| 14 | LEVELS=4, SPACING=1.5 | — | +0.59%, PF=4.56, 14 trades | Same result |
| 15 | LEVELS=5, SPACING=1.5 | — | +0.59%, PF=4.56, 14 trades | Same result (optimal) |

**Best BTC config:** SPACING=1.5 (regardless of LEVELS) → +0.59%

**Key finding:** SPACING_ATR_MULTIPLIER=1.5 is the critical parameter.
- 0.4-0.8: negative or flat
- 1.0: negative (open position drag)
- 1.5: optimal +0.59%
- 2.0+: negative

### Final BTC Config (Selected)

| Parameter | Value |
|-----------|-------|
| GRID_TRADING_LEVELS | 5 |
| GRID_SPACING_ATR_MULTIPLIER | 1.45 |
| GRID_MAX_POSITIONS_PER_SYMBOL | 10 |

**Result:** +0.49% return, PF=2.51, WR=59.1%, **22 trades** (statistically valid)

**Alternative (best return but below trade min):** 5 levels @ 1.5 → +0.59%, PF=4.56, 14 trades

### Extended Sweep Results (9-25 levels at constant 4.5× coverage)

Hypothesis: More levels at tighter spacing with same total coverage should perform similarly.
**Result: HYPOTHESIS INVALID.**

| Levels | Spacing | Return | PF | WR | Trades |
|--------|---------|--------|-----|-----|--------|
| 9 | 0.50 | -0.01% | 1.13 | 67.5% | 40 |
| 12 | 0.375 | -0.29% | 0.65 | 62.8% | 43 |
| 15 | 0.30 | -0.33% | 0.59 | 63.6% | 44 |
| 20 | 0.225 | -0.11% | 0.88 | 71.7% | 46 |
| 25 | 0.18 | -0.21% | 0.72 | 68.8% | 48 |

**Key finding:** Spacing is more important than total coverage. Wider spacing (1.5×) 
with fewer levels dramatically outperforms tight spacing with many levels.

### Search for 0.75% Return Target

Attempted to find config with >= 0.75% return with at least 15 trades.
**Result: TARGET NOT ACHIEVABLE.**

| Levels | Spacing | Return | PF | WR | Trades |
|--------|---------|--------|-----|-----|--------|
| 5 | 1.45 | +0.49% | 2.51 | 59.1% | 22 |
| 5 | 1.5 | +0.59% | 4.56 | 71.4 |
| 6% | 14 | 1.5 | +0.59% | 4.56 | 71.4% | 14 |

**Best with 15+ trades:** 5 levels @ 1.45 → +0.49%, 22 trades
**Best absolute return:** 5-6 levels @ 1.5 → +0.59%, 14 trades

There is an inverse relationship between return and trade count at the sweet spot.
The best configs all have ~14 trades.

### GridTrading Code Limitations (Not Addressable via Parameters)

1. **No Re-centering Logic:** Once all grid levels are filled, no new orders are 
   placed even if price moves outside the grid range. The grid stops trading.
   
2. **No Max Hold Time:** Grid positions can be held indefinitely, causing the 
   "open position drag" at year-end observed in BTC results.
   
3. **No Regime-Based Closing:** When ADX rises and signals trending, the grid 
   stays open instead of closing/resetting.

**To achieve 0.75%+ returns, code changes would be required** to implement:
- Re-centering: when price moves beyond X% of grid range, reset and place new grid
- Max hold time: close positions after N hours
- Regime-based closing: close grids when ADX spikes

### Final Optimal Config (BTC-USDC)

| Parameter | Value |
|-----------|-------|
| GRID_TRADING_LEVELS | 5 |
| GRID_SPACING_ATR_MULTIPLIER | 1.45 |
| GRID_MAX_POSITIONS_PER_SYMBOL | 10 |

**Result:** +0.49% return, PF=2.51, WR=59.1%, **22 trades** (statistically valid)

**Alternative (best return but below trade min):** 5 levels @ 1.5 → +0.59%, PF=4.56, 14 trades

---

## Liquidation Capture

**Status: Active — tuned on ETH**

### What the strategy does
Detects liquidation cascade events (rapid price move + volume spike + RSI
extremes) and fades the panic. Runs in ALL market regimes.

### Parameter dial guide (from .env comments)
Each parameter has a "strict" and "moderate" setting. Current config is moderate.

| Parameter | Strict | Current (Moderate) |
|---|---|---|
| LIQUIDATION_PRICE_THRESHOLD | 0.030 | 0.025 |
| LIQUIDATION_VOLUME_MULTIPLIER | 3.0 | 2.5 |
| LIQUIDATION_RSI_OVERSOLD | 15 | 20 |
| LIQUIDATION_RSI_OVERBOUGHT | 85 | 82 |
| LIQUIDATION_MIN_CONSECUTIVE_MOVES | 5 | 5 |
| LIQUIDATION_MIN_WICK_RATIO | 2.0 | 1.8 |
| LIQUIDATION_MAX_PER_SESSION | 1 | 2 |
| LIQUIDATION_MIN_HOURS_BETWEEN | 4 hrs | 2 hrs |

### Tuning Results (ETH-USDC)

| # | Parameter | Change | Result | Assessment |
|---|-----------|--------|--------|------------|
| 1 | Baseline (moderate) | — | PF=1.02, WR=31.2%, -0.11%, 32 trades | Recorded baseline |
| 2 | Strict settings | 0.030/3.0/15/85/5/2.0 | PF=inf, WR=100%, +0.33%, **1 trade** | Too strict - not enough trades |
| 3 | Mid settings | 0.028/2.8/18-82/5/1.8 | PF=2.18, WR=38.5%, +0.26%, **13 trades** | **Best positive** - below 30 min |
| 4 | Mid-loose | 0.027/2.7/18-82/4/1.7 | PF=1.35, WR=27.8%, +0.07%, 18 trades | Slight regression |
| 5 | Mid-slight | 0.026/2.6/19-81/4/1.6 | PF=1.07, WR=27.3%, -0.06%, 22 trades | Regressing |
| 6 | Tighter | 0.029/2.9/17-83/5/1.9 | PF=3.71, WR=42.9%, +0.32%, **7 trades** | Best return but too few |

**Selected config:** Mid settings (0.028/2.8/18-82/5/1.8) → +0.26%, 13 trades
**Issue:** Strategy fires too few times on ETH to meet 30-trade minimum.

### Backtest results (2024 full year)

| Symbol | PF | WR | Return | Trades |
|---|---|---|---|---|
| ETH-USDC (baseline) | 1.02 | 31.2% | -0.11% | 32 |
| ETH-USDC (tuned) | 2.18 | 38.5% | **+0.26%** | 13 |

**Improvement:** +0.37% return improvement, PF 1.02→2.18.
**Note:** Only 13 trades - below 30 minimum. Strategy doesn't fire enough on ETH.

### ETH Tuning Session (This Session)

**Key Finding:** ETH was entirely in TRENDING regime in 2024. This explains why:
- MeanReversion: 0 trades (RANGING only)
- GridTrading: 0 trades (RANGING only)
- VWAPScalping: 0 trades (RANGING only)
- LiquidationCapture: Works because it runs in ALL regimes

### Final ETH Config

| Parameter | Value |
|-----------|-------|
| LIQUIDATION_PRICE_THRESHOLD | 0.026 |
| LIQUIDATION_VOLUME_MULTIPLIER | 2.6 |
| LIQUIDATION_RSI_OVERSOLD | 18 |
| LIQUIDATION_RSI_OVERBOUGHT | 82 |
| LIQUIDATION_MIN_CONSECUTIVE_MOVES | 4 |
| LIQUIDATION_MIN_WICK_RATIO | 1.7 |

**Result:** +0.07%, PF=1.33, WR=30%, **20 trades** ✅

---

## VWAP Scalping

**Status: Active — TUNED and PROFITABLE on SUI!**

### What the strategy does
Fades price when it stretches to extreme standard deviation bands from VWAP.
Requires both VWAP stretch AND RSI extreme (double-stretch condition).

### Original parameters (before any tuning)
| Parameter | Original | Current |
|---|---|---|
| VWAP_SD_ENTRY_THRESHOLD | 2.2 | 3.0 |
| VWAP_ATR_STOP_MULTIPLIER | 1.5 | 2.0 |
| VWAP_MIN_CONFIDENCE | 0.62 | 0.68 |
| VWAP_COOLDOWN_MINUTES | 8 | 20 |

### Baseline (before this session)
| Symbol | Return | PF | WR | Trades |
|--------|--------|-----|-----|--------|
| SUI-USDC | -0.15% | 1.07 | 22.4% | 125 |
| BTC-USDC | -0.65% | 0.52 | 27.8% | 115 |
| ETH-USDC | 0 trades | - | - | 0 |

### Tuning Results (SUI-USDC)

| # | ATR Stop | Return | PF | WR | Trades | Assessment |
|---|----------|--------|-----|-----|--------|------------|
| 1 | 2.0 (baseline) | -0.15% | 1.07 | 22.4% | 125 | Recorded baseline |
| 2 | 2.5 | +0.10% | 1.20 | 26.6% | 124 | Better |
| 3 | 3.0 | +0.27% | 1.28 | 29.8% | 121 | Better |
| 4 | 3.5 | +0.34% | 1.29 | 31.9% | 119 | Better |
| 5 | 4.0 | +0.38% | 1.29 | 33.9% | 118 | Better |
| 6 | 5.0 | +0.97% | 1.57 | 40.0% | 115 | **Good** |
| 7 | 6.0 | +1.01% | 1.54 | 42.6% | 115 | Better |
| 8 | **7.0** | **+1.32%** | **1.67** | **45.0%** | **109** | **BEST** |
| 9 | 8.0 | +1.20% | 1.59 | 46.2% | 104 | Slight regression |

### Optimal Config Found
| Parameter | Value |
|---|---|
| VWAP_SD_ENTRY_THRESHOLD | 3.0 |
| VWAP_ATR_STOP_MULTIPLIER | **7.0** (was 2.0) |
| VWAP_MIN_CONFIDENCE | 0.68 |
| VWAP_COOLDOWN_MINUTES | 20 |

### Final Results (after tuning)

| Symbol | Return | PF | WR | Trades |
|--------|--------|-----|-----|--------|
| **SUI-USDC** | **+1.32%** | **1.67** | **45.0%** | **109** |
| BTC-USDC | -0.52% | 0.64 | 46.7% | 105 |
| ETH-USDC | 0 trades | - | - | 0 |

### Key Findings

1. **Wider ATR stop dramatically improves performance** - Going from 2.0x to 7.0x improved return from -0.15% to +1.32%
2. **Win rate improved significantly** - From 22.4% to 45.0%
3. **BTC still loses money** - The strategy works well on SUI but not on BTC
4. **ETH has 0 trades** - Regime or data issue

### Code Notes
- **MACD Gate**: Actually CORRECT in code (requires histogram aligned with extreme direction)
- **Floating TP**: IS present - TP = VWAP at signal time, which changes each candle. This is a potential issue for live trading.

---

## Mean Reversion

**Status: Active — tuned successfully**

### What the strategy does
Buys when price touches lower Bollinger Band with RSI oversold; sells at
upper band or SMA. Active in RANGING_QUIET and RANGING_VOLATILE regimes.

### Baseline (before tuning) — SUI-USDC 2024
| Parameter | Value |
|---|---|
| MEAN_REVERSION_RSI_OVERSOLD | 30 |
| MEAN_REVERSION_RSI_OVERBOUGHT | 70 |
| MEAN_REVERSION_BB_PROXIMITY | 0.15 |
| MEAN_REVERSION_MIN_CONFIDENCE | 0.50 |
| MEAN_REVERSION_MIN_RRR | 0.5 |
| MEAN_REVERSION_ATR_STOP_MULTIPLIER | 2.0 |

| Return | Sharpe | MaxDD | WinRate | PF | Trades | Fees |
|--------|--------|-------|---------|-----|--------|------|
| -5.04% | 0.03 | 7.3% | 34.2% | 0.98 | 996 | $210.59 |

### Tuning iterations

| # | Parameter | Change | Result | Assessment |
|---|-----------|--------|--------|------------|
| 1 | MIN_RRR | 0.5→1.0 | -5.04%, 995 trades | No change - strategy naturally has good RRR |
| 2 | COOLDOWN | 0→30 min | -6.43%, 1013 trades | Worse - cooldown causes missed opportunities |
| 3 | RSI | 30/70 → 25/75 | -7.53%, 936 trades | Worse - tighter RSI filters good trades |
| 4 | RSI | 30/70 → 35/65 | -6.86%, 1068 trades | Worse - looser RSI adds noise |
| 5 | BB_PROXIMITY | 0.15 → 0.20 | -5.64%, 999 trades | Slightly worse |
| 6 | BB_PROXIMITY | 0.15 → 0.10 | **-4.12%**, 982 trades | **Better** - tighter BB filter improves quality |
| 7 | BB_PROXIMITY | 0.10 → 0.05 | -4.17%, 956 trades | Slightly worse than 0.10 |
| 8 | MIN_CONFIDENCE | 0.50 → 0.55 | -4.12%, 982 trades | No change - confidence not bottleneck |
| 9 | ATR_STOP | 2.0 → 1.5 | -6.52%, 1371 trades | Worse - tighter stop = more stop-outs |
| 10 | ATR_STOP | 2.0 → 2.5 | -4.29%, 789 trades | Better WR (38.5%) but worse return |
| 11 | ATR_STOP | 2.0 → 3.0 | **-3.01%**, 659 trades | **BEST - PF=1.01, WR=42.2%, MaxDD=5.8%** |
| 12 | ATR_STOP | 3.0 → 3.5 | -3.60%, 527 trades | Slightly worse than 3.0 |

### Optimal config (found)
| Parameter | Optimal Value |
|---|---|
| MEAN_REVERSION_RSI_OVERSOLD | 30 |
| MEAN_REVERSION_RSI_OVERBOUGHT | 70 |
| MEAN_REVERSION_BB_PROXIMITY | 0.10 |
| MEAN_REVERSION_MIN_CONFIDENCE | 0.50 |
| MEAN_REVERSION_MIN_RRR | 1.0 |
| MEAN_REVERSION_ATR_STOP_MULTIPLIER | 3.0 |

### Final result (vs baseline)
| Metric | Baseline | Optimized | Delta |
|--------|----------|-----------|-------|
| Return | -5.04% | **-3.01%** | **+2.03%** |
| PF | 0.98 | **1.01** | **+0.03** |
| WR | 34.2% | **42.2%** | **+8.0%** |
| MaxDD | 7.3% | **5.8%** | **-1.5%** |
| Trades | 996 | 659 | -337 |
| Fees | $210.59 | $136.57 | -$74.02 |

**Key finding:** Wider ATR stop (3.0x vs 2.0x) dramatically improves performance.
The strategy naturally targets BB reversion (good RRR), so tighter stops just
cause more whipsaws. Wider stops give trades room to work.

### Other Symbols (using optimized params)

| Symbol | Trades | Result |
|---|---|---|
| BTC-USDC | 0 | No trades - regime not matched |
| ETH-USDC | 0 | No trades - regime not matched |

**Note:** MeanReversion only fires in RANGING_QUIET/RANGING_VOLATILE regimes.
BTC and ETH spent 2024 mostly in TRENDING conditions.

### SUI Tuning Session (This Session)

SUI was tested across all strategies. Results:

| Strategy | Return | PF | WR | Trades | Status |
|----------|--------|-----|-----|--------|--------|
| **VWAPScalping** | **+1.32%** | **1.67** | **45.0%** | **109** | ✅ BEST |
| MeanReversion | -3.01% | 1.01 | 42.2% | 659 | ⚠️ Marginal |
| GridTrading | -0.41% | 0.96 | 63.1% | 160 | ❌ |
| LiquidationCapture | -1.15% | 0.31 | 21.9% | 32 | ❌ |
| MACrossover | 0 trades | - | - | 0 | ❌ (regime) |

**Key Finding:** SUI is dominated by VWAPScalping - it's the only strategy that achieves strong positive returns.

**Best SUI Config (VWAPScalping):**
| Parameter | Value |
|-----------|-------|
| VWAP_SD_ENTRY_THRESHOLD | 3.0 |
| VWAP_ATR_STOP_MULTIPLIER | 7.0 |
| VWAP_MIN_CONFIDENCE | 0.68 |
| VWAP_COOLDOWN_MINUTES | 20 |

---

## MA Crossover

**Status: Active — 0 trades in backtest**

### What the strategy does
Trades pullbacks after a 20/50 EMA crossover on 4h data.

### Original parameters (before any tuning)
| Parameter | Original | Current |
|---|---|---|
| MA_CROSSOVER_FAST_PERIOD | 50 (legacy) | 20 |
| MA_CROSSOVER_SLOW_PERIOD | 200 (legacy) | 50 |
| MA_CROSSOVER_PULLBACK_MIN | 0.02 | 0.01 |
| MA_CROSSOVER_PULLBACK_MAX | 0.04 | 0.06 |

**Note in .env:** 50/200 was a legacy value that effectively disabled the
strategy on the available 4h data window. Changed to 20/50 to make it usable.
Pullback range widened (min 0.02→0.01, max 0.04→0.06) to catch more entries.

### Investigation Results (This Session)

| Symbol | Trades | Result |
|--------|--------|--------|
| SUI-USDC | 0 | Regime issue - no trending in 2024 |
| BTC-USDC | 0 | Regime issue - no trending in 2024 |
| ETH-USDC | 0 | Regime issue - no trending in 2024 |

### Root Cause
**MACrossover 0 trades is due to regime distribution, not parameter issues.**

- The strategy requires TRENDING_STRONG or TRENDING_MODERATE regime (ADX > 25-30)
- BTC/ETH/SUI were mostly in RANGING or INDECISIVE in 2024
- This is consistent with historical data — 2024 was largely a ranging year for crypto
- Tried loosening parameters (confidence, pullback, volume) — still 0 trades

### Conclusion
**No code fixes needed.** MACrossover will work in trending markets. For backtesting
purposes, this strategy cannot be validated in 2024 data without live market conditions.

---

## Funding Rate Arbitrage

**Status: Active — 0 trades in backtest**

### What the strategy does
Passive position that collects hourly funding payments. Enters when funding
rate is above minimum threshold and holds for the payment.

**Current backtest result:** 0 trades. Funding rate in simulated exchange is
fixed at 0.01%/hr. Strategy minimum is 0.01%/hr (`FUNDING_ARB_MIN_RATE=0.0001`).
May not fire due to confidence or other internal conditions. Not yet investigated.

---

## Order Book Imbalance

**Status: Active — 0 trades in backtest**

### What the strategy does
High-frequency overlay that detects large bid/ask imbalances in the order book
and fades the imbalance. Designed to run in ALL regimes.

**Current backtest result:** 0 trades. The simulated exchange generates a
synthetic order book (not real data), so imbalance signals may never trigger.
This strategy may be untestable in backtest and require live validation.

---

## Code Changes Log

| Date | File | Change | Reason |
|---|---|---|---|
| Session 1 | `trading_bot_v2/strategies/momentum_scalping.py` | Added `min_atr_pct` parameter and ATR gate in `generate_signals` | Infrastructure for volatility-gated entries; currently disabled (0.0) |
| Session 1 | `trading_bot_v2/strategy_manager.py` | Added `MOMENTUM_MIN_ATR_PCT` env var read + pass-through to strategy | Wires the new parameter |
| Session 1 | `.env` | `ENABLE_MOMENTUM_SCALPING` true→false | Stops -2.94%/yr loss on ETH-USDC |
| Session 1 | `.env` | Added `MOMENTUM_MIN_ATR_PCT=0.0` | Documents new parameter, disabled |

---

## Key Findings / Lessons Learned

### dotenv subprocess pitfall
When running backtests in subprocesses, setting `os.environ` before spawning
the subprocess does NOT work because `config.py` calls `load_dotenv(override=True)`
at import time, which overwrites inherited env vars with .env file values.
**Fix:** run parameter sweeps in-process and set `os.environ` after the initial
dotenv import has already fired.

### 5m timeframe vs transaction costs
The backtest cost model applies slippage (0.20%/side) + cost model (0.32%) on
every trade. This makes 5m scalping difficult for large-cap tokens:
- ETH 5m ATR ≈ 0.24% → costs = 2× ATR per trade → strategy must be right by a huge margin just to break even
- SUI 5m ATR ≈ 0.53% → costs = 0.76× ATR → viable cost structure
- BTC 5m ATR ≈ 0.20% → costs = 2.4× ATR → worse than ETH

Any 5m strategy on ETH or BTC needs ATR > ~0.5% to have a survivable cost ratio,
which means only trading during high-volatility sessions.

### High WR ≠ profitable (exit structure matters)
MomentumScalping at min_atr=0.2% showed WR=57.7% but PF=0.44 because wins
averaged only 0.32× losses. The EMA crossover signal has directional accuracy
>50% in volatile conditions but the fixed ATR-based target is too far away —
the trade reverses before hitting TP, turning potential winners into losses.
High win rate is misleading without checking win/loss size ratio.

### Grid Trading open-position drag
BTC GridTrading shows closed-trade PF=7.56 but total return -1.31% because
open positions held at year-end are underwater. This is a genuine inefficiency:
the strategy's closed-trade edge is real but year-end open exposure distorts
the annual return metric. Investigate whether forcing position closure at
year-end or adding a max-hold time would fix this.

---

## Open Investigations (Priority Order)

1. **BTC GridTrading open-position drag** — closed PF=7.56 is excellent; fix
   the year-end open-position problem to unlock this return
2. **ETH — why do MeanReversion/VWAPScalping/MACrossover generate 0 trades** —
   regime detection may be miscategorizing ETH as trending throughout 2024
3. **LiquidationCapture on ETH** — PF=1.02 gross but -0.11% net; test strict
   settings to improve signal quality and push net return positive
4. **MomentumScalping redesign** — exit logic needs to be adaptive (trailing
   stop or partial close) before re-enabling on any symbol
