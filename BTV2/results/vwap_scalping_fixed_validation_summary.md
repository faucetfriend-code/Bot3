# VWAP Scalping FIXED Validation Results

## What Changed
- `VWAP_ATR_STOP_MULTIPLIER` changed from 7.0 → 0.7

## Test Configuration
- Period: 2020-01-01 to 2025-12-31 (6 years)
- Timeframe: 5m bars
- Data: 630,830 bars from G:/Candle Data/BTCUSDT_5m.parquet
- Modes: Normal (as-is) and Stricter validation

---

## Per-Year Results (Fixed - ATR=0.7)

### Normal Mode
| Year | Trades | WR% | PF  | Net% | Sharpe | MaxDD% |
|------|--------|-----|-----|------|--------|---------|
| 2020 | 39     | 48.7| 1.25| -4.5 | 0.52   | 6.1     |
| 2021 | 24     | 41.7| 1.52| -1.4 | 0.65   | 4.5     |
| 2022 | 21     | 28.6| 0.31| -6.1 | -2.19  | 6.2     |
| 2023 | 34     | 41.2| 1.31| -3.7 | 0.49   | 4.5     |
| 2024 | 22     | 36.4| 0.87| -3.7 | -0.25  | 3.7     |
| 2025 | 20     | 25.0| 0.36| -5.4 | -1.92  | 5.4     |
| **TOTAL** | **160** | **36.9%** | **0.94** | **-24.8%** | **-0.45** | **5.1%** |

### Strict Mode
| Year | Trades | WR% | PF  | Net% | Sharpe | MaxDD% |
|------|--------|-----|-----|------|--------|---------|
| 2020 | 26     | 53.8| 1.50| -2.2 | 0.77   | 4.0     |
| 2021 | 19     | 42.1| 1.92| +0.1 | 0.87   | 4.3     |
| 2022 | 8      | 25.0| 0.30| -2.2 | -1.37  | 2.4     |
| 2023 | 24     | 29.2| 0.65| -4.8 | -0.83  | 5.6     |
| 2024 | 15     | 40.0| 1.65| -1.2 | 0.74   | 1.9     |
| 2025 | 12     | 33.3| 0.44| -3.1 | -1.19  | 3.1     |
| **TOTAL** | **104** | **37.2%** | **1.08** | **-13.4%** | **-0.17** | **3.6%** |

---

## Comparison: ATR=7.0 vs ATR=0.7

| Metric | Previous (ATR=7.0) | Fixed (ATR=0.7) | Change |
|--------|-------------------|-----------------|--------|
| **Normal Total Net%** | -19.3% | -24.8% | **-5.5%** (worse) |
| **Strict Total Net%** | -9.5% | -13.4% | **-3.9%** (worse) |
| **Normal Avg MaxDD%** | 4.96% | 5.1% | +0.14% (worse) |
| **Strict Avg MaxDD%** | 3.48% | 3.6% | +0.12% (worse) |
| **Normal Total Trades** | 158 | 160 | +2 |
| **Strict Total Trades** | 91 | 104 | +13 |

---

## CRITICAL FINDING

**The ATR stop fix did NOT improve performance - it made things WORSE!**

### Why This Happened
1. **TP-SL Distance Mismatch**: The strategy exits at Session VWAP (mean) for TP, but uses ATR-based SL. On 5m bars, price often reaches the session mean before hitting the tighter ATR stop.

2. **Tighter Stop = More Stops**: ATR=0.7 (≈$350 on BTC at typical volatility) is TOO TIGHT for 5m scalping. Normal price oscillations easily trigger the stop.

3. **Exit Structure Problem**: The strategy uses "mean reversion to session VWAP" as TP but ATR-based SL. With tight stops, the SL distance is often smaller than the TP distance, resulting in poor reward-to-risk.

### Key Insight
- ATR=7.0 was "too wide" in absolute terms BUT it created favorable RRR by ensuring SL > TP distance
- ATR=0.7 is "correct" by convention BUT creates unfavorable RRR on the 5m timeframe

---

## Recommendations

1. **Revert to wider ATR stops** (5.0-7.0 range) for better RRR
2. **Or change TP mode** to ATR-based (atr_target × ATR) instead of VWAP-based
3. **Or use longer timeframes** (15m, 1h) where tighter stops work better

---

## Files Generated
- BTV2/results/vwap_scalping_fixed_validation.csv (raw data)
- BTV2/results/vwap_scalping_fixed_validation_summary.md (this report)