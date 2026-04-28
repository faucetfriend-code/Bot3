# VWAP All Entry Modes + Trend Direction Detection Report

## Test Configuration
- **Symbol**: BTCUSDT
- **Timeframe**: 5m (2024 full year)
- **Exit Resolution**: 1m (if available, else 5m)
- **Base Parameters**:
  - `sd_threshold`: 2.0
  - `adx_max`: 30.0
  - `rsi_max`: 50.0
  - `volume_mult`: 2.0
  - `atr_stop`: 0.7
  - `atr_target`: 3.5
  - `use_session_filter`: True
  - `tp_mode`: vwap
  - `require_reversal_candle`: True

---

## Test 1: Individual Entry Modes

| Mode | Trades | WR% | PF | Gross% | Net% |
|------|--------|-----|-----|--------|------|
| bull_pullback | 30 | 36.7 | 0.67 | -1.6 | -10.6 |
| bear_pullback | 14 | 35.7 | 0.28 | -1.6 | -5.8 |
| mean_reversion | 86 | 33.7 | 0.53 | -5.8 | -31.6 |
| cross | 18 | 5.6 | 0.11 | -3.5 | -8.9 |
| momentum | 3 | 0.0 | 0.00 | -0.5 | -1.4 |
| deviation | 22 | 31.8 | 0.63 | -1.7 | -8.3 |

**Best Individual Mode**: momentum (Net: -1.4%, WR: 0.0%, Trades: 3)

---

## Test 2+3: Dynamic Mode Selection (Trend-Based)

### Trend Detection Methods

| Trend Detection | Trades | WR% | PF | Gross% | Net% |
|-----------------|--------|-----|-----|--------|------|
| EMA Direction | 50 | 30.0 | 0.55 | -3.4 | -18.4 |
| Price Position | 7 | 14.3 | 0.02 | -1.2 | -3.3 |
| ADX + EMA | 98 | 36.7 | 0.54 | -6.4 | -35.8 |

**Best Dynamic Method**: Price Position (Net: -3.3%, WR: 14.3%, Trades: 7)

---

## Comparison: Static vs Dynamic

| Approach | Best Mode/Method | Trades | WR% | PF | Net% |
|----------|-----------------|--------|-----|-----|------|
| Static (Individual) | momentum | 3 | 0.0 | 0.00 | -1.4 |
| Dynamic (Trend-Based) | Price Position | 7 | 14.3 | 0.02 | -3.3 |

### Verdict
**Dynamic mode selection UNDERPERFORMS by 1.9% net return** vs the best static mode.

---

## Entry Mode Selection Logic

| Trend Direction | Entry Mode | Rationale |
|-----------------|------------|-----------|
| UPTREND | bull_pullback | Buy dips in uptrend |
| DOWNTREND | bear_pullback | Short rallies in downtrend |
| RANGING | mean_reversion | Fade to VWAP in range |
| BREAKOUT | cross | Trade VWAP crosses |
| MOMENTUM | momentum | Ride strong moves |
| EXTREME | deviation | Fade extreme deviations |

## Trend Detection Methods

1. **EMA Direction**: 1h EMA(9) vs EMA(21) — simple trend filter
2. **Price Position**: Price vs 1h VWAP — mean-reversion context
3. **ADX + EMA**: ADX > 25 + EMA direction — strength + direction combined

---
*Generated: 2026-04-04 13:54:26*
