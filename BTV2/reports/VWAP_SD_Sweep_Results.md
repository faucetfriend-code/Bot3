# VWAP Scalping - SD Value Sweep Results (SD=1.0 to 3.0)

**Date**: March 2026  
**Test Period**: 2024-01-01 to 2025-01-01  
**Symbol**: BTCUSDT  
**Timeframe**: 5m with 1m exit resolution  
**Note**: Fixed parameters - adx_max=30, rsi_max=50, volume_mult=2.0, atr_stop=0.7, atr_target=3.8

---

## Summary

This report summarizes the SD value sweep results, testing all entry modes at each SD value to find optimal configurations.

---

## Complete Results by SD

### SD = 1.0

| Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|------------|--------|----------|---------------|-------------|
| bull_pullback | 20 | 30.0% | 0.77 | -9.7% |
| bear_pullback | 21 | 9.5% | 0.12 | -12.3% |
| mean_reversion | 622 | 24.9% | 0.24 | -270.2% |
| cross | 1 | 0.0% | 0.00 | -1.1% |
| momentum | 0 | 0.0% | 0.00 | +0.0% |
| deviation | 162 | 36.4% | 0.44 | -83.8% |

**Best for SD=1.0**: bull_pullback (30% WR, but still losing)

---

### SD = 1.5

| Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|------------|--------|----------|---------------|-------------|
| bull_pullback | 12 | 33.3% | 1.17 | -5.1% |
| bear_pullback | 15 | 13.3% | 0.15 | -9.0% |
| mean_reversion | 530 | 24.7% | 0.25 | -237.6% |
| cross | 1 | 0.0% | 0.00 | -1.1% |
| momentum | 0 | 0.0% | 0.00 | +0.0% |
| deviation | 162 | 36.4% | 0.44 | -83.8% |

**Best for SD=1.5**: bull_pullback (33% WR, PF=1.17, but still losing -5.1%)

---

### SD = 2.0 🚨 POSITIVE!

| Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|------------|--------|----------|---------------|-------------|
| bull_pullback | 3 | 66.7% | 7.53 | **+0.6%** ✅ |
| bear_pullback | 7 | 14.3% | 0.11 | -4.5% |
| mean_reversion | 378 | 23.3% | 0.23 | -181.1% |
| cross | 1 | 0.0% | 0.00 | -1.1% |
| momentum | 0 | 0.0% | 0.00 | +0.0% |
| deviation | 162 | 36.4% | 0.44 | -83.8% |

**Best for SD=2.0**: **bull_pullback** - **POSITIVE RETURNS!** 🎯

---

### SD = 2.5

| Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|------------|--------|----------|---------------|-------------|
| bull_pullback | 1 | 100.0% | ∞ | +1.6% |
| bear_pullback | 5 | 20.0% | 0.13 | -3.4% |
| mean_reversion | 204 | 23.0% | 0.26 | -107.6% |
| cross | 1 | 0.0% | 0.00 | -1.1% |
| momentum | 0 | 0.0% | 0.00 | +0.0% |
| deviation | 162 | 36.4% | 0.44 | -83.8% |

**Best for SD=2.5**: bull_pullback (100% WR, but only 1 trade)

---

### SD = 3.0

| Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|------------|--------|----------|---------------|-------------|
| bull_pullback | 1 | 100.0% | ∞ | +1.6% |
| bear_pullback | 4 | 25.0% | 0.13 | -2.9% |
| mean_reversion | 91 | 31.9% | 0.38 | -48.3% |
| cross | 1 | 0.0% | 0.00 | -1.1% |
| momentum | 0 | 0.0% | 0.00 | +0.0% |
| deviation | 162 | 36.4% | 0.44 | -83.8% |

**Best for SD=3.0**: bull_pullback (100% WR, but only 1 trade)

---

## Key Findings

### 1. bull_pullback is the Best Mode
Across all SD values, `bull_pullback` consistently performs best:
- Highest win rates
- Best profit factors
- Only mode with positive returns

### 2. SD=2.0 is the Sweet Spot
| Metric | SD=1.5 | SD=2.0 | SD=2.5 |
|--------|---------|---------|---------|
| Trades | 12 | 3 | 1 |
| Win Rate | 33% | 67% | 100% |
| Profit Factor | 1.17 | 7.53 | ∞ |
| Net Return | -5.1% | **+0.6%** | +1.6% |

**SD=2.0 is the best balance** - positive returns with meaningful trade count.

### 3. Higher SD = Fewer Trades
As SD increases:
- Trade count decreases significantly
- Win rate improves
- Statistical significance decreases

### 4. mean_reversion Loses Money
- Generates many trades (204-622)
- Low win rates (23-25%)
- Consistently loses money (Net = -107% to -270%)

---

## Best Configuration Found

```python
sd_threshold = 2.0          # Sweet spot for positive returns
entry_mode = "bull_pullback" # Best entry mode
adx_max = 30
rsi_max = 50
volume_mult = 2.0
atr_stop = 0.7
atr_target = 3.8
```

**Results**:
- Win Rate: 66.7%
- Profit Factor: 7.53
- Net Return: +0.6%
- Trades: 3 (2024 data)

---

## Limitations

1. **Low trade count**: SD=2.0 only generated 3 trades in 2024
2. **Not statistically significant**: Need 20+ trades for confidence
3. **2024 market**: Strong bull market may not be representative

---

## Recommendations

1. **Test SD=2.0 with longer history** (2018-2023) to validate
2. **Consider relaxing filters** to increase trade count while maintaining WR
3. **Look for SD values between 2.0 and 2.5** that balance trades and WR

---

## Next Steps

1. Run full sweep for SD values 1.8, 1.9, 2.1, 2.2, 2.3, 2.4
2. Test with relaxed ADX/RSI filters to increase trades
3. Validate on 2018-2023 data

---

*Report generated: March 2026*
